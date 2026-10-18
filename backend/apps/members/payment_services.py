"""Payment drafts, posting, and published corrections.

Lock order inside approval: payment, membership, cash session, cash
transaction, receipt sequence, then the receipt. The receipt guard reads the
payment status, so the posted update is written immediately before the receipt
insert and still after the sequence allocation.

Void and refund lock the original charge, its corrections, receipts, and the
membership when one exists, then the destination cash session. A failure rolls
all of it back. Runtime may mark a posted charge void only in the same
transaction as its posted reversal, void evidence, and cash link when the
original movement exists.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import record_cash_movement, revalidate_cash_actor
from apps.common.finance.exceptions import CacheableIdempotentFailure, FinanceStateConflict
from apps.members.models import Member, Membership, Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.members.services import gym_today, refresh_membership_payment_status
from apps.sales.models import RetailSale
from apps.tenants.models import Gym, GymMembership, GymSettings, StaffProfile
from apps.tenants.sequence_services import allocate_formatted_number, ensure_receipt_sequence

_ZERO = Decimal("0.00")


class PaymentPostingConflict(FinanceStateConflict):
    """The locked payment or its source can no longer accept this command."""


class RefundExceedsBalance(FinanceStateConflict):
    default_code = "REFUND_EXCEEDS_BALANCE"
    default_detail = "The refund exceeds the refundable balance."


class RetailCorrectionUnavailable(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY  # type: ignore[assignment]
    default_code = "RETAIL_CORRECTION_UNAVAILABLE"
    default_detail = (
        "Retail product charges stay posted until retail void and returns publish "
        "a stock and destination-session contract."
    )


def create_payment_draft(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Payment:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.create")
    member, membership = _sources(gym=gym, fields=fields)
    received_at = _received_at(gym=gym, fields=fields)
    payment = Payment.objects.create(
        gym=gym,
        member=member,
        membership=membership,
        entry_type=Payment.EntryType.CHARGE,
        purpose=fields["purpose"],
        description=fields["description"],
        amount=fields["amount"],
        currency=fields["currency"],
        method=fields["method"],
        business_date=fields["business_date"],
        received_at=received_at,
        reference_number=str(fields.get("reference_number") or ""),
        status=Payment.Status.DRAFT,
        legacy_unlinked_cash=False,
        created_with_reception_scope=_holds_reception(gym_id=gym.id, membership_id=actor.id),
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_DRAFT_CREATED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return payment


def update_payment_draft(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Payment:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.update")
    payment = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    if payment.status != Payment.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft payment can be edited."]})
    merged = _merged_fields(payment, fields)
    member, membership = _sources(gym=gym, fields=merged)
    received_at = _received_at(gym=gym, fields=merged)
    payment.member = member
    payment.membership = membership
    payment.purpose = merged["purpose"]
    payment.description = merged["description"]
    payment.amount = merged["amount"]
    payment.currency = merged["currency"]
    payment.method = merged["method"]
    payment.business_date = merged["business_date"]
    payment.received_at = received_at
    payment.reference_number = str(merged.get("reference_number") or "")
    payment.updated_by_membership_id = actor.id
    payment.save()
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_DRAFT_UPDATED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return payment


def submit_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Payment:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.submit")
    payment = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    if payment.status != Payment.Status.DRAFT:
        raise ValidationError({"status": ["Only a draft payment can be submitted."]})
    now = timezone.now()
    payment.status = Payment.Status.PENDING_APPROVAL
    payment.submitted_at = now
    payment.submitted_by_membership_id = actor.id
    payment.submitted_with_reception_scope = _holds_reception(gym_id=gym.id, membership_id=actor.id)
    payment.updated_by_membership_id = actor.id
    payment.save()
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_SUBMITTED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return payment


def approve_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[Payment, Receipt, CashSession | None, CashTransaction | None, dict[str, object] | None]:
    if not connection.in_atomic_block:
        raise RuntimeError("Payment approval requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    payment = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if payment.status != Payment.Status.PENDING_APPROVAL:
        raise PaymentPostingConflict()
    if actor.id in {payment.created_by_membership_id, payment.submitted_by_membership_id}:
        raise PermissionDenied()
    membership = _lock_membership(payment)
    movement: CashTransaction | None = None
    if payment.method == Payment.Method.CASH:
        if cash_session_id is None:
            raise ValidationError({"cash_session_id": ["A cash payment requires an open session."]})
        movement = record_cash_movement(
            gym=gym,
            actor=actor,
            session_id=cash_session_id,
            direction=CashTransaction.Direction.IN,
            source_type=CashTransaction.SourceType.PAYMENT,
            source_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            business_date=payment.business_date,
            description=payment.description,
            reason="",
            reversal_of_id=None,
        )
        _assert_reception_till(payment=payment, session=movement.cash_session)
    elif cash_session_id is not None:
        raise ValidationError({"cash_session_id": ["Only a cash payment can use a cash session."]})
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=payment, actor=actor
    )
    now = timezone.now()
    payment.status = Payment.Status.POSTED
    payment.approved_at = now
    payment.approved_by_membership_id = actor.id
    payment.posted_at = now
    payment.posted_by_membership_id = actor.id
    payment.updated_by_membership_id = actor.id
    payment.legacy_unlinked_cash = False
    if movement is not None:
        payment.cash_session = movement.cash_session
        payment.cash_transaction = movement
    payment.save()
    receipt = _issue_receipt(
        gym=gym,
        actor=actor,
        payment=payment,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=now,
    )
    settlement = None
    if membership is not None:
        refresh_membership_payment_status(membership)
        membership.refresh_from_db()
        paid = _posted_total(membership)
        settlement = {
            "membership_id": membership.id,
            "payment_status": membership.payment_status,
            "paid_amount": paid,
            "balance_amount": Decimal(membership.final_amount) - paid,
        }
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_APPROVED_POSTED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"receipt_number": receipt.receipt_number},
    )
    _audit(
        gym=gym,
        actor=actor,
        action="RECEIPT_ISSUED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        module="receipts",
        target_type="Receipt",
        target_id=receipt.id,
        extra={"receipt_number": receipt.receipt_number},
    )
    session = movement.cash_session if movement is not None else None
    return payment, receipt, session, movement, settlement


def reject_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Payment:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    payment = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if payment.status != Payment.Status.PENDING_APPROVAL:
        raise PaymentPostingConflict()
    if actor.id in {payment.created_by_membership_id, payment.submitted_by_membership_id}:
        raise PermissionDenied()
    now = timezone.now()
    payment.status = Payment.Status.REJECTED
    payment.rejected_at = now
    payment.rejected_by_membership_id = actor.id
    payment.rejection_reason = reason
    payment.updated_by_membership_id = actor.id
    payment.save()
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_REJECTED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return payment


def cancel_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Payment:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.update")
    payment = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    if payment.status != Payment.Status.DRAFT or payment.submitted_at is not None:
        raise ValidationError({"status": ["Only an unsubmitted draft can be cancelled."]})
    now = timezone.now()
    payment.status = Payment.Status.CANCELLED
    payment.cancelled_at = now
    payment.cancelled_by_membership_id = actor.id
    payment.cancellation_reason = reason
    payment.updated_by_membership_id = actor.id
    payment.save()
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_CANCELLED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return payment


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
    if not membership_has_permission(membership=actor, code="payments.approve"):
        raise PermissionDenied()


def _lock_payment(*, gym_id: UUID, payment_id: UUID) -> Payment:
    payment = Payment.objects.select_for_update().filter(gym_id=gym_id, id=payment_id).first()
    if payment is None:
        raise Http404
    return payment


def _lock_membership(payment: Payment) -> Membership | None:
    if payment.purpose != Payment.Purpose.MEMBERSHIP:
        if payment.membership_id is not None:
            raise ValidationError({"membership_id": ["An other payment has no source reference."]})
        return None
    if payment.member_id is None or payment.membership_id is None:
        raise ValidationError({"membership_id": ["A membership payment requires its source."]})
    membership = (
        Membership.objects.select_for_update()
        .filter(gym_id=payment.gym_id, id=payment.membership_id, member_id=payment.member_id)
        .first()
    )
    if membership is None:
        raise ValidationError({"membership_id": ["The membership does not belong to this member."]})
    if payment.currency != membership.currency:
        raise ValidationError({"currency": ["Currency must match the membership."]})
    paid = _posted_total(membership)
    if paid + Decimal(payment.amount) > Decimal(membership.final_amount):
        raise PaymentPostingConflict()
    return membership


def _posted_total(membership: Membership) -> Decimal:
    signed = (
        Payment.objects.filter(
            gym_id=membership.gym_id,
            membership_id=membership.id,
        )
        .filter(
            Q(
                entry_type=Payment.EntryType.CHARGE,
                status__in=[Payment.Status.POSTED, Payment.Status.VOID],
            )
            | Q(
                entry_type__in=[Payment.EntryType.REFUND, Payment.EntryType.REVERSAL],
                status=Payment.Status.POSTED,
            )
        )
        .aggregate(
            charges=Sum("amount", filter=Q(entry_type=Payment.EntryType.CHARGE)),
            returns=Sum(
                "amount",
                filter=Q(entry_type__in=[Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]),
            ),
        )
    )
    charges = signed["charges"] if isinstance(signed["charges"], Decimal) else _ZERO
    returns = signed["returns"] if isinstance(signed["returns"], Decimal) else _ZERO
    return (charges - returns).quantize(Decimal("0.01"))


def _holds_reception(*, gym_id: UUID, membership_id: UUID) -> bool:
    return holds_protected_role(
        gym_id=gym_id,
        membership_id=membership_id,
        role_key="RECEPTIONIST",
    )


def _assert_reception_till(*, payment: Payment, session: CashSession) -> None:
    scoped = (
        (payment.created_with_reception_scope, payment.created_by_membership_id),
        (payment.submitted_with_reception_scope, payment.submitted_by_membership_id),
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


def _allocate_receipt_number(
    *, gym: Gym, payment: Payment, actor: GymMembership
) -> tuple[str, int, int]:
    settings_row = GymSettings.objects.filter(gym_id=gym.id).first()
    if settings_row is None:
        raise ValidationError({"receipt": ["Gym settings are required before receipt allocation."]})
    year = payment.business_date.year
    ensure_receipt_sequence(
        gym=gym,
        prefix=settings_row.receipt_number_prefix,
        actor_membership_id=actor.id,
        year=year,
    )
    number = allocate_formatted_number(gym_id=gym.id, sequence_key=f"RECEIPT:{year}")
    return number, int(number.rsplit("-", 1)[1]), year


def _issue_receipt(
    *,
    gym: Gym,
    actor: GymMembership,
    payment: Payment,
    number: str,
    sequence_value: int,
    sequence_year: int,
    issued_at: datetime,
) -> Receipt:
    member_number, member_name = _member_snapshot(payment)
    issuer = _issuer_name(actor)
    if not issuer or len(issuer) > 201:
        raise ValidationError({"issuer": ["The approver name is required on the receipt."]})
    return Receipt.objects.create(
        gym=gym,
        payment=payment,
        receipt_number=number,
        sequence_key=f"RECEIPT:{sequence_year}",
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=issued_at,
        gym_code=gym.code,
        gym_name=gym.name,
        gym_address=_gym_address(gym),
        member_number=member_number,
        member_name=member_name,
        description=payment.description,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        business_date=payment.business_date,
        issuer_name=issuer,
        status=Receipt.Status.ISSUED,
        created_by_membership_id=actor.id,
    )


def _sources(*, gym: Gym, fields: dict[str, Any]) -> tuple[Member | None, Membership | None]:
    if fields["currency"] != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    if fields["purpose"] not in {Payment.Purpose.MEMBERSHIP, Payment.Purpose.OTHER}:
        raise ValidationError({"purpose": ["This payment purpose is not available yet."]})
    member = None
    member_id = fields.get("member_id")
    if member_id is not None:
        member = Member.objects.filter(gym_id=gym.id, id=member_id).first()
        if member is None:
            raise ValidationError({"member_id": ["Select a member of this gym."]})
    membership = None
    if fields["purpose"] == Payment.Purpose.MEMBERSHIP:
        if member is None or fields.get("membership_id") is None:
            raise ValidationError(
                {"membership_id": ["A membership payment requires the member and membership."]}
            )
        membership = Membership.objects.filter(
            gym_id=gym.id,
            id=fields["membership_id"],
            member_id=member.id,
        ).first()
        if membership is None:
            raise ValidationError(
                {"membership_id": ["The membership does not belong to this member."]}
            )
    elif fields.get("membership_id") is not None:
        raise ValidationError({"membership_id": ["An other payment has no source reference."]})
    return member, membership


def _merged_fields(payment: Payment, fields: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "member_id": payment.member_id,
        "membership_id": payment.membership_id,
        "purpose": payment.purpose,
        "description": payment.description,
        "amount": payment.amount,
        "currency": payment.currency,
        "method": payment.method,
        "business_date": payment.business_date,
        "received_at": payment.received_at,
        "reference_number": payment.reference_number,
    }
    merged.update(fields)
    return merged


def _received_at(*, gym: Gym, fields: dict[str, Any]) -> datetime:
    received_at = fields.get("received_at") or timezone.now()
    if timezone.is_naive(received_at):
        raise ValidationError({"received_at": ["Use a timezone-aware time."]})
    if received_at > timezone.now():
        raise ValidationError({"received_at": ["Received time cannot be in the future."]})
    local_date = received_at.astimezone(ZoneInfo(gym.timezone)).date()
    if local_date != fields["business_date"]:
        raise ValidationError(
            {"received_at": ["Received time must fall on the business date in the gym timezone."]}
        )
    return received_at


def _issuer_name(actor: GymMembership) -> str:
    personal = f"{actor.user.first_name} {actor.user.last_name}".strip()
    if personal:
        return personal
    profile = (
        StaffProfile.objects.filter(gym_id=actor.gym_id, gym_membership_id=actor.id)
        .only("display_name")
        .first()
    )
    if profile is not None:
        display = str(profile.display_name or "").strip()
        if display:
            return display
    return str(actor.user.email or "").strip()


def _member_snapshot(payment: Payment) -> tuple[str, str]:
    if payment.member_id is None or payment.member is None:
        return "Walk-in", "Walk-in"
    return payment.member.member_number, payment.member.display_name


def _gym_address(gym: Gym) -> str:
    parts = [
        gym.address_line1,
        gym.address_line2,
        gym.city,
        gym.region,
        gym.postal_code,
        gym.country_code,
    ]
    return ", ".join(part for part in parts if part)[:800]


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    payment: Payment,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    module: str = "payments",
    target_type: str = "Payment",
    target_id: UUID | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    values: dict[str, object] = {
        "status": payment.status,
        "purpose": payment.purpose,
        "method": payment.method,
        "amount": money_text(Decimal(payment.amount)),
        "currency": payment.currency,
        "business_date": payment.business_date.isoformat(),
    }
    if extra:
        values.update(extra)
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module=module,
        target_type=target_type,
        target_id=payment.id if target_id is None else target_id,
        request_id=request_id,
        new_values=values,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def post_retail_product_payment(
    *,
    gym: Gym,
    approver: GymMembership,
    retail_sale_id: UUID,
    member: Member | None,
    created_by_membership_id: UUID,
    submitted_by_membership_id: UUID | None,
    amount: Decimal,
    currency: str,
    method: str,
    business_date: Any,
    reference_number: str,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[Payment, Receipt, CashTransaction | None]:
    """Post one product charge for a retail sale inside the caller's transaction.

    Public payment drafts still reject ``PRODUCT``. This path inserts a draft,
    submits it, and posts it so the payment guards, receipt allocation, and
    cash rules stay in force. The retail approval service is the only caller.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("Retail payment posting requires an active transaction.")
    if currency != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    if submitted_by_membership_id is None:
        raise ValidationError({"status": ["Only a submitted retail sale can be posted."]})
    if approver.id in {created_by_membership_id, submitted_by_membership_id}:
        raise PermissionDenied()
    if method != Payment.Method.CASH and cash_session_id is not None:
        raise ValidationError({"cash_session_id": ["Only a cash sale can use a cash session."]})
    if method == Payment.Method.CASH and cash_session_id is None:
        raise ValidationError({"cash_session_id": ["A cash sale requires an open session."]})
    received_at = _retail_received_at(gym=gym, business_date=business_date)
    now = timezone.now()
    payment = Payment.objects.create(
        gym=gym,
        member=member,
        membership=None,
        retail_sale_id=retail_sale_id,
        entry_type=Payment.EntryType.CHARGE,
        purpose=Payment.Purpose.PRODUCT,
        description="Retail sale",
        amount=amount,
        currency=currency,
        method=method,
        business_date=business_date,
        received_at=received_at,
        reference_number=reference_number,
        status=Payment.Status.DRAFT,
        legacy_unlinked_cash=False,
        created_with_reception_scope=_holds_reception(
            gym_id=gym.id, membership_id=created_by_membership_id
        ),
        created_by_membership_id=created_by_membership_id,
        updated_by_membership_id=approver.id,
    )
    payment.status = Payment.Status.PENDING_APPROVAL
    payment.submitted_at = now
    payment.submitted_by_membership_id = submitted_by_membership_id
    payment.submitted_with_reception_scope = _holds_reception(
        gym_id=gym.id, membership_id=submitted_by_membership_id
    )
    payment.updated_by_membership_id = approver.id
    payment.save()
    movement: CashTransaction | None = None
    if method == Payment.Method.CASH:
        assert cash_session_id is not None
        movement = record_cash_movement(
            gym=gym,
            actor=approver,
            session_id=cash_session_id,
            direction=CashTransaction.Direction.IN,
            source_type=CashTransaction.SourceType.PAYMENT,
            source_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            business_date=payment.business_date,
            description=payment.description,
            reason="",
            reversal_of_id=None,
        )
        _assert_reception_till(payment=payment, session=movement.cash_session)
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=payment, actor=approver
    )
    posted_at = timezone.now()
    payment.status = Payment.Status.POSTED
    payment.approved_at = posted_at
    payment.approved_by_membership_id = approver.id
    payment.posted_at = posted_at
    payment.posted_by_membership_id = approver.id
    payment.updated_by_membership_id = approver.id
    payment.legacy_unlinked_cash = False
    if movement is not None:
        payment.cash_session = movement.cash_session
        payment.cash_transaction = movement
    payment.save()
    receipt = _issue_receipt(
        gym=gym,
        actor=approver,
        payment=payment,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=posted_at,
    )
    _audit(
        gym=gym,
        actor=approver,
        action="PAYMENT_APPROVED_POSTED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"receipt_number": receipt.receipt_number, "retail_sale_id": str(retail_sale_id)},
    )
    return payment, receipt, movement


