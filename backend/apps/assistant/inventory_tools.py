"""Catalog and stock-adjustment commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Each body is the JSON form of the view's validated
serializer data. A product id stays in the update payload so the executor can
place it on the view route. Adjustment keeps the product id in the body.
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
from apps.common.finance.idempotency import IdempotentResponse
from apps.inventory.serializers import (
    ProductCategoryWriteSerializer,
    ProductUpdateSerializer,
    ProductWriteSerializer,
    StockAdjustSerializer,
    category_payload,
    movement_payload,
    product_payload,
)
from apps.inventory.services import (
    adjust_route,
    adjust_stock,
    category_route,
    create_category,
    create_product,
    product_route,
    update_product,
)
from apps.tenants.models import Gym

CATEGORY_METHOD = "POST"
PRODUCT_METHOD = "POST"
UPDATE_PRODUCT_METHOD = "PATCH"
UPDATE_PRODUCT_ROUTE = "/api/v1/products/{product_id}/"
ADJUST_METHOD = "POST"
_MANAGE = ("inventory.manage",)
_ADJUST_PERMISSIONS = ("inventory.adjust",)


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any


def _definition(name: str, description: str, permissions: tuple[str, ...]) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="inventory",
        operation="write",
        permissions=permissions,
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
            raise ExecutionRejected("The inventory command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise ExecutionRejected("The inventory command is not a JSON body.", code="payload")


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


def category_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the category body whose finance hash matches the HTTP view."""

    return _finance_body(ProductCategoryWriteSerializer, data)


def product_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the product body whose finance hash matches the HTTP view."""

    return _finance_body(ProductWriteSerializer, data)


def product_update_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the patch body whose finance hash matches the HTTP view."""

    return _finance_body(ProductUpdateSerializer, data)


def stock_adjust_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the adjustment body whose finance hash matches the HTTP view."""

    return _finance_body(StockAdjustSerializer, data)


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


def create_category_command(payload: Mapping[str, object]) -> dict[str, object]:
    return category_finance_body(_without(payload, "gym_id"))


def create_product_command(payload: Mapping[str, object]) -> dict[str, object]:
    return product_finance_body(_without(payload, "gym_id"))


def update_product_command(payload: Mapping[str, object]) -> dict[str, object]:
    command = product_update_finance_body(_without(payload, "gym_id", "product_id"))
    if "product_id" in payload:
        command["product_id"] = _route_token(payload.get("product_id"))
    return command


def adjust_stock_command(payload: Mapping[str, object]) -> dict[str, object]:
    return stock_adjust_finance_body(_without(payload, "gym_id"))


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


def _stored(
    *,
    status: int,
    message: str,
    data: dict[str, object],
    resource_type: str,
    resource_id: UUID,
) -> IdempotentResponse:
    rendered = {"success": True, "data": data, "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _create_product_category(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ProductCategoryWriteSerializer, call.payload)
    row = create_category(
        gym=call.gym,
        actor=call.actor,
        fields=fields,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _stored(
        status=201,
        message="Product category created",
        data=category_payload(row),
        resource_type="ProductCategory",
        resource_id=row.id,
    )


def _create_product(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ProductWriteSerializer, call.payload)
    row = create_product(
        gym=call.gym,
        actor=call.actor,
        fields=fields,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _stored(
        status=201,
        message="Product created",
        data=product_payload(row),
        resource_type="Product",
        resource_id=row.id,
    )


def _update_product(call: ToolCall) -> IdempotentResponse:
    product_id = _required_uuid(call.payload.get("product_id"))
    fields = _validated_with(ProductUpdateSerializer, _without(call.payload, "product_id"))
    row = update_product(
        gym=call.gym,
        actor=call.actor,
        product_id=product_id,
        fields=fields,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _stored(
        status=200,
        message="Product updated",
        data=product_payload(row),
        resource_type="Product",
        resource_id=row.id,
    )


def _adjust_stock(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(StockAdjustSerializer, call.payload)
    movement, product = adjust_stock(
        gym=call.gym,
        actor=call.actor,
        fields=fields,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _stored(
        status=201,
        message="Stock adjusted",
        data={"movement": movement_payload(movement), "product": product_payload(product)},
        resource_type="StockMovement",
        resource_id=movement.id,
    )


def _category_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=create_category_command)


def _product_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=create_product_command)


def _update_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=update_product_command)


def _adjust_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=adjust_stock_command)


_CATEGORY = RegisteredTool(
    contract=_contract(
        _definition("create_product_category", "Create a product category", _MANAGE),
        (FieldRequirement("name", "Name"),),
        http_method=CATEGORY_METHOD,
        route=category_route(),
    ),
    finance_mutate=_create_product_category,
    prepare=_category_prepare,
)
_PRODUCT = RegisteredTool(
    contract=_contract(
        _definition("create_product", "Create a product", _MANAGE),
        (
            FieldRequirement("name", "Name"),
            FieldRequirement("category_id", "Category"),
            FieldRequirement("unit", "Unit"),
            FieldRequirement("sale_price", "Sale price"),
            FieldRequirement("current_unit_cost", "Current unit cost"),
            FieldRequirement("default_unit_cost", "Default unit cost"),
            FieldRequirement("low_stock_threshold", "Low stock threshold"),
        ),
        http_method=PRODUCT_METHOD,
        route=product_route(product_id=None),
    ),
    finance_mutate=_create_product,
    prepare=_product_prepare,
)
_UPDATE = RegisteredTool(
    contract=_contract(
        _definition("update_product", "Update a product", _MANAGE),
        (FieldRequirement("product_id", "Product"),),
        http_method=UPDATE_PRODUCT_METHOD,
        route=UPDATE_PRODUCT_ROUTE,
        route_fields=("product_id",),
    ),
    finance_mutate=_update_product,
    prepare=_update_prepare,
)
_ADJUST = RegisteredTool(
    contract=_contract(
        _definition("adjust_stock", "Adjust stock", _ADJUST_PERMISSIONS),
        (
            FieldRequirement("product_id", "Product"),
            FieldRequirement("quantity", "Quantity"),
            FieldRequirement("movement_type", "Movement type"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method=ADJUST_METHOD,
        route=adjust_route(),
    ),
    finance_mutate=_adjust_stock,
    prepare=_adjust_prepare,
)
_REGISTERED = {
    tool.contract.definition.name: tool for tool in (_CATEGORY, _PRODUCT, _UPDATE, _ADJUST)
}
INVENTORY_TOOL_NAMES = tuple(sorted(_REGISTERED))


def inventory_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def inventory_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered inventory command. Production registration follows its tests."""

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
