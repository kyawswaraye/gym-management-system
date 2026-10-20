"""Transactional trainer-assignment commands."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.classes.exceptions import ClassTypeInactive
from apps.classes.models import ClassType
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership, Role, StaffProfile
from apps.trainers.exceptions import (
    AssignmentOverlap,
    AssignmentStateConflict,
    MemberNotActive,
    ServicePackageConflict,
    TrainerNotEligible,
)
from apps.trainers.models import ServicePackage, TrainerAssignment
from apps.trainers.selectors import assignment_scope, trainer_member_scope

_EXCLUSION_VIOLATION = "23P01"
_TRAINER_ROLE = "TRAINER"


def _exclusion_violation(exc: IntegrityError) -> bool:
    return getattr(exc.__cause__, "sqlstate", None) == _EXCLUSION_VIOLATION


def _audit_values(row: TrainerAssignment) -> dict[str, Any]:
    return {
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "member_id": str(row.member_id),
        "effective_from": row.effective_from.isoformat(),
        "effective_until": None if row.effective_until is None else row.effective_until.isoformat(),
        "status": row.status,
        "reason": row.reason,
        "ended_at": None if row.ended_at is None else row.ended_at.isoformat(),
    }


def _lock_member(*, gym: Gym, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym=gym, id=member_id).first()
    if member is None:
        raise Http404
    if member.status != Member.Status.ACTIVE:
        raise MemberNotActive()
    return member


def _is_active_trainer(profile: StaffProfile) -> bool:
    if profile.status != StaffProfile.Status.ACTIVE:
        return False
    membership = (
        GymMembership.objects.select_for_update().filter(id=profile.gym_membership_id).first()
    )
    if (
        membership is None
        or membership.gym_id != profile.gym_id
        or membership.status != GymMembership.Status.ACTIVE
    ):
        return False
    return Role.objects.filter(
        gym_id=profile.gym_id,
        is_active=True,
        key=_TRAINER_ROLE,
        source_template__key=_TRAINER_ROLE,
        gymmembershiprole__gym_id=profile.gym_id,
        gymmembershiprole__gym_membership_id=membership.id,
    ).exists()


def _lock_trainer(
    *, gym: Gym, actor: GymMembership, trainer_staff_profile_id: UUID
) -> StaffProfile:
    profile = (
        StaffProfile.objects.select_for_update()
        .filter(gym=gym, id=trainer_staff_profile_id)
        .first()
    )
    if profile is None:
        raise Http404
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if scope.restricted and profile.id != scope.trainer_staff_profile_id:
        raise Http404
    if not _is_active_trainer(profile):
        raise TrainerNotEligible()
    return profile


def _ranges_overlap(
    *,
    gym: Gym,
    trainer: StaffProfile,
    member: Member,
    effective_from: date,
    effective_until: date | None,
) -> bool:
    queryset = TrainerAssignment.objects.filter(
        gym=gym,
        trainer_staff_profile=trainer,
        member=member,
        status=TrainerAssignment.Status.ACTIVE,
    ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=effective_from))
    if effective_until is not None:
        queryset = queryset.filter(effective_from__lte=effective_until)
    return queryset.exists()


def _require_ordered_dates(*, effective_from: date, effective_until: date | None) -> None:
    if effective_until is not None and effective_until < effective_from:
        raise ValidationError({"effective_until": ["End date cannot be before the start date."]})


def _lock_visible_assignment(
    *, gym: Gym, actor: GymMembership, assignment_id: UUID
) -> TrainerAssignment:
    assignment = (
        TrainerAssignment.objects.select_for_update().filter(gym=gym, id=assignment_id).first()
    )
    if assignment is None:
        raise Http404
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if scope.restricted and assignment.trainer_staff_profile_id != scope.trainer_staff_profile_id:
        raise Http404
    if assignment.status != TrainerAssignment.Status.ACTIVE:
        raise AssignmentStateConflict()
    return assignment


def _write_audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    row: TrainerAssignment,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    old_values: dict[str, Any] | None = None,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="trainers",
        target_type="TrainerAssignment",
        target_id=row.id,
        request_id=request_id,
        old_values=old_values,
        new_values=_audit_values(row),
        ip_address=ip_address,
        user_agent=user_agent,
    )


@transaction.atomic
def create_assignment(
    *,
    gym: Gym,
    actor: GymMembership,
    trainer_staff_profile_id: UUID,
    member_id: UUID,
    effective_from: date,
    effective_until: date | None,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> TrainerAssignment:
    set_gym_context(gym_id=gym.id)
    if trainer_member_scope(gym_id=gym.id, actor=actor).restricted:
        raise Http404
    _require_ordered_dates(effective_from=effective_from, effective_until=effective_until)
    member = _lock_member(gym=gym, member_id=member_id)
    trainer = _lock_trainer(gym=gym, actor=actor, trainer_staff_profile_id=trainer_staff_profile_id)
    if _ranges_overlap(
        gym=gym,
        trainer=trainer,
        member=member,
        effective_from=effective_from,
        effective_until=effective_until,
    ):
        raise AssignmentOverlap()
    row = TrainerAssignment(
        gym=gym,
        trainer_staff_profile=trainer,
        member=member,
        effective_from=effective_from,
        effective_until=effective_until,
        status=TrainerAssignment.Status.ACTIVE,
        ended_at=None,
        reason=reason,
        created_by_membership_id=actor.id,
    )
    try:
        row.save()
    except IntegrityError as exc:
        if _exclusion_violation(exc):
            raise AssignmentOverlap() from exc
        raise
    _write_audit(
        gym=gym,
        actor=actor,
        action="TRAINER_ASSIGNMENT_CREATED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def end_assignment(
    *,
    gym: Gym,
    actor: GymMembership,
    assignment_id: UUID,
    effective_until: date,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> TrainerAssignment:
    set_gym_context(gym_id=gym.id)
    row = _lock_visible_assignment(gym=gym, actor=actor, assignment_id=assignment_id)
    _require_ordered_dates(effective_from=row.effective_from, effective_until=effective_until)
    previous = _audit_values(row)
    row.status = TrainerAssignment.Status.ENDED
    row.effective_until = effective_until
    row.ended_at = timezone.now()
    row.reason = reason
    _cache_assignment_details(row)
    row.save(update_fields=["status", "effective_until", "ended_at", "reason", "updated_at"])
    _write_audit(
        gym=gym,
        actor=actor,
        action="TRAINER_ASSIGNMENT_ENDED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


@transaction.atomic
def cancel_assignment(
    *,
    gym: Gym,
    actor: GymMembership,
    assignment_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> TrainerAssignment:
    set_gym_context(gym_id=gym.id)
    row = _lock_visible_assignment(gym=gym, actor=actor, assignment_id=assignment_id)
    previous = _audit_values(row)
    _cache_assignment_details(row)
    row.status = TrainerAssignment.Status.CANCELLED
    row.ended_at = timezone.now()
    row.reason = reason
    row.save(update_fields=["status", "ended_at", "reason", "updated_at"])
    _write_audit(
        gym=gym,
        actor=actor,
        action="TRAINER_ASSIGNMENT_CANCELLED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        old_values=previous,
    )
    return row


def _cache_assignment_details(row: TrainerAssignment) -> None:
    row.trainer_staff_profile = StaffProfile.objects.get(
        gym_id=row.gym_id, id=row.trainer_staff_profile_id
    )
    row.member = Member.objects.get(gym_id=row.gym_id, id=row.member_id)


_PACKAGE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_PACKAGE_SPACE = re.compile(r"\s+")
_UNIQUE_VIOLATION = "23505"


def canonical_package_name(value: str) -> tuple[str, str]:
    """Return the stored name and its gym-unique uppercase form."""

    collapsed = _PACKAGE_SPACE.sub(" ", value.strip())
    if collapsed == "":
        raise ValidationError({"name": ["Enter a package name."]})
    return collapsed, collapsed.upper()


def _package_conflict(exc: IntegrityError) -> ServicePackageConflict | None:
    cause = exc.__cause__
    if getattr(cause, "sqlstate", None) != _UNIQUE_VIOLATION:
        return None
    constraint = str(cause)
    field = "name" if "svc_pkg_gym_name_uniq" in constraint else "code"
    return ServicePackageConflict(field)


@transaction.atomic
def create_service_package(
    *,
    gym: Gym,
    actor: GymMembership,
    package_type: str,
    code: str,
    name: str,
    session_count: int,
    validity_value: int,
    validity_unit: str,
    price: Decimal,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    class_type_id: UUID | None = None,
) -> ServicePackage:
    """Create one active package definition.

    PT rejects a class type. CLASS requires an active same-gym class type.
    """

    set_gym_context(gym_id=gym.id)
    if package_type == ServicePackage.PackageType.PT and class_type_id is not None:
        raise ValidationError({"class_type_id": ["A PT package cannot name a class type."]})
    if package_type == ServicePackage.PackageType.CLASS:
        if class_type_id is None:
            raise ValidationError({"class_type_id": ["A class package requires a class type."]})
        class_type = ClassType.objects.select_for_update().filter(gym=gym, id=class_type_id).first()
        if class_type is None:
            raise Http404
        if not class_type.is_active:
            raise ClassTypeInactive()
    elif package_type != ServicePackage.PackageType.PT:
        raise ValidationError({"type": ["Select a valid package type."]})
    if _PACKAGE_CODE.fullmatch(code) is None:
        raise ValidationError({"code": ["Enter an uppercase package code."]})
    stored_name, normalized_name = canonical_package_name(name)
    try:
        row = ServicePackage.objects.create(
            gym=gym,
            type=package_type,
            code=code,
            name=stored_name,
            normalized_name=normalized_name,
            session_count=session_count,
            validity_value=validity_value,
            validity_unit=validity_unit,
            price=price,
            currency=gym.currency,
            class_type_id=class_type_id,
            is_active=True,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        conflict = _package_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="SERVICE_PACKAGE_CREATED",
        module="trainers",
        target_type="ServicePackage",
        target_id=row.id,
        request_id=request_id,
        new_values={
            "type": row.type,
            "code": row.code,
            "name": row.name,
            "session_count": row.session_count,
            "validity_value": row.validity_value,
            "validity_unit": row.validity_unit,
            "price": f"{row.price:.2f}",
            "currency": row.currency,
            "is_active": row.is_active,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row