def post_retail_product_refund(
    *,
    gym: Gym,
    actor: GymMembership,
    original: Payment,
    amount: Decimal,
    business_date: date,
    reason: str,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[Payment, Receipt, CashTransaction | None]:
    """Post one product refund without voiding the original charge or receipt.

    Public payment refund still rejects ``PRODUCT``. The retail return service
    is the only caller. It locks the charge, its corrections, receipts, and
    the destination session before this insert. The refund copies the original
    method and reference. Cash uses an outbound movement with ``reversal_of``
    empty so several refunds can share the original in movement.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("Retail product refund requires an active transaction.")
    if (
        original.gym_id != gym.id
        or original.purpose != Payment.Purpose.PRODUCT
        or original.entry_type != Payment.EntryType.CHARGE
        or original.status != Payment.Status.POSTED
        or original.retail_sale_id is None
        or original.reversal_payment_id is not None
        or original.membership_id is not None
        or original.legacy_unlinked_cash
    ):
        raise PaymentPostingConflict()
    refund_amount = Decimal(amount).quantize(Decimal("0.01"))
    if refund_amount <= 0 or refund_amount > Decimal(original.amount):
        raise RefundExceedsBalance()
    refund_id = uuid4()
    movement = _refund_cash(
        gym=gym,
        actor=actor,
        source_id=refund_id,
        amount=refund_amount,
        currency=original.currency,
        method=original.method,
        business_date=business_date,
        description=f"Refund: {original.description}"[:255],
        reason=reason,
        cash_session_id=cash_session_id,
    )
    now = timezone.now()
    refund = _insert_posted_correction(
        gym=gym,
        actor=actor,
        original=original,
        correction_id=refund_id,
        entry_type=Payment.EntryType.REFUND,
        amount=refund_amount,
        method=original.method,
        business_date=business_date,
        description=f"Refund: {original.description}"[:255],
        reference_number=original.reference_number,
        movement=movement,
        legacy=False,
        posted_at=now,
        retail_sale_id=original.retail_sale_id,
    )
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=refund, actor=actor
    )
    receipt = _issue_receipt(
        gym=gym,
        actor=actor,
        payment=refund,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=now,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_REFUND_POSTED",
        payment=refund,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "original_payment_id": str(original.id),
            "retail_sale_id": str(original.retail_sale_id),
        },
    )
    return refund, receipt, movement


def _retail_received_at(*, gym: Gym, business_date: Any) -> datetime:
    zone = ZoneInfo(gym.timezone)
    now = timezone.now()
    if business_date > now.astimezone(zone).date():
        raise ValidationError({"business_date": ["Business date cannot be in the future."]})
    if business_date == now.astimezone(zone).date():
        return now
    return datetime.combine(business_date, time(12, 0), tzinfo=zone)


def command_route(*, payment_id: UUID | None, action: str) -> str:
    if payment_id is None:
        return "/api/v1/payments/"
    if not action:
        return f"/api/v1/payments/{payment_id}/"
    return f"/api/v1/payments/{payment_id}/{action}/"


def void_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    reason: str,
    business_date: date | None,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Void one posted charge and post an equal reversal in the same transaction."""

    if not connection.in_atomic_block:
        raise RuntimeError("Payment void requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.void")
    original = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.void")
    _reject_retail_charge(original)
    if original.entry_type != Payment.EntryType.CHARGE or original.status != Payment.Status.POSTED:
        raise PaymentPostingConflict()
    related = _lock_related_payments(original)
    receipts = _lock_payment_receipts(original, related)
    if _refundable_amount(original, related) != Decimal(original.amount):
        raise PaymentPostingConflict()
    membership = _lock_existing_membership(original)
    chosen_date = _correction_business_date(
        gym=gym, original_date=original.business_date, requested=business_date
    )
    reversal_id = uuid4()
    legacy = bool(original.legacy_unlinked_cash)
    movement = _correction_cash(
        gym=gym,
        actor=actor,
        original=original,
        source_id=reversal_id,
        amount=Decimal(original.amount),
        business_date=chosen_date,
        reason=reason,
        cash_session_id=cash_session_id,
        legacy=legacy,
        link_original_movement=not legacy and original.method == Payment.Method.CASH,
    )
    now = timezone.now()
    reversal = _insert_posted_correction(
        gym=gym,
        actor=actor,
        original=original,
        correction_id=reversal_id,
        entry_type=Payment.EntryType.REVERSAL,
        amount=Decimal(original.amount),
        method=original.method,
        business_date=chosen_date,
        description=f"Reversal: {original.description}"[:255],
        reference_number=original.reference_number,
        movement=movement,
        legacy=legacy,
        posted_at=now,
    )
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=reversal, actor=actor
    )
    correction_receipt = _issue_receipt(
        gym=gym,
        actor=actor,
        payment=reversal,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=now,
    )
    original_receipt = _void_locked_receipt(
        receipts=receipts, payment_id=original.id, actor=actor, reason=reason, voided_at=now
    )
    original.status = Payment.Status.VOID
    original.voided_at = now
    original.voided_by_membership_id = actor.id
    original.void_reason = reason
    original.reversal_payment = reversal
    original.updated_by_membership_id = actor.id
    original.save()
    settlement = _settlement_after(membership)
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_VOIDED",
        payment=original,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"reversal_payment_id": str(reversal.id)},
    )
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_REVERSAL_POSTED",
        payment=reversal,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"original_payment_id": str(original.id)},
    )
    return {
        "original": original,
        "correction": reversal,
        "original_receipt": original_receipt,
        "correction_receipt": correction_receipt,
        "cash_transactions": [] if movement is None else [movement],
        "refundable_amount": Decimal("0.00"),
        "settlement": settlement,
    }


