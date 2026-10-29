"""Commission payout and the internal payout reversal used by a future sale void.

Record-payment requires a protected owner or manager. The accrual creator may
pay only when they are the owner, or both manager and receptionist. Otherwise
another actor must pay. The command locks the commission, posts one protected
expense in that creator's name, inserts one child, and recomputes the parent.
Reversal is not a public route. It must run inside the caller's transaction,
locks the commission before the child, voids the linked expense, marks the
child ``VOID``, and recomputes the parent from the remaining posted children.
Parent ``VOID`` stays reserved for sale void.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import InternalError, ProgrammingError, connection, transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashTransaction
from apps.cashbook.selectors import membership_has_permission
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.expenses.services import post_pt_commission_expense, void_pt_commission_expense
from apps.members.models import Payment
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from apps.trainers.exceptions import (
    CommissionOverpay,
    CommissionPayoutConflict,
    SaleCurrencyMismatch,
)
from apps.trainers.models import PTCommission, PTCommissionPayment, PTSale
from apps.trainers.selectors import commission_visibility, gym_local_date, holds_protected_role

_PAYABLE = frozenset({PTCommission.Status.ACCRUED, PTCommission.Status.PARTIALLY_PAID})
_OWNER_ROLE = "OWNER"
_MANAGER_ROLE = "MANAGER"
_TRAINER_ROLE = "TRAINER"
_RECEPTION_ROLE = "RECEPTIONIST"
_SPACE = re.compile(r"\s+")


def command_route(*, commission_id: UUID) -> str:
    return f"/api/v1/pt-commissions/{commission_id}/record-payment/"


def record_pt_commission_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    commission_id: UUID,
    amount: Decimal,
    business_date: date,
    payment_method: str,
    cash_session_id: UUID | None,
    reference_number: str,
    policy_note: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[PTCommission, PTCommission, PTCommissionPayment, Expense, CashTransaction | None]:
    """Pay part or all of one visible commission and return before, after, and evidence."""

    gym, actor = _reload(
        gym_id=gym.id, membership_id=actor.id, permission_code="pt.commissions.pay"
    )
    _assert_payer(gym_id=gym.id, actor=actor)
    if payment_method != Payment.Method.CASH and cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["Only a cash commission payout can use a cash session."]}
        )
    if payment_method == Payment.Method.CASH and cash_session_id is None:
        raise ValidationError(
            {"cash_session_id": ["A cash commission payout requires an open session."]}
        )
    reference = _optional_text(reference_number)
    note = _optional_text(policy_note)
    commission = _lock_visible_commission(gym=gym, actor=actor, commission_id=commission_id)
    if commission.currency != gym.currency:
        raise SaleCurrencyMismatch()
    if amount > commission.outstanding_amount:
        raise CommissionOverpay()
    if commission.status not in _PAYABLE:
        raise CommissionPayoutConflict()
    if actor.id == commission.created_by_membership_id and not _may_pay_own_commission(
        gym_id=gym.id, membership_id=actor.id
    ):
        raise PermissionDenied()
    before = copy.copy(commission)
    sale = _lock_sale(commission)
    if business_date < sale.service_date or business_date > gym_local_date(gym):
        raise ValidationError(
            {
                "business_date": [
                    "The payout date must run from the sale service date through today."
                ]
            }
        )
    label = f"PT commission payout {reference}".rstrip()
    expense, movement = post_pt_commission_expense(
        gym=gym,
        approver=actor,
        created_by_membership_id=commission.created_by_membership_id,
        submitted_by_membership_id=commission.created_by_membership_id,
        business_date=business_date,
        description=label[:255],
        amount=amount,
        currency=commission.currency,
        payment_method=payment_method,
        cash_session_id=cash_session_id,
        policy_note=note,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    payment = PTCommissionPayment(
        gym=gym,
        pt_commission=commission,
        amount=amount,
        business_date=business_date,
        payment_method=payment_method,
        reference_number=reference,
        policy_note=note,
        status=PTCommissionPayment.Status.POSTED,
        expense=expense,
        cash_session_id=None if movement is None else movement.cash_session_id,
        cash_transaction=movement,
        created_by_membership_id=actor.id,
    )
    _save_payment(payment)
    _recompute_commission(commission)
    _audit(
        gym=gym,
        actor=actor,
        action="PT_COMMISSION_PAID",
        commission=commission,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"payment_id": str(payment.id), "amount": f"{amount:.2f}"},
    )
    return before, commission, payment, expense, movement


def reverse_pt_commission_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment: PTCommissionPayment,
    cash_session_id: UUID | None,
    reason: str,
    business_date: date,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTCommissionPayment:
    """Void one payout inside the caller's transaction.

    The caller must not already hold the payout or commission lock. This helper
    locks the commission, then the payout, then the expense. It is not a public
    route and does not mark the parent commission ``VOID``.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("PT commission reversal requires an active transaction.")
    set_gym_context(gym_id=gym.id)
    if payment.gym_id != gym.id:
        raise Http404
    note = _required_text(reason)
    commission = _lock_commission(gym_id=gym.id, commission_id=payment.pt_commission_id)
    locked = (
        PTCommissionPayment.objects.select_for_update(of=("self",))
        .filter(gym_id=gym.id, id=payment.id, pt_commission_id=commission.id)
        .first()
    )
    if locked is None:
        raise Http404
    if locked.status != PTCommissionPayment.Status.POSTED:
        raise CommissionPayoutConflict()
    if locked.payment_method == Payment.Method.CASH and cash_session_id is None:
        raise ValidationError(
            {"cash_session_id": ["A cash commission payout requires an open destination session."]}
        )
    if locked.payment_method != Payment.Method.CASH and cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["Do not map a cash session for a non-cash commission payout."]}
        )
    expense = (
        Expense.objects.select_for_update().filter(gym_id=gym.id, id=locked.expense_id).first()
    )
    if expense is None:
        raise CommissionPayoutConflict()
    _original, reversal, movement = void_pt_commission_expense(
        gym=gym,
        actor=actor,
        original=expense,
        reason=note,
        business_date=business_date,
        cash_session_id=cash_session_id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    now = timezone.now()
    locked.status = PTCommissionPayment.Status.VOID
    locked.voided_at = now
    locked.voided_by_membership_id = actor.id
    locked.void_reason = note
    locked.reversal_expense = reversal
    locked.reversal_cash_transaction = movement
    _save_payment(locked)
    _recompute_commission(commission)
    _audit(
        gym=gym,
        actor=actor,
        action="PT_COMMISSION_PAYOUT_REVERSED",
        commission=commission,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"payment_id": str(locked.id), "reversal_expense_id": str(reversal.id)},
    )
    return locked


