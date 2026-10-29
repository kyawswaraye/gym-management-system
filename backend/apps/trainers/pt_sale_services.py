"""Personal-training sale drafts, submission, approval, and rejection.

Create, update, and cancel stay inside the caller's idempotency transaction.
Submit freezes the sale, its optional package, and its optional payment.
Approval posts that payment, inserts one accrued commission, and when a package
is selected inserts one grant and one GRANT ledger row. Rejection creates no
receipt, cash movement, commission, expense, package, or ledger row.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.db import InternalError, ProgrammingError, connection, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashTransaction
from apps.cashbook.selectors import membership_has_permission
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Payment, Receipt
from apps.members.payment_services import (
    cancel_linked_pt_payment_draft,
    create_linked_pt_payment_draft,
    post_linked_pt_payment,
    reject_linked_pt_payment,
    submit_linked_pt_payment,
    update_linked_pt_payment_draft,
)
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, StaffProfile
from apps.trainers.exceptions import (
    AssignmentNotEffective,
    MemberNotActive,
    PackageNotEligible,
    PTSaleStateConflict,
    SaleCurrencyMismatch,
    TrainerNotEligible,
)
from apps.trainers.models import MemberServicePackage, PTCommission, PTSale, TrainerAssignment
from apps.trainers.package_services import grant_for_posted_sale, lock_eligible_pt_package
from apps.trainers.selectors import holds_protected_role, pt_sale_visibility, trainer_member_scope

_SPACE = re.compile(r"\s+")
_MONEY = Decimal("0.01")
_TRAINER_ROLE = "TRAINER"


def preview_sale_amounts(
    *,
    gross_fee: Decimal,
    percent: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return commission, gym share, paid, and outstanding for a draft preview."""

    commission = (gross_fee * percent / Decimal("100")).quantize(_MONEY, rounding=ROUND_HALF_UP)
    gym_share = (gross_fee - commission).quantize(_MONEY, rounding=ROUND_HALF_UP)
    paid = Decimal("0.00")
    return commission, gym_share, paid, commission


def collapsed_text(value: str) -> str:
    return _SPACE.sub(" ", value.strip())


def command_route(*, sale_id: UUID | None, action: str) -> str:
    if sale_id is None:
        return "/api/v1/pt-sales/"
    if not action:
        return f"/api/v1/pt-sales/{sale_id}/"
    return f"/api/v1/pt-sales/{sale_id}/{action}/"


