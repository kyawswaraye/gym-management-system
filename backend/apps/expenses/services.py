"""Expense drafts and the single posting path.

Lock order inside approval: expense, receipt file, cash session, then the
cash transaction. ``mutate()`` reloads the actor and live permission. A
failure rolls every side effect back. Public void stays unpublished.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import record_cash_movement, revalidate_cash_actor
from apps.expenses.exceptions import ExpensePostingConflict, ProtectedExpenseCategory
from apps.expenses.models import Expense
from apps.expenses.serializers import merged_expense_fields, money_amount
from apps.files.constants import EXPENSE_RECEIPT_PURPOSE, GYM_OWNER
from apps.files.models import FileAsset
from apps.members.models import Payment
from apps.tenants.models import ExpenseCategory, Gym, GymMembership

_SERVICE_ONLY = frozenset({"STAFF_SALARY", "PT_COMMISSION"})
_RESERVED_CODES = frozenset(
    {
        "RENT",
        "UTILITIES",
        "INTERNET",
        "STAFF_SALARY",
        "PT_COMMISSION",
        "CLEANING_SUPPLIES",
        "MAINTENANCE",
        "MARKETING",
        "WATER_DRINKS",
        "OTHER",
    }
)


def create_category(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ExpenseCategory:
    gym, actor = _reload(
        gym_id=gym.id, membership_id=actor.id, permission_code="expense_categories.manage"
    )
    code = str(fields["code"])
    if code in _RESERVED_CODES:
        raise ProtectedExpenseCategory()
    name = str(fields["name"])
    normalized = name.strip().upper()
    try:
        category = ExpenseCategory.objects.create(
            gym=gym,
            code=code,
            name=name,
            normalized_name=normalized,
            system_key=None,
            display_order=int(fields.get("display_order", 100)),
            is_active=True,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": ["A category with this code or name already exists."]}
        ) from exc
    _audit_category(
        gym=gym,
        actor=actor,
        action="EXPENSE_CATEGORY_CREATED",
        category=category,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return category


def update_category(
    *,
    gym: Gym,
    actor: GymMembership,
    category_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ExpenseCategory:
    gym, actor = _reload(
        gym_id=gym.id, membership_id=actor.id, permission_code="expense_categories.manage"
    )
    category = (
        ExpenseCategory.objects.select_for_update().filter(gym_id=gym.id, id=category_id).first()
    )
    if category is None:
        raise Http404
    if category.system_key:
        raise ProtectedExpenseCategory()
    if "name" in fields:
        category.name = str(fields["name"])
        category.normalized_name = category.name.strip().upper()
    if "display_order" in fields:
        category.display_order = int(fields["display_order"])
    if "is_active" in fields:
        category.is_active = bool(fields["is_active"])
        category.archived_at = None if category.is_active else timezone.now()
    category.updated_by_membership_id = actor.id
    try:
        category.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"name": ["A category with this code or name already exists."]}
        ) from exc
    _audit_category(
        gym=gym,
        actor=actor,
        action="EXPENSE_CATEGORY_UPDATED",
        category=category,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return category


def create_expense_draft(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Expense:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.create")
    category, receipt = _sources(gym=gym, fields=fields, require_clean=False)
    expense = Expense.objects.create(
        gym=gym,
        category=category,
        business_date=fields["business_date"],
        description=fields["description"],
        amount=fields["amount"],
        currency=fields["currency"],
        payment_method=fields["payment_method"],
        receipt_file_asset=receipt,
        policy_note=str(fields.get("policy_note") or ""),
        status=Expense.Status.DRAFT,
        created_with_reception_scope=_holds_reception(gym_id=gym.id, membership_id=actor.id),
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_DRAFT_CREATED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return expense


def update_expense_draft(
    *,
    gym: Gym,
    actor: GymMembership,
    expense_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Expense:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.update")
    expense = _lock_visible_expense(gym_id=gym.id, expense_id=expense_id, actor=actor)
    if expense.status != Expense.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft expense can be edited."]})
    merged = merged_expense_fields(expense, fields)
    category, receipt = _sources(gym=gym, fields=merged, require_clean=False)
    expense.category = category
    expense.business_date = merged["business_date"]
    expense.description = merged["description"]
    expense.amount = merged["amount"]
    expense.currency = merged["currency"]
    expense.payment_method = merged["payment_method"]
    expense.receipt_file_asset = receipt
    expense.policy_note = str(merged.get("policy_note") or "")
    expense.updated_by_membership_id = actor.id
    expense.save()
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_DRAFT_UPDATED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return expense


def submit_expense(
    *,
    gym: Gym,
    actor: GymMembership,
    expense_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Expense:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.submit")
    expense = _lock_visible_expense(gym_id=gym.id, expense_id=expense_id, actor=actor)
    if expense.status != Expense.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft expense can be submitted."]})
    _sources(
        gym=gym,
        fields={
            "category_id": expense.category_id,
            "currency": expense.currency,
            "receipt_file_asset_id": expense.receipt_file_asset_id,
        },
        require_clean=True,
        lock_receipt=True,
    )
    now = timezone.now()
    expense.status = Expense.Status.PENDING_APPROVAL
    expense.submitted_at = now
    expense.submitted_by_membership_id = actor.id
    expense.submitted_with_reception_scope = _holds_reception(gym_id=gym.id, membership_id=actor.id)
    expense.updated_by_membership_id = actor.id
    expense.save()
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_SUBMITTED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return expense


def approve_expense(
    *,
    gym: Gym,
    actor: GymMembership,
    expense_id: UUID,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[Expense, CashSession | None, CashTransaction | None]:
    if not connection.in_atomic_block:
        raise RuntimeError("Expense approval requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    expense = _lock_expense(gym_id=gym.id, expense_id=expense_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if expense.status != Expense.Status.PENDING_APPROVAL:
        raise ExpensePostingConflict()
    if actor.id in {expense.created_by_membership_id, expense.submitted_by_membership_id}:
        raise PermissionDenied()
    _sources(
        gym=gym,
        fields={
            "category_id": expense.category_id,
            "currency": expense.currency,
            "receipt_file_asset_id": expense.receipt_file_asset_id,
        },
        require_clean=True,
        lock_receipt=True,
    )
    movement: CashTransaction | None = None
    if expense.payment_method == Payment.Method.CASH:
        if cash_session_id is None:
            raise ValidationError({"cash_session_id": ["A cash expense requires an open session."]})
        movement = record_cash_movement(
            gym=gym,
            actor=actor,
            session_id=cash_session_id,
            direction=CashTransaction.Direction.OUT,
            source_type=CashTransaction.SourceType.EXPENSE,
            source_id=expense.id,
            amount=expense.amount,
            currency=expense.currency,
            business_date=expense.business_date,
            description=expense.description,
            reason="",
            reversal_of_id=None,
        )
        _assert_reception_till(expense=expense, session=movement.cash_session)
    elif cash_session_id is not None:
        raise ValidationError({"cash_session_id": ["Only a cash expense can use a cash session."]})
    now = timezone.now()
    expense.status = Expense.Status.POSTED
    expense.approved_at = now
    expense.approved_by_membership_id = actor.id
    expense.posted_at = now
    expense.posted_by_membership_id = actor.id
    expense.updated_by_membership_id = actor.id
    if movement is not None:
        expense.cash_session = movement.cash_session
        expense.cash_transaction = movement
    expense.save()
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_APPROVED_POSTED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session = movement.cash_session if movement is not None else None
    return expense, session, movement


def reject_expense(
    *,
    gym: Gym,
    actor: GymMembership,
    expense_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Expense:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    expense = _lock_expense(gym_id=gym.id, expense_id=expense_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if expense.status != Expense.Status.PENDING_APPROVAL:
        raise ExpensePostingConflict()
    if actor.id in {expense.created_by_membership_id, expense.submitted_by_membership_id}:
        raise PermissionDenied()
    now = timezone.now()
    expense.status = Expense.Status.REJECTED
    expense.rejected_at = now
    expense.rejected_by_membership_id = actor.id
    expense.rejection_reason = reason
    expense.updated_by_membership_id = actor.id
    expense.save()
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_REJECTED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return expense


def cancel_expense(
    *,
    gym: Gym,
    actor: GymMembership,
    expense_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Expense:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="expenses.update")
    expense = _lock_visible_expense(gym_id=gym.id, expense_id=expense_id, actor=actor)
    if expense.status != Expense.Status.DRAFT or expense.submitted_at is not None:
        raise ValidationError({"status": ["Only an unsubmitted draft can be cancelled."]})
    now = timezone.now()
    expense.status = Expense.Status.CANCELLED
    expense.cancelled_at = now
    expense.cancelled_by_membership_id = actor.id
    expense.reason = reason
    expense.updated_by_membership_id = actor.id
    expense.save()
    _audit(
        gym=gym,
        actor=actor,
        action="EXPENSE_CANCELLED",
        expense=expense,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return expense


def command_route(*, expense_id: UUID | None, action: str) -> str:
    if expense_id is None:
        return "/api/v1/expenses/"
    if not action:
        return f"/api/v1/expenses/{expense_id}/"
    return f"/api/v1/expenses/{expense_id}/{action}/"


def category_route(*, category_id: UUID | None) -> str:
    if category_id is None:
        return "/api/v1/expense-categories/"
    return f"/api/v1/expense-categories/{category_id}/"


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
    if not membership_has_permission(membership=actor, code="expenses.approve"):
        raise PermissionDenied()


def _lock_expense(*, gym_id: UUID, expense_id: UUID) -> Expense:
    expense = Expense.objects.select_for_update().filter(gym_id=gym_id, id=expense_id).first()
    if expense is None:
        raise Http404
    return expense


def _lock_visible_expense(*, gym_id: UUID, expense_id: UUID, actor: GymMembership) -> Expense:
    """Hide another person's expense from a protected Reception membership."""

    expense = _lock_expense(gym_id=gym_id, expense_id=expense_id)
    if not _holds_reception(gym_id=gym_id, membership_id=actor.id):
        return expense
    if actor.id not in {expense.created_by_membership_id, expense.submitted_by_membership_id}:
        raise Http404
    return expense


