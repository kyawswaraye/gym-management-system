"""Personal-training booking commands.

Lock order is gym settings when a later class command shares the member, then
the trainer profile, the member, the metered package, and the booking row.
Attendance writes the ledger after the booking update and then refreshes the
package status. Cancellation writes no ledger row.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from apps.audit.services import write_tenant_audit
from apps.classes.exceptions import BookingStateConflict, ScheduleConflict
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership, Role, StaffProfile
from apps.trainers.exceptions import (
    AssignmentNotEffective,
    LedgerBounds,
    MemberNotActive,
    PackageNotEligible,
    TrainerNotEligible,
)
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    ServiceSessionLedger,
    TrainerAssignment,
)
from apps.trainers.package_services import _assert_adjuster, _remaining, _store_status
from apps.trainers.selectors import assignment_scope, gym_local_date

_EXCLUSION = "23P01"
_UNIQUE = "23505"


def create_pt_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    trainer_staff_profile_id: UUID,
    member_id: UUID,
    starts_at: Any,
    ends_at: Any,
    member_service_package_id: UUID | None,
    unmetered: bool,
    unmetered_reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTBooking:
    if starts_at >= ends_at:
        raise ScheduleConflict()
    _assert_meter(gym=gym, actor=actor, unmetered=unmetered, reason=unmetered_reason)
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if scope.restricted and scope.trainer_staff_profile_id != trainer_staff_profile_id:
        raise Http404
    try:
        with transaction.atomic():
            trainer = _lock_trainer(gym_id=gym.id, profile_id=trainer_staff_profile_id)
            member = _lock_member(gym_id=gym.id, member_id=member_id)
            _assert_visible_member(
                gym=gym, actor=actor, member_id=member.id, restricted=scope.restricted
            )
            local_day = starts_at.astimezone(ZoneInfo(gym.timezone)).date()
            _assert_assignment(
                gym_id=gym.id,
                trainer_id=trainer.id,
                member_id=member.id,
                local_day=local_day,
            )
            package = _lock_pt_package(
                gym=gym,
                member_id=member.id,
                package_id=member_service_package_id,
                unmetered=unmetered,
                local_day=local_day,
            )
            booking = PTBooking(
                gym=gym,
                trainer_staff_profile=trainer,
                member=member,
                member_service_package=package,
                starts_at=starts_at,
                ends_at=ends_at,
                unmetered=unmetered,
                unmetered_reason=unmetered_reason if unmetered else "",
                status=PTBooking.Status.BOOKED,
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
            )
            booking.save()
            _audit(
                gym=gym,
                actor=actor,
                action="PT_BOOKING_CREATED",
                booking=booking,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
    except (IntegrityError, DatabaseError) as exc:
        _raise_db(exc)
    return booking


def attend_pt_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTBooking:
    return _terminal(
        gym=gym,
        actor=actor,
        booking_id=booking_id,
        status=PTBooking.Status.ATTENDED,
        reason=ServiceSessionLedger.Reason.ATTENDED,
        action="PT_BOOKING_ATTENDED",
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def no_show_pt_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTBooking:
    return _terminal(
        gym=gym,
        actor=actor,
        booking_id=booking_id,
        status=PTBooking.Status.NO_SHOW,
        reason=ServiceSessionLedger.Reason.POLICY_NO_SHOW,
        action="PT_BOOKING_NO_SHOW",
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def cancel_pt_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTBooking:
    booking = _visible_booking(gym=gym, actor=actor, booking_id=booking_id)
    try:
        with transaction.atomic():
            row = PTBooking.objects.select_for_update().get(gym_id=gym.id, id=booking.id)
            if row.status != PTBooking.Status.BOOKED:
                raise BookingStateConflict()
            now = timezone.now()
            row.status = PTBooking.Status.CANCELLED
            row.cancelled_at = now
            row.cancelled_by_membership_id = actor.id
            row.reason = reason
            row.updated_by_membership_id = actor.id
            row.save()
            _audit(
                gym=gym,
                actor=actor,
                action="PT_BOOKING_CANCELLED",
                booking=row,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
    except (IntegrityError, DatabaseError) as exc:
        _raise_db(exc)
    return row


def _terminal(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    status: str,
    reason: str,
    action: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> PTBooking:
    booking = _visible_booking(gym=gym, actor=actor, booking_id=booking_id)
    try:
        with transaction.atomic():
            package = None
            if booking.member_service_package_id is not None:
                package = (
                    MemberServicePackage.objects.select_for_update()
                    .filter(gym_id=gym.id, id=booking.member_service_package_id)
                    .first()
                )
            row = PTBooking.objects.select_for_update().get(gym_id=gym.id, id=booking.id)
            if row.status != PTBooking.Status.BOOKED:
                raise BookingStateConflict()
            now = timezone.now()
            if status == PTBooking.Status.ATTENDED and now < row.starts_at:
                raise BookingStateConflict()
            if status == PTBooking.Status.NO_SHOW and now < row.ends_at:
                raise BookingStateConflict()
            row.status = status
            row.updated_by_membership_id = actor.id
            if status == PTBooking.Status.ATTENDED:
                row.attended_at = now
                row.attended_by_membership_id = actor.id
            else:
                row.no_show_at = now
                row.no_show_by_membership_id = actor.id
            row.save()
            if not row.unmetered:
                if package is None:
                    raise PackageNotEligible()
                _consume(gym=gym, actor=actor, package=package, booking=row, reason=reason)
            _audit(
                gym=gym,
                actor=actor,
                action=action,
                booking=row,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
    except (IntegrityError, DatabaseError) as exc:
        _raise_db(exc)
    return row


def _consume(
    *,
    gym: Gym,
    actor: GymMembership,
    package: MemberServicePackage,
    booking: PTBooking,
    reason: str,
) -> None:
    remaining = _remaining(package)
    if remaining <= 0:
        raise LedgerBounds()
    ServiceSessionLedger.objects.create(
        gym=gym,
        member_service_package=package,
        session_delta=-1,
        reason=reason,
        pt_booking=booking,
        occurred_at=timezone.now(),
        created_by_membership_id=actor.id,
    )
    _store_status(package, actor=actor, remaining=remaining - 1)


def _visible_booking(*, gym: Gym, actor: GymMembership, booking_id: UUID) -> PTBooking:
    booking = (
        PTBooking.objects.filter(gym_id=gym.id, id=booking_id)
        .select_related("trainer_staff_profile", "member")
        .first()
    )
    if booking is None:
        raise Http404
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if scope.restricted and scope.trainer_staff_profile_id != booking.trainer_staff_profile_id:
        raise Http404
    _assert_visible_member(
        gym=gym,
        actor=actor,
        member_id=booking.member_id,
        restricted=scope.restricted,
    )
    return booking


def _assert_visible_member(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    restricted: bool,
) -> None:
    if not restricted:
        return
    scope = assignment_scope(gym_id=gym.id, actor=actor)
    if scope.trainer_staff_profile_id is None:
        raise Http404
    local_day = gym_local_date(gym)
    if not _assignment_exists(
        gym_id=gym.id,
        trainer_id=scope.trainer_staff_profile_id,
        member_id=member_id,
        local_day=local_day,
    ):
        raise Http404


def _assert_meter(*, gym: Gym, actor: GymMembership, unmetered: bool, reason: str) -> None:
    if not unmetered:
        return
    if reason.strip() == "":
        raise PackageNotEligible()
    _assert_adjuster(gym=gym, actor=actor)


def _lock_trainer(*, gym_id: UUID, profile_id: UUID) -> StaffProfile:
    profile = StaffProfile.objects.select_for_update().filter(gym_id=gym_id, id=profile_id).first()
    if profile is None:
        raise Http404
    if profile.status != StaffProfile.Status.ACTIVE or not _holds_trainer_role(profile):
        raise TrainerNotEligible()
    return profile


def _holds_trainer_role(profile: StaffProfile) -> bool:
    membership = GymMembership.objects.filter(id=profile.gym_membership_id).first()
    if (
        membership is None
        or membership.gym_id != profile.gym_id
        or membership.status != GymMembership.Status.ACTIVE
    ):
        return False
    return Role.objects.filter(
        gym_id=profile.gym_id,
        is_active=True,
        key="TRAINER",
        source_template__key="TRAINER",
        gymmembershiprole__gym_id=profile.gym_id,
        gymmembershiprole__gym_membership_id=membership.id,
    ).exists()


def _lock_member(*, gym_id: UUID, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym_id=gym_id, id=member_id).first()
    if member is None:
        raise Http404
    if member.status != Member.Status.ACTIVE:
        raise MemberNotActive()
    return member


def _assert_assignment(*, gym_id: UUID, trainer_id: UUID, member_id: UUID, local_day: Any) -> None:
    if not _assignment_exists(
        gym_id=gym_id,
        trainer_id=trainer_id,
        member_id=member_id,
        local_day=local_day,
    ):
        raise AssignmentNotEffective()


def _assignment_exists(*, gym_id: UUID, trainer_id: UUID, member_id: UUID, local_day: Any) -> bool:
    return (
        TrainerAssignment.objects.filter(
            gym_id=gym_id,
            trainer_staff_profile_id=trainer_id,
            member_id=member_id,
            status=TrainerAssignment.Status.ACTIVE,
            effective_from__lte=local_day,
        )
        .filter(
            Q(effective_until__isnull=True) | Q(effective_until__gte=local_day),
        )
        .exists()
    )


def _lock_pt_package(
    *,
    gym: Gym,
    member_id: UUID,
    package_id: UUID | None,
    unmetered: bool,
    local_day: Any,
) -> MemberServicePackage | None:
    if unmetered:
        if package_id is not None:
            raise PackageNotEligible()
        return None
    if package_id is None:
        raise PackageNotEligible()
    package = (
        MemberServicePackage.objects.select_for_update()
        .filter(gym_id=gym.id, id=package_id)
        .first()
    )
    if (
        package is None
        or package.member_id != member_id
        or package.package_type != "PT"
        or package.status != MemberServicePackage.Status.ACTIVE
        or local_day < package.valid_from
        or local_day > package.valid_until
        or _remaining(package) <= 0
    ):
        raise PackageNotEligible()
    return package


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    booking: PTBooking,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        target_type="pt_booking",
        target_id=booking.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        module="trainers",
        new_values={"status": booking.status},
    )


def _raise_db(exc: BaseException) -> None:
    code = str(getattr(getattr(exc, "__cause__", None), "pgcode", "") or "")
    message = str(exc).lower()
    if code == _EXCLUSION or "overlap" in message:
        raise ScheduleConflict() from exc
    if code == _UNIQUE:
        raise ScheduleConflict() from exc
    if (
        "closed bookings" in message
        or "immutable" in message
        or "transition" in message
        or "before the start" in message
        or "before the end" in message
    ):
        raise BookingStateConflict() from exc
    if "remaining" in message or "balance" in message or "effect is missing" in message:
        raise LedgerBounds() from exc
    if "not eligible" in message or "assignment" in message:
        raise PackageNotEligible() from exc
    raise exc
