"""Retail sale commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Approve and reject confirm the stored sale id and
``money_text`` of the sale total outside the view-body hash. Each finance body matches the view
body, and the route id is a separate field so the view's sale id remains in
that hash.
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

from apps.assistant.approval import (
    CONFIRMATION_FIELDS,
    approval_loader,
    attach_confirmation,
    retail_snapshot,
)
from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import (
    ApprovalSnapshot,
    ExecutionMode,
    ToolCall,
    ToolContract,
    execute_confirmed_tool,
)
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.cashbook.serializers import transaction_payload
from apps.common.finance.idempotency import IdempotentResponse
from apps.inventory.quantities import format_quantity
from apps.members.payment_serializers import money_text, payment_payload, receipt_payload
from apps.sales.serializers import (
    ApproveRetailSaleSerializer,
    CancelRetailSaleSerializer,
    EmptyCommandSerializer,
    RejectRetailSaleSerializer,
    RetailSaleUpdateSerializer,
    RetailSaleWriteSerializer,
    ReturnRetailSaleSerializer,
    VoidRetailSaleSerializer,
    return_payload,
    sale_payload,
)
from apps.sales.services import (
    approve_sale,
    cancel_sale,
    create_sale,
    reject_sale,
    return_sale,
    submit_sale,
    update_sale,
    void_sale,
)
from apps.tenants.models import Gym

CREATE_ROUTE = "/api/v1/retail-sales/"
UPDATE_ROUTE = "/api/v1/retail-sales/{sale_route_id}/"
SUBMIT_ROUTE = "/api/v1/retail-sales/{sale_route_id}/submit/"
APPROVE_ROUTE = "/api/v1/retail-sales/{sale_route_id}/approve/"
REJECT_ROUTE = "/api/v1/retail-sales/{sale_route_id}/reject/"
CANCEL_ROUTE = "/api/v1/retail-sales/{sale_route_id}/cancel/"
VOID_ROUTE = "/api/v1/retail-sales/{sale_route_id}/void/"
RETURN_ROUTE = "/api/v1/retail-sales/{sale_route_id}/return/"
_ROUTE_KEYS = ("sale_route_id", "retail_sale_id")


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any
    load_approval: Any = None


def _definition(
    name: str,
    description: str,
    permission: str,
    *,
    approval: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="retail",
        operation="write",
        permissions=(permission,),
        features=("finance",),
        confirmation_required=True,
        approval_required=approval,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    http_method: str,
    route: str,
    route_fields: tuple[str, ...] = (),
    confirmation_fields: tuple[str, ...] = (),
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
        confirmation_fields=confirmation_fields,
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExecutionRejected("The retail command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        rendered: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExecutionRejected("The retail command is not a JSON body.", code="payload")
            rendered[key] = _json_value(item)
        return rendered
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise ExecutionRejected("The retail command is not a JSON body.", code="payload")


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


def create_retail_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the create body whose finance hash matches the HTTP view."""

    return _finance_body(RetailSaleWriteSerializer, data)


def update_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the update body whose finance hash matches the HTTP view."""

    return {"retail_sale_id": sale_id, **_finance_body(RetailSaleUpdateSerializer, data)}


def cancel_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    return {"retail_sale_id": sale_id, **_finance_body(CancelRetailSaleSerializer, data)}


def void_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the void body whose finance hash matches the HTTP view."""

    fields = _validated_with(VoidRetailSaleSerializer, data)
    return {
        "retail_sale_id": sale_id,
        "reason": str(fields["reason"]),
        "business_date": _json_value(fields.get("business_date")),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
    }


def return_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the return body whose finance hash matches the HTTP view."""

    fields = _validated_with(ReturnRetailSaleSerializer, data)
    return {
        "retail_sale_id": sale_id,
        "reason": str(fields["reason"]),
        "business_date": _json_value(fields.get("business_date")),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
        "lines": _json_value(fields["lines"]),
    }


def create_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_retail_sale_body(_without(payload, "gym_id"))


def update_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_retail_sale_body(
        _without(payload, "gym_id", "retail_sale_id", "sale_route_id"),
        sale_id="",
    )
    return _bind_route(
        body,
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
    )


def submit_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", *_ROUTE_KEYS))
    return _bind_route(
        {},
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
    )


def cancel_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_retail_sale_body(
        _without(payload, "gym_id", "retail_sale_id", "sale_route_id"),
        sale_id="",
    )
    return _bind_route(
        body,
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
    )


_APPROVAL_STRIP = (
    "gym_id",
    "retail_sale_id",
    "sale_route_id",
    "record_id",
    "amount",
    "confirmed",
)


def approve_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the approve body whose finance hash matches the HTTP view."""

    fields = _validated_with(ApproveRetailSaleSerializer, data)
    return {
        "retail_sale_id": sale_id,
        "cash_session_id": _json_value(fields.get("cash_session_id")),
    }


def reject_retail_sale_body(data: Mapping[str, object], *, sale_id: str) -> dict[str, object]:
    """Return the reject body whose finance hash matches the HTTP view."""

    fields = _validated_with(RejectRetailSaleSerializer, data)
    return {"retail_sale_id": sale_id, "reason": str(fields["rejection_reason"])}


