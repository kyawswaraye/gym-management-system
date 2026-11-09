"""Announcement and manual check-in tools. Each one calls the existing service.

Publishing inserts the announcement's in-app and pending push rows inside the
service transaction. It does not call the push transport. Manual check-in
stores the assistant execution identity in the check-in's existing
idempotency key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.announcements.serializers import (
    AnnouncementPatchSerializer,
    AnnouncementWriteSerializer,
    EmptyCommandSerializer,
)
from apps.announcements.services import (
    cancel_announcement,
    create_announcement,
    publish_announcement,
    update_announcement,
)
from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.checkin_services import check_out_check_in, create_manual_check_in
from apps.members.serializers import CheckInCheckoutSerializer, CheckInCreateSerializer


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    effect: Any


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
    features: tuple[str, ...],
    module: str,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module=module,
        operation="write",
        permissions=permissions,
        features=features,
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.SAME_TRANSACTION,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _body(payload: Mapping[str, object], *omit: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in omit}


def _validated(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _identifier(payload: Mapping[str, object], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValidationError({key: ["Enter a valid identifier."]})
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError({key: ["Enter a valid identifier."]}) from exc


def _audit(call: ToolCall) -> dict[str, Any]:
    return {
        "request_id": call.request_id,
        "ip_address": call.ip_address,
        "user_agent": call.user_agent[:512],
        "actor_user": call.actor.user,
    }


def _create_announcement(call: ToolCall) -> str:
    row = create_announcement(
        gym=call.gym,
        actor=call.actor,
        fields=_validated(AnnouncementWriteSerializer, call.payload),
        **_audit(call),
    )
    return str(row.id)


def _update_announcement(call: ToolCall) -> str:
    announcement_id = _identifier(call.payload, "announcement_id")
    row = update_announcement(
        gym=call.gym,
        actor=call.actor,
        announcement_id=announcement_id,
        fields=_validated(AnnouncementPatchSerializer, _body(call.payload, "announcement_id")),
        **_audit(call),
    )
    return str(row.id)


def _publish_announcement(call: ToolCall) -> str:
    announcement_id = _identifier(call.payload, "announcement_id")
    _validated(EmptyCommandSerializer, _body(call.payload, "announcement_id"))
    row = publish_announcement(
        gym=call.gym,
        actor=call.actor,
        announcement_id=announcement_id,
        **_audit(call),
    )
    return str(row.id)


def _cancel_announcement(call: ToolCall) -> str:
    announcement_id = _identifier(call.payload, "announcement_id")
    _validated(EmptyCommandSerializer, _body(call.payload, "announcement_id"))
    row = cancel_announcement(
        gym=call.gym,
        actor=call.actor,
        announcement_id=announcement_id,
        **_audit(call),
    )
    return str(row.id)


def _create_manual_check_in(call: ToolCall) -> str:
    payload = _validated(CheckInCreateSerializer, call.payload)
    if "occurred_at" in payload and "checkins.update" not in call.permission_codes:
        raise PermissionDenied("Backdated check-in requires check-in correction permission.")
    result = create_manual_check_in(
        gym=call.gym,
        actor=call.actor,
        member_id=payload["member_id"],
        occurred_at=payload.get("occurred_at"),
        note=str(payload.get("note", "")),
        idempotency_key=call.execution_identity,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(result.check_in.id)


def _check_out_check_in(call: ToolCall) -> str:
    check_in_id = _identifier(call.payload, "check_in_id")
    payload = _validated(CheckInCheckoutSerializer, _body(call.payload, "check_in_id"))
    check_in = check_out_check_in(
        gym=call.gym,
        actor=call.actor,
        check_in_id=check_in_id,
        occurred_at=payload.get("occurred_at"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(check_in.id)


def _tools() -> tuple[RegisteredTool, ...]:
    announcements = ("members",)
    checkins = ("members", "memberships")
    specs: tuple[tuple[ToolDefinition, tuple[FieldRequirement, ...], Any], ...] = (
        (
            _definition(
                "create_announcement",
                "Create a draft announcement",
                ("announcements.manage",),
                announcements,
                "announcements",
            ),
            (
                FieldRequirement("type", "Announcement type"),
                FieldRequirement("title", "Title"),
                FieldRequirement("body", "Body"),
                FieldRequirement("audience", "Audience"),
            ),
            _create_announcement,
        ),
        (
            _definition(
                "update_announcement",
                "Update a draft announcement",
                ("announcements.manage",),
                announcements,
                "announcements",
            ),
            (FieldRequirement("announcement_id", "Announcement"),),
            _update_announcement,
        ),
        (
            _definition(
                "publish_announcement",
                "Publish or schedule an announcement",
                ("announcements.manage",),
                announcements,
                "announcements",
            ),
            (FieldRequirement("announcement_id", "Announcement"),),
            _publish_announcement,
        ),
        (
            _definition(
                "cancel_announcement",
                "Cancel an announcement",
                ("announcements.manage",),
                announcements,
                "announcements",
            ),
            (FieldRequirement("announcement_id", "Announcement"),),
            _cancel_announcement,
        ),
        (
            _definition(
                "create_manual_check_in",
                "Record a manual check-in",
                ("checkins.create",),
                checkins,
                "checkins",
            ),
            (FieldRequirement("member_id", "Member"),),
            _create_manual_check_in,
        ),
        (
            _definition(
                "check_out_check_in",
                "Check out an open visit",
                ("checkins.update",),
                checkins,
                "checkins",
            ),
            (FieldRequirement("check_in_id", "Check-in"),),
            _check_out_check_in,
        ),
    )
    return tuple(
        RegisteredTool(contract=_contract(definition, fields), effect=effect)
        for definition, fields, effect in specs
    )


_REGISTERED = {tool.contract.definition.name: tool for tool in _tools()}
ACTIVITY_TOOL_NAMES = tuple(sorted(_REGISTERED))


def activity_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def execute_registered_tool(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    goal: Goal,
    step_id: UUID,
    expected_version: int,
    payload: Mapping[str, object],
    confirmed: bool,
) -> ExecutionResult:
    """Run one registered announcement or check-in tool as the signed-in staff member."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_confirmed_tool(
        request=request,
        view=view,
        contract=tool.contract,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
        effect=tool.effect,
    )
