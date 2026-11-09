"""Class booking commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. A create body is the JSON form of the validated
``ClassBookingWriteSerializer`` data. Attend and no-show hash an empty object.
Cancellation hashes the validated cancel body. The booking id stays in the
payload so the executor can place it on the view route.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
from apps.classes.booking_services import (
    attend_class_booking,
    cancel_class_booking,
    create_class_booking,
    no_show_class_booking,
)
from apps.classes.booking_views import EmptyCommandSerializer
from apps.classes.serializers import (
    ClassBookingCancelSerializer,
    ClassBookingWriteSerializer,
    class_booking_payload,
)
from apps.common.finance.idempotency import IdempotentResponse
from apps.tenants.models import Gym

CREATE_BOOKING_ROUTE = "/api/v1/class-bookings/"
CREATE_BOOKING_METHOD = "POST"
ATTEND_BOOKING_ROUTE = "/api/v1/class-bookings/{booking_id}/attend/"
NO_SHOW_BOOKING_ROUTE = "/api/v1/class-bookings/{booking_id}/no-show/"
CANCEL_BOOKING_ROUTE = "/api/v1/class-bookings/{booking_id}/cancel/"
BOOKING_ACTION_METHOD = "POST"
_BOOK_OR_MANAGE = ("classes.book", "classes.manage")


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="classes",
        operation="write",
        permissions=permissions,
        features=("pt",),
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    http_method: str,
    route: str,
    route_fields: tuple[str, ...] = (),
    permission_mode: str = "all",
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        permission_mode=permission_mode,
        http_method=http_method,
        route=route,
        route_fields=route_fields,
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, UUID):
        return str(value)
    raise ExecutionRejected("The booking command is not a JSON body.", code="payload")


def _validated_with(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def booking_create_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the create body whose finance hash matches the HTTP view."""

    validated = _validated_with(ClassBookingWriteSerializer, data)
    return {key: _json_value(value) for key, value in validated.items()}


def booking_empty_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the empty attend or no-show body used by the HTTP view."""

    validated = _validated_with(EmptyCommandSerializer, data)
    return {key: _json_value(value) for key, value in validated.items()}


def booking_cancel_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    validated = _validated_with(ClassBookingCancelSerializer, data)
    return {"reason": str(validated["reason"])}


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


def _without(payload: Mapping[str, object], *keys: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in keys}


def create_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed create command. A matching gym id is not hashed."""

    return booking_create_finance_body(_without(payload, "gym_id"))


def _id_command(
    payload: Mapping[str, object],
    finance_body: Callable[[Mapping[str, object]], dict[str, object]],
) -> dict[str, object]:
    command = finance_body(_without(payload, "gym_id", "booking_id"))
    if "booking_id" in payload:
        command["booking_id"] = _route_token(payload.get("booking_id"))
    return command


def attend_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed attend command. ``booking_id`` stays for the route."""

    return _id_command(payload, booking_empty_finance_body)


def no_show_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed no-show command. ``booking_id`` stays for the route."""

    return _id_command(payload, booking_empty_finance_body)


