"""Expense commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Approve and reject stay unregistered until the stored
amount can be split from the route. Each finance body matches the view body,
and the route id is a separate field so the view's category or expense id
remains in that hash.
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
from apps.cashbook.serializers import session_payload, transaction_payload
from apps.common.finance.idempotency import IdempotentResponse
from apps.expenses.serializers import (
    CancelExpenseSerializer,
    EmptyCommandSerializer,
    ExpenseCategoryUpdateSerializer,
    ExpenseCategoryWriteSerializer,
    ExpenseUpdateSerializer,
    ExpenseWriteSerializer,
    VoidExpenseSerializer,
    category_payload,
    expense_payload,
)
from apps.expenses.services import (
    cancel_expense,
    create_category,
    create_expense_draft,
    submit_expense,
    update_category,
    update_expense_draft,
    void_expense,
)
from apps.tenants.models import Gym

CATEGORY_ROUTE = "/api/v1/expense-categories/"
CATEGORY_UPDATE_ROUTE = "/api/v1/expense-categories/{category_route_id}/"
EXPENSE_ROUTE = "/api/v1/expenses/"
EXPENSE_UPDATE_ROUTE = "/api/v1/expenses/{expense_route_id}/"
SUBMIT_ROUTE = "/api/v1/expenses/{expense_route_id}/submit/"
CANCEL_ROUTE = "/api/v1/expenses/{expense_route_id}/cancel/"
VOID_ROUTE = "/api/v1/expenses/{expense_route_id}/void/"
_ROUTE_KEYS = (
    "category_route_id",
    "expense_route_id",
    "category_id",
    "expense_id",
)


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any


def _definition(name: str, description: str, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="expenses",
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
            raise ExecutionRejected("The expense command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise ExecutionRejected("The expense command is not a JSON body.", code="payload")


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


def create_category_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the category body whose finance hash matches the HTTP view."""

    return _finance_body(ExpenseCategoryWriteSerializer, data)


def update_category_body(data: Mapping[str, object], *, category_id: str) -> dict[str, object]:
    """Return the category update body whose finance hash matches the HTTP view."""

    return {"category_id": category_id, **_finance_body(ExpenseCategoryUpdateSerializer, data)}


