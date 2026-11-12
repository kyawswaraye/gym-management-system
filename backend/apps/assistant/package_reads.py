"""PT and class package reads through the existing package selectors.

Remaining sessions come from the stored session ledger. The tool does not
invent a balance, and it does not confirm or write.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
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
from apps.trainers.models import MemberServicePackage, ServicePackage
from apps.trainers.selectors import get_visible_member_package, page_member_packages
from apps.trainers.serializers import member_package_payload
from apps.users.serializers import StrictSerializer

SEARCH_PT_PACKAGES = "search_pt_packages"
PACKAGE_READ_TOOL_NAMES = (SEARCH_PT_PACKAGES,)


class PackageReadQuerySerializer(StrictSerializer):
    package_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=MemberServicePackage.Status.choices, required=False)
    package_type = serializers.ChoiceField(
        choices=ServicePackage.PackageType.choices,
        required=False,
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def package_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def package_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered package read. The payload does not choose the gym."""

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


def search_pt_packages(call: ReadCall) -> dict[str, object]:
    """Return granted packages this actor can see in the session gym."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(call.payload)
    package_id = query.get("package_id")
    if isinstance(package_id, UUID):
        row = get_visible_member_package(gym=call.gym, actor=call.actor, package_id=package_id)
        rendered = _row(row)
        member_id = query.get("member_id")
        if isinstance(member_id, UUID) and rendered["member_id"] != str(member_id):
            raise Http404
        if query.get("status") and rendered["status"] != query["status"]:
            raise Http404
        if query.get("package_type") and rendered["package_type"] != query["package_type"]:
            raise Http404
        return {"results": [rendered], "has_more": False, "next_after": None}
    rows, meta = page_member_packages(
        gym=call.gym,
        actor=call.actor,
        status=str(query.get("status") or ""),
        member_id=query.get("member_id") if isinstance(query.get("member_id"), UUID) else None,
        package_type=str(query.get("package_type") or ""),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="pt",
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


def _query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = PackageReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _row(row: MemberServicePackage) -> dict[str, object]:
    payload = member_package_payload(row)
    return {
        "id": str(payload["id"]),
        "member_id": str(payload["member_id"]),
        "member_name": str(payload["member_name"]),
        "package_code": str(payload["package_code"]),
        "package_name": str(payload["package_name"]),
        "package_type": str(payload["package_type"]),
        "status": str(payload["status"]),
        "granted_sessions": _session_count(payload["granted_sessions"]),
        "remaining_sessions": _session_count(payload["remaining_sessions"]),
        "valid_from": _date_text(payload["valid_from"]),
        "valid_until": _date_text(payload["valid_until"]),
    }


def _session_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionRejected("The session count is not stored.", code="read_result")
    return value


def _date_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_READS = {
    SEARCH_PT_PACKAGES: _contract(
        _definition(
            SEARCH_PT_PACKAGES,
            "Search granted PT and class packages in this gym, including remaining sessions.",
        ),
        (
            FieldRequirement("package_id", "Exact granted package id. Optional.", required=False),
            FieldRequirement(
                "member_id",
                "Member id returned by search_members. Optional.",
                required=False,
            ),
            FieldRequirement(
                "status",
                "ACTIVE, EXHAUSTED, EXPIRED, or VOID. Optional.",
                required=False,
            ),
            FieldRequirement(
                "package_type",
                "PT or CLASS. Optional.",
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
}
_READERS = {SEARCH_PT_PACKAGES: search_pt_packages}
