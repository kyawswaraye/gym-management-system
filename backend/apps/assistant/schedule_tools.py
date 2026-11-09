"""Class schedule and occurrence commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. A create or update body is the JSON form of the view's
``schedule_command_body``. Cancellation hashes ``{"reason": reason}``. Resource
ids stay in the payload so the executor can place them on the view route.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, cast
from uuid import UUID

from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.classes.serializers import (
    ClassOccurrenceCancelSerializer,
    ClassSchedulePatchSerializer,
    ClassScheduleWriteSerializer,
    class_occurrence_payload,
    class_schedule_payload,
)
from apps.classes.services import (
    cancel_class_occurrence,
    create_class_schedule,
    update_class_schedule,
)
from apps.classes.views import schedule_command_body
from apps.common.finance.idempotency import IdempotentResponse
from apps.tenants.models import Gym

SCHEDULE_ROUTE = "/api/v1/class-schedules/"
SCHEDULE_METHOD = "POST"
UPDATE_SCHEDULE_ROUTE = "/api/v1/class-schedules/{schedule_id}/"
UPDATE_SCHEDULE_METHOD = "PATCH"
CANCEL_OCCURRENCE_ROUTE = "/api/v1/class-occurrences/{occurrence_id}/cancel/"
CANCEL_OCCURRENCE_METHOD = "POST"


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any


def _definition() -> ToolDefinition:
    return ToolDefinition(
        name="create_class_schedule",
        description="Create a class schedule",
        module="classes",
        operation="write",
        permissions=("classes.manage",),
        features=("pt",),
        confirmation_required=True,
    )


def _contract() -> ToolContract:
    definition = _definition()
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=(
                FieldRequirement("class_type_id", "Class type"),
                FieldRequirement("trainer_staff_profile_id", "Trainer"),
                FieldRequirement("weekdays", "Weekdays"),
                FieldRequirement("local_start_time", "Start time"),
                FieldRequirement("local_end_time", "End time"),
                FieldRequirement("effective_from", "Start date"),
                FieldRequirement("effective_until", "End date"),
                FieldRequirement("location", "Location"),
                FieldRequirement("location_is_exclusive", "Exclusive location"),
                FieldRequirement("capacity", "Capacity"),
            ),
        ),
        http_method=SCHEDULE_METHOD,
        route=SCHEDULE_ROUTE,
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise ExecutionRejected("The schedule command is not a JSON body.", code="payload")


def schedule_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the JSON body whose finance hash matches the HTTP view."""

    serializer = ClassScheduleWriteSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    return {key: _json_value(value) for key, value in view_body.items()}


