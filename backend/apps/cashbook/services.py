"""Cash-book commands.

Lock order is the cash session first, then its cash transactions. ``mutate``
runs inside the idempotency runner's business transaction. Claim, completion,
and failure recording stay in that runner. This module does not open another
durable idempotency transaction and does not perform external I/O.

``record_cash_movement`` is the internal helper later payment, expense,
commission, and reversal increments call. It has no HTTP route.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.exceptions import CashMovementRejected, CashSessionUnavailable
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import (
    holds_protected_role,
    membership_has_permission,
    restricts_to_own_sessions,
)
from apps.common.finance.money import parse_money
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership

_AUTOMATIC = {
    CashTransaction.SourceType.PAYMENT,
    CashTransaction.SourceType.EXPENSE,
    CashTransaction.SourceType.COMMISSION,
}


def assert_current_business_date(*, gym: Gym, business_date: date) -> None:
    if business_date != gym_today(gym):
        raise ValidationError({"business_date": ["Use the gym's current business date."]})


def revalidate_cash_actor(
    *,
    gym_id: UUID,
    membership_id: UUID,
    permission_code: str,
) -> tuple[Gym, GymMembership]:
    """Reload the actor inside the business transaction and fail closed."""

    now = timezone.now()
    membership = (
        GymMembership.objects.select_related("user", "gym")
        .filter(
            id=membership_id,
            gym_id=gym_id,
            status=GymMembership.Status.ACTIVE,
            user__is_active=True,
            gym__status=Gym.Status.ACTIVE,
            gym__subscription_status__in=[
                Gym.SubscriptionStatus.TRIAL,
                Gym.SubscriptionStatus.ACTIVE,
            ],
        )
        .first()
    )
    entitled = GymFeatureEntitlement.objects.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(effective_until__isnull=True) | Q(effective_until__gt=now),
        gym_id=gym_id,
        feature__code="finance",
        enabled=True,
    ).exists()
    if (
        membership is None
        or not entitled
        or not membership_has_permission(membership=membership, code=permission_code)
    ):
        raise PermissionDenied()
    return membership.gym, membership


def assert_owner_may_adjust(actor: GymMembership) -> None:
    """Interim rule until an Owner-configurable adjustment policy exists.

    Holding the protected Reception role always denies adjustment. Otherwise
    only the protected Owner role may adjust, even when ``cashbook.adjust``
    is granted to a Manager, Accountant, or custom role.
    """

    if holds_protected_role(
        gym_id=actor.gym_id,
        membership_id=actor.id,
        role_key="RECEPTIONIST",
    ):
        raise PermissionDenied()
    if not holds_protected_role(
        gym_id=actor.gym_id,
        membership_id=actor.id,
        role_key="OWNER",
    ):
        raise PermissionDenied()


def open_cash_session(
    *,
    gym: Gym,
    actor: GymMembership,
    register_code: str,
    business_date: date,
    opening_cash: Decimal,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CashSession:
    """Open one till. The caller holds the idempotency business transaction."""

    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code="cashbook.open",
    )
    assert_current_business_date(gym=gym, business_date=business_date)
    opening = parse_money(opening_cash)
    if opening < 0:
        raise ValidationError({"opening_cash": ["Enter a non-negative amount."]})
    try:
        with transaction.atomic():
            session = CashSession.objects.create(
                gym=gym,
                business_date=business_date,
                register_code=register_code,
                currency=gym.currency,
                opening_cash=opening,
                cash_sales=Decimal("0.00"),
                cash_expenses=Decimal("0.00"),
                manual_net=Decimal("0.00"),
                expected_closing=opening,
                status=CashSession.Status.OPEN,
                opened_by_membership=actor,
            )
    except IntegrityError as exc:
        if getattr(exc.__cause__, "sqlstate", None) == "23505":
            raise CashSessionUnavailable() from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CASH_SESSION_OPENED",
        module="cashbook",
        target_type="CashSession",
        target_id=session.id,
        request_id=request_id,
        new_values={
            "register_code": session.register_code,
            "business_date": session.business_date.isoformat(),
            "currency": session.currency,
            "opening_cash": format(opening, "f"),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return session


def record_manual_adjustment(
    *,
    gym: Gym,
    actor: GymMembership,
    session_id: UUID,
    direction: str,
    amount: Decimal,
    currency: str,
    business_date: date,
    description: str,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[CashSession, CashTransaction]:
    gym, actor, session = _lock_open_session(
        gym=gym,
        actor=actor,
        session_id=session_id,
        code="cashbook.adjust",
    )
    _require_same_day(session, currency=currency, business_date=business_date)
    movement = record_cash_movement(
        gym=gym,
        actor=actor,
        session_id=session.id,
        direction=direction,
        source_type=CashTransaction.SourceType.MANUAL,
        source_id=None,
        amount=amount,
        currency=currency,
        business_date=business_date,
        description=description,
        reason=reason,
        reversal_of_id=None,
    )
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CASH_MANUAL_ADJUSTMENT_RECORDED",
        module="cashbook",
        target_type="CashTransaction",
        target_id=movement.id,
        request_id=request_id,
        new_values={
            "cash_session_id": str(session.id),
            "direction": movement.direction,
            "amount": format(movement.amount, "f"),
            "reason": movement.reason,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.refresh_from_db()
    return session, movement


def reconcile_cash_session(
    *,
    gym: Gym,
    actor: GymMembership,
    session_id: UUID,
    actual_closing: Decimal,
    difference_reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CashSession:
    gym, actor, session = _lock_open_session(
        gym=gym,
        actor=actor,
        session_id=session_id,
        code="cashbook.reconcile",
    )
    sales, expenses, manual = recompute_cash_totals(session)
    expected = parse_money(session.opening_cash + sales - expenses + manual)
    counted = parse_money(actual_closing)
    if counted < 0:
        raise ValidationError({"actual_closing": ["Enter a non-negative amount."]})
    difference = parse_money(counted - expected)
    reason = difference_reason.strip()
    if difference != 0 and reason == "":
        raise ValidationError({"difference_reason": ["Enter a reason for the difference."]})
    session.cash_sales = sales
    session.cash_expenses = expenses
    session.manual_net = manual
    session.expected_closing = expected
    session.actual_closing = counted
    session.difference = difference
    session.difference_reason = reason
    session.status = CashSession.Status.CLOSED
    session.closed_by_membership = actor
    session.closed_at = timezone.now()
    session.save(
        update_fields=[
            "cash_sales",
            "cash_expenses",
            "manual_net",
            "expected_closing",
            "actual_closing",
            "difference",
            "difference_reason",
            "status",
            "closed_by_membership",
            "closed_at",
            "updated_at",
        ]
    )
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CASH_SESSION_RECONCILED",
        module="cashbook",
        target_type="CashSession",
        target_id=session.id,
        request_id=request_id,
        new_values={
            "expected_closing": format(expected, "f"),
            "actual_closing": format(counted, "f"),
            "difference": format(difference, "f"),
            "status": session.status,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return session


def record_cash_movement(
    *,
    gym: Gym,
    actor: GymMembership,
    session_id: UUID,
    direction: str,
    source_type: str,
    source_id: UUID | None,
    amount: Decimal,
    currency: str,
    business_date: date,
    description: str,
    reason: str,
    reversal_of_id: UUID | None,
) -> CashTransaction:
    """Lock the session, append one movement, and recompute its totals.

    Callers must already be inside ``transaction.atomic()``. This helper takes
    ``SELECT ... FOR UPDATE`` itself and does not trust a session object loaded
    earlier. Payment, expense, and commission rows require ``source_id``.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("A cash movement requires an active transaction.")
    session = CashSession.objects.select_for_update().filter(gym_id=gym.id, id=session_id).first()
    if session is None or session.status != CashSession.Status.OPEN:
        raise CashSessionUnavailable()
    _require_same_day(session, currency=currency, business_date=business_date)
    if source_type in _AUTOMATIC and source_id is None:
        raise ValidationError({"source_id": ["Enter a source."]})
    movement_amount = parse_money(amount)
    if movement_amount <= 0:
        raise ValidationError({"amount": ["Enter an amount greater than zero."]})
    reversal = None
    if reversal_of_id is not None:
        reversal = (
            CashTransaction.objects.select_for_update()
            .filter(gym_id=gym.id, id=reversal_of_id)
            .first()
        )
        if reversal is None:
            raise CashSessionUnavailable()
    movement = CashTransaction.objects.create(
        gym=gym,
        cash_session=session,
        direction=direction,
        source_type=source_type,
        source_id=source_id,
        amount=movement_amount,
        currency=session.currency,
        business_date=session.business_date,
        occurred_at=timezone.now(),
        description=description,
        reason=reason,
        reversal_of=reversal,
        created_by_membership=actor,
    )
    sales, expenses, manual = recompute_cash_totals(session)
    _store_open_totals(session, sales=sales, expenses=expenses, manual=manual)
    return movement


