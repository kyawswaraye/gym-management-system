from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
from apps.assistant.execution import ExecutionRejected
from apps.assistant.inventory_tools import (
    ADJUST_METHOD,
    CATEGORY_METHOD,
    INVENTORY_TOOL_NAMES,
    PRODUCT_METHOD,
    UPDATE_PRODUCT_METHOD,
    UPDATE_PRODUCT_ROUTE,
    adjust_stock_command,
    category_finance_body,
    create_category_command,
    execute_registered_tool,
    inventory_tool_contracts,
    inventory_tool_definitions,
    product_finance_body,
    product_update_finance_body,
    stock_adjust_finance_body,
    update_product_command,
)
from apps.assistant.models import Goal
from apps.assistant.registry import production_registry
from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
from apps.assistant.training_tools import TRAINING_TOOL_NAMES
from apps.common.finance.idempotency import canonical_request_hash
from apps.inventory.serializers import (
    ProductCategoryWriteSerializer,
    ProductUpdateSerializer,
    ProductWriteSerializer,
    StockAdjustSerializer,
)
from apps.inventory.services import adjust_route, category_route, product_route
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "adjust_stock",
    "create_product",
    "create_product_category",
    "update_product",
)


def _category_raw() -> dict[str, object]:
    return {"code": "DRINKS", "name": "  Cold Drinks  "}


def _product_raw() -> dict[str, object]:
    return {
        "sku": "WATER-1",
        "name": "  Spring Water  ",
        "category_id": str(uuid4()).upper(),
        "unit": "BTL",
        "sale_price": "25.00",
        "current_unit_cost": "10.00",
        "default_unit_cost": "9.00",
        "low_stock_threshold": "2.000",
        "barcode": "5901234",
    }


def _adjust_raw() -> dict[str, object]:
    return {
        "product_id": str(uuid4()).upper(),
        "quantity": "3.000",
        "movement_type": "PURCHASE",
        "business_date": "2026-11-09",
        "reason": "  Received  ",
        "unit_cost": "12.50",
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="inv-unit", name="Inventory", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset() if codes is None else codes
    return request, Goal(id=uuid4(), gym=gym, version=1)


def _assert_same_hash(
    left: dict[str, object],
    right: dict[str, object],
    *,
    method: str,
    route: str,
) -> None:
    gym_id = uuid4()
    actor_id = uuid4()
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=left,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=right,
    )


