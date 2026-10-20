"""Class catalog, schedule materialization, and occurrence cancellation."""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.classes.exceptions import (
    ClassTypeConflict,
    ClassTypeInactive,
    OccurrenceStateConflict,
    ScheduleConflict,
    TrainerNotEligible,
)
from apps.classes.models import ClassOccurrence, ClassSchedule, ClassType
from apps.classes.recurrence import MAX_INCLUSIVE_DAYS, Materialization, materialize_occurrences
from apps.classes.selectors import (
    class_scope_profile_ids,
    get_visible_occurrence,
    get_visible_schedule,
)
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership, Role, StaffProfile

_SPACE = re.compile(r"\s+")
_UNIQUE_VIOLATION = "23505"
_EXCLUSION_VIOLATION = "23P01"
_TRAINER_ROLE = "TRAINER"


def canonical_label(value: str, *, field: str) -> tuple[str, str]:
    collapsed = _SPACE.sub(" ", value.strip())
    if collapsed == "":
        raise ValidationError({field: ["Enter a value."]})
    return collapsed, collapsed.upper()


def _exclusion_violation(exc: IntegrityError) -> bool:
    return getattr(exc.__cause__, "sqlstate", None) == _EXCLUSION_VIOLATION


def _unique_violation(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    if getattr(cause, "sqlstate", None) != _UNIQUE_VIOLATION:
        return None
    return str(cause)


def _guard_failure(exc: Exception) -> bool:
    message = str(exc)
    return "class_schedule_guard" in message or "class_occurrence_guard" in message


def _finish_constraints() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _db_now() -> Any:
    with connection.cursor() as cursor:
        cursor.execute("SELECT now()")
        value = cursor.fetchone()
    if value is None:
        return timezone.now()
    return value[0]


def _lock_location(*, gym_id: UUID, normalized_location: str, exclusive: bool) -> None:
    if not exclusive:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"class-location:{gym_id}:{normalized_location}"],
        )


def _lock_profiles(*, gym: Gym, profile_ids: list[UUID]) -> dict[UUID, StaffProfile]:
    locked: dict[UUID, StaffProfile] = {}
    for profile_id in sorted(set(profile_ids), key=lambda item: item.bytes):
        profile = StaffProfile.objects.select_for_update().filter(gym=gym, id=profile_id).first()
        if profile is None:
            raise Http404
        locked[profile_id] = profile
    return locked


def _lock_class_types(*, gym: Gym, class_type_ids: list[UUID]) -> dict[UUID, ClassType]:
    locked: dict[UUID, ClassType] = {}
    for class_type_id in sorted(set(class_type_ids), key=lambda item: item.bytes):
        row = ClassType.objects.select_for_update().filter(gym=gym, id=class_type_id).first()
        if row is None:
            raise Http404
        locked[class_type_id] = row
    return locked


def _require_own_trainer(*, gym: Gym, actor: GymMembership, trainer_id: UUID) -> None:
    profile_ids = class_scope_profile_ids(gym_id=gym.id, actor=actor)
    if profile_ids is None:
        return
    if trainer_id not in profile_ids:
        raise Http404


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


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError({"timezone": ["Unsupported timezone."]}) from exc


def _assert_range(*, effective_from: date, effective_until: date) -> None:
    if effective_until < effective_from:
        raise ValidationError({"effective_until": ["The end date is before the start date."]})
    if (effective_until - effective_from).days + 1 > MAX_INCLUSIVE_DAYS:
        raise ValidationError({"effective_until": ["The inclusive range is at most 366 days."]})


def _materialize(
    *,
    effective_from: date,
    effective_until: date,
    weekdays: list[int],
    local_start_time: time,
    local_end_time: time,
    zone_name: str,
) -> Materialization:
    _assert_range(effective_from=effective_from, effective_until=effective_until)
    if local_end_time <= local_start_time:
        raise ValidationError(
            {"local_end_time": ["End time must be after the start time on the same day."]}
        )
    return materialize_occurrences(
        effective_from=effective_from,
        effective_until=effective_until,
        weekdays=tuple(weekdays),
        local_start_time=local_start_time,
        local_end_time=local_end_time,
        zone=_zone(zone_name),
    )


