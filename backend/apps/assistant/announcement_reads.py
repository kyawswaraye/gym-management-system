"""Announcement reads through the existing announcement page selector."""

from __future__ import annotations

from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.announcements.services import announcement_payload, page_announcements
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.users.serializers import StrictSerializer

SEARCH_ANNOUNCEMENTS = "search_announcements"
ANNOUNCEMENT_READ_TOOL_NAMES = (SEARCH_ANNOUNCEMENTS,)
_TYPES = ("EVENT", "PROMOTION", "GENERAL")
_AUDIENCES = (
    "ALL_ACTIVE_MEMBERS",
    "PT_CLIENTS",
    "EXPIRING_WITHIN_7_DAYS",
    "EXPIRING_WITHIN_30_DAYS",
)
_STATUSES = ("DRAFT", "SCHEDULED", "PUBLISHED", "CANCELLED", "EXPIRED")


class AnnouncementReadQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=_STATUSES, required=False, allow_blank=True)
    type = serializers.ChoiceField(choices=_TYPES, required=False, allow_blank=True)
    audience = serializers.ChoiceField(choices=_AUDIENCES, required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


def announcement_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def announcement_read_tool_contracts() -> dict[str, ToolContract]:
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


def search_announcements(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    serializer = AnnouncementReadQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    rows, meta = page_announcements(
        gym=call.gym,
        status=str(query.get("status") or ""),
        announcement_type=str(query.get("type") or ""),
        audience=str(query.get("audience") or ""),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_row(announcement_payload(row)) for row in rows],
        "has_more": bool(meta.get("has_more")),
        "next_after": meta.get("next_after"),
    }


def _row(payload: Mapping[str, object]) -> dict[str, object]:
    body = payload.get("body")
    text = body if isinstance(body, str) else ""
    return {
        "id": str(payload.get("id") or ""),
        "type": payload.get("type"),
        "title": payload.get("title"),
        "body": text[:400],
        "audience": payload.get("audience"),
        "status": payload.get("status"),
        "starts_at": _when(payload.get("starts_at")),
        "ends_at": _when(payload.get("ends_at")),
        "published_at": _when(payload.get("published_at")),
        "cancelled_at": _when(payload.get("cancelled_at")),
        "expired_at": _when(payload.get("expired_at")),
    }


def _when(value: object) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())
    return str(value)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


def _definition() -> ToolDefinition:
    return ToolDefinition(
        name=SEARCH_ANNOUNCEMENTS,
        description=(
            "Search this gym's announcements. Status is DRAFT, SCHEDULED, "
            "PUBLISHED, CANCELLED, or EXPIRED. A draft is not published."
        ),
        module="announcements",
        operation="read",
        permissions=("announcements.view",),
        features=("members",),
    )


_READS = {
    SEARCH_ANNOUNCEMENTS: ToolContract(
        definition=_definition(),
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=SEARCH_ANNOUNCEMENTS,
            description="Announcement list for this gym.",
            fields=(
                FieldRequirement("status", "Optional status filter.", required=False),
                FieldRequirement("type", "EVENT, PROMOTION, or GENERAL. Optional.", required=False),
                FieldRequirement("audience", "Optional audience filter.", required=False),
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
    )
}
_READERS = {SEARCH_ANNOUNCEMENTS: search_announcements}