def test_inventory_tools_are_closed_finance_commands() -> None:
    contracts = inventory_tool_contracts()
    definitions = {tool.name: tool for tool in inventory_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == INVENTORY_TOOL_NAMES == _NAMES
    assert set(INVENTORY_TOOL_NAMES).isdisjoint(TRAINING_TOOL_NAMES)
    assert set(INVENTORY_TOOL_NAMES).isdisjoint(SCHEDULE_TOOL_NAMES)
    assert set(INVENTORY_TOOL_NAMES).isdisjoint(BOOKING_TOOL_NAMES)
    assert set(TRAINING_TOOL_NAMES) <= set(names)
    assert set(SCHEDULE_TOOL_NAMES) <= set(names)
    assert set(BOOKING_TOOL_NAMES) <= set(names)
    assert set(INVENTORY_TOOL_NAMES) <= set(names)
    category = contracts["create_product_category"]
    product = contracts["create_product"]
    update = contracts["update_product"]
    adjust = contracts["adjust_stock"]
    assert category.http_method == CATEGORY_METHOD == "POST"
    assert category.route == category_route() == "/api/v1/product-categories/"
    assert product.http_method == PRODUCT_METHOD == "POST"
    assert product.route == product_route(product_id=None) == "/api/v1/products/"
    assert update.http_method == UPDATE_PRODUCT_METHOD == "PATCH"
    assert update.route == UPDATE_PRODUCT_ROUTE
    assert update.route_fields == ("product_id",)
    assert adjust.http_method == ADJUST_METHOD == "POST"
    assert adjust.route == adjust_route() == "/api/v1/stock-movements/adjust/"
    assert adjust.route_fields == ()
    assert category.definition.permissions == ("inventory.manage",)
    assert product.definition.permissions == ("inventory.manage",)
    assert update.definition.permissions == ("inventory.manage",)
    assert adjust.definition.permissions == ("inventory.adjust",)
    for contract in contracts.values():
        assert contract.mode == "finance_idempotent"
        assert contract.finance_closed is True
        assert contract.permission_mode == "all"
        assert contract.definition.features == ("finance",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "inventory"


def test_finance_bodies_match_the_view_request_hash() -> None:
    category_raw = _category_raw()
    category_serializer = ProductCategoryWriteSerializer(data=category_raw)
    category_serializer.is_valid(raise_exception=True)
    category_body = category_finance_body(category_raw)
    assert create_category_command(category_raw) == category_body
    assert category_body["name"] == "Cold Drinks"
    assert category_body["is_active"] is True
    _assert_same_hash(
        category_body,
        dict(category_serializer.validated_data),
        method="POST",
        route=category_route(),
    )
    product_raw = _product_raw()
    product_serializer = ProductWriteSerializer(data=product_raw)
    product_serializer.is_valid(raise_exception=True)
    product_body = product_finance_body(product_raw)
    assert product_body["sale_price"] == "25.00"
    assert product_body["low_stock_threshold"] == "2.000"
    assert product_body["name"] == "Spring Water"
    assert product_body["is_active"] is True
    _assert_same_hash(
        product_body,
        dict(product_serializer.validated_data),
        method="POST",
        route=product_route(product_id=None),
    )
    product_id = uuid4()
    update_raw = {"product_id": str(product_id).upper(), "name": "  Still Water  "}
    update_command = update_product_command(update_raw)
    update_serializer = ProductUpdateSerializer(data={"name": "  Still Water  "})
    update_serializer.is_valid(raise_exception=True)
    update_body = product_update_finance_body({"name": "  Still Water  "})
    route = f"/api/v1/products/{product_id}/"
    assert update_command["product_id"] == str(product_id)
    assert update_body == {"name": "Still Water"}
    _assert_same_hash(
        update_body,
        dict(update_serializer.validated_data),
        method="PATCH",
        route=route,
    )
    adjust_raw = _adjust_raw()
    adjust_serializer = StockAdjustSerializer(data=adjust_raw)
    adjust_serializer.is_valid(raise_exception=True)
    adjust_body = stock_adjust_finance_body(adjust_raw)
    assert adjust_stock_command(adjust_raw) == adjust_body
    assert adjust_body["quantity"] == "3.000"
    assert adjust_body["unit_cost"] == "12.50"
    assert adjust_body["business_date"] == "2026-11-09"
    assert adjust_body["reason"] == "Received"
    _assert_same_hash(
        adjust_body,
        dict(adjust_serializer.validated_data),
        method="POST",
        route=adjust_route(),
    )


def test_permission_and_gym_stop_before_an_inventory_service() -> None:
    request, goal = _session()
    request.gym_membership.is_owner = False
    payloads = (
        ("create_product_category", _category_raw()),
        ("create_product", _product_raw()),
        ("update_product", {"product_id": str(uuid4()), "name": "Still Water"}),
        ("adjust_stock", _adjust_raw()),
    )
    for tool_name, payload in payloads:
        with pytest.raises(PermissionDenied):
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload=payload,
                confirmed=True,
            )
    request.permission_codes = frozenset({"inventory.manage", "inventory.adjust"})
    for tool_name, payload in payloads:
        with pytest.raises(ExecutionRejected) as mismatched:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={**payload, "gym_id": str(uuid4())},
                confirmed=True,
            )
        assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="adjust_stock",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_adjust_raw(), "movement_type": "OPENING"},
            confirmed=True,
        )
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="update_product",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"product_id": str(uuid4())},
            confirmed=True,
        )
