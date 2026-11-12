"""Authenticated turns execute reads immediately and writes only after confirmation."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.assistant import turns as turns_module
from apps.assistant.dispatch import execute_confirmed_write, execute_offered_read
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.provider import FunctionCall, ModelTurn
from apps.assistant.turns import confirm_step, run_turn
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.tenants.models import Gym
from tests.security.test_assistant_payment_reads import _draft, _owner
from tests.security.test_assistant_schedule import _enable
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings, staff_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_ASSISTANT = {
    "ASSISTANT_ENABLED": "true",
    "GEMINI_API_KEY": "k" * 20,
    "GEMINI_MODEL": "gemini-test",
}


class _Script:
    def __init__(self) -> None:
        self.mode = "text"
        self.arguments: dict[str, object] = {}
        self.follow_up = "Done."

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del declarations, transport
        if "previous change is saved" in system_instruction:
            return ModelTurn(
                "The member is saved.",
                (),
                ({"text": "The member is saved."},),
            )
        if _already_answered(contents):
            return ModelTurn(self.follow_up, (), ({"text": self.follow_up},))
        if self.mode in {"read", "invent"}:
            name = "search_payments"
        elif self.mode == "write":
            name = "create_member"
        elif self.mode == "approve":
            name = "approve_payment"
        else:
            return ModelTurn(
                "Hello from the assistant.",
                (),
                ({"text": "Hello from the assistant."},),
            )
        call = FunctionCall(name, dict(self.arguments))
        return ModelTurn(
            "",
            (call,),
            ({"functionCall": {"name": call.name, "args": call.arguments}},),
        )


def _already_answered(contents: list[dict[str, object]]) -> bool:
    for item in contents:
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and "functionResponse" in part:
                return True
    return False


def _rows(gym: Gym) -> tuple[int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            Member.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
        )


def _settings() -> dict[str, str]:
    return {**jwt_settings(), **_ASSISTANT}


@override_settings(**_settings())
def test_turn_reads_do_not_write_and_the_client_cannot_choose_the_gym(monkeypatch) -> None:
    gym, owner = _owner("TN11", "tn11-owner@example.test")
    script = _Script()
    script.arguments = {"limit": 5, "confirmed": True, "gym_id": str(uuid4())}
    seen: list[dict[str, object]] = []

    def wrapped(**kwargs: object) -> dict[str, object]:
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        seen.append(dict(payload))
        return execute_offered_read(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    monkeypatch.setattr("apps.assistant.turns.execute_offered_read", wrapped)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    denied = client.post("/api/v1/assistant/turns/", {"message": "Hello"}, format="json")
    unknown = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Hello", "gym_id": str(gym.id), "confirmed": True},
        format="json",
        **headers,
    )
    created = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Hello"},
        format="json",
        **headers,
    )
    assert denied.status_code == 403
    assert unknown.status_code == 400
    assert created.status_code == 200, created.json()
    body = created.json()["data"]
    assert body["message"]["body"] == "Hello from the assistant."
    script.mode = "read"
    read = client.post(
        "/api/v1/assistant/turns/",
        {"message": "List payments", "conversation_id": body["conversation_id"]},
        format="json",
        **headers,
    )
    assert read.status_code == 200, read.json()
    assert seen and "confirmed" not in seen[0] and "gym_id" not in seen[0]
    assert read.json()["data"]["pending_confirmation"] is None
    assert _rows(gym) == (0, 0)
    keyed = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Again"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="same-key",
        **headers,
    )
    assert keyed.status_code == 400


@override_settings(**_settings())
def test_writes_wait_for_confirmation_and_replay_does_not_repeat(monkeypatch) -> None:
    gym, owner = _owner("TN12", "tn12-owner@example.test")
    _enable(gym, "members")
    other_gym, other_owner = _owner("TN13", "tn13-owner@example.test")
    colleague = add_staff(gym, email="tn12-desk@example.test", role_key="RECEPTIONIST")
    script = _Script()
    script.mode = "write"
    script.arguments = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "join_date": "2026-11-11",
        "confirmed": True,
        "gym_id": str(other_gym.id),
    }
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    proposed = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Add Ada Lovelace"},
        format="json",
        **headers,
    )
    assert proposed.status_code == 200, proposed.json()
    pending = proposed.json()["data"]["pending_confirmation"]
    assert pending["tool_name"] == "Create a member"
    assert proposed.json()["data"]["tool_activity"] == [
        {"name": "Create a member", "status": "proposed"}
    ]
    assert "create_member" not in str(proposed.json()["data"])
    assert _rows(gym) == (0, 0)
    step_id = pending["step_id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step = WorkflowStep.objects.get(id=step_id)
        stored = step.required_input["payload"]
    assert step.tool_name == "create_member"
    assert "confirmed" not in stored
    assert "gym_id" not in stored
    refused = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": False},
        format="json",
        **headers,
    )
    altered = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True, "amount": "1.00", "gym_id": str(other_gym.id)},
        format="json",
        **headers,
    )
    foreign = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(other_owner)}",
    )
    colleague_response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(colleague)}",
    )
    assert refused.status_code == 400
    assert altered.status_code == 400
    assert foreign.status_code == 404
    assert colleague_response.status_code == 404
    assert _rows(gym) == (0, 0)
    confirmed = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["data"]["outcome"] == "committed"
    assert confirmed.json()["data"]["tool_activity"] == []
    assert "create_member" not in str(confirmed.json()["data"])
    assert confirmed.json()["data"]["pending_confirmation"] is None
    replay = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    assert replay.status_code == 200, replay.json()
    assert _rows(gym) == (1, 1)
    script.arguments = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "join_date": "2026-11-11",
        "confirmed": True,
    }
    second = client.post(
        "/api/v1/assistant/turns/",
        {
            "message": "Add Grace Hopper",
            "conversation_id": proposed.json()["data"]["conversation_id"],
        },
        format="json",
        **headers,
    )
    assert second.status_code == 200, second.json()
    second_step = second.json()["data"]["pending_confirmation"]["step_id"]
    rejected = client.post(
        f"/api/v1/assistant/confirmations/{second_step}/reject/",
        {},
        format="json",
        **headers,
    )
    assert rejected.status_code == 200, rejected.json()
    assert rejected.json()["data"]["outcome"] == "cancelled"
    assert rejected.json()["data"]["tool_activity"] == [
        {"name": "Create a member", "status": "cancelled"}
    ]
    assert "create_member" not in str(rejected.json()["data"])
    assert _rows(gym) == (1, 1)


@override_settings(**_settings())
def test_approval_confirmation_uses_the_stored_amount_and_rechecks_permission(
    monkeypatch,
) -> None:
    gym, owner = _owner("TN14", "tn14-owner@example.test")
    payment = _draft(gym, owner, "40.00")
    script = _Script()
    script.mode = "approve"
    script.arguments = {
        "payment_id": str(payment.id),
        "confirmed": True,
        "gym_id": str(uuid4()),
        "record_id": str(uuid4()),
        "amount": "1.00",
    }
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    def forbidden(**_kwargs: object) -> None:
        raise AssertionError("approval write ran before confirmation")

    monkeypatch.setattr("apps.assistant.turns.execute_confirmed_write", forbidden)
    request = Request(APIRequestFactory().post("/api/v1/assistant/turns/"))
    request.user = owner.user
    request.gym = gym
    request.gym_membership = owner
    request.permission_codes = frozenset({"payments.approve", "payments.view"})
    request.request_id = "req_turn"
    result = run_turn(
        request=request,
        view=APIView(),
        message="Approve the payment",
        conversation_id=None,
    )
    pending = result["pending_confirmation"]
    assert isinstance(pending, dict)
    approval = pending["approval"]
    assert isinstance(approval, dict)
    assert approval["record_id"] == str(payment.id)
    assert approval["amount"] == "40.00"
    assert _rows(gym) == (0, 0)
    request.permission_codes = frozenset()
    request.gym_membership.is_owner = False
    request.role_keys = frozenset()
    monkeypatch.setattr("apps.assistant.turns.execute_confirmed_write", execute_confirmed_write)
    with pytest.raises(PermissionDenied):
        confirm_step(request=request, view=APIView(), step_id=pending["step_id"])
    assert _rows(gym) == (0, 0)
    other_gym, other_owner = _owner("TN15", "tn15-owner@example.test")
    foreign = Request(APIRequestFactory().post("/api/v1/assistant/confirmations/confirm/"))
    foreign.user = other_owner.user
    foreign.gym = other_gym
    foreign.gym_membership = other_owner
    foreign.permission_codes = frozenset({"payments.approve"})
    with pytest.raises(Http404):
        confirm_step(request=foreign, view=APIView(), step_id=pending["step_id"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        goal = Goal.objects.get(id=pending["goal_id"])
    assert goal.status == Goal.Status.WAITING_CONFIRMATION
    assert goal.is_active is True


@override_settings(**_settings())
def test_confirmation_keeps_the_stored_member_name(monkeypatch) -> None:
    gym, owner = _owner("TN16", "tn16-owner@example.test")
    _enable(gym, "members")
    script = _Script()
    script.mode = "write"
    script.arguments = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "join_date": "2026-11-11",
        "confirmed": True,
        "gym_id": str(uuid4()),
    }
    real = turns_module.resolve_references
    swap = {"enabled": False}

    def wrapped(
        gym: Gym,
        conversation: object,
        payload: dict[str, object],
        **kwargs: object,
    ):
        resolved, labels, failure = real(gym, conversation, payload, **kwargs)  # type: ignore[arg-type]
        if swap["enabled"]:
            changed = dict(resolved)
            changed["first_name"] = "Swapped"
            return changed, labels, failure
        return resolved, labels, failure

    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    monkeypatch.setattr("apps.assistant.turns.resolve_references", wrapped)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    proposed = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Add Ada Lovelace"},
        format="json",
        **headers,
    )
    assert proposed.status_code == 200, proposed.json()
    step_id = proposed.json()["data"]["pending_confirmation"]["step_id"]
    swap["enabled"] = True
    confirmed = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        saved = Member.objects.get(gym=gym)
    assert saved.first_name == "Ada"
    assert saved.last_name == "Lovelace"


@override_settings(**_settings())
def test_unsupported_burmese_lookup_does_not_invent_or_call_the_model(monkeypatch) -> None:
    _gym, owner = _owner("TN17", "tn17-owner@example.test")

    def fail(**_kwargs: object) -> None:
        raise AssertionError("model called")

    monkeypatch.setattr("apps.assistant.turns.generate_content", fail)
    client = APIClient()
    response = client.post(
        "/api/v1/assistant/turns/",
        {"message": "Aung Aung ရဲ့ ဝင်ရောက်မှတ်တမ်း ပြပါ။ MEM-000101"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(owner)}",
    )

    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    body = data["message"]["body"]
    assert "မဖတ်နိုင်" in body
    assert "Aung Aung" in body
    assert "MEM-000101" in body
    assert "MEM-000102" not in body
    assert "ACTIVE" not in body
    assert "create_manual_check_in" not in body
    assert "Traceback" not in body
    assert data["pending_confirmation"] is None
    assert data["tool_activity"] == []


@override_settings(**_settings())
def test_staff_response_hides_tool_names_and_ungrounded_read_claims(monkeypatch) -> None:
    _gym, owner = _owner("TN18", "tn18-owner@example.test")
    script = _Script()
    script.mode = "invent"
    script.arguments = {"limit": 5, "confirmed": True, "gym_id": str(uuid4())}
    script.follow_up = (
        "Hla Hla paid 99999 on 1999-01-01 status POSTED. "
        'SELECT * FROM payments. Traceback (most recent call last). {"limit": 5}.'
    )
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    client = APIClient()
    response = client.post(
        "/api/v1/assistant/turns/",
        {"message": "List payments"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(owner)}",
    )

    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    rendered = str(data)
    assert data["tool_activity"] == [{"name": "Search payments in this gym", "status": "completed"}]
    assert set(data["tool_activity"][0]) == {"name", "status"}
    assert data["message"]["body"] == "No stored record matches that."
    for hidden in (
        "search_payments",
        "Hla",
        "99999",
        "1999-01-01",
        "POSTED",
        "SELECT",
        "Traceback",
        "traceback",
        "gym_id",
        '"limit"',
    ):
        assert hidden not in rendered