def _insert_occurrences(
    *,
    gym: Gym,
    actor: GymMembership,
    schedule: ClassSchedule,
    plan: Materialization,
    now: Any,
    preserved_dates: set[date],
) -> None:
    """Insert future planned rows that do not already occupy a local date.

    Past instants are left untouched. A date that already has an occurrence,
    including one a later booking increment refuses to delete, is not rebuilt.
    """

    stamped = timezone.now()
    rows = [
        ClassOccurrence(
            gym=gym,
            class_schedule=schedule,
            class_type_id=schedule.class_type_id,
            trainer_staff_profile_id=schedule.trainer_staff_profile_id,
            location=schedule.location,
            normalized_location=schedule.normalized_location,
            location_is_exclusive=schedule.location_is_exclusive,
            capacity=schedule.capacity,
            starts_at=item.starts_at,
            ends_at=item.ends_at,
            local_date=item.local_date,
            status=ClassOccurrence.Status.SCHEDULED,
            created_at=stamped,
            updated_at=stamped,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
        for item in plan.occurrences
        if item.local_date not in preserved_dates and item.starts_at > now
    ]
    if rows:
        ClassOccurrence.objects.bulk_create(rows)


def _delete_regenerable(*, gym_id: UUID, schedule_id: UUID, cutoff: Any) -> None:
    """Delete future scheduled rows the booking seam still treats as unbooked.

    ``app_class_occurrence_blocks_regeneration`` keeps an occurrence that has a
    booked, attended, or no-show class booking. This delete does not rewrite
    occurrence history.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM classes_class_occurrence
            WHERE gym_id = %s
              AND class_schedule_id = %s
              AND status = 'SCHEDULED'
              AND starts_at > %s
              AND NOT app_class_occurrence_blocks_regeneration(gym_id, id)
            """,
            [str(gym_id), str(schedule_id), cutoff],
        )


