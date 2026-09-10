from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.tenants.models import Gym, GymMembership, GymSettings


def _add_duration(start: date, value: int, unit: str) -> date:
    if unit == MembershipPlan.DurationUnit.DAY:
        return start + timedelta(days=value - 1)
    if unit == MembershipPlan.DurationUnit.WEEK:
        return start + timedelta(weeks=value) - timedelta(days=1)
    months = value * (12 if unit == MembershipPlan.DurationUnit.YEAR else 1)
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


@transaction.atomic
def create_member(*, gym: Gym, actor: GymMembership, data: dict[str, Any]) -> Member:
    settings = GymSettings.objects.select_for_update().get(gym=gym)
    member_number = data.pop("member_number", "").strip()
    if not member_number:
        member_number = f"{settings.member_number_prefix}-{settings.next_member_number:06d}"
        settings.next_member_number += 1
        settings.updated_by_membership_id = actor.id
        settings.save(
            update_fields=["next_member_number", "updated_by_membership_id", "updated_at"]
        )
    return Member.objects.create(
        gym=gym,
        member_number=member_number,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
        **data,
    )


@transaction.atomic
def create_plan(*, gym: Gym, actor: GymMembership, data: dict[str, Any]) -> MembershipPlan:
    if data["currency"] != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    return MembershipPlan.objects.create(
        gym=gym,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
        **data,
    )


@transaction.atomic
def create_membership(
    *,
    gym: Gym,
    actor: GymMembership,
    member: Member,
    plan: MembershipPlan,
    start_date: date,
    discount_amount: Decimal,
) -> Membership:
    Member.objects.select_for_update().get(gym=gym, id=member.id)
    expiry_date = _add_duration(start_date, plan.duration_value, plan.duration_unit)
    overlap = Membership.objects.filter(
        gym=gym,
        member=member,
        status__in=[Membership.Status.PENDING, Membership.Status.ACTIVE],
        start_date__lte=expiry_date,
        expiry_date__gte=start_date,
    ).exists()
    if overlap:
        raise ValidationError({"start_date": ["Membership dates overlap an active membership."]})
    if discount_amount < 0 or discount_amount > plan.price:
        raise ValidationError(
            {"discount_amount": ["Discount must be between zero and plan price."]}
        )
    source = (
        Membership.RevenueSource.RENEWAL
        if Membership.objects.filter(gym=gym, member=member).exists()
        else Membership.RevenueSource.NEW_MEMBER
    )
    return Membership.objects.create(
        gym=gym,
        member=member,
        plan=plan,
        plan_code=plan.code,
        plan_name=plan.name,
        duration_value=plan.duration_value,
        duration_unit=plan.duration_unit,
        start_date=start_date,
        expiry_date=expiry_date,
        list_price=plan.price,
        discount_amount=discount_amount,
        final_amount=plan.price - discount_amount,
        currency=plan.currency,
        revenue_source=source,
        created_by_membership_id=actor.id,
    )


def refresh_membership_payment_status(membership: Membership) -> None:
    entries = Payment.objects.filter(
        gym=membership.gym,
        membership=membership,
        status__in=[Payment.Status.POSTED, Payment.Status.VOID],
    )
    paid = sum(
        (
            -payment.amount
            if payment.entry_type in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
            else payment.amount
            for payment in entries
        ),
        Decimal("0"),
    )
    if paid <= 0:
        value = Membership.PaymentStatus.UNPAID
    elif paid < membership.final_amount:
        value = Membership.PaymentStatus.PARTIAL
    else:
        value = Membership.PaymentStatus.PAID
    if membership.payment_status != value:
        Membership.objects.filter(gym=membership.gym, id=membership.id).update(payment_status=value)


@transaction.atomic
def submit_payment(*, payment: Payment, actor: GymMembership) -> Payment:
    payment = Payment.objects.select_for_update().get(gym=payment.gym, id=payment.id)
    if payment.status != Payment.Status.DRAFT:
        raise ValidationError({"status": ["Only draft payments can be submitted."]})
    payment.status = Payment.Status.PENDING_APPROVAL
    payment.submitted_at = timezone.now()
    payment.submitted_by_membership_id = actor.id
    payment.updated_by_membership_id = actor.id
    payment.save()
    return payment