def refund_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    payment_id: UUID,
    amount: Decimal,
    reason: str,
    method: str,
    business_date: date,
    reference_number: str,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Post one immutable refund without changing the original charge."""

    if not connection.in_atomic_block:
        raise RuntimeError("Payment refund requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.refund")
    original = _lock_payment(gym_id=gym.id, payment_id=payment_id)
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="payments.refund")
    _reject_retail_charge(original)
    if original.entry_type != Payment.EntryType.CHARGE or original.status != Payment.Status.POSTED:
        raise PaymentPostingConflict()
    if original.currency != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    related = _lock_related_payments(original)
    receipts = _lock_payment_receipts(original, related)
    refundable = _refundable_amount(original, related)
    refund_amount = Decimal(amount).quantize(Decimal("0.01"))
    if refund_amount <= 0 or refund_amount > refundable:
        raise RefundExceedsBalance()
    membership = _lock_existing_membership(original)
    chosen_date = _correction_business_date(
        gym=gym, original_date=original.business_date, requested=business_date
    )
    refund_id = uuid4()
    movement = _refund_cash(
        gym=gym,
        actor=actor,
        source_id=refund_id,
        amount=refund_amount,
        currency=original.currency,
        method=method,
        business_date=chosen_date,
        description=f"Refund: {original.description}"[:255],
        reason=reason,
        cash_session_id=cash_session_id,
    )
    now = timezone.now()
    refund = _insert_posted_correction(
        gym=gym,
        actor=actor,
        original=original,
        correction_id=refund_id,
        entry_type=Payment.EntryType.REFUND,
        amount=refund_amount,
        method=method,
        business_date=chosen_date,
        description=f"Refund: {original.description}"[:255],
        reference_number=reference_number,
        movement=movement,
        legacy=False,
        posted_at=now,
    )
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=refund, actor=actor
    )
    correction_receipt = _issue_receipt(
        gym=gym,
        actor=actor,
        payment=refund,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=now,
    )
    original_receipt = next(
        (
            row
            for row in receipts
            if row.payment_id == original.id and row.status == Receipt.Status.ISSUED
        ),
        None,
    )
    settlement = _settlement_after(membership)
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_REFUND_POSTED",
        payment=refund,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"original_payment_id": str(original.id)},
    )
    return {
        "original": original,
        "correction": refund,
        "original_receipt": original_receipt,
        "correction_receipt": correction_receipt,
        "cash_transactions": [] if movement is None else [movement],
        "refundable_amount": (refundable - refund_amount).quantize(Decimal("0.01")),
        "settlement": settlement,
    }


def void_receipt(
    *,
    gym: Gym,
    actor: GymMembership,
    receipt_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Receipt:
    """Void one issued receipt document. Payment and cash rows stay unchanged."""

    if not connection.in_atomic_block:
        raise RuntimeError("Receipt void requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="receipts.void")
    receipt = (
        Receipt.objects.select_for_update()
        .filter(gym_id=gym.id, id=receipt_id)
        .order_by("id")
        .first()
    )
    if receipt is None:
        raise Http404
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="receipts.void")
    if receipt.status != Receipt.Status.ISSUED:
        raise PaymentPostingConflict()
    payment = Payment.objects.filter(gym_id=gym.id, id=receipt.payment_id).first()
    if payment is None:
        raise PaymentPostingConflict()
    if payment.purpose == Payment.Purpose.PRODUCT or payment.retail_sale_id is not None:
        raise RetailCorrectionUnavailable()
    if RetailSale.objects.filter(
        gym_id=gym.id, receipt_id=receipt.id, status=RetailSale.Status.POSTED
    ).exists():
        raise RetailCorrectionUnavailable()
    now = timezone.now()
    receipt.status = Receipt.Status.VOID
    receipt.voided_at = now
    receipt.voided_by_membership_id = actor.id
    receipt.void_reason = reason
    receipt.save(
        update_fields=[
            "status",
            "voided_at",
            "voided_by_membership_id",
            "void_reason",
            "updated_at",
        ]
    )
    _audit(
        gym=gym,
        actor=actor,
        action="RECEIPT_VOIDED",
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        module="receipts",
        target_type="Receipt",
        target_id=receipt.id,
        extra={"receipt_number": receipt.receipt_number},
    )
    return receipt


def _reject_retail_charge(payment: Payment) -> None:
    if payment.purpose == Payment.Purpose.PRODUCT or payment.retail_sale_id is not None:
        raise RetailCorrectionUnavailable()


def reverse_retail_product_payment(
    *,
    gym: Gym,
    actor: GymMembership,
    original: Payment,
    receipts: list[Receipt],
    reason: str,
    business_date: date,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Reverse one locked posted product charge inside a retail void.

    Public payment void and refund still reject ``PRODUCT``. The caller locks
    the charge, its receipts, the original cash movement, and the destination
    session before calling this helper.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("Retail payment reversal requires an active transaction.")
    if (
        original.gym_id != gym.id
        or original.purpose != Payment.Purpose.PRODUCT
        or original.retail_sale_id is None
        or original.entry_type != Payment.EntryType.CHARGE
        or original.status != Payment.Status.POSTED
    ):
        raise PaymentPostingConflict()
    related = list(
        Payment.objects.filter(gym_id=original.gym_id, original_payment_id=original.id).order_by(
            "id"
        )
    )
    if related or _refundable_amount(original, related) != Decimal(original.amount):
        raise PaymentPostingConflict()
    reversal_id = uuid4()
    movement = _correction_cash(
        gym=gym,
        actor=actor,
        original=original,
        source_id=reversal_id,
        amount=Decimal(original.amount),
        business_date=business_date,
        reason=reason,
        cash_session_id=cash_session_id,
        legacy=False,
        link_original_movement=original.method == Payment.Method.CASH,
    )
    now = timezone.now()
    reversal = _insert_posted_correction(
        gym=gym,
        actor=actor,
        original=original,
        correction_id=reversal_id,
        entry_type=Payment.EntryType.REVERSAL,
        amount=Decimal(original.amount),
        method=original.method,
        business_date=business_date,
        description=f"Reversal: {original.description}"[:255],
        reference_number=original.reference_number,
        movement=movement,
        legacy=False,
        posted_at=now,
        retail_sale_id=original.retail_sale_id,
    )
    number, sequence_value, sequence_year = _allocate_receipt_number(
        gym=gym, payment=reversal, actor=actor
    )
    correction_receipt = _issue_receipt(
        gym=gym,
        actor=actor,
        payment=reversal,
        number=number,
        sequence_value=sequence_value,
        sequence_year=sequence_year,
        issued_at=now,
    )
    original_receipt = _void_locked_receipt(
        receipts=receipts, payment_id=original.id, actor=actor, reason=reason, voided_at=now
    )
    original.status = Payment.Status.VOID
    original.voided_at = now
    original.voided_by_membership_id = actor.id
    original.void_reason = reason
    original.reversal_payment = reversal
    original.updated_by_membership_id = actor.id
    original.save()
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_VOIDED",
        payment=original,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "reversal_payment_id": str(reversal.id),
            "retail_sale_id": str(original.retail_sale_id),
        },
    )
    _audit(
        gym=gym,
        actor=actor,
        action="PAYMENT_REVERSAL_POSTED",
        payment=reversal,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"original_payment_id": str(original.id)},
    )
    return {
        "original": original,
        "correction": reversal,
        "original_receipt": original_receipt,
        "correction_receipt": correction_receipt,
        "cash_transaction": movement,
    }


def _lock_related_payments(original: Payment) -> list[Payment]:
    return list(
        Payment.objects.select_for_update()
        .filter(gym_id=original.gym_id, original_payment_id=original.id)
        .order_by("id")
    )


def _lock_payment_receipts(original: Payment, related: list[Payment]) -> list[Receipt]:
    payment_ids = [original.id, *[row.id for row in related]]
    return list(
        Receipt.objects.select_for_update()
        .filter(gym_id=original.gym_id, payment_id__in=payment_ids)
        .order_by("id")
    )


def _refundable_amount(original: Payment, related: list[Payment]) -> Decimal:
    used = sum(
        (
            Decimal(row.amount)
            for row in related
            if row.status == Payment.Status.POSTED
            and row.entry_type in {Payment.EntryType.REFUND, Payment.EntryType.REVERSAL}
        ),
        _ZERO,
    )
    return (Decimal(original.amount) - used).quantize(Decimal("0.01"))


def _lock_existing_membership(payment: Payment) -> Membership | None:
    if payment.membership_id is None:
        return None
    member_id = payment.member_id
    if member_id is None:
        raise ValidationError({"membership_id": ["The membership does not belong to this gym."]})
    membership = (
        Membership.objects.select_for_update()
        .filter(gym_id=payment.gym_id, id=payment.membership_id, member_id=member_id)
        .first()
    )
    if membership is None:
        raise ValidationError({"membership_id": ["The membership does not belong to this gym."]})
    return membership


def _correction_business_date(*, gym: Gym, original_date: date, requested: date | None) -> date:
    chosen = gym_today(gym) if requested is None else requested
    if chosen > gym_today(gym):
        raise ValidationError({"business_date": ["The business date cannot be after today."]})
    if chosen < original_date:
        raise ValidationError(
            {"business_date": ["The business date cannot be before the original business date."]}
        )
    return chosen


def _correction_cash(
    *,
    gym: Gym,
    actor: GymMembership,
    original: Payment,
    source_id: UUID,
    amount: Decimal,
    business_date: date,
    reason: str,
    cash_session_id: UUID | None,
    legacy: bool,
    link_original_movement: bool,
) -> CashTransaction | None:
    if legacy:
        if cash_session_id is not None:
            raise ValidationError(
                {
                    "cash_session_id": [
                        "A legacy unlinked cash charge has no original movement to correct."
                    ]
                }
            )
        return None
    if original.method != Payment.Method.CASH:
        if cash_session_id is not None:
            raise ValidationError(
                {"cash_session_id": ["Only a cash correction can use a cash session."]}
            )
        return None
    if cash_session_id is None:
        raise ValidationError({"cash_session_id": ["A cash correction requires an open session."]})
    return record_cash_movement(
        gym=gym,
        actor=actor,
        session_id=cash_session_id,
        direction=CashTransaction.Direction.OUT,
        source_type=CashTransaction.SourceType.PAYMENT,
        source_id=source_id,
        amount=amount,
        currency=original.currency,
        business_date=business_date,
        description=f"Reversal: {original.description}"[:255],
        reason=reason[:255],
        reversal_of_id=original.cash_transaction_id if link_original_movement else None,
    )


def _refund_cash(
    *,
    gym: Gym,
    actor: GymMembership,
    source_id: UUID,
    amount: Decimal,
    currency: str,
    method: str,
    business_date: date,
    description: str,
    reason: str,
    cash_session_id: UUID | None,
) -> CashTransaction | None:
    if method != Payment.Method.CASH:
        if cash_session_id is not None:
            raise ValidationError(
                {"cash_session_id": ["Only a cash refund can use a cash session."]}
            )
        return None
    if cash_session_id is None:
        raise ValidationError({"cash_session_id": ["A cash refund requires an open session."]})
    return record_cash_movement(
        gym=gym,
        actor=actor,
        session_id=cash_session_id,
        direction=CashTransaction.Direction.OUT,
        source_type=CashTransaction.SourceType.PAYMENT,
        source_id=source_id,
        amount=amount,
        currency=currency,
        business_date=business_date,
        description=description[:255],
        reason=reason[:255],
        reversal_of_id=None,
    )


def _insert_posted_correction(
    *,
    gym: Gym,
    actor: GymMembership,
    original: Payment,
    correction_id: UUID,
    entry_type: str,
    amount: Decimal,
    method: str,
    business_date: date,
    description: str,
    reference_number: str,
    movement: CashTransaction | None,
    legacy: bool,
    posted_at: datetime,
    retail_sale_id: UUID | None = None,
) -> Payment:
    reception = _holds_reception(gym_id=gym.id, membership_id=actor.id)
    return Payment.objects.create(
        id=correction_id,
        gym=gym,
        member_id=original.member_id,
        membership_id=original.membership_id,
        retail_sale_id=retail_sale_id,
        original_payment=original,
        entry_type=entry_type,
        purpose=original.purpose,
        description=description,
        amount=amount,
        currency=original.currency,
        method=method,
        business_date=business_date,
        received_at=posted_at,
        reference_number=reference_number,
        status=Payment.Status.POSTED,
        submitted_at=posted_at,
        submitted_by_membership_id=actor.id,
        approved_at=posted_at,
        approved_by_membership_id=actor.id,
        posted_at=posted_at,
        posted_by_membership_id=actor.id,
        cash_session=None if movement is None else movement.cash_session,
        cash_transaction=movement,
        legacy_unlinked_cash=legacy,
        created_with_reception_scope=reception,
        submitted_with_reception_scope=reception,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )


def _void_locked_receipt(
    *,
    receipts: list[Receipt],
    payment_id: UUID,
    actor: GymMembership,
    reason: str,
    voided_at: datetime,
) -> Receipt | None:
    issued = next(
        (
            row
            for row in receipts
            if row.payment_id == payment_id and row.status == Receipt.Status.ISSUED
        ),
        None,
    )
    if issued is None:
        return None
    issued.status = Receipt.Status.VOID
    issued.voided_at = voided_at
    issued.voided_by_membership_id = actor.id
    issued.void_reason = reason
    issued.save(
        update_fields=[
            "status",
            "voided_at",
            "voided_by_membership_id",
            "void_reason",
            "updated_at",
        ]
    )
    return issued


def _settlement_after(membership: Membership | None) -> dict[str, object] | None:
    if membership is None:
        return None
    refresh_membership_payment_status(membership)
    membership.refresh_from_db()
    paid = _posted_total(membership)
    return {
        "membership_id": membership.id,
        "payment_status": membership.payment_status,
        "paid_amount": paid,
        "balance_amount": Decimal(membership.final_amount) - paid,
    }