@transaction.atomic
def create_class_type(
    *,
    gym: Gym,
    actor: GymMembership,
    code: str,
    name: str,
    description: str,
    default_duration_minutes: int,
    default_capacity: int,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassType:
    set_gym_context(gym_id=gym.id)
    stored_name, normalized_name = canonical_label(name, field="name")
    description = _SPACE.sub(" ", description.strip())
    try:
        row = ClassType.objects.create(
            gym=gym,
            code=code,
            name=stored_name,
            normalized_name=normalized_name,
            description=description,
            default_duration_minutes=default_duration_minutes,
            default_capacity=default_capacity,
            is_active=True,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        constraint = _unique_violation(exc)
        if constraint is not None and "class_type_gym_code_uniq" in constraint:
            raise ClassTypeConflict("code") from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CLASS_TYPE_CREATED",
        module="classes",
        target_type="ClassType",
        target_id=row.id,
        request_id=request_id,
        new_values={"code": row.code, "name": row.name, "is_active": row.is_active},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def update_class_type(
    *,
    gym: Gym,
    actor: GymMembership,
    class_type_id: UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassType:
    set_gym_context(gym_id=gym.id)
    row = ClassType.objects.select_for_update().filter(gym=gym, id=class_type_id).first()
    if row is None:
        raise Http404
    if "name" in changes:
        stored_name, normalized_name = canonical_label(str(changes["name"]), field="name")
        row.name = stored_name
        row.normalized_name = normalized_name
    if "description" in changes:
        row.description = _SPACE.sub(" ", str(changes["description"]).strip())
    if "default_duration_minutes" in changes:
        row.default_duration_minutes = int(changes["default_duration_minutes"])
    if "default_capacity" in changes:
        row.default_capacity = int(changes["default_capacity"])
    if "is_active" in changes:
        row.is_active = bool(changes["is_active"])
    row.updated_by_membership_id = actor.id
    row.save()
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CLASS_TYPE_UPDATED",
        module="classes",
        target_type="ClassType",
        target_id=row.id,
        request_id=request_id,
        new_values={"code": row.code, "name": row.name, "is_active": row.is_active},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def create_class_schedule(
    *,
    gym: Gym,
    actor: GymMembership,
    class_type_id: UUID,
    trainer_staff_profile_id: UUID,
    weekdays: list[int],
    local_start_time: time,
    local_end_time: time,
    effective_from: date,
    effective_until: date,
    location: str,
    location_is_exclusive: bool,
    capacity: int,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassSchedule:
    set_gym_context(gym_id=gym.id)
    _require_own_trainer(gym=gym, actor=actor, trainer_id=trainer_staff_profile_id)
    stored_location, normalized_location = canonical_label(location, field="location")
    profiles = _lock_profiles(gym=gym, profile_ids=[trainer_staff_profile_id])
    _lock_location(
        gym_id=gym.id,
        normalized_location=normalized_location,
        exclusive=location_is_exclusive,
    )
    class_types = _lock_class_types(gym=gym, class_type_ids=[class_type_id])
    class_type = class_types[class_type_id]
    if not class_type.is_active:
        raise ClassTypeInactive()
    trainer = profiles[trainer_staff_profile_id]
    if not _is_active_trainer(trainer):
        raise TrainerNotEligible()
    gym_row = Gym.objects.select_for_update().get(id=gym.id)
    plan = _materialize(
        effective_from=effective_from,
        effective_until=effective_until,
        weekdays=weekdays,
        local_start_time=local_start_time,
        local_end_time=local_end_time,
        zone_name=gym_row.timezone,
    )
    try:
        schedule = ClassSchedule.objects.create(
            gym=gym,
            class_type=class_type,
            trainer_staff_profile=trainer,
            weekdays=weekdays,
            local_start_time=local_start_time,
            local_end_time=local_end_time,
            effective_from=effective_from,
            effective_until=effective_until,
            timezone=gym_row.timezone,
            location=stored_location,
            normalized_location=normalized_location,
            location_is_exclusive=location_is_exclusive,
            capacity=capacity,
            status=ClassSchedule.Status.ACTIVE,
            skipped_local_dates=list(plan.skipped_local_dates),
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
        now = timezone.now()
        rows = [
            ClassOccurrence(
                gym=gym,
                class_schedule=schedule,
                class_type=class_type,
                trainer_staff_profile=trainer,
                location=stored_location,
                normalized_location=normalized_location,
                location_is_exclusive=location_is_exclusive,
                capacity=capacity,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                local_date=item.local_date,
                status=ClassOccurrence.Status.SCHEDULED,
                created_at=now,
                updated_at=now,
                created_by_membership_id=actor.id,
                updated_by_membership_id=actor.id,
            )
            for item in plan.occurrences
        ]
        if rows:
            ClassOccurrence.objects.bulk_create(rows)
        _finish_constraints()
    except IntegrityError as exc:
        if _exclusion_violation(exc):
            raise ScheduleConflict() from exc
        raise
    except Exception as exc:
        if _guard_failure(exc):
            raise ScheduleConflict() from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CLASS_SCHEDULE_CREATED",
        module="classes",
        target_type="ClassSchedule",
        target_id=schedule.id,
        request_id=request_id,
        new_values={
            "class_type_id": str(class_type.id),
            "trainer_staff_profile_id": str(trainer.id),
            "weekdays": weekdays,
            "effective_from": effective_from.isoformat(),
            "effective_until": effective_until.isoformat(),
            "timezone": schedule.timezone,
            "skipped_local_dates": [item.isoformat() for item in plan.skipped_local_dates],
            "occurrence_count": len(rows),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return schedule


@transaction.atomic
def update_class_schedule(
    *,
    gym: Gym,
    actor: GymMembership,
    schedule_id: UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassSchedule:
    set_gym_context(gym_id=gym.id)
    current = get_visible_schedule(gym_id=gym.id, actor=actor, schedule_id=schedule_id)
    next_trainer_id = changes.get("trainer_staff_profile_id", current.trainer_staff_profile_id)
    _require_own_trainer(gym=gym, actor=actor, trainer_id=next_trainer_id)
    next_class_type_id = changes.get("class_type_id", current.class_type_id)
    next_location = str(changes.get("location", current.location))
    stored_location, normalized_location = canonical_label(next_location, field="location")
    next_exclusive = bool(changes.get("location_is_exclusive", current.location_is_exclusive))
    profiles = _lock_profiles(
        gym=gym,
        profile_ids=[current.trainer_staff_profile_id, next_trainer_id],
    )
    for key in sorted({current.normalized_location, normalized_location}):
        exclusive = next_exclusive if key == normalized_location else current.location_is_exclusive
        if key == current.normalized_location and key == normalized_location:
            exclusive = current.location_is_exclusive or next_exclusive
        _lock_location(gym_id=gym.id, normalized_location=key, exclusive=exclusive)
    class_types = _lock_class_types(
        gym=gym, class_type_ids=[current.class_type_id, next_class_type_id]
    )
    schedule = ClassSchedule.objects.select_for_update().filter(gym=gym, id=schedule_id).first()
    if schedule is None or schedule.status != ClassSchedule.Status.ACTIVE:
        raise Http404
    list(
        ClassOccurrence.objects.select_for_update()
        .filter(
            gym=gym,
            class_schedule=schedule,
            status=ClassOccurrence.Status.SCHEDULED,
            starts_at__gt=_db_now(),
        )
        .order_by("id")
    )
    if (
        next_class_type_id != schedule.class_type_id
        and not class_types[next_class_type_id].is_active
    ):
        raise ClassTypeInactive()
    trainer = profiles[next_trainer_id]
    if not _is_active_trainer(trainer):
        raise TrainerNotEligible()
    weekdays = list(changes.get("weekdays", schedule.weekdays))
    local_start = changes.get("local_start_time", schedule.local_start_time)
    local_end = changes.get("local_end_time", schedule.local_end_time)
    effective_from = changes.get("effective_from", schedule.effective_from)
    effective_until = changes.get("effective_until", schedule.effective_until)
    capacity = int(changes.get("capacity", schedule.capacity))
    plan = _materialize(
        effective_from=effective_from,
        effective_until=effective_until,
        weekdays=weekdays,
        local_start_time=local_start,
        local_end_time=local_end,
        zone_name=schedule.timezone,
    )
    now = _db_now()
    try:
        _delete_regenerable(gym_id=gym.id, schedule_id=schedule.id, cutoff=now)
        preserved = set(
            ClassOccurrence.objects.filter(gym=gym, class_schedule=schedule).values_list(
                "local_date", flat=True
            )
        )
        schedule.class_type_id = next_class_type_id
        schedule.trainer_staff_profile_id = next_trainer_id
        schedule.weekdays = weekdays
        schedule.local_start_time = local_start
        schedule.local_end_time = local_end
        schedule.effective_from = effective_from
        schedule.effective_until = effective_until
        schedule.location = stored_location
        schedule.normalized_location = normalized_location
        schedule.location_is_exclusive = next_exclusive
        schedule.capacity = capacity
        schedule.skipped_local_dates = [
            item for item in plan.skipped_local_dates if item not in preserved
        ]
        schedule.updated_by_membership_id = actor.id
        schedule.save()
        _insert_occurrences(
            gym=gym,
            actor=actor,
            schedule=schedule,
            plan=plan,
            now=now,
            preserved_dates=preserved,
        )
        _finish_constraints()
    except IntegrityError as exc:
        if _exclusion_violation(exc):
            raise ScheduleConflict() from exc
        raise
    except Exception as exc:
        if _guard_failure(exc):
            raise ScheduleConflict() from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CLASS_SCHEDULE_UPDATED",
        module="classes",
        target_type="ClassSchedule",
        target_id=schedule.id,
        request_id=request_id,
        new_values={
            "class_type_id": str(schedule.class_type_id),
            "trainer_staff_profile_id": str(schedule.trainer_staff_profile_id),
            "weekdays": list(schedule.weekdays),
            "timezone": schedule.timezone,
            "skipped_local_dates": [item.isoformat() for item in schedule.skipped_local_dates],
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return schedule


@transaction.atomic
def cancel_class_occurrence(
    *,
    gym: Gym,
    actor: GymMembership,
    occurrence_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ClassOccurrence:
    set_gym_context(gym_id=gym.id)
    visible = get_visible_occurrence(gym_id=gym.id, actor=actor, occurrence_id=occurrence_id)
    _require_own_trainer(gym=gym, actor=actor, trainer_id=visible.trainer_staff_profile_id)
    stored_reason, _normalized = canonical_label(reason, field="reason")
    _lock_profiles(gym=gym, profile_ids=[visible.trainer_staff_profile_id])
    _lock_location(
        gym_id=gym.id,
        normalized_location=visible.normalized_location,
        exclusive=visible.location_is_exclusive,
    )
    row = ClassOccurrence.objects.select_for_update().filter(gym=gym, id=occurrence_id).first()
    if row is None:
        raise Http404
    if row.status != ClassOccurrence.Status.SCHEDULED:
        raise OccurrenceStateConflict()
    row.status = ClassOccurrence.Status.CANCELLED
    row.reason = stored_reason
    row.cancelled_at = timezone.now()
    row.cancelled_by_membership_id = actor.id
    row.updated_by_membership_id = actor.id
    try:
        row.save()
        _finish_constraints()
    except Exception as exc:
        if _guard_failure(exc) or "attended" in str(exc).lower() or "no-show" in str(exc).lower():
            raise OccurrenceStateConflict() from exc
        raise
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action="CLASS_OCCURRENCE_CANCELLED",
        module="classes",
        target_type="ClassOccurrence",
        target_id=row.id,
        request_id=request_id,
        new_values={"status": row.status, "reason": row.reason},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row
