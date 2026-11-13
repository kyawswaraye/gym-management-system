"""Read-only class occurrence and booking tools.

These calls use the class list selectors and the trainer visibility rules. They
do not create, book, or cancel anything. Class type is not a filter because the
occurrence list does not support it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core import signing
from django.db.models import Count, Q, QuerySet
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.classes.models import ClassBooking, ClassOccurrence, ClassSchedule, ClassType
from apps.classes.selectors import (
    get_visible_occurrence,
    page_class_bookings,
    page_class_occurrences,
    page_class_schedules,
    page_class_types,
)
from apps.tenants.models import Gym
from apps.trainers.selectors import gym_local_date, scope_to_assigned_members
from apps.users.serializers import StrictSerializer

SEARCH_CLASS_OCCURRENCES = "search_class_occurrences"
SEARCH_BOOKINGS = "search_bookings"
SEARCH_CLASS_TYPES = "search_class_types"
SEARCH_CLASS_SCHEDULES = "search_class_schedules"
CLASS_READ_TOOL_NAMES = (
    SEARCH_CLASS_OCCURRENCES,
    SEARCH_BOOKINGS,
    SEARCH_CLASS_TYPES,
    SEARCH_CLASS_SCHEDULES,
)
_BOOKING_CURSOR_SALT = "assistant.class_booking.occurrence.v1"


class ClassOccurrenceSearchSerializer(StrictSerializer):
    occurrence_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    status = serializers.ChoiceField(choices=ClassOccurrence.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError(
                {"date_to": ["The end date is before the start date."]}
            )
        return attrs


class BookingSearchSerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False)
    occurrence_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    status = serializers.ChoiceField(choices=ClassBooking.Status.choices, required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError(
                {"date_to": ["The end date is before the start date."]}
            )
        return attrs


def class_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def class_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered class read. The payload does not choose the gym."""

    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_class_occurrences(call: ReadCall) -> dict[str, object]:
    """Return class occurrences on gym-local dates."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    zone = _zone(call.gym)
    query = _query(ClassOccurrenceSearchSerializer, call.payload)
    occurrence_id = query.get("occurrence_id")
    if isinstance(occurrence_id, UUID):
        row = get_visible_occurrence(
            gym_id=call.gym.id, actor=call.actor, occurrence_id=occurrence_id
        )
        if not _occurrence_matches(row, query):
            raise Http404
        detailed = _detailed_occurrences(call.gym, [row])
        return {
            "results": [_occurrence_payload(detailed[0], zone)],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_class_occurrences(
        gym_id=call.gym.id,
        actor=call.actor,
        status=str(query.get("status", "")),
        trainer_staff_profile_id=_uuid_or_none(query.get("trainer_staff_profile_id")),
        date_from=_date_or_none(query.get("date_from")),
        date_to=_date_or_none(query.get("date_to")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [
            _occurrence_payload(row, zone) for row in _detailed_occurrences(call.gym, rows)
        ],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_bookings(call: ReadCall) -> dict[str, object]:
    """Return class bookings visible to this staff member."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    zone = _zone(call.gym)
    query = _query(BookingSearchSerializer, call.payload)
    occurrence_id = query.get("occurrence_id")
    if isinstance(occurrence_id, UUID):
        return _bookings_for_occurrence(call, query, zone, occurrence_id)
    rows, meta = page_class_bookings(
        gym=call.gym,
        actor=call.actor,
        status=str(query.get("status", "")),
        trainer_staff_profile_id=None,
        member_id=_uuid_or_none(query.get("member_id")),
        date_from=_date_or_none(query.get("date_from")),
        date_to=_date_or_none(query.get("date_to")),
        view="list",
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    results = [_booking_payload(row, zone) for row in _detailed_bookings(call.gym, rows)]
    return {
        "results": results,
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
        "ambiguous": _ambiguous(results),
    }


def _bookings_for_occurrence(
    call: ReadCall,
    query: Mapping[str, Any],
    zone: ZoneInfo,
    occurrence_id: UUID,
) -> dict[str, object]:
    occurrence = get_visible_occurrence(
        gym_id=call.gym.id, actor=call.actor, occurrence_id=occurrence_id
    )
    if not _date_matches(occurrence, query):
        raise Http404
    status = str(query.get("status", ""))
    member_id = _uuid_or_none(query.get("member_id"))
    scope = [
        str(call.gym.id),
        str(call.actor.id),
        str(occurrence.id),
        status,
        "" if member_id is None else str(member_id),
    ]
    after_id = _cursor_id(str(query.get("after") or ""), scope)
    queryset = _visible_occurrence_bookings(
        call, occurrence_id=occurrence.id, status=status, member_id=member_id
    )
    if after_id is not None:
        queryset = queryset.filter(id__gt=after_id)
    limit = int(query["limit"])
    rows = list(queryset[: limit + 1])
    page = rows[:limit]
    cursor = None
    if len(rows) > limit and page:
        cursor = signing.dumps({"scope": scope, "id": str(page[-1].id)}, salt=_BOOKING_CURSOR_SALT)
    results = [_booking_payload(row, zone) for row in _detailed_bookings(call.gym, page)]
    return {
        "results": results,
        "has_more": len(rows) > limit,
        "next_after": cursor,
        "ambiguous": _ambiguous(results),
    }


def _visible_occurrence_bookings(
    call: ReadCall,
    *,
    occurrence_id: UUID,
    status: str,
    member_id: UUID | None,
) -> QuerySet[ClassBooking]:
    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    queryset = ClassBooking.objects.filter(gym_id=call.gym.id, class_occurrence_id=occurrence_id)
    queryset = scope_to_assigned_members(
        queryset,
        gym_id=call.gym.id,
        actor=call.actor,
        as_of=gym_local_date(call.gym),
        member_ref="member_id",
    )
    if status:
        queryset = queryset.filter(status=status)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    return queryset.order_by("id")


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="classes",
        operation="read",
        permissions=("classes.view",),
        features=("pt",),
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        finance_closed=False,
    )


