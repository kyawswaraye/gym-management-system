"""Class schedule creation through the existing finance command.

The handler does not call ``run_idempotent``. The executor does, outside the
assistant transaction. The hashed body is the JSON form of the view's
``schedule_command_body``.
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
from apps.classes.serializers import ClassScheduleWriteSerializer, class_schedule_payload
from apps.classes.services import create_class_schedule
from apps.classes.views import schedule_command_body
from apps.common.finance.idempotency import IdempotentResponse
from apps.tenants.models import Gym

SCHEDULE_ROUTE = "/api/v1/class-schedules/"
SCHEDULE_METHOD = "POST"


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any


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


_TOOL = RegisteredTool(contract=_contract(), finance_mutate=_create_class_schedule)
_REGISTERED = {_TOOL.contract.definition.name: _TOOL}
SCHEDULE_TOOL_NAMES = tuple(sorted(_REGISTERED))


def schedule_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def schedule_tool_contract() -> ToolContract:
    return _TOOL.contract


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
    """Run the class schedule tool. Registration in production follows its tests."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    command = payload
    if isinstance(payload, dict):
        command = _command_payload(request, payload, confirmed=confirmed)
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