def create_expense_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the expense body whose finance hash matches the HTTP view."""

    return _finance_body(ExpenseWriteSerializer, data)


def update_expense_body(data: Mapping[str, object], *, expense_id: str) -> dict[str, object]:
    """Return the expense update body whose finance hash matches the HTTP view."""

    return {"expense_id": expense_id, **_finance_body(ExpenseUpdateSerializer, data)}


def cancel_expense_body(data: Mapping[str, object], *, expense_id: str) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    return {"expense_id": expense_id, **_finance_body(CancelExpenseSerializer, data)}


def void_expense_body(data: Mapping[str, object], *, expense_id: str) -> dict[str, object]:
    """Return the void body whose finance hash matches the HTTP view."""

    fields = _validated_with(VoidExpenseSerializer, data)
    return {
        "expense_id": expense_id,
        "reason": str(fields["reason"]),
        "business_date": _json_value(fields.get("business_date")),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
    }


def create_category_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_category_body(_without(payload, "gym_id"))


def update_category_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_category_body(
        _without(payload, "gym_id", "category_id", "category_route_id"),
        category_id="",
    )
    return _bind_route(
        body,
        payload,
        source="category_id",
        route_key="category_route_id",
        body_key="category_id",
    )


def create_expense_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_expense_body(_without(payload, "gym_id"))


def update_expense_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_expense_body(
        _without(payload, "gym_id", "expense_id", "expense_route_id"),
        expense_id="",
    )
    return _bind_route(
        body,
        payload,
        source="expense_id",
        route_key="expense_route_id",
        body_key="expense_id",
    )


def submit_expense_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", *_ROUTE_KEYS))
    return _bind_route(
        {},
        payload,
        source="expense_id",
        route_key="expense_route_id",
        body_key="expense_id",
    )


def cancel_expense_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_expense_body(
        _without(payload, "gym_id", "expense_id", "expense_route_id"),
        expense_id="",
    )
    return _bind_route(
        body,
        payload,
        source="expense_id",
        route_key="expense_route_id",
        body_key="expense_id",
    )


def void_expense_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_expense_body(
        _without(payload, "gym_id", "expense_id", "expense_route_id"),
        expense_id="",
    )
    return _bind_route(
        body,
        payload,
        source="expense_id",
        route_key="expense_route_id",
        body_key="expense_id",
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


def _render_category(category: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": category_payload(category), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="ExpenseCategory",
        resource_id=category.id,
    )


def _render_expense(expense: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": expense_payload(expense), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="Expense",
        resource_id=expense.id,
    )


def _render_void(original: Any, reversal: Any, movement: Any) -> IdempotentResponse:
    session = None if movement is None else movement.cash_session
    rendered = {
        "success": True,
        "data": {
            "original_expense": expense_payload(original),
            "reversal_expense": expense_payload(reversal),
            "cash_session": None if session is None else session_payload(session),
            "cash_transaction": None if movement is None else transaction_payload(movement),
        },
        "message": "Expense voided",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="Expense",
        resource_id=reversal.id,
    )


def _create_category(call: ToolCall) -> IdempotentResponse:
    category = create_category(
        gym=call.gym,
        actor=call.actor,
        fields=_validated_with(ExpenseCategoryWriteSerializer, call.payload),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_category(category, "Expense category created", 201)


def _update_category(call: ToolCall) -> IdempotentResponse:
    category = update_category(
        gym=call.gym,
        actor=call.actor,
        category_id=_required_uuid(call.payload.get("category_route_id")),
        fields=_validated_with(
            ExpenseCategoryUpdateSerializer,
            _without(call.payload, "category_id", "category_route_id"),
        ),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_category(category, "Expense category updated", 200)


def _create_expense(call: ToolCall) -> IdempotentResponse:
    expense = create_expense_draft(
        gym=call.gym,
        actor=call.actor,
        fields=_validated_with(ExpenseWriteSerializer, call.payload),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_expense(expense, "Expense draft created", 201)


def _update_expense(call: ToolCall) -> IdempotentResponse:
    expense = update_expense_draft(
        gym=call.gym,
        actor=call.actor,
        expense_id=_required_uuid(call.payload.get("expense_route_id")),
        fields=_validated_with(
            ExpenseUpdateSerializer,
            _without(call.payload, "expense_id", "expense_route_id"),
        ),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_expense(expense, "Expense draft updated", 200)


def _submit_expense(call: ToolCall) -> IdempotentResponse:
    _validated_with(
        EmptyCommandSerializer,
        _without(call.payload, "expense_id", "expense_route_id"),
    )
    expense = submit_expense(
        gym=call.gym,
        actor=call.actor,
        expense_id=_required_uuid(call.payload.get("expense_route_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_expense(expense, "Expense submitted", 200)


def _cancel_expense(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        CancelExpenseSerializer,
        _without(call.payload, "expense_id", "expense_route_id"),
    )
    expense = cancel_expense(
        gym=call.gym,
        actor=call.actor,
        expense_id=_required_uuid(call.payload.get("expense_route_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_expense(expense, "Expense cancelled", 200)


def _void_expense(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(VoidExpenseSerializer, _present(call.payload, *_ROUTE_KEYS))
    original, reversal, movement = void_expense(
        gym=call.gym,
        actor=call.actor,
        expense_id=_required_uuid(call.payload.get("expense_route_id")),
        reason=str(fields["reason"]),
        business_date=fields.get("business_date"),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_void(original, reversal, movement)


def _command_prepare(command: Callable[[Mapping[str, object]], dict[str, object]]) -> Any:
    def prepare(
        request: Request,
        payload: Mapping[str, object],
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        return _prepare(request, payload, confirmed=confirmed, command=command)

    return prepare


_CATEGORY = RegisteredTool(
    contract=_contract(
        _definition(
            "create_expense_category",
            "Create an expense category",
            "expense_categories.manage",
        ),
        (FieldRequirement("name", "Name"),),
        http_method="POST",
        route=CATEGORY_ROUTE,
    ),
    finance_mutate=_create_category,
    prepare=_command_prepare(create_category_command),
)
_CATEGORY_UPDATE = RegisteredTool(
    contract=_contract(
        _definition(
            "update_expense_category",
            "Update an expense category",
            "expense_categories.manage",
        ),
        (FieldRequirement("category_id", "Expense category"),),
        http_method="PATCH",
        route=CATEGORY_UPDATE_ROUTE,
        route_fields=("category_route_id",),
    ),
    finance_mutate=_update_category,
    prepare=_command_prepare(update_category_command),
)
_CREATE = RegisteredTool(
    contract=_contract(
        _definition("create_expense", "Create an expense draft", "expenses.create"),
        (
            FieldRequirement("category_id", "Expense category"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("description", "Description"),
            FieldRequirement("amount", "Amount"),
            FieldRequirement("currency", "Currency"),
            FieldRequirement("payment_method", "Payment method"),
        ),
        http_method="POST",
        route=EXPENSE_ROUTE,
    ),
    finance_mutate=_create_expense,
    prepare=_command_prepare(create_expense_command),
)
_UPDATE = RegisteredTool(
    contract=_contract(
        _definition("update_expense", "Update an expense draft", "expenses.update"),
        (FieldRequirement("expense_id", "Expense"),),
        http_method="PATCH",
        route=EXPENSE_UPDATE_ROUTE,
        route_fields=("expense_route_id",),
    ),
    finance_mutate=_update_expense,
    prepare=_command_prepare(update_expense_command),
)
_SUBMIT = RegisteredTool(
    contract=_contract(
        _definition("submit_expense", "Submit an expense draft", "expenses.submit"),
        (FieldRequirement("expense_id", "Expense"),),
        http_method="POST",
        route=SUBMIT_ROUTE,
        route_fields=("expense_route_id",),
    ),
    finance_mutate=_submit_expense,
    prepare=_command_prepare(submit_expense_command),
)
_CANCEL = RegisteredTool(
    contract=_contract(
        _definition("cancel_expense", "Cancel an expense draft", "expenses.update"),
        (
            FieldRequirement("expense_id", "Expense"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=CANCEL_ROUTE,
        route_fields=("expense_route_id",),
    ),
    finance_mutate=_cancel_expense,
    prepare=_command_prepare(cancel_expense_command),
)
_VOID = RegisteredTool(
    contract=_contract(
        _definition("void_expense", "Void a posted expense", "expenses.void"),
        (
            FieldRequirement("expense_id", "Expense"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=VOID_ROUTE,
        route_fields=("expense_route_id",),
    ),
    finance_mutate=_void_expense,
    prepare=_command_prepare(void_expense_command),
)
_REGISTERED = {
    tool.contract.definition.name: tool
    for tool in (
        _CATEGORY,
        _CATEGORY_UPDATE,
        _CREATE,
        _UPDATE,
        _SUBMIT,
        _CANCEL,
        _VOID,
    )
}
EXPENSE_TOOL_NAMES = tuple(sorted(_REGISTERED))


def expense_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def expense_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered expense command. Production registration follows its tests."""

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
