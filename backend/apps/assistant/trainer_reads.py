"""Trainer and assignment reads through the existing trainer selectors.

History is the stored assignment status. Ended and cancelled rows stay in the
list. The tool does not create an assignment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db.models import Count, Q
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.trainers.models import TrainerAssignment
from apps.trainers.selectors import (
    gym_local_date,
    page_assignments,
    page_trainers,
    visible_assignments,
)
from apps.users.serializers import StrictSerializer

SEARCH_TRAINERS = "search_trainers"
SEARCH_TRAINER_ASSIGNMENTS = "search_trainer_assignments"
TRAINER_READ_TOOL_NAMES = (SEARCH_TRAINERS, SEARCH_TRAINER_ASSIGNMENTS)


class TrainerReadQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class AssignmentReadQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=TrainerAssignment.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)
    summary = serializers.BooleanField(required=False, default=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def trainer_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def trainer_read_tool_contracts() -> dict[str, ToolContract]:
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


def search_trainers(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _trainer_query(call.payload)
    rows, meta = page_trainers(
        gym_id=call.gym.id,
        actor=call.actor,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_trainer_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_trainer_assignments(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _assignment_query(call.payload)
    if query.get("summary"):
        return _workload(call, int(query["limit"]))
    trainer_id = query.get("trainer_staff_profile_id")
    member_id = query.get("member_id")
    rows, meta = page_assignments(
        gym_id=call.gym.id,
        actor=call.actor,
        status=str(query.get("status") or ""),
        trainer_staff_profile_id=trainer_id if isinstance(trainer_id, UUID) else None,
        member_id=member_id if isinstance(member_id, UUID) else None,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_assignment_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _workload(call: ReadCall, limit: int) -> dict[str, object]:
    today = gym_local_date(call.gym)
    rows = (
        visible_assignments(gym_id=call.gym.id, actor=call.actor)
        .filter(status=TrainerAssignment.Status.ACTIVE, effective_from__lte=today)
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=today))
        .values("trainer_staff_profile_id", "trainer_staff_profile__display_name")
        .annotate(active_members=Count("member_id", distinct=True))
        .order_by("trainer_staff_profile__display_name")[:limit]
    )
    return {
        "results": [
            {
                "trainer_staff_profile_id": str(row["trainer_staff_profile_id"]),
                "trainer_name": row["trainer_staff_profile__display_name"],
                "active_members": int(row["active_members"]),
            }
            for row in rows
        ],
        "has_more": False,
        "next_after": None,
    }


def _trainer_row(row: Any) -> dict[str, object]:
    return {
        "id": str(row.id),
        "staff_code": row.staff_code,
        "display_name": row.display_name,
        "status": row.status,
        "employment_title": row.employment_title,
    }


def _assignment_row(row: TrainerAssignment) -> dict[str, object]:
    until = "" if row.effective_until is None else row.effective_until.isoformat()
    return {
        "id": str(row.id),
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "trainer_name": row.trainer_staff_profile.display_name,
        "member_id": str(row.member_id),
        "member_name": f"{row.member.first_name} {row.member.last_name}".strip(),
        "status": row.status,
        "effective_from": row.effective_from.isoformat(),
        "effective_until": until,
    }


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="trainers",
        operation="read",
        permissions=("pt.schedule.view",),
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
    )


def _trainer_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = TrainerReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _assignment_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = AssignmentReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_LIMIT = (
    FieldRequirement("limit", "Page size from 1 to 25. Optional.", required=False, kind="integer"),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_READS = {
    SEARCH_TRAINERS: _contract(
        _definition(
            SEARCH_TRAINERS,
            "Search trainers in this gym. Returns the staff profiles this caller can see.",
        ),
        _LIMIT,
    ),
    SEARCH_TRAINER_ASSIGNMENTS: _contract(
        _definition(
            SEARCH_TRAINER_ASSIGNMENTS,
            "Search trainer assignments in this gym. Status is ACTIVE, ENDED, or CANCELLED. "
            "Set summary true for each trainer's current active member count.",
        ),
        (
            FieldRequirement(
                "status",
                "ACTIVE, ENDED, or CANCELLED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "trainer_staff_profile_id",
                "Trainer staff profile id. Optional.",
                required=False,
            ),
            FieldRequirement("member_id", "Member id. Optional.", required=False),
            FieldRequirement(
                "summary",
                "True for one row per trainer with the active member count. Optional.",
                required=False,
                kind="boolean",
            ),
            *_LIMIT,
        ),
    ),
}
_READERS = {
    SEARCH_TRAINERS: search_trainers,
    SEARCH_TRAINER_ASSIGNMENTS: search_trainer_assignments,
}
