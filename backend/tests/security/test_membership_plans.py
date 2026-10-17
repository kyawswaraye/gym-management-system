from __future__ import annotations

import os
from decimal import Decimal

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import MembershipPlan
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

READ_FIELDS = {
    "id",
    "code",
    "name",
    "description",
    "duration_value",
    "duration_unit",
    "price",
    "currency",
    "is_active",
    "archived_at",
    "created_at",
    "updated_at",
}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _plan_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "code": "MONTH",
        "name": "Monthly",
        "description": "Floor access",
        "duration_value": 1,
        "duration_unit": "MONTH",
        "price": "1500.00",
    }
    body.update(overrides)
    return body


def _enable_memberships(gym: Gym, actor_id: object) -> None:
    feature, _created = Feature.objects.get_or_create(
        code="memberships",
        defaults={"name": "Memberships", "description": "Memberships module"},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.get_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.PLAN,
                "effective_from": timezone.now(),
                "created_by_membership_id": actor_id,
            },
        )


def _stored(gym_id: object, plan_id: str) -> MembershipPlan:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return MembershipPlan.objects.get(id=plan_id)


@override_settings(**jwt_settings())
def test_membership_plan_api_is_scoped_authoritative_and_idempotent() -> None:
    client, platform_access = platform_token("plan-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="PLAN-A", owner_email="plan-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="PLAN-B", owner_email="plan-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym_a, owner_a.id)
    _enable_memberships(gym_b, owner_b.id)
    trainer = add_staff(gym_a, email="plan-trainer-a@example.test", role_key="TRAINER")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer))
    api = _client()

    denied = api.post("/api/v1/membership-plans/", _plan_body(), format="json", **trainer_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

    forged_currency = api.post(
        "/api/v1/membership-plans/",
        _plan_body(currency="USD", gym_id=str(gym_b.id)),
        format="json",
        **owner_headers,
    )
    assert forged_currency.status_code == 400, forged_currency.json()
    assert set(forged_currency.json()["error"]["fields"]) >= {"currency", "gym_id"}

    created = api.post("/api/v1/membership-plans/", _plan_body(), format="json", **owner_headers)
    assert created.status_code == 201, created.json()
    plan = created.json()["data"]
    assert set(plan) == READ_FIELDS
    assert plan["currency"] == gym_a.currency == "THB"
    assert plan["price"] == "1500.00"
    assert plan["is_active"] is True
    assert plan["archived_at"] is None
    assert created.json()["message"] == "Membership plan created"
    plan_id = plan["id"]

    duplicate = api.post(
        "/api/v1/membership-plans/", _plan_body(name="Other"), format="json", **owner_headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "MEMBERSHIP_PLAN_CONFLICT"
    second = api.post(
        "/api/v1/membership-plans/",
        _plan_body(code="WEEK", name="Weekly", duration_unit="WEEK", price="500.00"),
        format="json",
        **owner_headers,
    )
    assert second.status_code == 201, second.json()

    listed = api.get(
        "/api/v1/membership-plans/?gym_id=" + str(gym_b.id),
        **owner_headers,
        HTTP_X_GYM_ID=str(gym_b.id),
    )
    assert listed.status_code == 400
    assert "gym_id" in listed.json()["error"]["fields"]
    own_list = api.get(
        "/api/v1/membership-plans/?limit=1", **owner_headers, HTTP_X_GYM_ID=str(gym_b.id)
    )
    assert own_list.status_code == 200, own_list.json()
    assert [row["code"] for row in own_list.json()["data"]] == ["MONTH"]
    assert own_list.json()["meta"]["has_more"] is True
    cursor = own_list.json()["meta"]["next_after"]
    next_page = api.get(f"/api/v1/membership-plans/?limit=1&after={cursor}", **owner_headers)
    assert next_page.status_code == 200, next_page.json()
    assert [row["code"] for row in next_page.json()["data"]] == ["WEEK"]
    assert next_page.json()["meta"]["has_more"] is False
    stolen_cursor = api.get(f"/api/v1/membership-plans/?after={cursor}", **other_headers)
    assert stolen_cursor.status_code == 400
    assert "after" in stolen_cursor.json()["error"]["fields"]
    assert api.get("/api/v1/membership-plans/", **trainer_headers).status_code == 200
    assert api.get("/api/v1/membership-plans/", **other_headers).json()["data"] == []

    hidden = api.get(f"/api/v1/membership-plans/{plan_id}/", **other_headers)
    assert hidden.status_code == 404
    hidden_update = api.patch(
        f"/api/v1/membership-plans/{plan_id}/",
        {"name": "Stolen"},
        format="json",
        **other_headers,
    )
    assert hidden_update.status_code == 404
    hidden_archive = api.post(
        f"/api/v1/membership-plans/{plan_id}/archive/", {}, format="json", **other_headers
    )
    assert hidden_archive.status_code == 404
    assert _stored(gym_a.id, plan_id).name == "Monthly"
    assert _stored(gym_a.id, plan_id).is_active is True

    trainer_update = api.patch(
        f"/api/v1/membership-plans/{plan_id}/",
        {"price": "10.00"},
        format="json",
        **trainer_headers,
    )
    trainer_archive = api.post(
        f"/api/v1/membership-plans/{plan_id}/archive/", {}, format="json", **trainer_headers
    )
    assert trainer_update.status_code == trainer_archive.status_code == 403
    assert trainer_update.json()["error"]["code"] == "PERMISSION_DENIED"

    gym_a.currency = "USD"
    gym_a.save(update_fields=["currency", "updated_at"])
    usd_plan = api.post(
        "/api/v1/membership-plans/",
        _plan_body(code="DAY", name="Daily", duration_unit="DAY", price="100.00"),
        format="json",
        **owner_headers,
    )
    assert usd_plan.status_code == 201, usd_plan.json()
    assert usd_plan.json()["data"]["currency"] == "USD"
    assert _stored(gym_a.id, plan_id).currency == "THB"

    currency_patch = api.patch(
        f"/api/v1/membership-plans/{plan_id}/",
        {"currency": "USD"},
        format="json",
        **owner_headers,
    )
    assert currency_patch.status_code == 400
    assert "currency" in currency_patch.json()["error"]["fields"]
    updated = api.patch(
        f"/api/v1/membership-plans/{plan_id}/",
        {"name": "Monthly revised", "price": "1600.50"},
        format="json",
        **owner_headers,
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["name"] == "Monthly revised"
    assert updated.json()["data"]["price"] == "1600.50"
    assert updated.json()["data"]["currency"] == "THB"
    assert _stored(gym_a.id, plan_id).currency == "THB"

    first_archive = api.post(
        f"/api/v1/membership-plans/{plan_id}/archive/", {}, format="json", **owner_headers
    )
    assert first_archive.status_code == 200, first_archive.json()
    assert first_archive.json()["data"]["is_active"] is False
    archived_at = first_archive.json()["data"]["archived_at"]
    updated_at = first_archive.json()["data"]["updated_at"]
    assert archived_at
    stored = _stored(gym_a.id, plan_id)
    assert stored.archived_at is not None
    original_archived_at = stored.archived_at

    repeated = api.post(
        f"/api/v1/membership-plans/{plan_id}/archive/", {}, format="json", **owner_headers
    )
    assert repeated.status_code == 200, repeated.json()
    assert repeated.json()["data"]["archived_at"] == archived_at
    assert repeated.json()["data"]["updated_at"] == updated_at
    assert repeated.json()["data"]["is_active"] is False
    stored = _stored(gym_a.id, plan_id)
    assert stored.archived_at == original_archived_at
    assert stored.is_active is False
    assert stored.price == Decimal("1600.50")