def recompute_cash_totals(session: CashSession) -> tuple[Decimal, Decimal, Decimal]:
    """Derive sales, expenses, and signed manual net from immutable movements."""

    locked = list(
        CashTransaction.objects.select_for_update()
        .filter(gym_id=session.gym_id, cash_session_id=session.id)
        .order_by("occurred_at", "id")
    )
    reversal_ids = [txn.reversal_of_id for txn in locked if txn.reversal_of_id is not None]
    originals = {
        row.id: row
        for row in CashTransaction.objects.select_for_update().filter(
            gym_id=session.gym_id,
            id__in=reversal_ids,
        )
    }
    sales = Decimal("0.00")
    expenses = Decimal("0.00")
    manual = Decimal("0.00")
    for txn in locked:
        signed = txn.amount if txn.direction == CashTransaction.Direction.IN else -txn.amount
        kind = txn.source_type
        if kind == CashTransaction.SourceType.REVERSAL:
            if txn.reversal_of_id is None:
                raise CashMovementRejected()
            original = originals.get(txn.reversal_of_id)
            if original is None or original.source_type == CashTransaction.SourceType.REVERSAL:
                raise CashMovementRejected()
            kind = original.source_type
        if kind == CashTransaction.SourceType.MANUAL:
            manual += signed
        elif kind == CashTransaction.SourceType.PAYMENT:
            sales += signed
        elif kind in _AUTOMATIC and kind != CashTransaction.SourceType.PAYMENT:
            expenses -= signed
        else:
            raise CashMovementRejected()
    return parse_money(sales), parse_money(expenses), parse_money(manual)