def _may_pay_own_commission(*, gym_id: UUID, membership_id: UUID) -> bool:
    """The owner, or one person who is both manager and receptionist, can."""

    if holds_protected_role(gym_id=gym_id, membership_id=membership_id, role_key=_OWNER_ROLE):
        return True
    return holds_protected_role(
        gym_id=gym_id, membership_id=membership_id, role_key=_MANAGER_ROLE
    ) and holds_protected_role(gym_id=gym_id, membership_id=membership_id, role_key=_RECEPTION_ROLE)


def _assert_payer(*, gym_id: UUID, actor: GymMembership) -> None:
    if holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key=_TRAINER_ROLE):
        raise PermissionDenied()
    manager = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key=_MANAGER_ROLE)
    owner = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key=_OWNER_ROLE)
    receptionist = holds_protected_role(
        gym_id=gym_id, membership_id=actor.id, role_key=_RECEPTION_ROLE
    )
    if receptionist and not manager and not owner:
        raise PermissionDenied()
    if not manager and not owner:
        raise PermissionDenied()


def _lock_visible_commission(
    *,
    gym: Gym,
    actor: GymMembership,
    commission_id: UUID,
) -> PTCommission:
    queryset, _binding = commission_visibility(gym=gym, actor=actor)
    row = (
        queryset.select_for_update(of=("self",))
        .select_related("pt_sale")
        .filter(id=commission_id)
        .first()
    )
    if row is None:
        raise Http404
    return row


def _lock_commission(*, gym_id: UUID, commission_id: UUID) -> PTCommission:
    row = (
        PTCommission.objects.select_for_update(of=("self",))
        .filter(gym_id=gym_id, id=commission_id)
        .first()
    )
    if row is None:
        raise CommissionPayoutConflict()
    return row


def _lock_sale(commission: PTCommission) -> PTSale:
    sale = (
        PTSale.objects.select_for_update(of=("self",))
        .filter(gym_id=commission.gym_id, id=commission.pt_sale_id, status=PTSale.Status.POSTED)
        .first()
    )
    if sale is None:
        raise CommissionPayoutConflict()
    return sale


def _recompute_commission(commission: PTCommission) -> None:
    total = PTCommissionPayment.objects.filter(
        gym_id=commission.gym_id,
        pt_commission_id=commission.id,
        status=PTCommissionPayment.Status.POSTED,
    ).aggregate(total=Sum("amount"))["total"]
    paid = Decimal("0.00") if total is None else Decimal(total)
    outstanding = commission.commission_amount - paid
    if paid == 0:
        status = PTCommission.Status.ACCRUED
        paid_at = None
    elif paid < commission.commission_amount:
        status = PTCommission.Status.PARTIALLY_PAID
        paid_at = None
    else:
        status = PTCommission.Status.PAID
        paid_at = timezone.now()
    commission.paid_amount = paid
    commission.outstanding_amount = outstanding
    commission.status = status
    commission.paid_at = paid_at
    try:
        with transaction.atomic():
            commission.save(
                update_fields=[
                    "paid_amount",
                    "outstanding_amount",
                    "status",
                    "paid_at",
                    "updated_at",
                ]
            )
    except (InternalError, ProgrammingError) as exc:
        _reraise_guard(exc)


def _save_payment(payment: PTCommissionPayment) -> None:
    try:
        with transaction.atomic():
            payment.save()
    except (InternalError, ProgrammingError) as exc:
        _reraise_guard(exc)


def _reraise_guard(exc: Exception) -> None:
    message = str(getattr(exc, "__cause__", exc))
    if "pt_commission_guard" in message or "pt_commission_payment_guard" in message:
        raise CommissionPayoutConflict() from exc
    raise exc


def _reload(
    *,
    gym_id: UUID,
    membership_id: UUID,
    permission_code: str,
) -> tuple[Gym, GymMembership]:
    set_gym_context(gym_id=gym_id)
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
        feature__code="pt",
        enabled=True,
    ).exists()
    if (
        membership is None
        or not entitled
        or not membership_has_permission(membership=membership, code=permission_code)
    ):
        raise PermissionDenied()
    return membership.gym, membership


def _optional_text(value: str) -> str:
    return _SPACE.sub(" ", value).strip()


def _required_text(value: str) -> str:
    collapsed = _optional_text(value)
    if collapsed == "":
        raise ValidationError({"reason": ["Enter a reason."]})
    return collapsed


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    commission: PTCommission,
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
        module="trainers",
        target_type="PTCommission",
        target_id=commission.id,
        request_id=request_id,
        new_values={
            "status": commission.status,
            "paid_amount": f"{commission.paid_amount:.2f}",
            "outstanding_amount": f"{commission.outstanding_amount:.2f}",
            "currency": commission.currency,
            **extra,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
