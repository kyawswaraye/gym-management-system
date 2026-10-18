"""Salary drafts and the single atomic posting path.

Lock order inside approval: salary period, its lines by id, the protected
``STAFF_SALARY`` category, then referenced cash sessions by id. ``mutate()``
reloads the actor and live permission. Expense and cash writes happen only
after those locks. Any failure rolls the period, lines, expenses, and cash
movements back. Void locks the posted period, its lines, the original
expenses and cash movements, then the named destination sessions. It does not
reopen a closed till.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import revalidate_cash_actor
from apps.expenses.models import Expense
from apps.expenses.services import post_staff_salary_expense, void_linked_salary_expense
from apps.members.models import Payment
from apps.members.services import gym_today
from apps.salaries.exceptions import SalaryPostingConflict
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.salaries.serializers import merged_period_fields, money_amount
from apps.tenants.models import ExpenseCategory, Gym, GymMembership, StaffProfile

_MAX_LINES = 200


def create_period(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.create")
    _require_ordered_dates(fields)
    period = SalaryPeriod.objects.create(
        gym=gym,
        period_start=fields["period_start"],
        period_end=fields["period_end"],
        business_date=fields["business_date"],
        description=fields["description"],
        status=SalaryPeriod.Status.DRAFT,
        created_with_reception_scope=_holds_reception(gym_id=gym.id, membership_id=actor.id),
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_CREATED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"line_count": 0},
    )
    return period


def update_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.update")
    period = _lock_visible_period(gym_id=gym.id, period_id=period_id, actor=actor)
    if period.status != SalaryPeriod.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft salary period can be edited."]})
    merged = merged_period_fields(period, fields)
    _require_ordered_dates(merged)
    period.period_start = merged["period_start"]
    period.period_end = merged["period_end"]
    period.business_date = merged["business_date"]
    period.description = merged["description"]
    period.updated_by_membership_id = actor.id
    period.save()
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_UPDATED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={},
    )
    return period


def replace_lines(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    lines: list[dict[str, Any]],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.update")
    period = _lock_visible_period(gym_id=gym.id, period_id=period_id, actor=actor)
    if period.status != SalaryPeriod.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft salary period can change lines."]})
    if len(lines) > _MAX_LINES:
        raise ValidationError({"lines": ["A salary period can contain at most 200 lines."]})
    staff_ids = [row["staff_profile_id"] for row in lines]
    if len(staff_ids) != len(set(staff_ids)):
        raise ValidationError({"lines": ["Each staff member can appear once in the period."]})
    for row in lines:
        if row["currency"] != gym.currency:
            raise ValidationError({"lines": ["Currency must match the selected gym."]})
    ordered_ids = sorted(staff_ids, key=str)
    profiles = {
        profile.id: profile
        for profile in StaffProfile.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=ordered_ids)
        .order_by("id")
    }
    if set(profiles) != set(staff_ids):
        raise ValidationError({"lines": ["Select staff profiles of this gym."]})
    list(period.lines.select_for_update().order_by("id"))
    period.lines.all().delete()
    StaffSalaryLine.objects.bulk_create(
        [
            StaffSalaryLine(
                gym=gym,
                salary_period=period,
                staff_profile=profiles[row["staff_profile_id"]],
                amount=row["amount"],
                currency=row["currency"],
                payment_method=row["payment_method"],
                external_reference=str(row.get("external_reference") or ""),
                description=row["description"],
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
            )
            for row in lines
        ]
    )
    period.updated_by_membership_id = actor.id
    period.save(update_fields=["updated_by_membership_id", "updated_at"])
    total = sum((row["amount"] for row in lines), start=Decimal("0.00"))
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_LINES_REPLACED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"line_count": len(lines), "amount": money_amount(total)},
    )
    return period


def submit_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.submit")
    period = _lock_visible_period(gym_id=gym.id, period_id=period_id, actor=actor)
    if period.status != SalaryPeriod.Status.DRAFT:
        raise SalaryPostingConflict()
    lines = list(period.lines.select_for_update().order_by("id"))
    if not lines:
        raise ValidationError({"lines": ["Add at least one salary line before submission."]})
    now = timezone.now()
    period.status = SalaryPeriod.Status.PENDING_APPROVAL
    period.submitted_at = now
    period.submitted_by_membership_id = actor.id
    period.submitted_with_reception_scope = _holds_reception(gym_id=gym.id, membership_id=actor.id)
    period.updated_by_membership_id = actor.id
    period.save()
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_SUBMITTED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"line_count": len(lines)},
    )
    return period


def approve_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    cash_sessions: list[dict[str, Any]],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[SalaryPeriod, list[Expense], list[CashTransaction]]:
    if not connection.in_atomic_block:
        raise RuntimeError("Salary approval requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    period = _lock_period(gym_id=gym.id, period_id=period_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if period.status != SalaryPeriod.Status.PENDING_APPROVAL:
        raise SalaryPostingConflict()
    submitter_id = period.submitted_by_membership_id
    if submitter_id is None or actor.id in {period.created_by_membership_id, submitter_id}:
        raise PermissionDenied()
    lines = list(period.lines.select_for_update().order_by("id"))
    if not lines:
        raise ValidationError({"lines": ["A salary period needs at least one line."]})
    category = (
        ExpenseCategory.objects.select_for_update()
        .filter(gym_id=gym.id, system_key="STAFF_SALARY", is_active=True)
        .first()
    )
    if category is None:
        raise ValidationError({"category": ["The staff salary category is not available."]})
    by_id = {line.id: line for line in lines}
    mappings = {row["line_id"]: row["cash_session_id"] for row in cash_sessions}
    if set(mappings) - set(by_id):
        raise ValidationError({"cash_sessions": ["Each mapping must name a line on this period."]})
    cash_lines = [line for line in lines if line.payment_method == Payment.Method.CASH]
    if set(mappings) != {line.id for line in cash_lines}:
        raise ValidationError(
            {"cash_sessions": ["Map every cash line once and do not map a non-cash line."]}
        )
    session_ids = sorted(set(mappings.values()), key=str)
    sessions = {
        session.id: session
        for session in CashSession.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=session_ids)
        .order_by("id")
    }
    if set(sessions) != set(session_ids):
        raise ValidationError({"cash_sessions": ["Select an open cash session of this gym."]})
    for line in cash_lines:
        session = sessions[mappings[line.id]]
        if (
            session.status != CashSession.Status.OPEN
            or session.currency != line.currency
            or session.business_date != period.business_date
            or session.gym_id != gym.id
        ):
            raise ValidationError(
                {
                    "cash_sessions": [
                        "Each cash line requires an open session with the same currency and date."
                    ]
                }
            )
        _assert_period_till(period=period, session=session)
    expenses: list[Expense] = []
    movements: list[CashTransaction] = []
    posted_lines: list[tuple[StaffSalaryLine, Expense, UUID | None]] = []
    try:
        for line in lines:
            session_id = mappings.get(line.id)
            expense, movement = post_staff_salary_expense(
                gym=gym,
                approver=actor,
                category=category,
                created_by_membership_id=period.created_by_membership_id,
                submitted_by_membership_id=submitter_id,
                business_date=period.business_date,
                description=line.description,
                amount=line.amount,
                currency=line.currency,
                payment_method=line.payment_method,
                cash_session_id=session_id,
                policy_note=f"Salary period {period.id} line {line.id}",
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            posted_lines.append((line, expense, session_id))
            expenses.append(expense)
            if movement is not None:
                movements.append(movement)
        now = timezone.now()
        period.status = SalaryPeriod.Status.POSTED
        period.approved_at = now
        period.approved_by_membership_id = actor.id
        period.posted_at = now
        period.posted_by_membership_id = actor.id
        period.updated_by_membership_id = actor.id
        period.save()
        for line, expense, session_id in posted_lines:
            line.expense = expense
            line.cash_session_id = session_id
            line.updated_by_membership_id = actor.id
            line.save(
                update_fields=[
                    "expense",
                    "cash_session",
                    "updated_by_membership_id",
                    "updated_at",
                ]
            )
    except IntegrityError as exc:
        raise SalaryPostingConflict() from exc
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_APPROVED_POSTED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "line_count": len(lines),
            "expense_count": len(expenses),
            "cash_count": len(movements),
        },
    )
    return period, expenses, movements


def void_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    reason: str,
    business_date: date | None,
    cash_sessions: list[dict[str, Any]],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[SalaryPeriod, list[tuple[StaffSalaryLine, Expense, CashTransaction | None]]]:
    """Void one posted period and post one reversal expense per line."""

    if not connection.in_atomic_block:
        raise RuntimeError("Salary void requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.void")
    period = _lock_period(gym_id=gym.id, period_id=period_id)
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.void")
    if period.status != SalaryPeriod.Status.POSTED:
        raise SalaryPostingConflict()
    lines = list(period.lines.select_for_update().order_by("id"))
    if not lines:
        raise SalaryPostingConflict()
    expense_ids = [line.expense_id for line in lines if line.expense_id is not None]
    expenses = {
        expense.id: expense
        for expense in Expense.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=expense_ids)
        .order_by("id")
    }
    if set(expenses) != set(expense_ids) or len(expense_ids) != len(lines):
        raise SalaryPostingConflict()
    category_ids = sorted({expense.category_id for expense in expenses.values()}, key=str)
    list(
        ExpenseCategory.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=category_ids)
        .order_by("id")
    )
    movement_ids = sorted(
        (
            expense.cash_transaction_id
            for expense in expenses.values()
            if expense.cash_transaction_id is not None
        ),
        key=str,
    )
    list(
        CashTransaction.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=movement_ids)
        .order_by("id")
    )
    existing = list(
        Expense.objects.select_for_update()
        .filter(gym_id=gym.id, original_expense_id__in=expense_ids)
        .order_by("id")
    )
    if existing or any(expense.reversal_expense_id is not None for expense in expenses.values()):
        raise SalaryPostingConflict()
    chosen_date = _correction_date(
        gym=gym, original_date=period.business_date, requested=business_date
    )
    by_id = {line.id: line for line in lines}
    mappings = {row["line_id"]: row["cash_session_id"] for row in cash_sessions}
    if set(mappings) - set(by_id):
        raise ValidationError({"cash_sessions": ["Each mapping must name a line on this period."]})
    cash_lines = [line for line in lines if line.payment_method == Payment.Method.CASH]
    if set(mappings) != {line.id for line in cash_lines}:
        raise ValidationError(
            {"cash_sessions": ["Map every cash line once and do not map a non-cash line."]}
        )
    session_ids = sorted(set(mappings.values()), key=str)
    sessions = {
        session.id: session
        for session in CashSession.objects.select_for_update()
        .filter(gym_id=gym.id, id__in=session_ids)
        .order_by("id")
    }
    if set(sessions) != set(session_ids):
        raise ValidationError({"cash_sessions": ["Select an open cash session of this gym."]})
    for line in cash_lines:
        session = sessions[mappings[line.id]]
        if (
            session.status != CashSession.Status.OPEN
            or session.gym_id != gym.id
            or session.currency != line.currency
            or session.business_date != chosen_date
        ):
            raise ValidationError(
                {
                    "cash_sessions": [
                        "Each cash line requires an open session for the correction date."
                    ]
                }
            )
    reversals: list[tuple[StaffSalaryLine, Expense, CashTransaction | None]] = []
    try:
        for line in lines:
            expense_id = line.expense_id
            if expense_id is None:
                raise SalaryPostingConflict()
            original = expenses[expense_id]
            _original, reversal, movement = void_linked_salary_expense(
                gym=gym,
                actor=actor,
                original=original,
                reason=reason,
                business_date=chosen_date,
                cash_session_id=mappings.get(line.id),
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            reversals.append((line, reversal, movement))
        now = timezone.now()
        period.status = SalaryPeriod.Status.VOID
        period.voided_at = now
        period.voided_by_membership_id = actor.id
        period.void_business_date = chosen_date
        period.reason = reason
        period.updated_by_membership_id = actor.id
        period.save()
    except IntegrityError as exc:
        raise SalaryPostingConflict() from exc
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_VOIDED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "line_count": len(lines),
            "reversal_count": len(reversals),
            "void_business_date": chosen_date.isoformat(),
        },
    )
    return period, reversals


def reject_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    period = _lock_period(gym_id=gym.id, period_id=period_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if period.status != SalaryPeriod.Status.PENDING_APPROVAL:
        raise SalaryPostingConflict()
    if actor.id in {period.created_by_membership_id, period.submitted_by_membership_id}:
        raise PermissionDenied()
    list(period.lines.select_for_update().order_by("id"))
    now = timezone.now()
    period.status = SalaryPeriod.Status.REJECTED
    period.rejected_at = now
    period.rejected_by_membership_id = actor.id
    period.rejection_reason = reason
    period.updated_by_membership_id = actor.id
    period.save()
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_REJECTED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={},
    )
    return period


def cancel_period(
    *,
    gym: Gym,
    actor: GymMembership,
    period_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> SalaryPeriod:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="salaries.update")
    period = _lock_visible_period(gym_id=gym.id, period_id=period_id, actor=actor)
    if period.status != SalaryPeriod.Status.DRAFT or period.submitted_at is not None:
        raise ValidationError({"status": ["Only an unsubmitted draft can be cancelled."]})
    list(period.lines.select_for_update().order_by("id"))
    now = timezone.now()
    period.status = SalaryPeriod.Status.CANCELLED
    period.cancelled_at = now
    period.cancelled_by_membership_id = actor.id
    period.reason = reason
    period.updated_by_membership_id = actor.id
    period.save()
    _audit(
        gym=gym,
        actor=actor,
        action="SALARY_PERIOD_CANCELLED",
        period=period,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={},
    )
    return period


def command_route(*, period_id: UUID | None, action: str) -> str:
    if period_id is None:
        return "/api/v1/salary-periods/"
    if not action:
        return f"/api/v1/salary-periods/{period_id}/"
    return f"/api/v1/salary-periods/{period_id}/{action}/"


def _reload(
    *, gym_id: UUID, membership_id: UUID, permission_code: str
) -> tuple[Gym, GymMembership]:
    return revalidate_cash_actor(
        gym_id=gym_id,
        membership_id=membership_id,
        permission_code=permission_code,
    )


def _assert_approver(*, gym_id: UUID, actor: GymMembership) -> None:
    if holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="RECEPTIONIST"):
        raise PermissionDenied()
    manager = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="MANAGER")
    owner = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="OWNER")
    if not manager and not owner:
        raise PermissionDenied()
    if not membership_has_permission(membership=actor, code="salaries.approve"):
        raise PermissionDenied()


def _lock_period(*, gym_id: UUID, period_id: UUID) -> SalaryPeriod:
    period = SalaryPeriod.objects.select_for_update().filter(gym_id=gym_id, id=period_id).first()
    if period is None:
        raise Http404
    return period


def _lock_visible_period(*, gym_id: UUID, period_id: UUID, actor: GymMembership) -> SalaryPeriod:
    period = _lock_period(gym_id=gym_id, period_id=period_id)
    if not _holds_reception(gym_id=gym_id, membership_id=actor.id):
        return period
    if actor.id not in {period.created_by_membership_id, period.submitted_by_membership_id}:
        raise Http404
    if period.status in {SalaryPeriod.Status.POSTED, SalaryPeriod.Status.VOID}:
        raise Http404
    return period


def _holds_reception(*, gym_id: UUID, membership_id: UUID) -> bool:
    return holds_protected_role(
        gym_id=gym_id,
        membership_id=membership_id,
        role_key="RECEPTIONIST",
    )


def _assert_period_till(*, period: SalaryPeriod, session: CashSession) -> None:
    scoped = (
        (period.created_with_reception_scope, period.created_by_membership_id),
        (period.submitted_with_reception_scope, period.submitted_by_membership_id),
    )
    for required, membership_id in scoped:
        if not required or membership_id is None:
            continue
        if session.opened_by_membership_id != membership_id:
            raise ValidationError(
                {
                    "cash_sessions": [
                        "The cash session must be opened by the responsible receptionist."
                    ]
                }
            )


def _correction_date(*, gym: Gym, original_date: date, requested: date | None) -> date:
    chosen = gym_today(gym) if requested is None else requested
    if chosen > gym_today(gym):
        raise ValidationError({"business_date": ["The business date cannot be after today."]})
    if chosen < original_date:
        raise ValidationError(
            {"business_date": ["The business date cannot be before the original business date."]}
        )
    return chosen


def _require_ordered_dates(fields: dict[str, Any]) -> None:
    if fields["period_end"] < fields["period_start"]:
        raise ValidationError({"period_end": ["Period end must be on or after period start."]})


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    period: SalaryPeriod,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    extra: dict[str, object],
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="salaries",
        target_type="SalaryPeriod",
        target_id=period.id,
        request_id=request_id,
        new_values={
            "status": period.status,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "business_date": period.business_date.isoformat(),
            **extra,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
