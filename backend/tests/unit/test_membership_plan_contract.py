from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.exceptions import ValidationError

from apps.members.serializers import (
    MembershipPlanArchiveSerializer,
    MembershipPlanCreateSerializer,
    MembershipPlanListQuerySerializer,
    MembershipPlanSerializer,
    MembershipPlanUpdateSerializer,
)
from apps.members.services import decode_plan_cursor, encode_plan_cursor

WRITE_FIELDS = {"code", "name", "description", "duration_value", "duration_unit", "price"}
READ_FIELDS = WRITE_FIELDS | {
    "id",
    "currency",
    "is_active",
    "archived_at",
    "created_at",
    "updated_at",
}
PLAN_ID = "7df4f2d6-79d3-4fd1-b850-968142b1b6dd"


def _create_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "code": "MONTH",
        "name": "Monthly",
        "description": "Unlimited floor access",
        "duration_value": 1,
        "duration_unit": "MONTH",
        "price": "1500.00",
    }
    body.update(overrides)
    return body


def test_membership_plan_create_allowlist_rejects_server_fields() -> None:
    rejected = MembershipPlanCreateSerializer(
        data=_create_body(
            gym_id=PLAN_ID,
            id=PLAN_ID,
            currency="USD",
            is_active=False,
            archived_at="2026-10-17T00:00:00Z",
            normalized_name="monthly",
            created_by_membership_id=PLAN_ID,
            updated_by_membership_id=PLAN_ID,
            created_at="2026-10-17T00:00:00Z",
            updated_at="2026-10-17T00:00:00Z",
        )
    )
    accepted = MembershipPlanCreateSerializer(data=_create_body())

    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "gym_id",
        "id",
        "currency",
        "is_active",
        "archived_at",
        "normalized_name",
        "created_by_membership_id",
        "updated_by_membership_id",
        "created_at",
        "updated_at",
    }
    assert accepted.is_valid(), accepted.errors
    assert set(accepted.validated_data) == WRITE_FIELDS
    assert accepted.validated_data["price"] == Decimal("1500.00")
    assert "currency" not in accepted.validated_data


def test_membership_plan_update_requires_a_writable_field() -> None:
    empty = MembershipPlanUpdateSerializer(data={})
    currency = MembershipPlanUpdateSerializer(data={"currency": "USD", "price": "10.00"})
    renamed = MembershipPlanUpdateSerializer(data={"name": "  Annual  ", "gym_id": PLAN_ID})
    price = MembershipPlanUpdateSerializer(data={"price": "10.50"})

    assert not empty.is_valid()
    assert not currency.is_valid()
    assert "currency" in currency.errors
    assert not renamed.is_valid()
    assert "gym_id" in renamed.errors
    assert price.is_valid(), price.errors
    assert set(price.validated_data) == {"price"}


def test_membership_plan_validation_rejects_invalid_price_and_duration() -> None:
    blank = MembershipPlanCreateSerializer(data=_create_body(code="   ", name=" "))
    negative = MembershipPlanCreateSerializer(data=_create_body(price="-1.00"))
    scale = MembershipPlanCreateSerializer(data=_create_body(price="10.129"))
    floated = MembershipPlanCreateSerializer(data=_create_body(price=10.5))
    duration = MembershipPlanCreateSerializer(
        data=_create_body(duration_value=0, duration_unit="HOUR")
    )

    assert not blank.is_valid()
    assert set(blank.errors) >= {"code", "name"}
    assert not negative.is_valid()
    assert "price" in negative.errors
    assert not scale.is_valid()
    assert "price" in scale.errors
    assert not floated.is_valid()
    assert "price" in floated.errors
    assert not duration.is_valid()
    assert set(duration.errors) >= {"duration_value", "duration_unit"}


def test_membership_plan_archive_and_list_query_are_strict() -> None:
    assert MembershipPlanArchiveSerializer(data={}).is_valid()
    archive = MembershipPlanArchiveSerializer(data={"reason": "retire", "is_active": False})
    query = MembershipPlanListQuerySerializer(data={"gym_id": PLAN_ID, "search": "month"})
    accepted = MembershipPlanListQuerySerializer(data={})

    assert not archive.is_valid()
    assert set(archive.errors) == {"reason", "is_active"}
    assert not query.is_valid()
    assert set(query.errors) == {"gym_id", "search"}
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["limit"] == 25
    assert "after" not in accepted.validated_data


def test_membership_plan_response_contract_hides_tenant_and_actor_ids() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    payload = MembershipPlanSerializer(
        SimpleNamespace(
            id=uuid4(),
            code="MONTH",
            name="Monthly",
            description="",
            duration_value=1,
            duration_unit="MONTH",
            price=Decimal("1500.00"),
            currency="THB",
            is_active=True,
            archived_at=None,
            created_at=now,
            updated_at=now,
            gym_id=uuid4(),
            created_by_membership_id=uuid4(),
            updated_by_membership_id=uuid4(),
        )
    ).data

    assert set(payload) == READ_FIELDS
    assert payload["currency"] == "THB"
    assert payload["price"] == "1500.00"
    assert payload["archived_at"] is None


def test_membership_plan_cursor_is_bound_to_the_gym() -> None:
    gym_id = uuid4()
    plan_id = uuid4()
    token = encode_plan_cursor(code="MONTH", plan_id=plan_id, gym_id=gym_id)

    assert decode_plan_cursor(token, gym_id=gym_id) == ("MONTH", plan_id)
    with pytest.raises(ValidationError):
        decode_plan_cursor(token, gym_id=uuid4())