def _approval_command(body: dict[str, object], payload: Mapping[str, object]) -> dict[str, object]:
    bound = _bind_route(
        body,
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
    )
    return attach_confirmation(bound, payload)


def approve_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return _approval_command(
        approve_retail_sale_body(_without(payload, *_APPROVAL_STRIP), sale_id=""),
        payload,
    )


def reject_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return _approval_command(
        reject_retail_sale_body(_without(payload, *_APPROVAL_STRIP), sale_id=""),
        payload,
    )


def void_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_retail_sale_body(
        _without(payload, "gym_id", "retail_sale_id", "sale_route_id"),
        sale_id="",
    )
    return _bind_route(
        body,
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
    )


def return_retail_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = return_retail_sale_body(
        _without(payload, "gym_id", "retail_sale_id", "sale_route_id"),
        sale_id="",
    )
    return _bind_route(
        body,
        payload,
        source="retail_sale_id",
        route_key="sale_route_id",
        body_key="retail_sale_id",
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


def _render_sale(sale: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": sale_payload(sale), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="RetailSale",
        resource_id=sale.id,
    )


def _stock_rows(movements: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "retail_sale_line_id": row.retail_sale_line_id,
            "product_id": row.product_id,
            "quantity": format_quantity(row.quantity),
            "movement_type": row.movement_type,
            "unit_cost_snapshot": money_text(row.unit_cost_snapshot),
            "business_date": row.business_date,
            "reverses_movement_id": row.reverses_movement_id,
        }
        for row in movements
    ]


def _render_void(result: Mapping[str, Any]) -> IdempotentResponse:
    movement = result["cash_transaction"]
    rendered = {
        "success": True,
        "data": {
            "retail_sale": sale_payload(result["sale"]),
            "original_payment": payment_payload(result["original"]),
            "reversal_payment": payment_payload(result["correction"]),
            "original_receipt": (
                None
                if result["original_receipt"] is None
                else receipt_payload(result["original_receipt"])
            ),
            "reversal_receipt": receipt_payload(result["correction_receipt"]),
            "cash_transaction": None if movement is None else transaction_payload(movement),
            "stock_movements": _stock_rows(list(result["stock_movements"])),
        },
        "message": "Retail sale voided",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="RetailSale",
        resource_id=result["sale"].id,
    )


def _render_return(result: Mapping[str, Any]) -> IdempotentResponse:
    movement = result["cash_transaction"]
    refund = result["refund_payment"]
    receipt = result["correction_receipt"]
    rendered = {
        "success": True,
        "data": {
            "retail_sale": sale_payload(result["sale"]),
            "retail_return": return_payload(result["retail_return"], result["return_lines"]),
            "refund_payment": None if refund is None else payment_payload(refund),
            "correction_receipt": None if receipt is None else receipt_payload(receipt),
            "cash_transaction": None if movement is None else transaction_payload(movement),
            "stock_movements": _stock_rows(list(result["stock_movements"])),
        },
        "message": "Retail sale returned",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="RetailReturn",
        resource_id=result["retail_return"].id,
    )


def _create_sale(call: ToolCall) -> IdempotentResponse:
    sale = create_sale(
        gym=call.gym,
        actor=call.actor,
        fields=_validated_with(RetailSaleWriteSerializer, call.payload),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(sale, "Retail sale created", 201)


def _update_sale(call: ToolCall) -> IdempotentResponse:
    sale = update_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        fields=_validated_with(
            RetailSaleUpdateSerializer,
            _without(call.payload, "retail_sale_id", "sale_route_id"),
        ),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(sale, "Retail sale updated", 200)


def _submit_sale(call: ToolCall) -> IdempotentResponse:
    _validated_with(
        EmptyCommandSerializer,
        _without(call.payload, "retail_sale_id", "sale_route_id"),
    )
    sale = submit_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(sale, "Retail sale submitted", 200)


def _cancel_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        CancelRetailSaleSerializer,
        _without(call.payload, "retail_sale_id", "sale_route_id"),
    )
    sale = cancel_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(sale, "Retail sale cancelled", 200)


_APPROVAL_KEYS = ("retail_sale_id", "sale_route_id", "record_id", "amount")


def _approve_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ApproveRetailSaleSerializer, _without(call.payload, *_APPROVAL_KEYS))
    sale, payment, receipt, movement = approve_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": {
            "retail_sale": sale_payload(sale),
            "payment": payment_payload(payment),
            "receipt": receipt_payload(receipt),
            "cash_transaction": None if movement is None else transaction_payload(movement),
        },
        "message": "Retail sale approved",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="RetailSale",
        resource_id=sale.id,
    )