def _command_payload(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    """Keep confirmation and gym selection on the server.

    A matching gym id is dropped so it cannot change the view hash. A
    disagreeing gym id is left for the executor to reject before a finance
    claim. ``confirmed`` inside the payload is never read as confirmation.
    """

    if confirmed is not True:
        return dict(payload)
    gym = getattr(request, "gym", None)
    if "gym_id" in payload and isinstance(gym, Gym) and gym.id is not None:
        if str(payload["gym_id"]) != str(gym.id):
            return dict(payload)
    return schedule_finance_body({key: value for key, value in payload.items() if key != "gym_id"})


def _validated(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer: Serializer[Any] = ClassScheduleWriteSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _create_class_schedule(call: ToolCall) -> IdempotentResponse:
    fields = _validated(call.payload)
    row = create_class_schedule(
        gym=call.gym,
        actor=call.actor,
        class_type_id=cast(UUID, fields["class_type_id"]),
        trainer_staff_profile_id=cast(UUID, fields["trainer_staff_profile_id"]),
        weekdays=cast(list[int], list(fields["weekdays"])),
        local_start_time=cast(time, fields["local_start_time"]),
        local_end_time=cast(time, fields["local_end_time"]),
        effective_from=cast(date, fields["effective_from"]),
        effective_until=cast(date, fields["effective_until"]),
        location=str(fields["location"]),
        location_is_exclusive=bool(fields["location_is_exclusive"]),
        capacity=int(fields["capacity"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": class_schedule_payload(row),
        "message": "Class schedule created",
    }
    return IdempotentResponse(
        status=201,
        body=JSONRenderer().render(rendered),
        resource_type="ClassSchedule",
        resource_id=row.id,
    )


def _definition_for(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="classes",
        operation="write",
        permissions=("classes.manage",),
        features=("pt",),
        confirmation_required=True,
    )


def _route_contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    http_method: str,
    route: str,
    route_fields: tuple[str, ...],
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        http_method=http_method,
        route=route,
        route_fields=route_fields,
    )


def _route_token(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return value
    return value


def _passes_gym_to_executor(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> bool:
    if confirmed is not True:
        return True
    if "gym_id" not in payload:
        return False
    gym = getattr(request, "gym", None)
    if not isinstance(gym, Gym) or gym.id is None:
        return False
    return str(payload["gym_id"]) != str(gym.id)


def schedule_patch_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the patch body whose finance hash matches the HTTP view."""

    serializer = ClassSchedulePatchSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    return {key: _json_value(value) for key, value in view_body.items()}


def update_schedule_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed update command. ``schedule_id`` stays for the route."""

    changes = {key: value for key, value in payload.items() if key not in {"gym_id", "schedule_id"}}
    command = schedule_patch_finance_body(changes)
    if "schedule_id" in payload:
        command["schedule_id"] = _route_token(payload.get("schedule_id"))
    return command


def occurrence_cancel_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    serializer = ClassOccurrenceCancelSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return {"reason": str(serializer.validated_data["reason"])}


def cancel_occurrence_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed cancel command. ``occurrence_id`` stays for the route."""

    reason = {
        key: value for key, value in payload.items() if key not in {"gym_id", "occurrence_id"}
    }
    command = occurrence_cancel_finance_body(reason)
    if "occurrence_id" in payload:
        command["occurrence_id"] = _route_token(payload.get("occurrence_id"))
    return command


def _update_command_payload(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    if _passes_gym_to_executor(request, payload, confirmed=confirmed):
        return dict(payload)
    return update_schedule_command(payload)


def _cancel_command_payload(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    if _passes_gym_to_executor(request, payload, confirmed=confirmed):
        return dict(payload)
    return cancel_occurrence_command(payload)


def _validated_with(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _required_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ExecutionRejected("The finance route resource is missing.", code="finance_route")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ExecutionRejected(
            "The finance route resource is not a valid identifier.",
            code="finance_route",
        ) from exc


def _update_class_schedule(call: ToolCall) -> IdempotentResponse:
    schedule_id = _required_uuid(call.payload.get("schedule_id"))
    changes = _validated_with(
        ClassSchedulePatchSerializer,
        {key: value for key, value in call.payload.items() if key != "schedule_id"},
    )
    row = update_class_schedule(
        gym=call.gym,
        actor=call.actor,
        schedule_id=schedule_id,
        changes=changes,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": class_schedule_payload(row),
        "message": "Class schedule updated",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="ClassSchedule",
        resource_id=row.id,
    )


def _cancel_class_occurrence(call: ToolCall) -> IdempotentResponse:
    occurrence_id = _required_uuid(call.payload.get("occurrence_id"))
    reason = _validated_with(
        ClassOccurrenceCancelSerializer,
        {"reason": call.payload.get("reason")},
    )
    row = cancel_class_occurrence(
        gym=call.gym,
        actor=call.actor,
        occurrence_id=occurrence_id,
        reason=str(reason["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": class_occurrence_payload(row),
        "message": "Class occurrence cancelled",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="ClassOccurrence",
        resource_id=row.id,
    )


def _update_definition() -> ToolDefinition:
    return _definition_for("update_class_schedule", "Update a class schedule")


def _cancel_definition() -> ToolDefinition:
    return _definition_for("cancel_class_occurrence", "Cancel a class occurrence")


_UPDATE = RegisteredTool(
    contract=_route_contract(
        _update_definition(),
        (FieldRequirement("schedule_id", "Schedule"),),
        http_method=UPDATE_SCHEDULE_METHOD,
        route=UPDATE_SCHEDULE_ROUTE,
        route_fields=("schedule_id",),
    ),
    finance_mutate=_update_class_schedule,
    prepare=_update_command_payload,
)
_CANCEL = RegisteredTool(
    contract=_route_contract(
        _cancel_definition(),
        (
            FieldRequirement("occurrence_id", "Occurrence"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method=CANCEL_OCCURRENCE_METHOD,
        route=CANCEL_OCCURRENCE_ROUTE,
        route_fields=("occurrence_id",),
    ),
    finance_mutate=_cancel_class_occurrence,
    prepare=_cancel_command_payload,
)
_TOOL = RegisteredTool(
    contract=_contract(),
    finance_mutate=_create_class_schedule,
    prepare=_command_payload,
)
_REGISTERED = {
    _TOOL.contract.definition.name: _TOOL,
    _UPDATE.contract.definition.name: _UPDATE,
    _CANCEL.contract.definition.name: _CANCEL,
}
SCHEDULE_TOOL_NAMES = tuple(sorted(_REGISTERED))


def schedule_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def schedule_tool_contract() -> ToolContract:
    return _TOOL.contract


def schedule_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool.contract for name, tool in _REGISTERED.items()}


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
    """Run one registered class command. Registration in production follows its tests."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    command = payload
    if isinstance(payload, dict):
        command = tool.prepare(request, payload, confirmed=confirmed)
    return execute_confirmed_tool(
        request=request,
        view=view,
        contract=tool.contract,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=command,
        confirmed=confirmed,
        finance_mutate=tool.finance_mutate,
    )