@transaction.atomic
def approve_payment(*, payment: Payment, actor: GymMembership) -> tuple[Payment, Receipt]:
    payment = (
        Payment.objects.select_for_update()
        .select_related("member", "membership")
        .get(gym=payment.gym, id=payment.id)
    )
    if payment.status != Payment.Status.PENDING_APPROVAL:
        raise ValidationError({"status": ["Only pending payments can be approved."]})
    if payment.submitted_by_membership_id == actor.id:
        raise ValidationError({"approver": ["Submitter cannot approve the same payment."]})
    if payment.membership is not None and payment.entry_type == Payment.EntryType.CHARGE:
        already_paid = Payment.objects.filter(
            gym=payment.gym,
            membership=payment.membership,
            status__in=[Payment.Status.POSTED, Payment.Status.VOID],
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if already_paid + payment.amount > payment.membership.final_amount:
            raise ValidationError(
                {"amount": ["Payment exceeds the authoritative membership balance."]}
            )
    payment.status = Payment.Status.POSTED
    payment.approved_at = timezone.now()
    payment.approved_by_membership_id = actor.id
    payment.updated_by_membership_id = actor.id
    payment.save()
    receipt = Receipt.objects.create(
        gym=payment.gym,
        payment=payment,
        receipt_number=f"{payment.business_date:%Y%m%d}-{str(payment.id).split('-')[0].upper()}",
        issued_at=timezone.now(),
        member_number=payment.member.member_number,
        member_name=payment.member.display_name,
        description=payment.description,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        business_date=payment.business_date,
        issuer_name=f"{actor.user.first_name} {actor.user.last_name}".strip() or actor.user.email,
        created_by_membership_id=actor.id,
    )
    membership = payment.membership
    if membership is not None:
        refresh_membership_payment_status(membership)
    return payment, receipt


@transaction.atomic
def refund_payment(
    *,
    payment: Payment,
    actor: GymMembership,
    amount: Decimal,
    reason: str,
) -> tuple[Payment, Receipt]:
    original = (
        Payment.objects.select_for_update()
        .select_related("member", "membership")
        .get(gym=payment.gym, id=payment.id)
    )
    if original.status != Payment.Status.POSTED or original.entry_type != Payment.EntryType.CHARGE:
        raise ValidationError({"status": ["Only a posted charge can be refunded."]})
    refunded = Payment.objects.filter(
        gym=original.gym,
        original_payment=original,
        entry_type=Payment.EntryType.REFUND,
        status=Payment.Status.POSTED,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if amount <= 0 or refunded + amount > original.amount:
        raise ValidationError({"amount": ["Refund exceeds the refundable balance."]})
    now = timezone.now()
    refund = Payment.objects.create(
        gym=original.gym,
        member=original.member,
        membership=original.membership,
        original_payment=original,
        entry_type=Payment.EntryType.REFUND,
        purpose=original.purpose,
        description=f"Refund: {reason}",
        amount=amount,
        currency=original.currency,
        method=original.method,
        business_date=timezone.localdate(),
        received_at=now,
        status=Payment.Status.POSTED,
        approved_at=now,
        approved_by_membership_id=actor.id,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    receipt = Receipt.objects.create(
        gym=refund.gym,
        payment=refund,
        receipt_number=f"{refund.business_date:%Y%m%d}-{str(refund.id).split('-')[0].upper()}",
        issued_at=now,
        member_number=refund.member.member_number,
        member_name=refund.member.display_name,
        description=refund.description,
        amount=refund.amount,
        currency=refund.currency,
        method=refund.method,
        business_date=refund.business_date,
        issuer_name=f"{actor.user.first_name} {actor.user.last_name}".strip() or actor.user.email,
        created_by_membership_id=actor.id,
    )
    if refund.membership is not None:
        refresh_membership_payment_status(refund.membership)
    return refund, receipt


@transaction.atomic
def void_payment(*, payment: Payment, actor: GymMembership, reason: str) -> tuple[Payment, Payment]:
    reversal, _receipt = refund_payment(
        payment=payment,
        actor=actor,
        amount=payment.amount,
        reason=f"Void: {reason}",
    )
    reversal.entry_type = Payment.EntryType.REVERSAL
    reversal.save(update_fields=["entry_type", "updated_at"])
    original = Payment.objects.select_for_update().get(gym=payment.gym, id=payment.id)
    original.status = Payment.Status.VOID
    original.voided_at = timezone.now()
    original.void_reason = reason
    original.updated_by_membership_id = actor.id
    original.save(
        update_fields=[
            "status",
            "voided_at",
            "void_reason",
            "updated_by_membership_id",
            "updated_at",
        ]
    )
    return original, reversal