def _reject_sale(call: ToolCall) -> IdempotentResponse:
    body = _without(call.payload, *_APPROVAL_KEYS)
    reason = body.pop("reason")
    fields = _validated_with(RejectRetailSaleSerializer, {**body, "rejection_reason": reason})
    sale = reject_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        reason=str(fields["rejection_reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(sale, "Retail sale rejected", 200)


def _load_retail_approval(gym: Gym, payload: Mapping[str, object]) -> ApprovalSnapshot:
    return retail_snapshot(gym, payload.get("sale_route_id"))


def _void_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(VoidRetailSaleSerializer, _present(call.payload, *_ROUTE_KEYS))
    result = void_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        reason=str(fields["reason"]),
        business_date=fields.get("business_date"),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_void(result)


def _return_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ReturnRetailSaleSerializer, _present(call.payload, *_ROUTE_KEYS))
    result = return_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("sale_route_id")),
        reason=str(fields["reason"]),
        business_date=fields.get("business_date"),
        cash_session_id=fields.get("cash_session_id"),
        lines=list(fields["lines"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_return(result)


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
        _definition("create_retail_sale", "Create a retail sale draft", "retail.create"),
        (
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("payment_method", "Payment method"),
            FieldRequirement("lines", "Sale lines"),
        ),
        http_method="POST",
        route=CREATE_ROUTE,
    ),
    finance_mutate=_create_sale,
    prepare=_command_prepare(create_retail_sale_command),
)
_UPDATE = RegisteredTool(
    contract=_contract(
        _definition("update_retail_sale", "Update a retail sale draft", "retail.update"),
        (FieldRequirement("retail_sale_id", "Retail sale"),),
        http_method="PATCH",
        route=UPDATE_ROUTE,
        route_fields=("sale_route_id",),
    ),
    finance_mutate=_update_sale,
    prepare=_command_prepare(update_retail_sale_command),
)
_SUBMIT = RegisteredTool(
    contract=_contract(
        _definition("submit_retail_sale", "Submit a retail sale draft", "retail.submit"),
        (FieldRequirement("retail_sale_id", "Retail sale"),),
        http_method="POST",
        route=SUBMIT_ROUTE,
        route_fields=("sale_route_id",),
    ),
    finance_mutate=_submit_sale,
    prepare=_command_prepare(submit_retail_sale_command),
)
_CANCEL = RegisteredTool(
    contract=_contract(
        _definition("cancel_retail_sale", "Cancel a retail sale draft", "retail.update"),
        (
            FieldRequirement("retail_sale_id", "Retail sale"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=CANCEL_ROUTE,
        route_fields=("sale_route_id",),
    ),
    finance_mutate=_cancel_sale,
    prepare=_command_prepare(cancel_retail_sale_command),
)
_VOID = RegisteredTool(
    contract=_contract(
        _definition("void_retail_sale", "Void a posted retail sale", "retail.void"),
        (
            FieldRequirement("retail_sale_id", "Retail sale"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=VOID_ROUTE,
        route_fields=("sale_route_id",),
    ),
    finance_mutate=_void_sale,
    prepare=_command_prepare(void_retail_sale_command),
)
_RETURN = RegisteredTool(
    contract=_contract(
        _definition("return_retail_sale", "Return part of a posted retail sale", "retail.void"),
        (
            FieldRequirement("retail_sale_id", "Retail sale"),
            FieldRequirement("reason", "Reason"),
            FieldRequirement("lines", "Return lines"),
        ),
        http_method="POST",
        route=RETURN_ROUTE,
        route_fields=("sale_route_id",),
    ),
    finance_mutate=_return_sale,
    prepare=_command_prepare(return_retail_sale_command),
)
_APPROVE = RegisteredTool(
    contract=_contract(
        _definition(
            "approve_retail_sale",
            "Approve a submitted retail sale that is waiting for approval",
            "retail.approve",
            approval=True,
        ),
        (
            FieldRequirement("retail_sale_id", "Retail sale"),
            FieldRequirement("record_id", "Stored sale"),
            FieldRequirement("amount", "Stored total"),
        ),
        http_method="POST",
        route=APPROVE_ROUTE,
        route_fields=("sale_route_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_approve_sale,
    prepare=_command_prepare(approve_retail_sale_command),
    load_approval=_load_retail_approval,
)
_REJECT = RegisteredTool(
    contract=_contract(
        _definition(
            "reject_retail_sale",
            "Reject a submitted retail sale that is waiting for approval",
            "retail.approve",
            approval=True,
        ),
        (
            FieldRequirement("retail_sale_id", "Retail sale"),
            FieldRequirement("record_id", "Stored sale"),
            FieldRequirement("amount", "Stored total"),
            FieldRequirement("reason", "Rejection reason"),
        ),
        http_method="POST",
        route=REJECT_ROUTE,
        route_fields=("sale_route_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_reject_sale,
    prepare=_command_prepare(reject_retail_sale_command),
    load_approval=_load_retail_approval,
)
_REGISTERED = {
    tool.contract.definition.name: tool
    for tool in (_CREATE, _UPDATE, _SUBMIT, _APPROVE, _REJECT, _CANCEL, _VOID, _RETURN)
}
RETAIL_TOOL_NAMES = tuple(sorted(_REGISTERED))


def retail_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def retail_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered retail command. Production registration follows its tests."""

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
        load_approval=approval_loader(request, command, tool.load_approval),
    )
