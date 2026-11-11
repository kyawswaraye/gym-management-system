"""Trainer assignment, service package, and class type tools.

Each handler calls the existing database service. Class type updates change
only the type row. Schedules, occurrences, and bookings stay on the values
stored when they were created.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.classes.serializers import ClassTypePatchSerializer, ClassTypeWriteSerializer
from apps.classes.services import create_class_type, update_class_type
from apps.trainers.serializers import (
    AssignmentCancelSerializer,
    AssignmentCreateSerializer,
    AssignmentEndSerializer,
    ServicePackageWriteSerializer,
)
from apps.trainers.services import (
    cancel_assignment,
    create_assignment,
    create_service_package,
    end_assignment,
)


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    effect: Any


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
    module: str,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module=module,
        operation="write",
        permissions=permissions,
        features=("pt",),
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


def _create_assignment(call: ToolCall) -> str:
    payload = _validated(AssignmentCreateSerializer, call.payload)
    row = create_assignment(
        gym=call.gym,
        actor=call.actor,
        trainer_staff_profile_id=payload["trainer_staff_profile_id"],
        member_id=payload["member_id"],
        effective_from=payload["effective_from"],
        effective_until=payload.get("effective_until"),
        reason=str(payload.get("reason") or ""),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _end_assignment(call: ToolCall) -> str:
    assignment_id = _identifier(call.payload, "assignment_id")
    payload = _validated(AssignmentEndSerializer, _body(call.payload, "assignment_id"))
    row = end_assignment(
        gym=call.gym,
        actor=call.actor,
        assignment_id=assignment_id,
        effective_until=payload["effective_until"],
        reason=str(payload["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _cancel_assignment(call: ToolCall) -> str:
    assignment_id = _identifier(call.payload, "assignment_id")
    payload = _validated(AssignmentCancelSerializer, _body(call.payload, "assignment_id"))
    row = cancel_assignment(
        gym=call.gym,
        actor=call.actor,
        assignment_id=assignment_id,
        reason=str(payload["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _create_service_package(call: ToolCall) -> str:
    payload = _validated(ServicePackageWriteSerializer, call.payload)
    price = payload["price"]
    if not isinstance(price, Decimal):
        raise ValidationError({"price": ["Enter a price."]})
    row = create_service_package(
        gym=call.gym,
        actor=call.actor,
        package_type=str(payload["type"]),
        code=str(payload["code"]),
        name=str(payload["name"]),
        session_count=int(payload["session_count"]),
        validity_value=int(payload["validity_value"]),
        validity_unit=str(payload["validity_unit"]),
        price=price,
        class_type_id=payload.get("class_type_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _create_class_type(call: ToolCall) -> str:
    payload = _validated(ClassTypeWriteSerializer, call.payload)
    row = create_class_type(
        gym=call.gym,
        actor=call.actor,
        code=str(payload["code"]),
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        default_duration_minutes=int(payload["default_duration_minutes"]),
        default_capacity=int(payload["default_capacity"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _update_class_type(call: ToolCall) -> str:
    class_type_id = _identifier(call.payload, "class_type_id")
    changes = _validated(ClassTypePatchSerializer, _body(call.payload, "class_type_id"))
    row = update_class_type(
        gym=call.gym,
        actor=call.actor,
        class_type_id=class_type_id,
        changes=changes,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(row.id)


def _tools() -> tuple[RegisteredTool, ...]:
    specs: tuple[tuple[ToolDefinition, tuple[FieldRequirement, ...], Any], ...] = (
        (
            _definition(
                "create_assignment",
                "Assign a trainer to a member",
                ("pt.assignments.manage",),
                "trainers",
            ),
            (
                FieldRequirement("trainer_staff_profile_id", "Trainer"),
                FieldRequirement("member_id", "Member"),
                FieldRequirement("effective_from", "Start date"),
            ),
            _create_assignment,
        ),
        (
            _definition(
                "end_assignment",
                "End an active trainer assignment",
                ("pt.assignments.manage",),
                "trainers",
            ),
            (
                FieldRequirement("assignment_id", "Assignment"),
                FieldRequirement("effective_until", "End date"),
                FieldRequirement("reason", "Reason"),
            ),
            _end_assignment,
        ),
        (
            _definition(
                "cancel_assignment",
                "Cancel an active trainer assignment",
                ("pt.assignments.manage",),
                "trainers",
            ),
            (
                FieldRequirement("assignment_id", "Assignment"),
                FieldRequirement("reason", "Reason"),
            ),
            _cancel_assignment,
        ),
        (
            _definition(
                "create_service_package",
                "Create a service package",
                ("pt.schedule.manage",),
                "trainers",
            ),
            (
                FieldRequirement("type", "Package type"),
                FieldRequirement("name", "Name"),
                FieldRequirement("session_count", "Sessions"),
                FieldRequirement("validity_value", "Validity"),
                FieldRequirement("validity_unit", "Validity unit"),
                FieldRequirement("price", "Price"),
            ),
            _create_service_package,
        ),
        (
            _definition(
                "create_class_type",
                "Create a class type",
                ("classes.manage",),
                "classes",
            ),
            (
                FieldRequirement("name", "Name"),
                FieldRequirement("default_duration_minutes", "Duration"),
                FieldRequirement("default_capacity", "Capacity"),
            ),
            _create_class_type,
        ),
        (
            _definition(
                "update_class_type",
                "Update a class type",
                ("classes.manage",),
                "classes",
            ),
            (FieldRequirement("class_type_id", "Class type"),),
            _update_class_type,
        ),
    )
    return tuple(
        RegisteredTool(contract=_contract(definition, fields), effect=effect)
        for definition, fields, effect in specs
    )


_REGISTERED = {tool.contract.definition.name: tool for tool in _tools()}
TRAINING_TOOL_NAMES = tuple(sorted(_REGISTERED))


def training_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def training_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered trainer, package, or class type tool."""

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
