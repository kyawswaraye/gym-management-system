"""Salary period commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Approve and reject stay unregistered until the stored
line total can be split from the route. Each finance body matches the view
body, and the route id is a separate field so the view's period id remains in
that hash. Line replacement uses the published ``PUT`` route.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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
from apps.cashbook.serializers import transaction_payload
from apps.common.finance.idempotency import IdempotentResponse
from apps.expenses.serializers import expense_payload
from apps.salaries.serializers import (
    CancelSalarySerializer,
    EmptyCommandSerializer,
    SalaryLinesReplaceSerializer,
    SalaryPeriodUpdateSerializer,
    SalaryPeriodWriteSerializer,
    VoidSalarySerializer,
    period_payload,
)
from apps.salaries.services import (
    cancel_period,
    create_period,
    replace_lines,
    submit_period,
    update_period,
    void_period,
)
from apps.tenants.models import Gym

CREATE_ROUTE = "/api/v1/salary-periods/"
UPDATE_ROUTE = "/api/v1/salary-periods/{period_route_id}/"
LINES_ROUTE = "/api/v1/salary-periods/{period_route_id}/lines/"
SUBMIT_ROUTE = "/api/v1/salary-periods/{period_route_id}/submit/"
CANCEL_ROUTE = "/api/v1/salary-periods/{period_route_id}/cancel/"
VOID_ROUTE = "/api/v1/salary-periods/{period_route_id}/void/"
_ROUTE_KEYS = ("period_route_id", "salary_period_id")


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any


def _definition(name: str, description: str, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="salaries",
        operation="write",
        permissions=(permission,),
        features=("finance",),
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    http_method: str,
    route: str,
    route_fields: tuple[str, ...] = (),
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        finance_closed=True,
        http_method=http_method,
        route=route,
        route_fields=route_fields,
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExecutionRejected("The salary command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        rendered: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExecutionRejected("The salary command is not a JSON body.", code="payload")
            rendered[key] = _json_value(item)
        return rendered
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise ExecutionRejected("The salary command is not a JSON body.", code="payload")


def _validated_with(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _finance_body(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, object]:
    validated = _validated_with(serializer_type, data)
    return {key: _json_value(value) for key, value in validated.items()}


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


def _bind_route(
    body: dict[str, object],
    payload: Mapping[str, object],
    *,
    source: str,
    route_key: str,
    body_key: str,
) -> dict[str, object]:
    if source not in payload:
        body.pop(body_key, None)
        return body
    token = _route_token(payload.get(source))
    if not isinstance(token, str):
        body.pop(body_key, None)
        body[route_key] = token
        return body
    try:
        canonical = str(UUID(token))
    except ValueError:
        body[body_key] = token
        body[route_key] = token
        return body
    body[body_key] = canonical
    body[route_key] = canonical
    return body


def create_salary_period_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the create body whose finance hash matches the HTTP view."""

    return _finance_body(SalaryPeriodWriteSerializer, data)


def update_salary_period_body(data: Mapping[str, object], *, period_id: str) -> dict[str, object]:
    """Return the update body whose finance hash matches the HTTP view."""

    return {"salary_period_id": period_id, **_finance_body(SalaryPeriodUpdateSerializer, data)}


def replace_salary_lines_body(data: Mapping[str, object], *, period_id: str) -> dict[str, object]:
    """Return the line body whose finance hash matches the HTTP view."""

    return {"salary_period_id": period_id, **_finance_body(SalaryLinesReplaceSerializer, data)}


def cancel_salary_period_body(data: Mapping[str, object], *, period_id: str) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    return {"salary_period_id": period_id, **_finance_body(CancelSalarySerializer, data)}


def void_salary_period_body(data: Mapping[str, object], *, period_id: str) -> dict[str, object]:
    """Return the void body whose finance hash matches the HTTP view."""

    fields = _validated_with(VoidSalarySerializer, data)
    return {
        "salary_period_id": period_id,
        "reason": str(fields["reason"]),
        "business_date": _json_value(fields.get("business_date")),
        "cash_sessions": _json_value(fields.get("cash_sessions", [])),
    }


