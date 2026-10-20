"""Void one posted personal-training sale.

Lock order matches record-payment and leaves room for a later booking lock:

1. commission
2. sale
3. destination cash sessions, by id
4. sale payment, then its receipts
5. member package

Payout rows are locked inside ``reverse_pt_commission_payment``, which takes
the commission first and then that child. Callers therefore pass posted
children in ``created_at``, ``id`` order and do not lock a child first.
A future booking lock belongs after the package and before the ledger row.
The ledger table has no update privilege, so the grant row is read, not locked.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import DatabaseError, connection
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession
from apps.members.models import Payment, Receipt
from apps.members.payment_services import reverse_pt_sale_payment
from apps.tenants.models import Gym, GymMembership
from apps.trainers.exceptions import PackageConsumed, PayoutReversalIncomplete, PTSaleStateConflict
from apps.trainers.models import (
    MemberServicePackage,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServiceSessionLedger,
)
from apps.trainers.pt_commission_services import reverse_pt_commission_payment
from apps.trainers.pt_sale_services import _reload, collapsed_text
from apps.trainers.selectors import gym_local_date, pt_sale_visibility

_CASH = "CASH"


def void_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    business_date: date,
    cash_session_id: UUID | None,
    payout_reversals: list[dict[str, Any]],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Reverse payouts, the optional payment, and an unconsumed grant."""

    if not connection.in_atomic_block:
        raise RuntimeError("PT sale void requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.void")
    note = _reason(reason)
    visible, _binding = pt_sale_visibility(gym=gym, actor=actor)
    if not visible.filter(id=sale_id).exists():
        raise Http404
    commission = _lock_commission(gym_id=gym.id, sale_id=sale_id)
    sale = _lock_sale(gym_id=gym.id, sale_id=sale_id)
    if sale.status != PTSale.Status.POSTED or commission.pt_sale_id != sale.id:
        raise PTSaleStateConflict()
    _require_business_date(gym=gym, sale=sale, business_date=business_date)
    posted = list(
        PTCommissionPayment.objects.filter(
            gym_id=gym.id,
            pt_commission_id=commission.id,
            status=PTCommissionPayment.Status.POSTED,
        ).order_by("created_at", "id")
    )
    mapping = _payout_map(posted=posted, supplied=payout_reversals)
    payment = _lock_payment(sale)
    _require_payment_session(payment=payment, cash_session_id=cash_session_id)
    _lock_sessions(
        gym_id=gym.id,
        session_ids=[
            *[value for value in mapping.values() if value is not None],
            *([] if cash_session_id is None else [cash_session_id]),
        ],
    )
    receipts = _lock_receipts(payment)
    package = _lock_package(sale)
    reversed_payouts = [
        reverse_pt_commission_payment(
            gym=gym,
            actor=actor,
            payment=row,
            cash_session_id=mapping[row.id],
            reason=note,
            business_date=business_date,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        for row in posted
    ]
    reversed_payment = None
    if payment is not None:
        reversed_payment = reverse_pt_sale_payment(
            gym=gym,
            actor=actor,
            original=payment,
            receipts=receipts,
            reason=note,
            business_date=business_date,
            cash_session_id=cash_session_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    refund = _refund_package(gym=gym, actor=actor, package=package)
    now = timezone.now()
    commission.status = PTCommission.Status.VOID
    commission.paid_amount = Decimal("0.00")
    commission.outstanding_amount = Decimal("0.00")
    commission.paid_at = None
    commission.save(
        update_fields=["status", "paid_amount", "outstanding_amount", "paid_at", "updated_at"]
    )
    sale.status = PTSale.Status.VOID
    sale.voided_at = now
    sale.voided_by_membership_id = actor.id
    sale.reason = note
    sale.reversal_pt_sale_id = None
    sale.updated_by_membership_id = actor.id
    sale.save(
        update_fields=[
            "status",
            "voided_at",
            "voided_by_membership_id",
            "reason",
            "reversal_pt_sale_id",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    except DatabaseError as exc:
        message = str(getattr(exc, "__cause__", exc))
        if "guard" in message:
            raise PTSaleStateConflict() from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="PT_SALE_VOIDED",
        module="trainers",
        target_type="PTSale",
        target_id=sale.id,
        request_id=request_id,
        new_values={
            "status": sale.status,
            "reason": note,
            "business_date": business_date.isoformat(),
            "commission_id": str(commission.id),
            "payout_count": len(reversed_payouts),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return {
        "sale": sale,
        "commission": commission,
        "payouts": reversed_payouts,
        "payment": reversed_payment,
        "package": package,
        "refund": refund,
    }


def _reason(value: str) -> str:
    note = collapsed_text(value)
    if note == "" or len(note) > 500:
        raise ValidationError({"reason": ["Enter a reason."]})
    return note


def _lock_commission(*, gym_id: UUID, sale_id: UUID) -> PTCommission:
    row = (
        PTCommission.objects.select_for_update(of=("self",))
        .filter(gym_id=gym_id, pt_sale_id=sale_id)
        .first()
    )
    if row is None:
        raise PTSaleStateConflict()
    return row


def _lock_sale(*, gym_id: UUID, sale_id: UUID) -> PTSale:
    row = PTSale.objects.select_for_update(of=("self",)).filter(gym_id=gym_id, id=sale_id).first()
    if row is None:
        raise Http404
    return row


def _require_business_date(*, gym: Gym, sale: PTSale, business_date: date) -> None:
    today = gym_local_date(gym)
    if business_date < sale.service_date or business_date > today:
        raise ValidationError(
            {"business_date": ["Business date must fall from the sale service date through today."]}
        )


def _payout_map(
    *,
    posted: list[PTCommissionPayment],
    supplied: list[dict[str, Any]],
) -> dict[UUID, UUID | None]:
    mapping: dict[UUID, UUID | None] = {}
    for supplied_row in supplied:
        payment_id = supplied_row["commission_payment_id"]
        if payment_id in mapping:
            raise PayoutReversalIncomplete()
        mapping[payment_id] = supplied_row.get("cash_session_id")
    expected = {child.id for child in posted}
    if set(mapping) != expected:
        raise PayoutReversalIncomplete()
    for child in posted:
        session_id = mapping[child.id]
        cash = child.payment_method == _CASH
        if cash and session_id is None:
            raise PayoutReversalIncomplete()
        if not cash and session_id is not None:
            raise PayoutReversalIncomplete()
    return mapping


def _require_payment_session(*, payment: Payment | None, cash_session_id: UUID | None) -> None:
    cash_payment = payment is not None and payment.method == _CASH
    if cash_payment and cash_session_id is None:
        raise ValidationError(
            {"cash_session_id": ["A cash payment void requires an open session."]}
        )
    if not cash_payment and cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["Only a cash payment void can use a cash session."]}
        )


def _lock_sessions(*, gym_id: UUID, session_ids: list[UUID]) -> None:
    unique = sorted(set(session_ids))
    if not unique:
        return
    found = list(
        CashSession.objects.select_for_update(of=("self",))
        .filter(gym_id=gym_id, id__in=unique)
        .order_by("id")
    )
    if len(found) != len(unique):
        raise ValidationError(
            {
                "cash_session_id": [
                    "Select an open cash session for the correction date and currency."
                ]
            }
        )


def _lock_payment(sale: PTSale) -> Payment | None:
    if sale.payment_id is None:
        return None
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .filter(gym_id=sale.gym_id, id=sale.payment_id)
        .first()
    )
    if (
        payment is None
        or payment.pt_sale_id != sale.id
        or payment.status != Payment.Status.POSTED
        or payment.entry_type != Payment.EntryType.CHARGE
    ):
        raise PTSaleStateConflict()
    return payment


def _lock_receipts(payment: Payment | None) -> list[Receipt]:
    if payment is None:
        return []
    return list(
        Receipt.objects.select_for_update(of=("self",))
        .filter(gym_id=payment.gym_id, payment_id=payment.id)
        .order_by("id")
    )


def _lock_package(sale: PTSale) -> MemberServicePackage | None:
    return (
        MemberServicePackage.objects.select_for_update(of=("self",))
        .filter(gym_id=sale.gym_id, pt_sale_id=sale.id)
        .first()
    )


def _refund_package(
    *,
    gym: Gym,
    actor: GymMembership,
    package: MemberServicePackage | None,
) -> ServiceSessionLedger | None:
    if package is None:
        return None
    total = package.ledger_entries.aggregate(total=Sum("session_delta"))["total"]
    remaining = 0 if total is None else int(total)
    if remaining != package.granted_sessions:
        raise PackageConsumed()
    grant = (
        ServiceSessionLedger.objects.filter(
            gym_id=gym.id,
            member_service_package_id=package.id,
            reason=ServiceSessionLedger.Reason.GRANT,
        )
        .order_by("id")
        .first()
    )
    if grant is None or grant.session_delta != package.granted_sessions:
        raise PTSaleStateConflict()
    refund = ServiceSessionLedger(
        gym=gym,
        member_service_package=package,
        session_delta=-package.granted_sessions,
        reason=ServiceSessionLedger.Reason.REFUND,
        reverses_entry=grant,
        occurred_at=timezone.now(),
        created_by_membership_id=actor.id,
    )
    refund.save()
    package.status = MemberServicePackage.Status.VOID
    package.updated_by_membership_id = actor.id
    package.save(update_fields=["status", "updated_by_membership_id", "updated_at"])
    return refund
