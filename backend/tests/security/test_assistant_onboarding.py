"""New-member onboarding. The provider is mocked and the gym is disposable."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.assistant.provider import FunctionCall, ModelTurn
from apps.classes.models import ClassBooking
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan
from apps.tenants.models import Gym
from apps.trainers.models import PTSale
from tests.security.test_assistant_payment_reads import _owner
from tests.security.test_assistant_schedule import _enable
from tests.security.test_tenant_gym_settings import jwt_settings, staff_token

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
_FIXED = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
_OPENING = "လူတစ်ယောက်လာပြီး member လုပ်မယ်။class တက်မယ်။pt လုပ်မယ်တဲ့။ဘာတွေလုပ်ရမလဲ?"
_SUPPLIED = "Wint War, 13-11-2026, Monthly_plan ဝယ်မယ်။"


class _Clock:
    def __init__(self) -> None:
        self.step = 0

    def __call__(self) -> datetime:
        self.step += 1
        return _FIXED + timedelta(seconds=self.step)


def _settings() -> dict[str, str]:
    return {**jwt_settings(), **_ASSISTANT}


def _brief_value(instruction: str, key: str) -> str:
    marker = "Workflow state, already stored:"
    start = instruction.find(marker)
    if start < 0:
        return ""
    brief = instruction[start:].split("\n", 1)[0]
    match = re.search(rf"(?:^|; ){re.escape(key)}=([^;]+)", brief)
    return match.group(1).strip() if match else ""


def _latest(contents: list[dict[str, object]]) -> str:
    if not contents:
        return ""
    parts = contents[-1].get("parts")
    if not isinstance(parts, list):
        return ""
    return " ".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


class _Script:
    def __init__(self) -> None:
        self.calls = 0
        self.mode = "explain"
        self.member_id = ""
        self.plan_id = ""

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del declarations, transport
        self.calls += 1
        latest = _latest(contents)
        if latest.startswith("The change is saved"):
            if self.mode == "await_membership":
                member_id = self.member_id or _brief_value(system_instruction, "member_id")
                return _call(
                    "create_membership",
                    {
                        "member_id": member_id,
                        "plan_id": self.plan_id,
                        "start_date": "2026-01-01",
                    },
                    "အတည်ပြုပါ။",
                )
            if self.mode == "await_class":
                return _text("အတန်းနဲ့ PT က သီးခြားပါ။ ဘယ်အတန်းနဲ့ ဘယ်အချိန်လဲ။")
            return _text("ရပါပြီ။")
        if self.mode == "explain":
            return _text("အဖွဲ့ဝင်အသစ်အတွက် နာမည်နဲ့ စတင်ရက် လိုပါတယ်။ အတန်းနဲ့ PT ကို နောက်မှ သီးခြားလုပ်ပါမယ်။")
        if self.mode == "register":
            return _call(
                "create_member",
                {"first_name": "Wint", "last_name": "War", "join_date": "2026-01-01"},
                "သိမ်းပြီးပါပြီ။",
            )
        if self.mode == "register_english":
            return _call(
                "create_member",
                {"first_name": "Wint", "last_name": "War", "join_date": "2026-01-01"},
                "The member is saved.",
            )
        if self.mode in {"missing", "invalid", "again"}:
            return _call(
                "create_member",
                {"first_name": "Wint", "last_name": "War", "join_date": "2026-01-01"},
                "အတည်ပြုပါ။",
            )
        if self.mode == "class_later":
            return _text("ဘယ်အတန်းအချိန်ဇယားလဲ။ အဖွဲ့ဝင်အသစ် ထပ်မဖွင့်ပါ။")
        if self.mode == "pt_later":
            return _text("PT က သီးခြားပါ။ ဘယ်ပက်ကေ့နဲ့ ဘယ်သင်တန်းဆရာလဲ။")
        return _text("ဘယ်ရက်လဲ။")


def _call(name: str, arguments: dict[str, object], text: str) -> ModelTurn:
    return ModelTurn(
        text,
        (FunctionCall(name, arguments),),
        ({"functionCall": {"name": name, "args": arguments}}, {"text": text}),
    )


def _text(body: str) -> ModelTurn:
    return ModelTurn(body, (), ({"text": body},))


def _plan(gym: Gym, actor_id: object) -> MembershipPlan:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return MembershipPlan.objects.create(
            gym=gym,
            code="Monthly_plan",
            name="Monthly",
            duration_value=1,
            duration_unit="MONTH",
            price=Decimal("50000.00"),
            currency=gym.currency,
            is_active=True,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _post(
    client: APIClient, headers: dict[str, str], payload: dict[str, object]
) -> dict[str, object]:
    response = client.post("/api/v1/assistant/turns/", payload, format="json", **headers)
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _confirm(client: APIClient, headers: dict[str, str], step_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _reject(client: APIClient, headers: dict[str, str], step_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/reject/",
        {},
        format="json",
        **headers,
    )
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _members(gym: Gym) -> list[Member]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return list(Member.objects.filter(gym=gym).order_by("created_at", "id"))


def _counts(gym: Gym) -> tuple[int, int, int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            Member.objects.filter(gym=gym).count(),
            Membership.objects.filter(gym=gym).count(),
            ClassBooking.objects.filter(gym=gym).count(),
            PTSale.objects.filter(gym=gym).count(),
        )


def _gym(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> tuple[Gym, object, APIClient, dict[str, str]]:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner(code, f"{code.lower()}-owner@example.test")
    gym.timezone = "Asia/Yangon"
    gym.save(update_fields=["timezone"])
    _enable(gym, "members")
    _enable(gym, "memberships")
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    return gym, owner, client, headers


def _field(pending: dict[str, object], name: str) -> str:
    fields = pending.get("fields")
    if not isinstance(fields, list):
        return ""
    for field in fields:
        if isinstance(field, dict) and field.get("name") == name:
            value = field.get("value")
            return value if isinstance(value, str) else ""
    return ""


@override_settings(**_settings())
def test_the_reported_onboarding_keeps_13_november_2026(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner, client, headers = _gym(monkeypatch, "OB01")
    plan = _plan(gym, owner.id)
    script = _Script()
    script.plan_id = str(plan.id)
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    script.mode = "explain"
    opened = _post(client, headers, {"message": _OPENING})
    conversation = str(opened["conversation_id"])
    assert opened["pending_confirmation"] is None
    assert "စတင်ရက်" in str(opened["message"]["body"])
    assert _counts(gym) == (0, 0, 0, 0)

    script.mode = "register"
    proposed = _post(client, headers, {"message": _SUPPLIED, "conversation_id": conversation})
    pending = proposed["pending_confirmation"]
    body = str(proposed["message"]["body"])
    assert isinstance(pending, dict), proposed
    assert _field(pending, "join_date") == "2026-11-13"
    assert "Wint War" in body
    assert "Monthly_plan" in body
    assert "13 November 2026" in body
    assert "2026-11-13" in body
    assert "မသိမ်းရသေး" in body
    assert "ဘယ်ရက်လဲ" not in body
    assert "သိမ်းပြီး" not in body
    assert _counts(gym) == (0, 0, 0, 0)

    calls = script.calls
    for question in ("ဘယ်ရက်လဲ။", "Which date?", "ဘယ်ရက်လဲ။"):
        answered = _post(client, headers, {"message": question, "conversation_id": conversation})
        answer = str(answered["message"]["body"])
        assert script.calls == calls
        assert "2026-11-13" in answer
        assert "13 November 2026" in answer
        assert "ဘယ်ရက်လဲ" not in answer
        assert "Which date" not in answer
        assert answered["pending_confirmation"] is not None
        assert _counts(gym) == (0, 0, 0, 0)

    reloaded = client.get(f"/api/v1/assistant/conversations/{conversation}/", **headers)
    assert reloaded.status_code == 200, reloaded.json()
    stored = reloaded.json()["data"]
    assert stored["pending_confirmation"] is not None
    assert all(not item["body"].startswith("workflow-state v1") for item in stored["messages"])
    assert any("2026-11-13" in item["body"] for item in stored["messages"])

    script.mode = "await_membership"
    created = _confirm(client, headers, str(pending["step_id"]))
    assert created["outcome"] == "committed"
    members = _members(gym)
    assert len(members) == 1
    assert members[0].first_name == "Wint"
    assert members[0].last_name == "War"
    assert members[0].join_date.isoformat() == "2026-11-13"
    script.member_id = str(members[0].id)
    membership_pending = created["pending_confirmation"]
    assert isinstance(membership_pending, dict), created
    assert _field(membership_pending, "start_date") == "2026-11-13"
    assert _counts(gym) == (1, 0, 0, 0)

    replay = _confirm(client, headers, str(pending["step_id"]))
    assert replay["outcome"] == "existing"
    assert _counts(gym) == (1, 0, 0, 0)

    script.mode = "await_class"
    assigned = _confirm(client, headers, str(membership_pending["step_id"]))
    assert assigned["outcome"] == "committed"
    assigned_body = str(assigned["message"]["body"])
    assert "ဘယ်အတန်း" in assigned_body
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = Membership.objects.get(gym=gym)
    assert membership.member_id == members[0].id
    assert membership.plan_id == plan.id
    assert membership.plan_code == "Monthly_plan"
    assert membership.start_date.isoformat() == "2026-11-13"
    assert _counts(gym) == (1, 1, 0, 0)

    calls = script.calls
    again = _confirm(client, headers, str(membership_pending["step_id"]))
    assert again["outcome"] == "existing"
    assert script.calls == calls
    assert _counts(gym) == (1, 1, 0, 0)

    script.mode = "class_later"
    classe = _post(
        client,
        headers,
        {"message": "class တက်မယ်။ ဘယ်အချိန်လဲ မပြောရသေးပါ။", "conversation_id": conversation},
    )
    assert "အဖွဲ့ဝင်အသစ် ထပ်မဖွင့်ပါ" in str(classe["message"]["body"])
    assert _counts(gym) == (1, 1, 0, 0)

    script.mode = "pt_later"
    training = _post(
        client,
        headers,
        {"message": "pt လုပ်မယ်။ trainer မပြောရသေးပါ။", "conversation_id": conversation},
    )
    assert "ဘယ်ပက်ကေ့" in str(training["message"]["body"])
    assert _counts(gym) == (1, 1, 0, 0)


@override_settings(**_settings())
def test_an_english_date_is_kept_and_confirmation_does_not_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, _owner_row, client, headers = _gym(monkeypatch, "OB02")
    script = _Script()
    script.mode = "register_english"
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    proposed = _post(
        client,
        headers,
        {"message": "Wint War, 13/11/2026, Monthly_plan"},
    )
    pending = proposed["pending_confirmation"]
    body = str(proposed["message"]["body"])
    assert isinstance(pending, dict), proposed
    assert _field(pending, "join_date") == "2026-11-13"
    assert "Wint War" in body
    assert "13 November 2026" in body
    assert "not saved until you confirm" in body
    assert _counts(gym) == (0, 0, 0, 0)

    calls = script.calls
    answered = _post(
        client,
        headers,
        {"message": "Which date?", "conversation_id": proposed["conversation_id"]},
    )
    assert script.calls == calls
    assert "13 November 2026" in str(answered["message"]["body"])
    assert "Which date" not in str(answered["message"]["body"])

    created = _confirm(client, headers, str(pending["step_id"]))
    assert created["outcome"] == "committed"
    assert _members(gym)[0].join_date.isoformat() == "2026-11-13"
    replay = _confirm(client, headers, str(pending["step_id"]))
    assert replay["outcome"] == "existing"
    assert len(_members(gym)) == 1


@override_settings(**_settings())
def test_a_missing_date_is_asked_once_and_then_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, _owner_row, client, headers = _gym(monkeypatch, "OB03")
    script = _Script()
    script.mode = "missing"
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    asked = _post(client, headers, {"message": "Wint War, Monthly_plan ဝယ်မယ်။"})
    assert asked["pending_confirmation"] is None
    assert "ဘယ်ရက်လဲ" in str(asked["message"]["body"])
    assert _counts(gym) == (0, 0, 0, 0)

    script.mode = "again"
    proposed = _post(
        client,
        headers,
        {"message": "13-11-2026", "conversation_id": asked["conversation_id"]},
    )
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    assert _field(pending, "join_date") == "2026-11-13"
    assert "ဘယ်ရက်လဲ" not in str(proposed["message"]["body"])
    assert _counts(gym) == (0, 0, 0, 0)


@override_settings(**_settings())
def test_an_invalid_date_is_explained_and_rejection_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, _owner_row, client, headers = _gym(monkeypatch, "OB04")
    script = _Script()
    script.mode = "invalid"
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    refused = _post(client, headers, {"message": "Wint War, 32-11-2026, Monthly_plan ဝယ်မယ်။"})
    assert refused["pending_confirmation"] is None
    body = str(refused["message"]["body"])
    assert "32-11-2026" in body
    assert "မှန်ကန်တဲ့ ရက်မဟုတ်ပါ" in body
    assert _counts(gym) == (0, 0, 0, 0)

    script.mode = "register"
    proposed = _post(
        client,
        headers,
        {
            "message": "Wint War, 13-11-2026, Monthly_plan ဝယ်မယ်။",
            "conversation_id": refused["conversation_id"],
        },
    )
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    rejected = _reject(client, headers, str(pending["step_id"]))
    assert rejected["outcome"] == "cancelled"
    assert _counts(gym) == (0, 0, 0, 0)
    kept = _post(
        client,
        headers,
        {"message": "ဘယ်ရက်လဲ။", "conversation_id": refused["conversation_id"]},
    )
    assert "2026-11-13" in str(kept["message"]["body"])
    assert _counts(gym) == (0, 0, 0, 0)