def create_salary_period_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_salary_period_body(_without(payload, "gym_id"))


def update_salary_period_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_salary_period_body(
        _without(payload, "gym_id", "salary_period_id", "period_route_id"),
        period_id="",
    )
    return _bind_route(
        body,
        payload,
        source="salary_period_id",
        route_key="period_route_id",
        body_key="salary_period_id",
    )


def replace_salary_lines_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = replace_salary_lines_body(
        _without(payload, "gym_id", "salary_period_id", "period_route_id"),
        period_id="",
    )
    return _bind_route(
        body,
        payload,
        source="salary_period_id",
        route_key="period_route_id",
        body_key="salary_period_id",
    )


def submit_salary_period_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", *_ROUTE_KEYS))
    return _bind_route(
        {},
        payload,
        source="salary_period_id",
        route_key="period_route_id",
        body_key="salary_period_id",
    )


def cancel_salary_period_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_salary_period_body(
        _without(payload, "gym_id", "salary_period_id", "period_route_id"),
        period_id="",
    )
    return _bind_route(
        body,
        payload,
        source="salary_period_id",
        route_key="period_route_id",
        body_key="salary_period_id",
    )


def void_salary_period_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_salary_period_body(
        _without(payload, "gym_id", "salary_period_id", "period_route_id"),
        period_id="",
    )
    return _bind_route(
        body,
        payload,
        source="salary_period_id",
        route_key="period_route_id",
        body_key="salary_period_id",
    )


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


def _present(payload: Mapping[str, object], *keys: str) -> dict[str, object]:
    return {key: value for key, value in _without(payload, *keys).items() if value is not None}


def _render_period(period: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": period_payload(period), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="SalaryPeriod",
        resource_id=period.id,
    )


def _render_void(period: Any, reversals: list[tuple[Any, Any, Any]]) -> IdempotentResponse:
    rendered = {
        "success": True,
        "data": {
            "salary_period": period_payload(period),
            "reversals": [
                {
                    "line_id": line.id,
                    "reversal_expense_id": reversal.id,
                    "reversal_expense": expense_payload(reversal),
                    "cash_transaction": None if movement is None else transaction_payload(movement),
                }
                for line, reversal, movement in reversals
            ],
        },
        "message": "Salary period voided",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="SalaryPeriod",
        resource_id=period.id,
    )


