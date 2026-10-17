"""Payment drafts and the single posting path.

Lock order inside approval: payment, membership, cash session, cash
transaction, receipt sequence, then the receipt. The receipt guard reads the
payment status, so the posted update is written immediately before the receipt
insert and still after the sequence allocation. A failure rolls all of it back.

Public void and refund stay unpublished. Until the reversal increment, only
the migration role or a superuser may move a posted charge to void and store
``reversal_payment``. ``gym_runtime`` cannot make that transition, and a
session setting is not an authorization boundary.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import record_cash_movement, revalidate_cash_actor
from apps.common.finance.exceptions import FinanceStateConflict
from apps.members.models import Member, Membership, Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.members.services import refresh_membership_payment_status
from apps.tenants.models import Gym, GymMembership, GymSettings, StaffProfile
from apps.tenants.sequence_services import allocate_formatted_number, ensure_receipt_sequence

_ZERO = Decimal("0.00")


class PaymentPostingConflict(FinanceStateConflict):
    """The locked payment or its source can no longer be posted."""


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


def command_route(*, payment_id: UUID | None, action: str) -> str:
    if payment_id is None:
        return "/api/v1/payments/"
    if not action:
        return f"/api/v1/payments/{payment_id}/"
    return f"/api/v1/payments/{payment_id}/{action}/"