def command_route(*, session_id: UUID, action: str) -> str:
    return f"/api/v1/cash-sessions/{session_id}/{action}/"


def _store_open_totals(
    session: CashSession,
    *,
    sales: Decimal,
    expenses: Decimal,
    manual: Decimal,
) -> None:
    session.cash_sales = sales
    session.cash_expenses = expenses
    session.manual_net = manual
    session.expected_closing = parse_money(session.opening_cash + sales - expenses + manual)
    session.save(
        update_fields=[
            "cash_sales",
            "cash_expenses",
            "manual_net",
            "expected_closing",
            "updated_at",
        ]
    )


def _lock_open_session(
    *,
    gym: Gym,
    actor: GymMembership,
    session_id: UUID,
    code: str,
) -> tuple[Gym, GymMembership, CashSession]:
    session = CashSession.objects.select_for_update().filter(gym_id=gym.id, id=session_id).first()
    if session is None:
        raise Http404
    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code=code,
    )
    if code == "cashbook.adjust":
        assert_owner_may_adjust(actor)
    if (
        restricts_to_own_sessions(gym_id=gym.id, membership_id=actor.id, permission_code=code)
        and session.opened_by_membership_id != actor.id
    ):
        raise Http404
    if session.status != CashSession.Status.OPEN:
        raise CashSessionUnavailable()
    return gym, actor, session


def _require_same_day(session: CashSession, *, currency: str, business_date: date) -> None:
    errors: dict[str, list[str]] = {}
    if currency != session.currency:
        errors["currency"] = ["Use the cash session currency."]
    if business_date != session.business_date:
        errors["business_date"] = ["Use the cash session business date."]
    if errors:
        raise ValidationError(errors)


def audit_context(request: Any) -> dict[str, Any]:
    address = request.META.get("REMOTE_ADDR")
    return {
        "request_id": str(getattr(request, "request_id", "") or "req_unknown"),
        "ip_address": str(address) if address else None,
        "user_agent": str(request.META.get("HTTP_USER_AGENT", "")),
    }