def _create_period(call: ToolCall) -> IdempotentResponse:
    period = create_period(
        gym=call.gym,
        actor=call.actor,
        fields=_validated_with(SalaryPeriodWriteSerializer, call.payload),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_period(period, "Salary period created", 201)


def _update_period(call: ToolCall) -> IdempotentResponse:
    period = update_period(
        gym=call.gym,
        actor=call.actor,
        period_id=_required_uuid(call.payload.get("period_route_id")),
        fields=_validated_with(
            SalaryPeriodUpdateSerializer,
            _without(call.payload, "salary_period_id", "period_route_id"),
        ),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_period(period, "Salary period updated", 200)


def _replace_lines(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        SalaryLinesReplaceSerializer,
        _without(call.payload, "salary_period_id", "period_route_id"),
    )
    period = replace_lines(
        gym=call.gym,
        actor=call.actor,
        period_id=_required_uuid(call.payload.get("period_route_id")),
        lines=list(fields["lines"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_period(period, "Salary lines replaced", 200)


def _submit_period(call: ToolCall) -> IdempotentResponse:
    _validated_with(
        EmptyCommandSerializer,
        _without(call.payload, "salary_period_id", "period_route_id"),
    )
    period = submit_period(
        gym=call.gym,
        actor=call.actor,
        period_id=_required_uuid(call.payload.get("period_route_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_period(period, "Salary period submitted", 200)


def _cancel_period(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        CancelSalarySerializer,
        _without(call.payload, "salary_period_id", "period_route_id"),
    )
    period = cancel_period(
        gym=call.gym,
        actor=call.actor,
        period_id=_required_uuid(call.payload.get("period_route_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_period(period, "Salary period cancelled", 200)


def _void_period(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(VoidSalarySerializer, _present(call.payload, *_ROUTE_KEYS))
    period, reversals = void_period(
        gym=call.gym,
        actor=call.actor,
        period_id=_required_uuid(call.payload.get("period_route_id")),
        reason=str(fields["reason"]),
        business_date=fields.get("business_date"),
        cash_sessions=list(fields.get("cash_sessions", [])),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_void(period, reversals)


def _command_prepare(command: Callable[[Mapping[str, object]], dict[str, object]]) -> Any:
    def prepare(
        request: Request,
        payload: Mapping[str, object],
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        return _prepare(request, payload, confirmed=confirmed, command=command)

    return prepare


_CREATE = RegisteredTool(
    contract=_contract(
        _definition("create_salary_period", "Create a salary period draft", "salaries.create"),
        (
            FieldRequirement("period_start", "Period start"),
            FieldRequirement("period_end", "Period end"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("description", "Description"),
        ),
        http_method="POST",
        route=CREATE_ROUTE,
    ),
    finance_mutate=_create_period,
    prepare=_command_prepare(create_salary_period_command),
)
_UPDATE = RegisteredTool(
    contract=_contract(
        _definition("update_salary_period", "Update a salary period draft", "salaries.update"),
        (FieldRequirement("salary_period_id", "Salary period"),),
        http_method="PATCH",
        route=UPDATE_ROUTE,
        route_fields=("period_route_id",),
    ),
    finance_mutate=_update_period,
    prepare=_command_prepare(update_salary_period_command),
)
_LINES = RegisteredTool(
    contract=_contract(
        _definition("replace_salary_lines", "Replace salary period lines", "salaries.update"),
        (
            FieldRequirement("salary_period_id", "Salary period"),
            FieldRequirement("lines", "Salary lines"),
        ),
        http_method="PUT",
        route=LINES_ROUTE,
        route_fields=("period_route_id",),
    ),
    finance_mutate=_replace_lines,
    prepare=_command_prepare(replace_salary_lines_command),
)
_SUBMIT = RegisteredTool(
    contract=_contract(
        _definition("submit_salary_period", "Submit a salary period draft", "salaries.submit"),
        (FieldRequirement("salary_period_id", "Salary period"),),
        http_method="POST",
        route=SUBMIT_ROUTE,
        route_fields=("period_route_id",),
    ),
    finance_mutate=_submit_period,
    prepare=_command_prepare(submit_salary_period_command),
)
_CANCEL = RegisteredTool(
    contract=_contract(
        _definition("cancel_salary_period", "Cancel a salary period draft", "salaries.update"),
        (
            FieldRequirement("salary_period_id", "Salary period"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=CANCEL_ROUTE,
        route_fields=("period_route_id",),
    ),
    finance_mutate=_cancel_period,
    prepare=_command_prepare(cancel_salary_period_command),
)
_VOID = RegisteredTool(
    contract=_contract(
        _definition("void_salary_period", "Void a posted salary period", "salaries.void"),
        (
            FieldRequirement("salary_period_id", "Salary period"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=VOID_ROUTE,
        route_fields=("period_route_id",),
    ),
    finance_mutate=_void_period,
    prepare=_command_prepare(void_salary_period_command),
)
_REGISTERED = {
    tool.contract.definition.name: tool
    for tool in (_CREATE, _UPDATE, _LINES, _SUBMIT, _CANCEL, _VOID)
}
SALARY_TOOL_NAMES = tuple(sorted(_REGISTERED))


def salary_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def salary_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered salary command. Production registration follows its tests."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    command: Mapping[str, object] = payload
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