def cancel_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the confirmed cancel command. ``booking_id`` stays for the route."""

    return _id_command(payload, booking_cancel_finance_body)


def _prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
    command: Callable[[Mapping[str, object]], dict[str, object]],
) -> dict[str, object]:
    if _passes_gym_to_executor(request, payload, confirmed=confirmed):
        return dict(payload)
    return command(payload)


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


def _response(row: Any, *, status: int, message: str) -> IdempotentResponse:
    rendered = {
        "success": True,
        "data": class_booking_payload(row),
        "message": message,
    }
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="ClassBooking",
        resource_id=row.id,
    )


def _create_class_booking(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ClassBookingWriteSerializer, call.payload)
    row = create_class_booking(
        gym=call.gym,
        actor=call.actor,
        class_occurrence_id=cast(UUID, fields["class_occurrence_id"]),
        member_id=cast(UUID, fields["member_id"]),
        member_service_package_id=cast(UUID | None, fields.get("member_service_package_id")),
        unmetered=bool(fields.get("unmetered", False)),
        unmetered_reason=str(fields.get("unmetered_reason", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _response(row, status=201, message="Class booking created")


def _attend_class_booking(call: ToolCall) -> IdempotentResponse:
    booking_id = _required_uuid(call.payload.get("booking_id"))
    _validated_with(EmptyCommandSerializer, _without(call.payload, "booking_id"))
    row = attend_class_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=booking_id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _response(row, status=200, message="Class booking attended")


def _mark_class_booking_no_show(call: ToolCall) -> IdempotentResponse:
    booking_id = _required_uuid(call.payload.get("booking_id"))
    _validated_with(EmptyCommandSerializer, _without(call.payload, "booking_id"))
    row = no_show_class_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=booking_id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _response(row, status=200, message="Class booking marked no-show")


def _cancel_class_booking(call: ToolCall) -> IdempotentResponse:
    booking_id = _required_uuid(call.payload.get("booking_id"))
    fields = _validated_with(ClassBookingCancelSerializer, _without(call.payload, "booking_id"))
    row = cancel_class_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=booking_id,
        reason=str(fields.get("reason", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _response(row, status=200, message="Class booking cancelled")


def _create_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=create_booking_command)


def _attend_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=attend_booking_command)


def _no_show_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=no_show_booking_command)


def _cancel_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=cancel_booking_command)


_CREATE = RegisteredTool(
    contract=_contract(
        _definition("create_class_booking", "Create a class booking", _BOOK_OR_MANAGE),
        (
            FieldRequirement("class_occurrence_id", "Class occurrence"),
            FieldRequirement("member_id", "Member"),
        ),
        http_method=CREATE_BOOKING_METHOD,
        route=CREATE_BOOKING_ROUTE,
        permission_mode="any",
    ),
    finance_mutate=_create_class_booking,
    prepare=_create_prepare,
)
_ATTEND = RegisteredTool(
    contract=_contract(
        _definition("attend_class_booking", "Attend a class booking", ("classes.attendance",)),
        (FieldRequirement("booking_id", "Booking"),),
        http_method=BOOKING_ACTION_METHOD,
        route=ATTEND_BOOKING_ROUTE,
        route_fields=("booking_id",),
    ),
    finance_mutate=_attend_class_booking,
    prepare=_attend_prepare,
)
_NO_SHOW = RegisteredTool(
    contract=_contract(
        _definition(
            "mark_class_booking_no_show",
            "Mark a class booking no-show",
            ("classes.attendance",),
        ),
        (FieldRequirement("booking_id", "Booking"),),
        http_method=BOOKING_ACTION_METHOD,
        route=NO_SHOW_BOOKING_ROUTE,
        route_fields=("booking_id",),
    ),
    finance_mutate=_mark_class_booking_no_show,
    prepare=_no_show_prepare,
)
_CANCEL = RegisteredTool(
    contract=_contract(
        _definition("cancel_class_booking", "Cancel a class booking", _BOOK_OR_MANAGE),
        (FieldRequirement("booking_id", "Booking"),),
        http_method=BOOKING_ACTION_METHOD,
        route=CANCEL_BOOKING_ROUTE,
        route_fields=("booking_id",),
        permission_mode="any",
    ),
    finance_mutate=_cancel_class_booking,
    prepare=_cancel_prepare,
)
_REGISTERED = {
    _CREATE.contract.definition.name: _CREATE,
    _ATTEND.contract.definition.name: _ATTEND,
    _NO_SHOW.contract.definition.name: _NO_SHOW,
    _CANCEL.contract.definition.name: _CANCEL,
}
BOOKING_TOOL_NAMES = tuple(sorted(_REGISTERED))


def booking_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def booking_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered class booking command. Production registration follows its tests."""

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