def _holds_reception(*, gym_id: UUID, membership_id: UUID) -> bool:
    return holds_protected_role(
        gym_id=gym_id,
        membership_id=membership_id,
        role_key="RECEPTIONIST",
    )


def _assert_reception_till(*, expense: Expense, session: CashSession) -> None:
    scoped = (
        (expense.created_with_reception_scope, expense.created_by_membership_id),
        (expense.submitted_with_reception_scope, expense.submitted_by_membership_id),
    )
    for required, membership_id in scoped:
        if not required or membership_id is None:
            continue
        if session.opened_by_membership_id != membership_id:
            raise ValidationError(
                {
                    "cash_session_id": [
                        "The cash session must be opened by the responsible receptionist."
                    ]
                }
            )


def _sources(
    *,
    gym: Gym,
    fields: dict[str, Any],
    require_clean: bool,
    lock_receipt: bool = False,
) -> tuple[ExpenseCategory, FileAsset | None]:
    if fields["currency"] != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    category = (
        ExpenseCategory.objects.select_for_update()
        .filter(gym_id=gym.id, id=fields["category_id"])
        .first()
    )
    if category is None or not category.is_active:
        raise ValidationError({"category_id": ["Select an active category of this gym."]})
    if category.system_key in _SERVICE_ONLY:
        raise ProtectedExpenseCategory()
    receipt_id = fields.get("receipt_file_asset_id")
    receipt = None
    if receipt_id is not None:
        queryset = FileAsset.objects.filter(gym_id=gym.id, id=receipt_id)
        if lock_receipt:
            queryset = queryset.select_for_update()
        receipt = queryset.first()
        if (
            receipt is None
            or receipt.purpose != EXPENSE_RECEIPT_PURPOSE
            or receipt.owner_type != GYM_OWNER
            or receipt.owner_id != gym.id
        ):
            raise ValidationError({"receipt_file_asset_id": ["Select an expense receipt file."]})
        if require_clean and (
            receipt.scan_status != FileAsset.ScanStatus.CLEAN
            or receipt.retention_status != FileAsset.RetentionStatus.ACTIVE
        ):
            raise ValidationError(
                {
                    "receipt_file_asset_id": [
                        "The expense receipt must be clean before this command."
                    ]
                }
            )
    return category, receipt


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    expense: Expense,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="expenses",
        target_type="Expense",
        target_id=expense.id,
        request_id=request_id,
        new_values={
            "status": expense.status,
            "category_id": str(expense.category_id),
            "payment_method": expense.payment_method,
            "amount": money_amount(expense.amount),
            "currency": expense.currency,
            "business_date": expense.business_date.isoformat(),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _audit_category(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    category: ExpenseCategory,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="expenses",
        target_type="ExpenseCategory",
        target_id=category.id,
        request_id=request_id,
        new_values={
            "code": category.code,
            "name": category.name,
            "is_active": category.is_active,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