def _query(
    serializer_type: type[serializers.Serializer[Any]], payload: Mapping[str, object]
) -> dict[str, Any]:
    serializer = serializer_type(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


def _zone(gym: Gym) -> ZoneInfo:
    try:
        return ZoneInfo(gym.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ExecutionRejected("The gym timezone is not available.", code="timezone") from exc


def _clock(instant: datetime, zone: ZoneInfo) -> str:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ExecutionRejected("The class time is not stored.", code="time")
    return instant.astimezone(zone).strftime("%H:%M")


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionRejected("The class time is not stored.", code="time")
    return value.isoformat()


def _date_or_none(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _uuid_or_none(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _occurrence_matches(row: ClassOccurrence, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    trainer_id = query.get("trainer_staff_profile_id")
    if isinstance(status, str) and row.status != status:
        return False
    if isinstance(trainer_id, UUID) and row.trainer_staff_profile_id != trainer_id:
        return False
    return _date_matches(row, query)


def _date_matches(row: ClassOccurrence, query: Mapping[str, Any]) -> bool:
    date_from = query.get("date_from")
    date_to = query.get("date_to")
    if isinstance(date_from, date) and row.local_date < date_from:
        return False
    return not (isinstance(date_to, date) and row.local_date > date_to)


def _detailed_occurrences(gym: Gym, rows: list[ClassOccurrence]) -> Sequence[ClassOccurrence]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(
        ClassOccurrence.objects.filter(gym_id=gym.id, id__in=order)
        .select_related("class_type")
        .annotate(
            booked_count=Count(
                "bookings",
                filter=~Q(bookings__status=ClassBooking.Status.CANCELLED),
            )
        )
    )
    if len(detailed) != len(order):
        raise ExecutionRejected("The class occurrence could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _detailed_bookings(gym: Gym, rows: list[ClassBooking]) -> list[ClassBooking]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(
        ClassBooking.objects.filter(gym_id=gym.id, id__in=order).select_related(
            "member", "class_occurrence", "class_occurrence__class_type"
        )
    )
    if len(detailed) != len(order):
        raise ExecutionRejected("The class booking could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _booked_count(row: ClassOccurrence) -> int:
    count = getattr(row, "booked_count", None)
    if isinstance(count, bool) or not isinstance(count, int):
        raise ExecutionRejected("The booked count is not stored.", code="read_result")
    return count


def _occurrence_payload(row: ClassOccurrence, zone: ZoneInfo) -> dict[str, object]:
    return {
        "id": str(row.id),
        "class_type_id": str(row.class_type_id),
        "class_name": row.class_type.name,
        "local_date": row.local_date.isoformat(),
        "start_time": _clock(row.starts_at, zone),
        "end_time": _clock(row.ends_at, zone),
        "capacity": row.capacity,
        "booked_count": _booked_count(row),
        "status": row.status,
    }


def _member_name(member: Any) -> str:
    return " ".join(
        part for part in (str(member.first_name).strip(), str(member.last_name).strip()) if part
    )


def _booking_payload(row: ClassBooking, zone: ZoneInfo) -> dict[str, object]:
    occurrence = row.class_occurrence
    member = row.member
    return {
        "id": str(row.id),
        "class_occurrence_id": str(row.class_occurrence_id),
        "member_id": str(row.member_id),
        "member_number": member.member_number,
        "member_name": _member_name(member),
        "class_name": occurrence.class_type.name,
        "local_date": occurrence.local_date.isoformat(),
        "start_time": _clock(occurrence.starts_at, zone),
        "end_time": _clock(occurrence.ends_at, zone),
        "status": row.status,
        "created_at": _instant(row.created_at),
        "attended_at": _instant(row.attended_at),
        "no_show_at": _instant(row.no_show_at),
        "cancelled_at": _instant(row.cancelled_at),
    }


def _ambiguous(results: list[dict[str, object]]) -> bool:
    seen: dict[str, set[str]] = {}
    for row in results:
        seen.setdefault(str(row["member_name"]).casefold(), set()).add(str(row["member_id"]))
    return any(len(member_ids) > 1 for member_ids in seen.values())


def _cursor_id(token: str, scope: list[str]) -> UUID | None:
    if token == "":
        return None
    try:
        loaded = signing.loads(token, salt=_BOOKING_CURSOR_SALT)
    except signing.BadSignature as exc:
        raise ValidationError({"after": ["The cursor is not valid."]}) from exc
    if not isinstance(loaded, dict) or loaded.get("scope") != scope:
        raise ValidationError({"after": ["The cursor is not valid."]})
    try:
        return UUID(str(loaded.get("id")))
    except ValueError as exc:
        raise ValidationError({"after": ["The cursor is not valid."]}) from exc


def search_class_types(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _type_query(call.payload)
    active = query.get("is_active")
    rows, meta = page_class_types(
        gym_id=call.gym.id,
        actor=call.actor,
        is_active=active if isinstance(active, bool) else None,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_type_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_class_schedules(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _schedule_query(call.payload)
    rows, meta = page_class_schedules(
        gym_id=call.gym.id,
        actor=call.actor,
        status=str(query.get("status") or ""),
        trainer_staff_profile_id=None,
        class_type_id=None,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_schedule_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


class ClassTypeReadQuerySerializer(StrictSerializer):
    is_active = serializers.BooleanField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class ClassScheduleReadQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=ClassSchedule.Status.choices, required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def _type_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = ClassTypeReadQuerySerializer(
        data={key: value for key, value in payload.items() if key != "gym_id"}
    )
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _schedule_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = ClassScheduleReadQuerySerializer(
        data={key: value for key, value in payload.items() if key != "gym_id"}
    )
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _type_row(row: ClassType) -> dict[str, object]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "duration_minutes": row.default_duration_minutes,
        "capacity": row.default_capacity,
        "status": "ACTIVE" if row.is_active else "INACTIVE",
    }


def _schedule_row(row: ClassSchedule) -> dict[str, object]:
    return {
        "id": str(row.id),
        "class_type": row.class_type.name,
        "trainer_name": row.trainer_staff_profile.display_name,
        "weekdays": ",".join(str(day) for day in row.weekdays),
        "start_time": row.local_start_time.isoformat(timespec="minutes"),
        "end_time": row.local_end_time.isoformat(timespec="minutes"),
        "effective_from": row.effective_from.isoformat(),
        "effective_until": row.effective_until.isoformat(),
        "status": row.status,
        "capacity": row.capacity,
        "location": row.location,
    }


_READS = {
    SEARCH_CLASS_OCCURRENCES: _contract(
        _definition(
            SEARCH_CLASS_OCCURRENCES,
            "Search class occurrences in this gym. Leave the dates empty for a general list. "
            "Set both dates to the gym-local day only when the staff asks about "
            "today or a stated date.",
        ),
        (
            FieldRequirement(
                "occurrence_id",
                "Exact class occurrence id. Optional.",
                required=False,
            ),
            FieldRequirement("date_from", "First gym-local date. Optional.", required=False),
            FieldRequirement("date_to", "Last gym-local date. Optional.", required=False),
            FieldRequirement(
                "status",
                "SCHEDULED, CANCELLED, or COMPLETED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "trainer_staff_profile_id",
                "Staff profile id of the class trainer. Optional.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
        ),
    ),
    SEARCH_BOOKINGS: _contract(
        _definition(
            SEARCH_BOOKINGS,
            "Search class bookings in this gym. Leave the dates empty for a general list. "
            "Set both dates only when the staff asks about today or a stated date.",
        ),
        (
            FieldRequirement(
                "member_id",
                "Member id returned by search_members. Optional.",
                required=False,
            ),
            FieldRequirement("occurrence_id", "Class occurrence id. Optional.", required=False),
            FieldRequirement("date_from", "First gym-local class date. Optional.", required=False),
            FieldRequirement("date_to", "Last gym-local class date. Optional.", required=False),
            FieldRequirement(
                "status",
                "BOOKED, ATTENDED, NO_SHOW, or CANCELLED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
        ),
    ),
    SEARCH_CLASS_TYPES: _contract(
        _definition(
            SEARCH_CLASS_TYPES,
            "Search class types in this gym. A class type is the catalog name, duration, "
            "and capacity. It is not a scheduled occurrence.",
        ),
        (
            FieldRequirement("is_active", "True for active types. Optional.", required=False),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
        ),
    ),
    SEARCH_CLASS_SCHEDULES: _contract(
        _definition(
            SEARCH_CLASS_SCHEDULES,
            "Search class schedule definitions in this gym. A schedule repeats on weekdays. "
            "It is not one class occurrence.",
        ),
        (
            FieldRequirement(
                "status",
                "ACTIVE, ENDED, or CANCELLED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
        ),
    ),
}
_READERS = {
    SEARCH_CLASS_OCCURRENCES: search_class_occurrences,
    SEARCH_BOOKINGS: search_bookings,
    SEARCH_CLASS_TYPES: search_class_types,
    SEARCH_CLASS_SCHEDULES: search_class_schedules,
}
