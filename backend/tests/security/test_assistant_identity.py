"""Product, plan, and package names stay inside the gym and are not guessed."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionResult
from apps.assistant.lookups import resolve_references
from apps.assistant.models import Message
from apps.assistant.records import (
    append_message,
    open_confirmation,
    open_conversation,
    stored_payload,
)
from apps.assistant.turns import confirm_step
from apps.common.db.tenant_context import set_gym_context
from apps.inventory.models import Product
from apps.inventory.services import create_category, create_product
from apps.members.models import MembershipPlan
from apps.members.services import create_plan
from apps.tenants.models import Gym, GymMembership, RolePermission
from apps.trainers.exceptions import ServicePackageConflict
from apps.trainers.models import ServicePackage
from apps.trainers.services import create_service_package
from tests.security.test_assistant_member_reads import _owner
from tests.security.test_assistant_schedule import _enable
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_OWNER_CODES = frozenset(
    {
        "inventory.view",
        "memberships.view",
        "pt.schedule.view",
        "pt.sales.create",
    }
)


def _codes(actor: GymMembership) -> frozenset[str]:
    with transaction.atomic():
        set_gym_context(gym_id=actor.gym_id)
        return frozenset(
            RolePermission.objects.filter(
                gym_id=actor.gym_id,
                role__gymmembershiprole__gym_membership=actor,
                role__gymmembershiprole__gym_id=actor.gym_id,
                role__is_active=True,
                permission__is_active=True,
            ).values_list("permission__code", flat=True)
        )


def _request(gym: Gym, actor: GymMembership) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = _codes(actor)
    request.request_id = "req_identity"
    return request


def _product(
    gym: Gym,
    actor: GymMembership,
    *,
    sku: str,
    name: str,
    category_id: object,
    active: bool = True,
) -> Product:
    return create_product(
        gym=gym,
        actor=actor,
        fields={
            "sku": sku,
            "name": name,
            "category_id": category_id,
            "unit": "BTL",
            "sale_price": "25.00",
            "current_unit_cost": "10.00",
            "default_unit_cost": "9.00",
            "low_stock_threshold": "2.000",
            "is_active": active,
        },
        request_id="req_identity_product",
        ip_address=None,
        user_agent="",
    )


def _plan(gym: Gym, actor: GymMembership, code: str, name: str) -> MembershipPlan:
    return create_plan(
        gym=gym,
        actor=actor,
        code=code,
        name=name,
        description="private plan note",
        duration_value=1,
        duration_unit=MembershipPlan.DurationUnit.MONTH,
        price=Decimal("50000.00"),
    )


def _package(gym: Gym, actor: GymMembership, code: str, name: str) -> ServicePackage:
    return create_service_package(
        gym=gym,
        actor=actor,
        package_type=ServicePackage.PackageType.PT,
        code=code,
        name=name,
        session_count=8,
        validity_value=30,
        validity_unit=ServicePackage.ValidityUnit.DAY,
        price=Decimal("80000.00"),
        request_id="req_identity_package",
        ip_address=None,
        user_agent="",
    )


@override_settings(**jwt_settings())
def test_duplicate_catalog_names_stay_unresolved_inside_the_gym() -> None:
    gym, owner = _owner("IDN-A", "idn-a-owner@example.test")
    other, other_owner = _owner("IDN-B", "idn-b-owner@example.test")
    _enable(gym, "finance")
    _enable(other, "finance")
    trainer = add_staff(gym, email="idn-a-trainer@example.test", role_key="TRAINER")
    owner_codes = _codes(owner)
    trainer_codes = _codes(trainer)
    assert {"inventory.view", "memberships.view", "pt.schedule.view"} <= owner_codes
    assert "inventory.view" not in trainer_codes
    assert "memberships.view" in trainer_codes

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=owner,
            fields={"code": "DRINKS", "name": "Drinks"},
            request_id="req_identity_category",
            ip_address=None,
            user_agent="",
        )
        first = _product(gym, owner, sku="WATER-A", name="Spring Water", category_id=category.id)
        second = _product(gym, owner, sku="WATER-B", name="Spring Water", category_id=category.id)
        archived = _product(
            gym,
            owner,
            sku="WATER-C",
            name="Old Water",
            category_id=category.id,
            active=False,
        )
        gold = _plan(gym, owner, "GOLD", "Monthly")
        silver = _plan(gym, owner, "SILVER", "Monthly")
        package = _package(gym, owner, "PT8A", "Desk Eight")
        with pytest.raises(ServicePackageConflict):
            _package(gym, owner, "PT8B", "Desk Eight")
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        other_category = create_category(
            gym=other,
            actor=other_owner,
            fields={"code": "DRINKS", "name": "Drinks"},
            request_id="req_identity_other_category",
            ip_address=None,
            user_agent="",
        )
        foreign = _product(
            other,
            other_owner,
            sku="WATER-A",
            name="Spring Water",
            category_id=other_category.id,
        )
        foreign_plan = _plan(other, other_owner, "GOLD", "Monthly")
        foreign_package = _package(other, other_owner, "PT8A", "Desk Eight")

    conversation = open_conversation(gym=gym, actor=owner, title="Catalog")
    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="Spring Water ယူမယ်",
    )
    _resolved, _labels, products = resolve_references(
        gym,
        conversation,
        {"product_id": str(first.id)},
        permission_codes=owner_codes,
    )
    assert products is not None
    assert products["error"] == "ambiguous"
    assert products["field"] == "product_id"
    assert {row["id"] for row in products["matches"]} == {str(first.id), str(second.id)}
    assert str(foreign.id) not in {row["id"] for row in products["matches"]}
    rendered = json.dumps(products)
    assert "10.00" not in rendered
    assert "25.00" not in rendered
    assert "private plan note" not in rendered

    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="WATER-B ကို ရွေးပါ",
    )
    chosen, chosen_labels, chosen_failure = resolve_references(
        gym,
        conversation,
        {"product_id": str(first.id), "lines": [{"product_id": "Spring Water", "quantity": "1"}]},
        permission_codes=owner_codes,
    )
    assert chosen_failure is None
    assert chosen["product_id"] == str(second.id)
    assert chosen["lines"][0]["product_id"] == str(second.id)
    assert chosen_labels["product_id"] == "Spring Water (WATER-B)"
    assert "WATER-B" in chosen_labels["lines.0.product_id"]

    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="that bottle is not in the catalog",
    )
    _resolved, _labels, missing = resolve_references(
        gym,
        conversation,
        {"product_id": str(uuid4())},
        permission_codes=owner_codes,
    )
    assert missing == {"error": "not_found", "field": "product_id"}
    _resolved, _labels, foreign_product = resolve_references(
        gym,
        conversation,
        {"product_id": str(foreign.id)},
        permission_codes=owner_codes,
    )
    assert foreign_product == {"error": "not_found", "field": "product_id"}
    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="WATER-C ကို ပြင်မယ်",
    )
    archived_id, _labels, archived_failure = resolve_references(
        gym,
        conversation,
        {"product_id": str(archived.id)},
        permission_codes=owner_codes,
    )
    assert archived_failure is None
    assert archived_id["product_id"] == str(archived.id)

    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="Monthly plan တစ်ခု",
    )
    _resolved, _labels, plans = resolve_references(
        gym,
        conversation,
        {"plan_id": str(gold.id)},
        permission_codes=trainer_codes,
    )
    assert plans is not None
    assert plans["error"] == "ambiguous"
    assert {row["id"] for row in plans["matches"]} == {str(gold.id), str(silver.id)}
    assert str(foreign_plan.id) not in {row["id"] for row in plans["matches"]}
    assert "50000" not in json.dumps(plans)
    assert "private plan note" not in json.dumps(plans)

    _resolved, _labels, denied_plan = resolve_references(
        gym,
        conversation,
        {"plan_id": "Monthly"},
        permission_codes=frozenset({"gym.view"}),
    )
    assert denied_plan == {"error": "permission_denied", "field": "plan_id"}

    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="Desk Eight package",
    )
    selected, labels, package_failure = resolve_references(
        gym,
        conversation,
        {"service_package_id": "Desk Eight"},
        permission_codes=trainer_codes,
    )
    assert package_failure is None
    assert selected["service_package_id"] == str(package.id)
    assert labels["service_package_id"] == "Desk Eight (PT8A)"
    _resolved, _labels, foreign_named = resolve_references(
        gym,
        conversation,
        {"service_package_id": str(foreign_package.id)},
        permission_codes=trainer_codes,
    )
    assert foreign_named == {"error": "not_found", "field": "service_package_id"}
    _resolved, _labels, denied_package = resolve_references(
        gym,
        conversation,
        {"service_package_id": "Desk Eight"},
        permission_codes=frozenset({"gym.view"}),
    )
    assert denied_package == {"error": "permission_denied", "field": "service_package_id"}
    _resolved, _labels, denied_product = resolve_references(
        gym,
        conversation,
        {"product_id": "Spring Water"},
        permission_codes=trainer_codes,
    )
    assert denied_product == {"error": "permission_denied", "field": "product_id"}
    assert "WATER-A" not in json.dumps(denied_product)


@override_settings(**jwt_settings())
def test_confirmation_keeps_the_selected_plan_id(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner = _owner("IDN-C", "idn-c-owner@example.test")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        gold = _plan(gym, owner, "GOLD", "Monthly")
        silver = _plan(gym, owner, "SILVER", "Monthly")
    conversation = open_conversation(gym=gym, actor=owner, title="Plan")
    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="plan code GOLD",
    )
    resolved, labels, failure = resolve_references(
        gym,
        conversation,
        {"plan_id": str(silver.id)},
        permission_codes=_OWNER_CODES,
    )
    assert failure is None
    assert resolved["plan_id"] == str(gold.id)
    opened = open_confirmation(
        gym=gym,
        actor=owner,
        conversation=conversation,
        intent="Update a membership plan",
        tool_name="update_plan",
        required_input={
            "actor_membership_id": str(owner.id),
            "payload": resolved,
            "approval": None,
            "summary": "Update a membership plan.",
            "labels": labels,
        },
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert opened is not None
    _goal, step = opened
    append_message(
        gym=gym,
        conversation=conversation,
        actor=owner,
        role=Message.Role.STAFF,
        body="plan code SILVER",
    )
    captured: dict[str, object] = {}

    def write(**kwargs: object) -> ExecutionResult:
        captured.update(kwargs)
        return ExecutionResult(
            outcome="in_progress",
            version=None,
            effect_reference=None,
            execution_identity=None,
        )

    def resolved_again(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("confirmation resolved the name again")

    monkeypatch.setattr("apps.assistant.turns.execute_confirmed_write", write)
    monkeypatch.setattr("apps.assistant.turns.resolve_references", resolved_again)
    confirm_step(request=_request(gym, owner), view=APIView(), step_id=step.id)

    assert captured["payload"] == {"plan_id": str(gold.id)}
    assert captured["confirmed"] is True
    assert stored_payload(step)["plan_id"] == str(gold.id)
