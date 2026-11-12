"""PostgreSQL coverage for payment read tools."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.approval import payment_snapshot
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.assistant.payment_reads import execute_registered_read
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Payment
from apps.members.payment_services import create_payment_draft
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
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

_HIDDEN = {
    "phone",
    "email",
    "contact",
    "member",
    "receipt",
    "receipt_body",
    "receipt_html",
    "reference_number",
}


def _request(gym: Gym, actor: GymMembership) -> Request:
    request = Request(APIRequestFactory().get("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset({"payments.view"})
    request.request_id = "req_payment_read"
    return request


def _read(gym: Gym, actor: GymMembership, tool_name: str, payload: dict[str, object]):
    return execute_registered_read(
        request=_request(gym, actor),
        view=APIView(),
        tool_name=tool_name,
        payload=payload,
    )


def _owner(code: str, email: str) -> tuple[Gym, GymMembership]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(client, token, code=code, owner_email=email)
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    return gym, owner


def _draft(gym: Gym, actor: GymMembership, amount: str) -> Payment:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_payment_draft(
            gym=gym,
            actor=actor,
            fields={
                "purpose": "OTHER",
                "description": "Day pass",
                "amount": Decimal(amount),
                "currency": gym.currency,
                "method": "BANK_TRANSFER",
                "business_date": gym_today(gym),
            },
            request_id="req_payment_read",
            ip_address=None,
            user_agent="",
        )


def _claims(gym: Gym, goal: Goal) -> tuple[int, int, int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


@override_settings(**jwt_settings())
def test_payment_reads_stay_in_the_actor_gym_and_write_nothing() -> None:
    gym, owner = _owner("RD01", "rd01-owner@example.test")
    other_gym, other_owner = _owner("RD02", "rd02-owner@example.test")
    first = _draft(gym, owner, "40.00")
    second = _draft(gym, owner, "15.50")
    foreign = _draft(other_gym, other_owner, "80.00")
    colleague = add_staff(gym, email="rd01-desk@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="rd01-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer, code="RDT", name="Ria")
    goal = _goal(gym, owner)
    before = _claims(gym, goal)

    page = _read(gym, owner, "search_payments", {"limit": 25})
    found = {row["id"] for row in page["results"]}
    assert found == {str(first.id), str(second.id)}
    assert str(foreign.id) not in found
    stored = {str(first.id): first, str(second.id): second}
    for row in page["results"]:
        assert _HIDDEN.isdisjoint(row)
        assert row["entry_type"] == stored[row["id"]].entry_type
        assert row["original_payment_id"] is None

    detail = _read(gym, owner, "get_payment", {"payment_id": str(first.id)})
    snapshot = payment_snapshot(gym, first.id)
    assert detail["record_id"] == snapshot.record_id
    assert detail["amount"] == snapshot.amount
    assert detail["amount"] == "40.00"
    assert detail["entry_type"] == first.entry_type
    assert detail["original_payment_id"] is None
    assert _HIDDEN.isdisjoint(detail)

    with pytest.raises(ExecutionRejected) as missing:
        _read(gym, owner, "get_payment", {"payment_id": str(foreign.id)})
    assert missing.value.code == "not_found"

    cursor_page = _read(gym, owner, "search_payments", {"limit": 1})
    token = cursor_page["next_after"]
    assert cursor_page["has_more"] is True
    assert isinstance(token, str)
    followed = _read(gym, owner, "search_payments", {"limit": 1, "after": token})
    assert len(followed["results"]) == 1
    with pytest.raises(ValidationError):
        _read(gym, colleague, "search_payments", {"limit": 1, "after": token})
    with pytest.raises(ValidationError):
        _read(other_gym, other_owner, "search_payments", {"limit": 1, "after": token})
    with pytest.raises(Http404):
        _read(gym, trainer, "search_payments", {})

    assert _claims(gym, goal) == before
    assert isinstance(first.id, UUID)