def create_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    trainer_staff_profile_id: UUID,
    service_date: date,
    description: str,
    gross_fee: Decimal,
    currency: str,
    payment: dict[str, Any] | None,
    service_package_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.create")
    _require_transaction()
    stored_description = _required_text(description, field="description")
    trainer = _lock_trainer(
        gym=gym,
        actor=actor,
        trainer_staff_profile_id=trainer_staff_profile_id,
    )
    member = _lock_member(gym_id=gym.id, member_id=member_id)
    _lock_assignment(
        gym_id=gym.id,
        trainer_staff_profile_id=trainer.id,
        member_id=member_id,
        service_date=service_date,
    )
    _require_gym_currency(gym=gym, currency=currency)
    package, snapshot = lock_eligible_pt_package(
        gym=gym,
        package_id=service_package_id,
        currency=currency,
    )
    row = PTSale(
        gym=gym,
        member_id=member_id,
        trainer_staff_profile=trainer,
        service_date=service_date,
        description=stored_description,
        gross_fee=gross_fee,
        currency=currency,
        commission_percent_snapshot=trainer.default_commission_percent,
        service_package=package,
        package_price_snapshot=snapshot,
        status=PTSale.Status.DRAFT,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    _save_row(row)
    if payment is not None:
        linked = create_linked_pt_payment_draft(
            gym=gym,
            actor=actor,
            sale_id=row.id,
            member=member,
            currency=currency,
            fields=payment,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row.payment = linked
        _save_row(row)
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_DRAFT_CREATED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=None,
    )
    return row


def update_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.update")
    _require_transaction()
    row = _lock_visible(gym=gym, actor=actor, sale_id=sale_id)
    if row.status != PTSale.Status.DRAFT:
        raise PTSaleStateConflict()
    previous = _audit_values(row)
    member_id = fields.get("member_id", row.member_id)
    trainer_id = fields.get("trainer_staff_profile_id", row.trainer_staff_profile_id)
    service_date = fields.get("service_date", row.service_date)
    description = row.description
    if "description" in fields:
        description = _required_text(str(fields["description"]), field="description")
    gross_fee = fields.get("gross_fee", row.gross_fee)
    currency = str(fields.get("currency", row.currency))
    if "service_package_id" in fields:
        package_id = fields["service_package_id"]
    else:
        package_id = row.service_package_id
    trainer = _lock_trainer(gym=gym, actor=actor, trainer_staff_profile_id=trainer_id)
    member = _lock_member(gym_id=gym.id, member_id=member_id)
    _lock_assignment(
        gym_id=gym.id,
        trainer_staff_profile_id=trainer.id,
        member_id=member_id,
        service_date=service_date,
    )
    _require_gym_currency(gym=gym, currency=currency)
    package, snapshot = lock_eligible_pt_package(
        gym=gym,
        package_id=package_id,
        currency=currency,
    )
    trainer_changed = trainer.id != row.trainer_staff_profile_id
    row.member_id = member_id
    row.trainer_staff_profile = trainer
    row.service_date = service_date
    row.description = description
    row.gross_fee = gross_fee
    row.currency = currency
    row.service_package = package
    row.package_price_snapshot = snapshot
    if trainer_changed:
        row.commission_percent_snapshot = trainer.default_commission_percent
    row.updated_by_membership_id = actor.id
    _save_row(row)
    if "payment" in fields:
        _sync_payment_draft(
            gym=gym,
            actor=actor,
            row=row,
            member=member,
            payment=fields["payment"],
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    else:
        _align_linked_payment_source(
            gym=gym,
            actor=actor,
            row=row,
            member=member,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_DRAFT_UPDATED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


def cancel_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.update")
    _require_transaction()
    row = _lock_visible(gym=gym, actor=actor, sale_id=sale_id)
    if row.status != PTSale.Status.DRAFT:
        raise PTSaleStateConflict()
    stored_reason = _required_text(reason, field="reason")
    previous = _audit_values(row)
    if row.payment_id is not None:
        linked = _lock_sale_payment(row)
        cancel_linked_pt_payment_draft(
            gym=gym,
            actor=actor,
            payment=linked,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row.payment = None
        _save_row(row)
    row.status = PTSale.Status.CANCELLED
    row.cancelled_at = timezone.now()
    row.cancelled_by_membership_id = actor.id
    row.reason = stored_reason
    row.updated_by_membership_id = actor.id
    _save_row(row)
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_CANCELLED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


def submit_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.submit")
    _require_transaction()
    row = _lock_visible(gym=gym, actor=actor, sale_id=sale_id)
    if row.status != PTSale.Status.DRAFT:
        raise PTSaleStateConflict()
    _revalidate_sale(gym=gym, actor=actor, row=row)
    previous = _audit_values(row)
    now = timezone.now()
    row.status = PTSale.Status.PENDING_APPROVAL
    row.submitted_at = now
    row.submitted_by_membership_id = actor.id
    row.updated_by_membership_id = actor.id
    _save_row(row)
    if row.payment_id is not None:
        submit_linked_pt_payment(
            gym=gym,
            actor=actor,
            payment=_lock_sale_payment(row),
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if row.payment is not None:
            row.payment.refresh_from_db()
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_SUBMITTED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


def approve_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[
    PTSale,
    Payment | None,
    Receipt | None,
    CashTransaction | None,
    PTCommission,
    MemberServicePackage | None,
]:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.approve")
    _require_transaction()
    _assert_approver(gym=gym, actor=actor)
    row = _lock_visible(gym=gym, actor=actor, sale_id=sale_id)
    _assert_approver(gym=gym, actor=actor)
    if row.status != PTSale.Status.PENDING_APPROVAL:
        raise PTSaleStateConflict()
    _deny_self_close(
        gym_id=gym.id,
        actor=actor,
        created_by=row.created_by_membership_id,
        submitted_by=row.submitted_by_membership_id,
    )
    _revalidate_sale(gym=gym, actor=actor, row=row)
    linked = _lock_sale_payment(row) if row.payment_id is not None else None
    if linked is None and cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["A sale without a payment cannot use a cash session."]}
        )
    if linked is not None and linked.method != Payment.Method.CASH and cash_session_id is not None:
        raise ValidationError({"cash_session_id": ["Only a cash payment can use a cash session."]})
    if linked is not None and linked.method == Payment.Method.CASH and cash_session_id is None:
        raise ValidationError({"cash_session_id": ["A cash payment requires an open session."]})
    previous = _audit_values(row)
    now = timezone.now()
    row.status = PTSale.Status.POSTED
    row.approved_at = now
    row.approved_by_membership_id = actor.id
    row.posted_at = now
    row.updated_by_membership_id = actor.id
    _save_row(row)
    payment: Payment | None = None
    receipt: Receipt | None = None
    movement: CashTransaction | None = None
    if linked is not None:
        payment, receipt, movement = post_linked_pt_payment(
            gym=gym,
            approver=actor,
            payment=linked,
            cash_session_id=cash_session_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row.payment = payment
    commission, gym_share, _paid, outstanding = preview_sale_amounts(
        gross_fee=row.gross_fee,
        percent=row.commission_percent_snapshot,
    )
    accrual = PTCommission.objects.create(
        gym=gym,
        pt_sale=row,
        trainer_staff_profile_id=row.trainer_staff_profile_id,
        gross_fee_snapshot=row.gross_fee,
        commission_percent_snapshot=row.commission_percent_snapshot,
        commission_amount=commission,
        gym_share=gym_share,
        paid_amount=Decimal("0.00"),
        outstanding_amount=outstanding,
        currency=row.currency,
        status=PTCommission.Status.ACCRUED,
        created_by_membership_id=actor.id,
    )
    granted = grant_for_posted_sale(
        gym=gym,
        actor=actor,
        sale=row,
        payment=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_APPROVED_POSTED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
        extra={
            "commission_id": str(accrual.id),
            "member_service_package_id": None if granted is None else str(granted.id),
        },
    )
    return row, payment, receipt, movement, accrual, granted


def reject_pt_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    rejection_reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="pt.sales.approve")
    _require_transaction()
    _assert_approver(gym=gym, actor=actor)
    row = _lock_visible(gym=gym, actor=actor, sale_id=sale_id)
    _assert_approver(gym=gym, actor=actor)
    if row.status != PTSale.Status.PENDING_APPROVAL:
        raise PTSaleStateConflict()
    _deny_self_close(
        gym_id=gym.id,
        actor=actor,
        created_by=row.created_by_membership_id,
        submitted_by=row.submitted_by_membership_id,
    )
    stored_reason = _required_text(rejection_reason, field="rejection_reason")
    previous = _audit_values(row)
    now = timezone.now()
    row.status = PTSale.Status.REJECTED
    row.rejected_at = now
    row.rejected_by_membership_id = actor.id
    row.rejection_reason = stored_reason
    row.updated_by_membership_id = actor.id
    _save_row(row)
    if row.payment_id is not None:
        reject_linked_pt_payment(
            gym=gym,
            actor=actor,
            payment=_lock_sale_payment(row),
            reason=stored_reason,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if row.payment is not None:
            row.payment.refresh_from_db()
    _audit(
        gym=gym,
        actor=actor,
        action="PT_SALE_REJECTED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


def _align_linked_payment_source(
    *,
    gym: Gym,
    actor: GymMembership,
    row: PTSale,
    member: Member,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    """Copy sale-owned payment source fields when the client omits the draft."""

    if row.payment_id is None:
        return
    linked = _lock_sale_payment(row)
    if linked.member_id == member.id and linked.currency == row.currency:
        return
    row.payment = update_linked_pt_payment_draft(
        gym=gym,
        actor=actor,
        payment=linked,
        member=member,
        fields={},
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _sync_payment_draft(
    *,
    gym: Gym,
    actor: GymMembership,
    row: PTSale,
    member: Member,
    payment: dict[str, Any] | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    if payment is None:
        if row.payment_id is None:
            return
        cancel_linked_pt_payment_draft(
            gym=gym,
            actor=actor,
            payment=_lock_sale_payment(row),
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row.payment = None
        _save_row(row)
        return
    if row.payment_id is None:
        linked = create_linked_pt_payment_draft(
            gym=gym,
            actor=actor,
            sale_id=row.id,
            member=member,
            currency=row.currency,
            fields=payment,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        row.payment = linked
        _save_row(row)
        return
    row.payment = update_linked_pt_payment_draft(
        gym=gym,
        actor=actor,
        payment=_lock_sale_payment(row),
        member=member,
        fields=payment,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _revalidate_sale(*, gym: Gym, actor: GymMembership, row: PTSale) -> None:
    _lock_trainer(gym=gym, actor=actor, trainer_staff_profile_id=row.trainer_staff_profile_id)
    _lock_member(gym_id=gym.id, member_id=row.member_id)
    _lock_assignment(
        gym_id=gym.id,
        trainer_staff_profile_id=row.trainer_staff_profile_id,
        member_id=row.member_id,
        service_date=row.service_date,
    )
    _require_gym_currency(gym=gym, currency=row.currency)
    if row.service_package_id is not None:
        _package, snapshot = lock_eligible_pt_package(
            gym=gym,
            package_id=row.service_package_id,
            currency=row.currency,
        )
        if snapshot != row.package_price_snapshot:
            raise PackageNotEligible()


def _may_close_own_sale(*, gym_id: UUID, membership_id: UUID) -> bool:
    """The owner, or one person who is both manager and receptionist, can.

    A manager who does not also work the desk still needs another approver.
    """

    if holds_protected_role(gym_id=gym_id, membership_id=membership_id, role_key="OWNER"):
        return True
    return holds_protected_role(
        gym_id=gym_id, membership_id=membership_id, role_key="MANAGER"
    ) and holds_protected_role(gym_id=gym_id, membership_id=membership_id, role_key="RECEPTIONIST")


def _deny_self_close(
    *,
    gym_id: UUID,
    actor: GymMembership,
    created_by: UUID | None,
    submitted_by: UUID | None,
) -> None:
    if actor.id not in {created_by, submitted_by}:
        return
    if _may_close_own_sale(gym_id=gym_id, membership_id=actor.id):
        return
    raise PermissionDenied()


def _assert_approver(*, gym: Gym, actor: GymMembership) -> None:
    manager = holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="MANAGER")
    owner = holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="OWNER")
    receptionist = holds_protected_role(
        gym_id=gym.id, membership_id=actor.id, role_key="RECEPTIONIST"
    )
    if receptionist and not manager and not owner:
        raise PermissionDenied()
    if not manager and not owner:
        raise PermissionDenied()
    if not membership_has_permission(membership=actor, code="pt.sales.approve"):
        raise PermissionDenied()


def _lock_sale_payment(row: PTSale) -> Payment:
    payment_id = row.payment_id
    if payment_id is None:
        raise PTSaleStateConflict()
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .select_related("member")
        .filter(gym_id=row.gym_id, id=payment_id, pt_sale_id=row.id)
        .first()
    )
    if payment is None:
        raise PTSaleStateConflict()
    return payment


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise RuntimeError("PT sale commands require an active transaction.")


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


def _lock_visible(*, gym: Gym, actor: GymMembership, sale_id: UUID) -> PTSale:
    queryset, _binding = pt_sale_visibility(gym=gym, actor=actor)
    row = queryset.select_for_update(of=("self",)).filter(id=sale_id).first()
    if row is None:
        raise Http404
    return row


def _lock_trainer(
    *,
    gym: Gym,
    actor: GymMembership,
    trainer_staff_profile_id: UUID,
) -> StaffProfile:
    scope = trainer_member_scope(gym_id=gym.id, actor=actor)
    if scope.restricted and trainer_staff_profile_id not in scope.profile_ids:
        own = StaffProfile.objects.filter(
            gym_id=gym.id,
            id=trainer_staff_profile_id,
            gym_membership_id=actor.id,
        ).exists()
        if own:
            raise TrainerNotEligible()
        raise Http404
    trainer = (
        StaffProfile.objects.select_for_update()
        .select_related("gym_membership")
        .filter(gym_id=gym.id, id=trainer_staff_profile_id)
        .first()
    )
    if trainer is None:
        raise Http404
    membership = (
        GymMembership.objects.select_for_update()
        .filter(gym_id=gym.id, id=trainer.gym_membership_id)
        .first()
    )
    protected = holds_protected_role(
        gym_id=gym.id,
        membership_id=trainer.gym_membership_id,
        role_key=_TRAINER_ROLE,
    )
    if (
        trainer.status != StaffProfile.Status.ACTIVE
        or membership is None
        or membership.status != GymMembership.Status.ACTIVE
        or not protected
    ):
        raise TrainerNotEligible()
    return trainer


def _lock_member(*, gym_id: UUID, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym_id=gym_id, id=member_id).first()
    if member is None:
        raise Http404
    if member.status != Member.Status.ACTIVE:
        raise MemberNotActive()
    return member


def _lock_assignment(
    *,
    gym_id: UUID,
    trainer_staff_profile_id: UUID,
    member_id: UUID,
    service_date: date,
) -> None:
    rows = list(
        TrainerAssignment.objects.select_for_update().filter(
            gym_id=gym_id,
            trainer_staff_profile_id=trainer_staff_profile_id,
            member_id=member_id,
            status=TrainerAssignment.Status.ACTIVE,
        )
    )
    covered = any(
        row.effective_from <= service_date
        and (row.effective_until is None or row.effective_until >= service_date)
        for row in rows
    )
    if not covered:
        raise AssignmentNotEffective()


def _require_gym_currency(*, gym: Gym, currency: str) -> None:
    if currency != gym.currency:
        raise SaleCurrencyMismatch()


def _required_text(value: str, *, field: str) -> str:
    collapsed = collapsed_text(value)
    if collapsed == "":
        raise ValidationError({field: ["Enter a value."]})
    return collapsed


def _save_row(row: PTSale) -> None:
    try:
        with transaction.atomic():
            row.save()
    except (InternalError, ProgrammingError) as exc:
        message = str(getattr(exc, "__cause__", exc))
        if "assignment is not effective" in message:
            raise AssignmentNotEffective() from exc
        if "trainer is not eligible" in message:
            raise TrainerNotEligible() from exc
        if "currency must match" in message:
            raise SaleCurrencyMismatch() from exc
        if "member is not active" in message:
            raise MemberNotActive() from exc
        if "package is not an eligible" in message:
            raise PackageNotEligible() from exc
        if "pt_sale_guard" in message:
            raise PTSaleStateConflict() from exc
        raise


def _audit_values(row: PTSale) -> dict[str, object]:
    return {
        "member_id": str(row.member_id),
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "service_date": row.service_date.isoformat(),
        "description": row.description,
        "gross_fee": f"{row.gross_fee:.2f}",
        "currency": row.currency,
        "commission_percent_snapshot": f"{row.commission_percent_snapshot:.2f}",
        "status": row.status,
        "reason": row.reason,
        "service_package_id": (
            None if row.service_package_id is None else str(row.service_package_id)
        ),
        "package_price_snapshot": (
            None if row.package_price_snapshot is None else f"{row.package_price_snapshot:.2f}"
        ),
    }


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    row: PTSale,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    old_values: dict[str, object] | None,
    extra: dict[str, object] | None = None,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="trainers",
        target_type="PTSale",
        target_id=row.id,
        request_id=request_id,
        old_values=old_values,
        new_values={**_audit_values(row), **(extra or {})},
        ip_address=ip_address,
        user_agent=user_agent,
    )
