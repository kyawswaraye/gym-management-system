"""Member reads through the existing member list and trainer scope.

The session gym and actor are the only scope. These tools do not confirm,
approve, or write an execution claim. Personal contact fields stay off the
result. A name that matches more than one member is returned as a list.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.http import Http404
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.models import Member
from apps.members.serializers import member_is_pt_client
from apps.members.services import gym_today, page_members
from apps.trainers.selectors import annotate_member_pt_client, scope_to_assigned_members
from apps.users.serializers import StrictSerializer

SEARCH_MEMBERS = "search_members"
GET_MEMBER = "get_member"
MEMBER_READ_TOOL_NAMES = (GET_MEMBER, SEARCH_MEMBERS)
_MYANMAR_DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")


class MemberReadQuerySerializer(StrictSerializer):
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        trim_whitespace=True,
    )
    member_number = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=32,
        trim_whitespace=True,
    )
    status = serializers.ChoiceField(choices=Member.Status.choices, required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


def member_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def member_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered member read. The payload does not choose the gym."""

    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    reader = _READERS[tool_name]
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=reader,
    )


def search_members(call: ReadCall) -> dict[str, object]:
    """Return one page of members this actor can see in the session gym."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(call.payload)
    number = str(query.get("member_number") or "")
    queryset = _visible_members(call)
    if number:
        queryset = queryset.filter(member_number=number)
    rows, meta = page_members(
        queryset,
        gym_id=call.gym.id,
        status=str(query.get("status", "")),
        search=str(query.get("search", "")),
        joined_from=None,
        joined_to=None,
        is_pt_client=None,
        as_of=gym_today(call.gym),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
        "ambiguous": _ambiguous(rows, name_search=bool(query.get("search")) and not number),
    }


def get_member(call: ReadCall) -> dict[str, object]:
    """Return one member this actor can see in the session gym."""

    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    member_id = _member_id(call.payload.get("member_id"))
    row = _visible_members(call).filter(id=member_id).first()
    if row is None:
        raise Http404
    return _row(row)


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="members",
        operation="read",
        permissions=("members.view",),
        features=("members",),
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...] = (),
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _visible_members(call: ReadCall) -> Any:
    gym_id = call.gym.id
    if gym_id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    as_of = gym_today(call.gym)
    return scope_to_assigned_members(
        annotate_member_pt_client(
            Member.objects.filter(gym=call.gym),
            gym_id=gym_id,
            as_of=as_of,
        ),
        gym_id=gym_id,
        actor=call.actor,
        as_of=as_of,
        member_ref="pk",
    )


def _query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = MemberReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    query = dict(serializer.validated_data)
    query["search"] = _plain_digits(str(query.get("search", "")))
    query["member_number"] = _plain_digits(str(query.get("member_number", "")))
    return query


def _row(row: Member) -> dict[str, object]:
    return {
        "id": str(row.id),
        "member_number": row.member_number,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "status": row.status,
        "join_date": row.join_date.isoformat(),
        "is_pt_client": member_is_pt_client(row),
    }


def _ambiguous(rows: list[Member], *, name_search: bool) -> bool:
    folded = [row.normalized_full_name.casefold() for row in rows]
    if len(folded) != len(set(folded)):
        return True
    return name_search and len(rows) > 1


def _member_id(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            raise Http404 from None
    raise Http404


def _plain_digits(value: str) -> str:
    return value.translate(_MYANMAR_DIGITS)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_READS = {
    SEARCH_MEMBERS: _contract(
        _definition(
            SEARCH_MEMBERS,
            "Search members in this gym by name or member number. Return every match.",
        ),
        (
            FieldRequirement(
                "search",
                "Name or part of a member number. Optional. Returns every match.",
                required=False,
            ),
            FieldRequirement(
                "member_number",
                "Exact member number, such as MEM-000004. Optional.",
                required=False,
            ),
            FieldRequirement(
                "status",
                "ACTIVE, EXPIRED, SUSPENDED, CANCELLED, or ARCHIVED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement(
                "after",
                "Cursor from the previous page. Optional.",
                required=False,
            ),
        ),
    ),
    GET_MEMBER: _contract(
        _definition(GET_MEMBER, "Read one member in this gym"),
        (
            FieldRequirement(
                "member_id",
                "Member id returned by search_members",
            ),
        ),
    ),
}
_READERS = {
    SEARCH_MEMBERS: search_members,
    GET_MEMBER: get_member,
}
