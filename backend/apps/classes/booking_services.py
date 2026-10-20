"""Class booking commands.

Lock order is gym settings, the member, the metered package, the occurrence,
then the booking. That order waits behind an occurrence cancel, which locks
the occurrence and then the booked rows, without taking the package lock.
Attendance writes a ledger row only when the booking is metered and, for a
no-show, the locked class no-show policy consumes a session.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import DatabaseError, IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.audit.services import write_tenant_audit
from apps.cashbook.selectors import membership_has_permission
from apps.classes.exceptions import (
    BookingStateConflict,
    ClassCutoff,
    ClassFull,
    ScheduleConflict,
)
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.classes.selectors import class_scope_profile_ids
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership, GymSettings
from apps.trainers.exceptions import LedgerBounds, MemberNotActive, PackageNotEligible
from apps.trainers.models import MemberServicePackage, ServicePackage, ServiceSessionLedger
from apps.trainers.package_services import _assert_adjuster, _remaining, _store_status
from apps.trainers.selectors import (
    gym_local_date,
    holds_protected_management_role,
    member_is_assigned,
    require_assigned_member,
    trainer_member_scope,
)

_EXCLUSION = "23P01"
_UNIQUE = "23505"


def create_class_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    class_occurrence_id: UUID,
    member_id: UUID,
    member_service_package_id: UUID | None,
    unmetered: bool,
    unmetered_reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    _assert_meter(gym=gym, actor=actor, unmetered=unmetered, reason=unmetered_reason)
    occurrence = _visible_occurrence(gym=gym, actor=actor, occurrence_id=class_occurrence_id)
    try:
        with transaction.atomic():
            _lock_settings(gym.id)
            member = _lock_member(gym_id=gym.id, member_id=member_id)
            _assert_member_scope(gym=gym, actor=actor, member_id=member.id)
            preview = ClassOccurrence.objects.filter(gym_id=gym.id, id=occurrence.id).first()
            if preview is None or preview.status != ClassOccurrence.Status.SCHEDULED:
                raise BookingStateConflict()
            package = _lock_class_package(
                gym=gym,
                member_id=member.id,
                package_id=member_service_package_id,
                unmetered=unmetered,
                class_type_id=preview.class_type_id,
                local_day=preview.local_date,
            )
            locked = (
                ClassOccurrence.objects.select_for_update()
                .filter(gym_id=gym.id, id=occurrence.id)
                .first()
            )
            if locked is None or locked.status != ClassOccurrence.Status.SCHEDULED:
                raise BookingStateConflict()
            taken = (
                ClassBooking.objects.filter(
                    gym_id=gym.id,
                    class_occurrence_id=locked.id,
                )
                .exclude(status=ClassBooking.Status.CANCELLED)
                .count()
            )
            if taken >= locked.capacity:
                raise ClassFull()
            booking = ClassBooking(
                gym=gym,
                class_occurrence=locked,
                member=member,
                member_service_package=package,
                unmetered=unmetered,
                unmetered_reason=unmetered_reason if unmetered else "",
                status=ClassBooking.Status.BOOKED,
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
            )
            booking.save()
            _audit(
                gym=gym,
                actor=actor,
                action="CLASS_BOOKING_CREATED",
                booking=booking,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
    except (IntegrityError, DatabaseError) as exc:
        _raise_db(exc)
    return booking


def create_member_class_booking(
    *,
    gym: Gym,
    member_id: UUID,
    actor_user: Any,
    class_occurrence_id: UUID,
    member_service_package_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    """Book the token member into a scheduled class.

    The member is not a manager: the package is required, unmetered stays false,
    and a past or non-scheduled occurrence is a booking-state conflict. Lock
    order matches the staff command.
    """

    occurrence = ClassOccurrence.objects.filter(gym_id=gym.id, id=class_occurrence_id).first()
    if occurrence is None:
        raise Http404
    try:
        with transaction.atomic():
            _lock_settings(gym.id)
            member = _lock_member(gym_id=gym.id, member_id=member_id)
            preview = ClassOccurrence.objects.filter(gym_id=gym.id, id=occurrence.id).first()
            if (
                preview is None
                or preview.status != ClassOccurrence.Status.SCHEDULED
                or preview.starts_at <= timezone.now()
            ):
                raise BookingStateConflict()
            package = _lock_class_package(
                gym=gym,
                member_id=member.id,
                package_id=member_service_package_id,
                unmetered=False,
                class_type_id=preview.class_type_id,
                local_day=preview.local_date,
            )
            locked = (
                ClassOccurrence.objects.select_for_update()
                .filter(gym_id=gym.id, id=occurrence.id)
                .first()
            )
            if (
                locked is None
                or locked.status != ClassOccurrence.Status.SCHEDULED
                or locked.starts_at <= timezone.now()
            ):
                raise BookingStateConflict()
            taken = (
                ClassBooking.objects.filter(
                    gym_id=gym.id,
                    class_occurrence_id=locked.id,
                )
                .exclude(status=ClassBooking.Status.CANCELLED)
                .count()
            )
            if taken >= locked.capacity:
                raise ClassFull()
            booking = ClassBooking(
                gym=gym,
                class_occurrence=locked,
                member=member,
                member_service_package=package,
                unmetered=False,
                unmetered_reason="",
                status=ClassBooking.Status.BOOKED,
                created_by_membership_id=None,
                updated_by_membership_id=None,
                created_by_member_id=member.id,
                updated_by_member_id=member.id,
            )
            booking.save()
            write_tenant_audit(
                gym=gym,
                actor_user=actor_user,
                actor_membership_id=None,
                action="CLASS_BOOKING_CREATED",
                target_type="class_booking",
                target_id=booking.id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
                module="classes",
                new_values={"status": booking.status},
            )
    except (IntegrityError, DatabaseError) as exc:
        _raise_db(exc)
    return booking


def attend_class_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    return _terminal(
        gym=gym,
        actor=actor,
        booking_id=booking_id,
        status=ClassBooking.Status.ATTENDED,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def no_show_class_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    return _terminal(
        gym=gym,
        actor=actor,
        booking_id=booking_id,
        status=ClassBooking.Status.NO_SHOW,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def cancel_class_booking(
    *,
    gym: Gym,
    actor: GymMembership,
    booking_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    booking = _visible_booking(gym=gym, actor=actor, booking_id=booking_id)
    try:
        with transaction.atomic():
            settings = _lock_settings(gym.id)
            occurrence = ClassOccurrence.objects.select_for_update().get(
                gym_id=gym.id,
                id=booking.class_occurrence_id,
            )
            row = ClassBooking.objects.select_for_update().get(gym_id=gym.id, id=booking.id)
            _assert_occurrence_scope(gym=gym, actor=actor, occurrence=occurrence)
            _assert_member_scope(gym=gym, actor=actor, member_id=row.member_id)
            if row.status != ClassBooking.Status.BOOKED:
                raise BookingStateConflict()
            late = _is_late(
                occurrence=occurrence, cutoff_minutes=settings.class_cancellation_cutoff_minutes
            )
            _assert_cancel_scope(
                gym=gym, actor=actor, booking=row, occurrence=occurrence, late=late
            )
            if late and reason.strip() == "":
                raise ClassCutoff()
            now = timezone.now()
            row.status = ClassBooking.Status.CANCELLED
            row.cancelled_at = now
            row.cancelled_by_membership_id = actor.id
            row.reason = reason
            row.updated_by_membership_id = actor.id
            row.save()
            _audit(
                gym=gym,
                actor=actor,
                action="CLASS_BOOKING_CANCELLED",
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
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassBooking:
    booking = _visible_booking(gym=gym, actor=actor, booking_id=booking_id)
    action = (
        "CLASS_BOOKING_ATTENDED"
        if status == ClassBooking.Status.ATTENDED
        else "CLASS_BOOKING_NO_SHOW"
    )
    try:
        with transaction.atomic():
            settings = _lock_settings(gym.id)
            package = None
            if booking.member_service_package_id is not None:
                package = (
                    MemberServicePackage.objects.select_for_update()
                    .filter(gym_id=gym.id, id=booking.member_service_package_id)
                    .first()
                )
            occurrence = ClassOccurrence.objects.select_for_update().get(
                gym_id=gym.id,
                id=booking.class_occurrence_id,
            )
            row = ClassBooking.objects.select_for_update().get(gym_id=gym.id, id=booking.id)
            _assert_occurrence_scope(gym=gym, actor=actor, occurrence=occurrence)
            _assert_member_scope(gym=gym, actor=actor, member_id=row.member_id)
            if row.status != ClassBooking.Status.BOOKED:
                raise BookingStateConflict()
            now = timezone.now()
            if status == ClassBooking.Status.ATTENDED and now < occurrence.starts_at:
                raise BookingStateConflict()
            if status == ClassBooking.Status.NO_SHOW and now < occurrence.ends_at:
                raise BookingStateConflict()
            row.status = status
            row.updated_by_membership_id = actor.id
            if status == ClassBooking.Status.ATTENDED:
                row.attended_at = now
                row.attended_by_membership_id = actor.id
            else:
                row.no_show_at = now
                row.no_show_by_membership_id = actor.id
            row.save()
            consume = not row.unmetered and (
                status == ClassBooking.Status.ATTENDED or settings.class_no_show_consumes_session
            )
            if consume:
                if package is None:
                    raise PackageNotEligible()
                ledger_reason = (
                    ServiceSessionLedger.Reason.ATTENDED
                    if status == ClassBooking.Status.ATTENDED
                    else ServiceSessionLedger.Reason.POLICY_NO_SHOW
                )
                _consume(
                    gym=gym,
                    actor=actor,
                    package=package,
                    booking=row,
                    reason=ledger_reason,
                )
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
    booking: ClassBooking,
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
        class_booking=booking,
        occurred_at=timezone.now(),
        created_by_membership_id=actor.id,
    )
    _store_status(package, actor=actor, remaining=remaining - 1)


def _visible_occurrence(*, gym: Gym, actor: GymMembership, occurrence_id: UUID) -> ClassOccurrence:
    occurrence = ClassOccurrence.objects.filter(gym_id=gym.id, id=occurrence_id).first()
    if occurrence is None:
        raise Http404
    _assert_occurrence_scope(gym=gym, actor=actor, occurrence=occurrence)
    return occurrence


def _visible_booking(*, gym: Gym, actor: GymMembership, booking_id: UUID) -> ClassBooking:
    booking = (
        ClassBooking.objects.filter(gym_id=gym.id, id=booking_id)
        .select_related("class_occurrence")
        .first()
    )
    if booking is None:
        raise Http404
    _assert_occurrence_scope(gym=gym, actor=actor, occurrence=booking.class_occurrence)
    _assert_member_scope(gym=gym, actor=actor, member_id=booking.member_id)
    return booking


def _assert_occurrence_scope(
    *, gym: Gym, actor: GymMembership, occurrence: ClassOccurrence
) -> None:
    profile_ids = class_scope_profile_ids(gym_id=gym.id, actor=actor)
    if profile_ids is None:
        return
    if occurrence.trainer_staff_profile_id not in profile_ids:
        raise Http404


def _assert_member_scope(*, gym: Gym, actor: GymMembership, member_id: UUID) -> None:
    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member_id,
        as_of=gym_local_date(gym),
    )


def _assert_cancel_scope(
    *,
    gym: Gym,
    actor: GymMembership,
    booking: ClassBooking,
    occurrence: ClassOccurrence,
    late: bool,
) -> None:
    _assert_occurrence_scope(gym=gym, actor=actor, occurrence=occurrence)
    profile_ids = class_scope_profile_ids(gym_id=gym.id, actor=actor)
    if profile_ids is not None and not _trainer_owns_member(
        gym=gym, actor=actor, member_id=booking.member_id
    ):
        raise Http404
    if late:
        manager = holds_protected_management_role(gym_id=gym.id, membership_id=actor.id)
        allowed = manager and membership_has_permission(membership=actor, code="classes.manage")
        if not allowed:
            raise ClassCutoff()
        return
    if profile_ids is None and membership_has_permission(membership=actor, code="classes.manage"):
        return
    if profile_ids is not None:
        return
    if booking.created_by_membership_id == actor.id:
        return
    raise PermissionDenied()


def _trainer_owns_member(*, gym: Gym, actor: GymMembership, member_id: UUID) -> bool:
    scope = trainer_member_scope(gym_id=gym.id, actor=actor)
    if not scope.restricted:
        return False
    return member_is_assigned(
        gym_id=gym.id,
        profile_ids=scope.profile_ids,
        member_id=member_id,
        as_of=gym_local_date(gym),
    )


def _is_late(*, occurrence: ClassOccurrence, cutoff_minutes: int) -> bool:
    cutoff_at = occurrence.starts_at - timedelta(minutes=cutoff_minutes)
    return timezone.now() >= cutoff_at


def _assert_meter(*, gym: Gym, actor: GymMembership, unmetered: bool, reason: str) -> None:
    if not unmetered:
        return
    if reason.strip() == "":
        raise PackageNotEligible()
    _assert_adjuster(gym=gym, actor=actor)


def _lock_settings(gym_id: UUID) -> GymSettings:
    return GymSettings.objects.select_for_update().get(gym_id=gym_id)


def _lock_member(*, gym_id: UUID, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym_id=gym_id, id=member_id).first()
    if member is None:
        raise Http404
    if member.status != Member.Status.ACTIVE:
        raise MemberNotActive()
    return member


def _lock_class_package(
    *,
    gym: Gym,
    member_id: UUID,
    package_id: UUID | None,
    unmetered: bool,
    class_type_id: UUID,
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
    definition = None
    if package is not None and package.service_package_id is not None:
        definition = ServicePackage.objects.filter(
            gym_id=gym.id,
            id=package.service_package_id,
        ).first()
    if (
        package is None
        or definition is None
        or package.member_id != member_id
        or package.package_type != "CLASS"
        or definition.type != "CLASS"
        or definition.class_type_id != class_type_id
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
    booking: ClassBooking,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        target_type="class_booking",
        target_id=booking.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        module="classes",
        new_values={"status": booking.status},
    )


def _raise_db(exc: BaseException) -> None:
    code = str(getattr(getattr(exc, "__cause__", None), "pgcode", "") or "")
    message = str(exc).lower()
    if "full" in message:
        raise ClassFull() from exc
    if code in {_EXCLUSION, _UNIQUE} or "overlap" in message:
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
    if "not eligible" in message or "not scheduled" in message:
        raise PackageNotEligible() from exc
    raise exc
