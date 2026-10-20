"""Class catalog reads and trainer-scoped schedule visibility."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Q, QuerySet
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.classes.models import ClassBooking, ClassOccurrence, ClassSchedule, ClassType
from apps.tenants.models import Gym, GymMembership
from apps.trainers.selectors import gym_local_date, scope_to_assigned_members, trainer_member_scope

_CURSOR_MAX_AGE = 60 * 60 * 24
_TYPE_SALT = "classes.class_type.v1"
_SCHEDULE_SALT = "classes.class_schedule.v1"
_OCCURRENCE_SALT = "classes.class_occurrence.v1"
_BOOKING_SALT = "classes.class_booking.v1"
_CALENDAR_DAYS = 42


def class_scope_profile_ids(*, gym_id: UUID, actor: GymMembership) -> tuple[UUID, ...] | None:
    """Return None for gym-wide access, or the caller's trainer profile ids.

    A protected trainer without protected owner or manager is restricted. An
    empty tuple matches no schedule or occurrence.
    """

    scope = trainer_member_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return None
    return scope.profile_ids


def _load_cursor(
    token: str,
    *,
    salt: str,
    scope: dict[str, str],
    position: set[str],
) -> dict[str, Any]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict) or set(loaded) != set(scope) | position:
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return loaded


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    return value


def _page(
    rows: list[Any],
    *,
    limit: int,
    salt: str,
    scope: dict[str, str],
    position: Any,
) -> tuple[list[Any], dict[str, Any]]:
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        payload = {**scope, **position(page[-1])}
        next_after = signing.dumps(payload, salt=salt)
    return page, {"has_more": has_more, "next_after": next_after}


def page_class_types(
    *,
    gym_id: UUID,
    actor: GymMembership,
    is_active: bool | None,
    limit: int,
    after: str,
) -> tuple[list[ClassType], dict[str, Any]]:
    filters = Q(gym_id=gym_id)
    if is_active is not None:
        filters &= Q(is_active=is_active)
    scope = {
        "gym_id": str(gym_id),
        "actor_id": str(actor.id),
        "is_active": "" if is_active is None else str(is_active).lower(),
    }
    queryset = ClassType.objects.filter(filters).order_by("code", "id")
    if after:
        loaded = _load_cursor(after, salt=_TYPE_SALT, scope=scope, position={"code", "id"})
        code = _text(loaded["code"])
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(Q(code__gt=code) | Q(code=code, id__gt=row_id))
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_TYPE_SALT,
        scope=scope,
        position=lambda row: {"code": row.code, "id": str(row.id)},
    )


def get_class_type(*, gym_id: UUID, class_type_id: UUID) -> ClassType:
    row = ClassType.objects.filter(gym_id=gym_id, id=class_type_id).first()
    if row is None:
        raise Http404
    return row


def visible_schedules(*, gym_id: UUID, actor: GymMembership) -> QuerySet[ClassSchedule]:
    queryset = ClassSchedule.objects.filter(gym_id=gym_id)
    profile_ids = class_scope_profile_ids(gym_id=gym_id, actor=actor)
    if profile_ids is None:
        return queryset
    if not profile_ids:
        return queryset.none()
    return queryset.filter(trainer_staff_profile_id__in=profile_ids)


def page_class_schedules(
    *,
    gym_id: UUID,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    class_type_id: UUID | None,
    limit: int,
    after: str,
) -> tuple[list[ClassSchedule], dict[str, Any]]:
    profile_ids = class_scope_profile_ids(gym_id=gym_id, actor=actor)
    if (
        profile_ids is not None
        and trainer_staff_profile_id is not None
        and trainer_staff_profile_id not in profile_ids
    ):
        return [], {"has_more": False, "next_after": None}
    queryset = visible_schedules(gym_id=gym_id, actor=actor)
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if class_type_id is not None:
        queryset = queryset.filter(class_type_id=class_type_id)
    scope = {
        "gym_id": str(gym_id),
        "actor_id": str(actor.id),
        "status": status,
        "trainer_staff_profile_id": ""
        if trainer_staff_profile_id is None
        else str(trainer_staff_profile_id),
        "class_type_id": "" if class_type_id is None else str(class_type_id),
        "restricted": "" if profile_ids is None else ",".join(str(item) for item in profile_ids),
    }
    position = {"effective_from", "local_start_time", "id"}
    queryset = queryset.order_by("effective_from", "local_start_time", "id")
    if after:
        loaded = _load_cursor(after, salt=_SCHEDULE_SALT, scope=scope, position=position)
        start_date = date.fromisoformat(_text(loaded["effective_from"]))
        start_time = time.fromisoformat(_text(loaded["local_start_time"]))
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(effective_from__gt=start_date)
            | Q(effective_from=start_date, local_start_time__gt=start_time)
            | Q(effective_from=start_date, local_start_time=start_time, id__gt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_SCHEDULE_SALT,
        scope=scope,
        position=lambda row: {
            "effective_from": row.effective_from.isoformat(),
            "local_start_time": row.local_start_time.isoformat(),
            "id": str(row.id),
        },
    )


def get_visible_schedule(*, gym_id: UUID, actor: GymMembership, schedule_id: UUID) -> ClassSchedule:
    row = visible_schedules(gym_id=gym_id, actor=actor).filter(id=schedule_id).first()
    if row is None:
        raise Http404
    return row


def visible_occurrences(*, gym_id: UUID, actor: GymMembership) -> QuerySet[ClassOccurrence]:
    queryset = ClassOccurrence.objects.filter(gym_id=gym_id)
    profile_ids = class_scope_profile_ids(gym_id=gym_id, actor=actor)
    if profile_ids is None:
        return queryset
    if not profile_ids:
        return queryset.none()
    return queryset.filter(trainer_staff_profile_id__in=profile_ids)


def page_class_occurrences(
    *,
    gym_id: UUID,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    after: str,
) -> tuple[list[ClassOccurrence], dict[str, Any]]:
    profile_ids = class_scope_profile_ids(gym_id=gym_id, actor=actor)
    if (
        profile_ids is not None
        and trainer_staff_profile_id is not None
        and trainer_staff_profile_id not in profile_ids
    ):
        return [], {"has_more": False, "next_after": None}
    queryset = visible_occurrences(gym_id=gym_id, actor=actor)
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(trainer_staff_profile_id=trainer_staff_profile_id)
    if date_from is not None:
        queryset = queryset.filter(local_date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(local_date__lte=date_to)
    scope = {
        "gym_id": str(gym_id),
        "actor_id": str(actor.id),
        "status": status,
        "trainer_staff_profile_id": ""
        if trainer_staff_profile_id is None
        else str(trainer_staff_profile_id),
        "date_from": "" if date_from is None else date_from.isoformat(),
        "date_to": "" if date_to is None else date_to.isoformat(),
        "restricted": "" if profile_ids is None else ",".join(str(item) for item in profile_ids),
    }
    position = {"starts_at", "id"}
    queryset = queryset.order_by("starts_at", "id")
    if after:
        loaded = _load_cursor(after, salt=_OCCURRENCE_SALT, scope=scope, position=position)
        starts_at = datetime.fromisoformat(_text(loaded["starts_at"]))
        if starts_at.tzinfo is None:
            raise ValidationError({"after": ["Invalid cursor."]})
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(starts_at__gt=starts_at) | Q(starts_at=starts_at, id__gt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_OCCURRENCE_SALT,
        scope=scope,
        position=lambda row: {"starts_at": row.starts_at.isoformat(), "id": str(row.id)},
    )


def page_class_bookings(
    *,
    gym: Gym,
    actor: GymMembership,
    status: str,
    trainer_staff_profile_id: UUID | None,
    member_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
    view: str,
    limit: int,
    after: str,
) -> tuple[list[ClassBooking], dict[str, Any]]:
    """Page bookings the caller may see.

    A protected trainer without protected owner or manager sees bookings only
    on that caller's occurrences whose member has a current effective
    assignment. Another trainer, an expired or closed assignment, and a
    member filter outside that set return an empty page.
    """

    gym_id = gym.id
    if view == "calendar":
        if date_from is None or date_to is None or date_to < date_from:
            raise ValidationError({"date_to": ["Choose a calendar range."]})
        if (date_to - date_from).days + 1 > _CALENDAR_DAYS:
            raise ValidationError({"date_to": ["The calendar range is at most 42 days."]})
    profile_ids = class_scope_profile_ids(gym_id=gym_id, actor=actor)
    if (
        profile_ids is not None
        and trainer_staff_profile_id is not None
        and trainer_staff_profile_id not in profile_ids
    ):
        return [], {"has_more": False, "next_after": None}
    queryset = ClassBooking.objects.filter(gym_id=gym_id).select_related("class_occurrence")
    if profile_ids is not None:
        if not profile_ids:
            return [], {"has_more": False, "next_after": None}
        queryset = queryset.filter(class_occurrence__trainer_staff_profile_id__in=profile_ids)
    queryset = scope_to_assigned_members(
        queryset,
        gym_id=gym_id,
        actor=actor,
        as_of=gym_local_date(gym),
        member_ref="member_id",
    )
    if status:
        queryset = queryset.filter(status=status)
    if trainer_staff_profile_id is not None:
        queryset = queryset.filter(
            class_occurrence__trainer_staff_profile_id=trainer_staff_profile_id
        )
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if date_from is not None:
        queryset = queryset.filter(class_occurrence__local_date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(class_occurrence__local_date__lte=date_to)
    scope = {
        "gym_id": str(gym_id),
        "actor_id": str(actor.id),
        "status": status,
        "trainer_staff_profile_id": ""
        if trainer_staff_profile_id is None
        else str(trainer_staff_profile_id),
        "member_id": "" if member_id is None else str(member_id),
        "date_from": "" if date_from is None else date_from.isoformat(),
        "date_to": "" if date_to is None else date_to.isoformat(),
        "view": view,
    }
    queryset = queryset.order_by("class_occurrence__starts_at", "id")
    if after:
        loaded = _load_cursor(after, salt=_BOOKING_SALT, scope=scope, position={"starts_at", "id"})
        starts_at = datetime.fromisoformat(_text(loaded["starts_at"]))
        if starts_at.tzinfo is None:
            raise ValidationError({"after": ["Invalid cursor."]})
        row_id = _uuid(loaded["id"])
        queryset = queryset.filter(
            Q(class_occurrence__starts_at__gt=starts_at)
            | Q(class_occurrence__starts_at=starts_at, id__gt=row_id)
        )
    rows = list(queryset[: limit + 1])
    return _page(
        rows,
        limit=limit,
        salt=_BOOKING_SALT,
        scope=scope,
        position=lambda row: {
            "starts_at": row.class_occurrence.starts_at.isoformat(),
            "id": str(row.id),
        },
    )


def get_visible_occurrence(
    *, gym_id: UUID, actor: GymMembership, occurrence_id: UUID
) -> ClassOccurrence:
    row = visible_occurrences(gym_id=gym_id, actor=actor).filter(id=occurrence_id).first()
    if row is None:
        raise Http404
    return row
