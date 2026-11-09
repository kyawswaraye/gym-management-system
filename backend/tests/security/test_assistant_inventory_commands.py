"""PostgreSQL security coverage for catalog and stock-adjustment tools."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.identity import execution_identity
from apps.assistant.inventory_tools import (
    adjust_stock_command,
    category_finance_body,
    execute_registered_tool,
    product_finance_body,
    update_product_command,
)
from apps.assistant.models import Goal, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.inventory.models import Product, ProductCategory, StockMovement
from apps.inventory.serializers import (
    ProductCategoryWriteSerializer,
    ProductWriteSerializer,
    StockAdjustSerializer,
)
from apps.inventory.services import adjust_stock, category_route, product_route
from apps.tenants.models import Gym, GymMembership, GymSettings, IdempotencyRecord
from apps.users.models import User
from tests.security.test_assistant_schedule import _enable, _goal, _profile
from tests.security.test_tenant_gym_settings import (
    add_staff,
    jwt_settings,
    platform_token,
    provision,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_CATEGORY = "create_product_category"
_PRODUCT = "create_product"
_UPDATE = "update_product"
_ADJUST = "adjust_stock"
_MANAGE = frozenset({"inventory.manage"})
_STOCK = frozenset({"inventory.adjust"})


def _step(goal: Goal, tool_name: str) -> WorkflowStep:
    found = WorkflowStep.objects.filter(goal=goal, tool_name=tool_name).first()
    if found is not None:
        return found
    with transaction.atomic():
        set_gym_context(gym_id=goal.gym_id)
        return WorkflowStep.objects.create(
            gym_id=goal.gym_id,
            workflow=goal.workflow,
            goal=goal,
            position=WorkflowStep.objects.filter(goal=goal).count() + 1,
            tool_name=tool_name,
        )


def _request(gym: Gym, actor: GymMembership, *, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_inventory_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str],
    confirmed: bool = True,
):
    return execute_registered_tool(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def _identity(goal: Goal, tool_name: str, payload: dict[str, object]) -> str:
    return execution_identity(
        gym_id=goal.gym_id,
        goal_id=goal.id,
        step_id=_step(goal, tool_name).id,
        tool_name=tool_name,
        attempt_number=_step(goal, tool_name).attempt_number,
        payload=payload,
    )


def _category_payload() -> dict[str, object]:
    return {"code": "DRINKS", "name": "  Cold Drinks  "}


def _product_payload(category_id: UUID) -> dict[str, object]:
    return {
        "sku": "WATER-1",
        "name": "  Spring Water  ",
        "category_id": str(category_id),
        "unit": "BTL",
        "sale_price": "25.00",
        "current_unit_cost": "10.00",
        "default_unit_cost": "9.00",
        "low_stock_threshold": "2.000",
        "barcode": "5901234",
    }


def _opening(product_id: UUID, quantity: str = "1.000") -> dict[str, object]:
    return {
        "product_id": str(product_id),
        "quantity": quantity,
        "movement_type": "OPENING",
        "business_date": "2026-11-09",
        "reason": "Opening count",
    }


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_gym_stop_before_finance() -> None:
    client, token = platform_token("inv1-platform@example.test")
    payload = provision(client, token, code="INV1", owner_email="inv1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="inv1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    category_payload = _category_payload()
    for tool_name, command, codes in (
        (_CATEGORY, category_payload, frozenset()),
        (_CATEGORY, category_payload, frozenset({"inventory.view"})),
        (_ADJUST, _opening(uuid4()), _MANAGE),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _CATEGORY, category_payload, 1, codes=_MANAGE)
    bare_user = User.objects.create_user("inv1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="INVB", name="Bare", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=bare.id)
        bare_actor = GymMembership.objects.create(
            gym=bare,
            user=bare_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=bare,
            created_by_membership_id=bare_actor.id,
            updated_by_membership_id=bare_actor.id,
        )
    bare_actor.user = bare_user
    bare_goal = _goal(bare, bare_actor)
    with pytest.raises(PermissionDenied):
        _run(bare, bare_actor, bare_goal, _CATEGORY, category_payload, 1, codes=_MANAGE)
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(
            gym,
            owner,
            goal,
            _CATEGORY,
            {**category_payload, "gym_id": str(uuid4())},
            1,
            codes=_MANAGE,
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as bad_product:
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"product_id": "not-a-uuid", "name": "Water"},
            1,
            codes=_MANAGE,
        )
    assert bad_product.value.code == "finance_route"
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _ADJUST,
            {**_opening(uuid4()), "unit_cost": "4.00"},
            1,
            codes=_STOCK,
        )
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert ProductCategory.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_catalog_replay_and_stock_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("inv2-platform@example.test")
    payload = provision(client, token, code="INV2", owner_email="inv2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    category_payload = _category_payload()
    created = _run(gym, owner, goal, _CATEGORY, category_payload, 1, codes=_MANAGE)
    created_again = _run(
        gym,
        owner,
        goal,
        _CATEGORY,
        category_payload,
        created.version,
        codes=_MANAGE,
    )
    category = ProductCategory.objects.get(id=created.effect_reference)
    prepared = category_finance_body(category_payload)
    serializer = ProductCategoryWriteSerializer(data=category_payload)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(goal, _CATEGORY, prepared)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == category_route()
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=category_route(),
        body=dict(serializer.validated_data),
    )
    assert category.name == "Cold Drinks"
    assert ProductCategory.objects.filter(gym=gym).count() == 1

    product_payload = _product_payload(category.id)
    product_result = _run(
        gym,
        owner,
        goal,
        _PRODUCT,
        product_payload,
        created.version,
        codes=_MANAGE,
    )
    product_again = _run(
        gym,
        owner,
        goal,
        _PRODUCT,
        product_payload,
        product_result.version,
        codes=_MANAGE,
    )
    product = Product.objects.get(id=product_result.effect_reference)
    product_serializer = ProductWriteSerializer(data=product_payload)
    product_serializer.is_valid(raise_exception=True)
    product_stored = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=product_result.execution_identity,
    )
    assert product_result.outcome == "committed"
    assert product_again.outcome == "existing"
    assert product_result.execution_identity == _identity(
        goal,
        _PRODUCT,
        product_finance_body(product_payload),
    )
    assert product_stored.route == product_route(product_id=None)
    assert product_stored.actor_member_id is None
    assert product_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=product_route(product_id=None),
        body=dict(product_serializer.validated_data),
    )
    assert product.sku == "WATER-1"
    assert product.current_unit_cost == Decimal("10.00")
    assert Product.objects.filter(gym=gym).count() == 1

    update_payload = {"product_id": str(product.id).upper(), "name": "  Still Water  "}
    updated = _run(gym, owner, goal, _UPDATE, update_payload, product_result.version, codes=_MANAGE)
    updated_again = _run(gym, owner, goal, _UPDATE, update_payload, updated.version, codes=_MANAGE)
    product.refresh_from_db()
    update_stored = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=updated.execution_identity,
    )
    assert updated.outcome == "committed"
    assert updated_again.outcome == "existing"
    assert updated.execution_identity == _identity(
        goal,
        _UPDATE,
        update_product_command(update_payload),
    )
    assert update_stored.http_method == "PATCH"
    assert update_stored.route == f"/api/v1/products/{product.id}/"
    assert product.name == "Still Water"

    calls = {"count": 0}
    real = adjust_stock

    def counting(**kwargs: object) -> tuple[StockMovement, Product]:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.inventory_tools.adjust_stock", counting)
    blocked = _opening(product.id, "-1.000")
    blocked["movement_type"] = "DAMAGE"
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _ADJUST, blocked, updated.version, codes=_STOCK)
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _ADJUST, blocked, updated.version, codes=_STOCK)
    assert calls["count"] == 2
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _ADJUST, adjust_stock_command(blocked)),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    assert StockMovement.objects.filter(gym=gym).count() == 0

    purchase = {
        "product_id": str(product.id),
        "quantity": "3.000",
        "movement_type": "PURCHASE",
        "business_date": "2026-11-09",
        "reason": "Received",
        "unit_cost": "12.50",
    }
    purchased = _run(gym, owner, goal, _ADJUST, purchase, updated.version, codes=_STOCK)
    purchased_again = _run(gym, owner, goal, _ADJUST, purchase, purchased.version, codes=_STOCK)
    product.refresh_from_db()
    movement = StockMovement.objects.get(id=purchased.effect_reference)
    purchase_serializer = StockAdjustSerializer(data=purchase)
    purchase_serializer.is_valid(raise_exception=True)
    purchase_stored = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=purchased.execution_identity,
    )
    assert purchased.outcome == "committed"
    assert purchased_again.outcome == "existing"
    assert calls["count"] == 3
    assert movement.movement_type == "PURCHASE"
    assert movement.quantity == Decimal("3.000")
    assert movement.unit_cost_snapshot == Decimal("12.50")
    assert product.current_unit_cost == Decimal("12.50")
    assert product.default_unit_cost == Decimal("12.50")
    assert StockMovement.objects.filter(gym=gym).count() == 1
    assert purchase_stored.route == "/api/v1/stock-movements/adjust/"
    assert purchase_stored.resource_type == "StockMovement"
    assert purchase_stored.actor_member_id is None
    assert purchase_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/stock-movements/adjust/",
        body=dict(purchase_serializer.validated_data),
    )
