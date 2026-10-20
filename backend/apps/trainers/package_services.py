"""Member package grants and the append-only session ledger.

Sale approval calls ``grant_for_posted_sale`` inside the sale transaction.
Admin grant and ledger adjust are their own idempotent commands. Consumption
rows stay unpublished until booking tables exist.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import connection
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.selectors import membership_has_permission
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Payment
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from apps.trainers.exceptions import (
    LedgerBounds,
    LedgerEntryIneligible,
    MemberNotActive,
    PackageNotEligible,
    SaleGrantRequired,
)
from apps.trainers.models import (
    MemberServicePackage,
    PTSale,
    ServicePackage,
    ServiceSessionLedger,
)
from apps.trainers.selectors import (
    gym_local_date,
    holds_protected_role,
    lock_visible_member_package,
)
from apps.trainers.validity import inclusive_until

_SPACE = re.compile(r"\s+")


def lock_eligible_pt_package(
    *,
    gym: Gym,
    package_id: UUID | None,
    currency: str,
) -> tuple[ServicePackage | None, Decimal | None]:
    """Return a locked active same-gym PT package and its price."""

    if package_id is None:
        return None, None
    package = ServicePackage.objects.filter(gym_id=gym.id, id=package_id).first()
    if (
        package is None
        or package.type != ServicePackage.PackageType.PT
        or not package.is_active
        or package.currency != currency
    ):
        raise PackageNotEligible()
    return package, package.price


def grant_for_posted_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale: PTSale,
    payment: Payment | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> MemberServicePackage | None:
    """Insert one package and one GRANT after the sale is posted."""

    if sale.service_package_id is None:
        return None
    if sale.status != PTSale.Status.POSTED:
        raise PackageNotEligible()
    package, snapshot = lock_eligible_pt_package(
        gym=gym,
        package_id=sale.service_package_id,
        currency=sale.currency,
    )
    if package is None or snapshot != sale.package_price_snapshot:
        raise PackageNotEligible()
    grant = _insert_grant(
        gym=gym,
        actor=actor,
        member_id=sale.member_id,
        package=package,
        source=MemberServicePackage.Source.PT_SALE,
        pt_sale=sale,
        payment=payment,
        valid_from=sale.service_date,
        complimentary=False,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        reason="",
        action="MEMBER_SERVICE_PACKAGE_GRANTED",
    )
    return grant


def grant_package_admin(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    service_package_id: UUID,
    business_date: date,
    complimentary: bool,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[MemberServicePackage, ServiceSessionLedger]:
    """Grant one package without a sale or payment."""

    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id)
    _require_transaction()
    _assert_adjuster(gym=gym, actor=actor)
    stored_reason = _required_text(reason, field="reason")
    member = _lock_member(gym_id=gym.id, member_id=member_id)
    package = ServicePackage.objects.filter(gym_id=gym.id, id=service_package_id).first()
    if package is None:
        raise Http404
    if not package.is_active:
        raise PackageNotEligible()
    if package.type == ServicePackage.PackageType.PT and not complimentary:
        raise SaleGrantRequired()
    if package.type not in {ServicePackage.PackageType.PT, ServicePackage.PackageType.CLASS}:
        raise PackageNotEligible()
    grant = _insert_grant(
        gym=gym,
        actor=actor,
        member_id=member.id,
        package=package,
        source=MemberServicePackage.Source.ADMIN_GRANT,
        pt_sale=None,
        payment=None,
        valid_from=business_date,
        complimentary=complimentary,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        reason=stored_reason,
        action="MEMBER_SERVICE_PACKAGE_GRANTED",
    )
    entry = grant.ledger_entries.get(reason=ServiceSessionLedger.Reason.GRANT)
    return grant, entry


def adjust_ledger(
    *,
    gym: Gym,
    actor: GymMembership,
    package_id: UUID,
    reverses_entry_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[MemberServicePackage, ServiceSessionLedger]:
    """Append one CORRECTION that negates an eligible non-grant entry."""

    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id)
    _require_transaction()
    _assert_adjuster(gym=gym, actor=actor)
    stored_reason = _required_text(reason, field="reason")
    package = lock_visible_member_package(gym=gym, actor=actor, package_id=package_id, manage=True)
    entry = ServiceSessionLedger.objects.filter(
        gym_id=gym.id,
        member_service_package_id=package.id,
        id=reverses_entry_id,
    ).first()
    if entry is None:
        raise Http404
    if entry.reason == ServiceSessionLedger.Reason.GRANT:
        raise LedgerEntryIneligible()
    if ServiceSessionLedger.objects.filter(
        gym_id=gym.id,
        reverses_entry_id=entry.id,
    ).exists():
        raise LedgerEntryIneligible()
    delta = -entry.session_delta
    remaining = _remaining(package) + delta
    if remaining < 0 or remaining > package.granted_sessions:
        raise LedgerBounds()
    correction = ServiceSessionLedger(
        gym=gym,
        member_service_package=package,
        session_delta=delta,
        reason=ServiceSessionLedger.Reason.CORRECTION,
        reverses_entry=entry,
        occurred_at=timezone.now(),
        created_by_membership_id=actor.id,
    )
    correction.save()
    _store_status(package, actor=actor, remaining=remaining)
    _audit(
        gym=gym,
        actor=actor,
        action="SESSION_LEDGER_CORRECTED",
        package=package,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"ledger_entry_id": str(correction.id), "reason": stored_reason},
    )
    return package, correction


def _insert_grant(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    package: ServicePackage,
    source: str,
    pt_sale: PTSale | None,
    payment: Payment | None,
    valid_from: date,
    complimentary: bool,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    reason: str,
    action: str,
) -> MemberServicePackage:
    valid_until = inclusive_until(valid_from, package.validity_value, package.validity_unit)
    today = gym_local_date(gym)
    status = _status_for_remaining(
        remaining=package.session_count,
        valid_until=valid_until,
        today=today,
    )
    grant = MemberServicePackage(
        gym=gym,
        member_id=member_id,
        service_package=package,
        source=source,
        pt_sale=pt_sale,
        payment=payment,
        package_type=package.type,
        package_code=package.code,
        package_name=package.name,
        granted_sessions=package.session_count,
        amount_snapshot=package.price,
        currency=package.currency,
        validity_value=package.validity_value,
        validity_unit=package.validity_unit,
        valid_from=valid_from,
        valid_until=valid_until,
        complimentary=complimentary,
        status=status,
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
    )
    grant.save()
    ServiceSessionLedger.objects.create(
        gym=gym,
        member_service_package=grant,
        session_delta=package.session_count,
        reason=ServiceSessionLedger.Reason.GRANT,
        occurred_at=timezone.now(),
        created_by_membership_id=actor.id,
    )
    _audit(
        gym=gym,
        actor=actor,
        action=action,
        package=grant,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"reason": reason} if reason else None,
    )
    return grant


def _store_status(
    package: MemberServicePackage,
    *,
    actor: GymMembership,
    remaining: int,
) -> None:
    today = gym_local_date(package.gym)
    package.status = _status_for_remaining(
        remaining=remaining,
        valid_until=package.valid_until,
        today=today,
    )
    package.updated_by_membership_id = actor.id
    package.save(update_fields=["status", "updated_by_membership_id", "updated_at"])


def _remaining(package: MemberServicePackage) -> int:
    total = package.ledger_entries.aggregate(total=Sum("session_delta"))["total"]
    return 0 if total is None else int(total)


def _status_for_remaining(*, remaining: int, valid_until: date, today: date) -> str:
    if remaining <= 0:
        return MemberServicePackage.Status.EXHAUSTED
    if today > valid_until:
        return MemberServicePackage.Status.EXPIRED
    return MemberServicePackage.Status.ACTIVE


def _assert_adjuster(*, gym: Gym, actor: GymMembership) -> None:
    if holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="RECEPTIONIST"):
        raise PermissionDenied()
    if holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="TRAINER"):
        raise PermissionDenied()
    manager = holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="MANAGER")
    owner = holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="OWNER")
    if not manager and not owner:
        raise PermissionDenied()


def _lock_member(*, gym_id: UUID, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym_id=gym_id, id=member_id).first()
    if member is None:
        raise Http404
    if member.status != Member.Status.ACTIVE:
        raise MemberNotActive()
    return member


def _reload(*, gym_id: UUID, membership_id: UUID) -> tuple[Gym, GymMembership]:
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
        or not membership_has_permission(membership=membership, code="pt.sessions.adjust")
    ):
        raise PermissionDenied()
    return membership.gym, membership


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise RuntimeError("Package commands require an active transaction.")


def _required_text(value: str, *, field: str) -> str:
    collapsed = _SPACE.sub(" ", value.strip())
    if collapsed == "":
        raise ValidationError({field: ["Enter a value."]})
    return collapsed


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    package: MemberServicePackage,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    extra: dict[str, object] | None,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="trainers",
        target_type="MemberServicePackage",
        target_id=package.id,
        request_id=request_id,
        old_values=None,
        new_values={
            "member_id": str(package.member_id),
            "service_package_id": str(package.service_package_id),
            "source": package.source,
            "status": package.status,
            "granted_sessions": package.granted_sessions,
            **(extra or {}),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
