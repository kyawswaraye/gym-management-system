"""Check-in history through the existing staff check-in list.

The session gym and actor are the only scope. A trainer sees assigned members.
The tool does not record a visit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Count
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.checkin_services import member_check_in_summary, page_check_ins
from apps.members.models import CheckIn, Member
from apps.trainers.selectors import gym_local_date, scope_to_assigned_members
from apps.users.serializers import StrictSerializer

SEARCH_CHECK_INS = "search_check_ins"
CHECKIN_READ_TOOL_NAMES = (SEARCH_CHECK_INS,)


class CheckInReadQuerySerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    absent_since = serializers.DateField(required=False)
    open_only = serializers.BooleanField(required=False, default=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def checkin_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def checkin_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
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


def search_check_ins(call: ReadCall) -> dict[str, object]:
    """Return visits, or members with no visit since a date."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    serializer = CheckInReadQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    absent = query.get("absent_since")
    if isinstance(absent, date):
        return _absent(call, absent, int(query["limit"]))
    member_id = query.get("member_id")
    rows, meta = page_check_ins(
        gym=call.gym,
        actor=call.actor,
        member_id=member_id if isinstance(member_id, UUID) else None,
        open_state="true" if query.get("open_only") else "",
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
        date_from=query.get("date_from") if isinstance(query.get("date_from"), date) else None,
        date_to=query.get("date_to") if isinstance(query.get("date_to"), date) else None,
    )
    rendered = [_row(row) for row in rows]
    if isinstance(member_id, UUID):
        _attach_member_summary(call, member_id, rendered)
    return {
        "results": rendered,
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _absent(call: ReadCall, since: date, limit: int) -> dict[str, object]:
    zone = ZoneInfo(call.gym.timezone)
    start = datetime.combine(since, datetime.min.time(), tzinfo=zone)
    visited = CheckIn.objects.filter(gym_id=call.gym.id, check_in_at__gte=start).values("member_id")
    members = scope_to_assigned_members(
        Member.objects.filter(gym=call.gym).exclude(status=Member.Status.ARCHIVED),
        gym_id=call.gym.id,
        actor=call.actor,
        as_of=gym_local_date(call.gym),
        member_ref="id",
    ).exclude(id__in=visited)
    rows = []
    for member in members.order_by("first_name", "last_name", "id")[:limit]:
        latest = (
            CheckIn.objects.filter(gym_id=call.gym.id, member_id=member.id)
            .order_by("-check_in_at", "-id")
            .values_list("check_in_at", flat=True)
            .first()
        )
        rows.append(
            {
                "member_id": str(member.id),
                "member_number": member.member_number,
                "member_name": f"{member.first_name} {member.last_name}".strip(),
                "last_check_in_at": "" if latest is None else latest.isoformat(),
                "absent_since": since.isoformat(),
            }
        )
    return {"results": rows, "has_more": False, "next_after": None}


def _attach_member_summary(call: ReadCall, member_id: UUID, rows: list[dict[str, object]]) -> None:
    member = Member.objects.filter(gym=call.gym, id=member_id).first()
    if member is None:
        return
    summary = member_check_in_summary(gym=call.gym, member=member)
    count = CheckIn.objects.filter(gym_id=call.gym.id, member_id=member_id).aggregate(
        total=Count("id")
    )
    total = int(count["total"])
    latest = summary.get("last_check_in_at")
    shown = latest.isoformat() if isinstance(latest, datetime) else ""
    if not rows:
        rows.append(
            {
                "member_id": str(member_id),
                "member_name": f"{member.first_name} {member.last_name}".strip(),
                "check_in_count": total,
                "last_check_in_at": shown,
            }
        )
        return
    for row in rows:
        row["check_in_count"] = total
        row["last_check_in_at"] = shown


def _row(row: CheckIn) -> dict[str, object]:
    return {
        "id": str(row.id),
        "member_id": str(row.member_id),
        "member_number": row.member.member_number,
        "member_name": f"{row.member.first_name} {row.member.last_name}".strip(),
        "check_in_at": row.check_in_at.isoformat(),
        "check_out_at": "" if row.check_out_at is None else row.check_out_at.isoformat(),
        "method": row.method,
    }


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="checkins",
        operation="read",
        permissions=("checkins.view",),
        features=("members",),
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
    )


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_READS = {
    SEARCH_CHECK_INS: _contract(
        _definition(
            SEARCH_CHECK_INS,
            "Search check-ins in this gym. Set date_from and date_to for a day or range. "
            "Set member_id for one member's history, count, and latest visit. "
            "Set absent_since to list members with no check-in on or after that date.",
        ),
        (
            FieldRequirement("member_id", "Member id. Optional.", required=False),
            FieldRequirement("date_from", "First gym-local day. Optional.", required=False),
            FieldRequirement("date_to", "Last gym-local day. Optional.", required=False),
            FieldRequirement(
                "absent_since",
                "List members with no check-in on or after this date. Optional.",
                required=False,
            ),
            FieldRequirement(
                "open_only",
                "True for visits that have not checked out. Optional.",
                required=False,
                kind="boolean",
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
_READERS = {SEARCH_CHECK_INS: search_check_ins}
