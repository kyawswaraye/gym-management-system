"""Database-backed member workflow. The provider is mocked and the gym is disposable."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.test import override_settings
from django.utils import timezone as django_timezone
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.assistant.configuration import ProviderFailure
from apps.assistant.executor import ReadCall
from apps.assistant.membership_reads import search_membership_plans
from apps.assistant.models import Message
from apps.assistant.provider import FunctionCall, ModelTurn, ProviderCallError
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan
from apps.tenants.models import Gym
from tests.security.test_assistant_payment_reads import _owner
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
_FIXED = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)


class _Clock:
    """Gym day stays 2026-11-12 while each timestamp stays ordered."""

    def __init__(self) -> None:
        self.step = 0

    def __call__(self) -> datetime:
        self.step += 1
        return _FIXED + timedelta(seconds=self.step)


def _settings() -> dict[str, str]:
    return {**jwt_settings(), **_ASSISTANT}


def _latest_parts(contents: list[dict[str, object]]) -> list[object]:
    if not contents:
        return []
    parts = contents[-1].get("parts")
    return parts if isinstance(parts, list) else []


def _responded(contents: list[dict[str, object]]) -> bool:
    return any(
        isinstance(part, dict) and "functionResponse" in part for part in _latest_parts(contents)
    )


class _Script:
    def __init__(self) -> None:
        self.mode = "text"
        self.member_id = ""
        self.plan_id = ""
        self.inactive_id = ""
        self.calls = 0

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del declarations, system_instruction, transport
        self.calls += 1
        latest = " ".join(
            part["text"]
            for part in _latest_parts(contents)
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if latest.startswith("The change is saved"):
            raise ProviderCallError(ProviderFailure.QUOTA)
        if self.mode == "fail":
            raise ProviderCallError(ProviderFailure.QUOTA)
        if _responded(contents):
            if self.mode in {"assign", "tomorrow"}:
                call = FunctionCall(
                    "create_membership",
                    {
                        "member_id": self.member_id,
                        "plan_id": self.plan_id,
                        "start_date": "2026-11-12" if self.mode == "tomorrow" else "2026-01-01",
                    },
                )
            elif self.mode == "inactive":
                call = FunctionCall(
                    "create_membership",
                    {
                        "member_id": self.member_id,
                        "plan_id": self.inactive_id,
                        "start_date": "2026-11-12",
                    },
                )
            else:
                return ModelTurn("ရပါပြီ။", (), ({"text": "ရပါပြီ။"},))
            return ModelTurn(
                "အတည်ပြုပါ။",
                (call,),
                (
                    {"functionCall": {"name": call.name, "args": call.arguments}},
                    {"text": "အတည်ပြုပါ။"},
                ),
            )
        if self.mode == "create":
            call = FunctionCall(
                "create_member",
                {
                    "first_name": "Kyaw",
                    "last_name": "Swar",
                    "join_date": "2026-01-01",
                },
            )
        elif self.mode in {"assign", "inactive", "tomorrow"}:
            call = FunctionCall("search_membership_plans", {"name": "Yoga"})
        elif self.mode == "second":
            call = FunctionCall(
                "create_member",
                {
                    "first_name": "Kyaw",
                    "last_name": "Swar",
                    "join_date": "2026-11-12",
                },
            )
        else:
            return ModelTurn("ရပါပြီ။", (), ({"text": "ရပါပြီ။"},))
        return ModelTurn(
            "အတည်ပြုပါ။",
            (call,),
            (
                {"functionCall": {"name": call.name, "args": call.arguments}},
                {"text": "အတည်ပြုပါ။"},
            ),
        )


def _plans(gym: Gym, actor_id: object) -> tuple[MembershipPlan, MembershipPlan]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        active = MembershipPlan.objects.create(
            gym=gym,
            code="YOGA",
            name="Yoga",
            duration_value=1,
            duration_unit="MONTH",
            price=Decimal("50000.00"),
            currency=gym.currency,
            is_active=True,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )
        inactive = MembershipPlan.objects.create(
            gym=gym,
            code="YOGA_2",
            name="Yoga",
            duration_value=1,
            duration_unit="MONTH",
            price=Decimal("40000.00"),
            currency=gym.currency,
            is_active=False,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )
    return active, inactive


def _members(gym: Gym) -> list[Member]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return list(Member.objects.filter(gym=gym).order_by("created_at", "id"))


def _post(
    client: APIClient,
    headers: dict[str, str],
    payload: dict[str, object],
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


@override_settings(**_settings())
def test_kyaw_swar_membership_survives_a_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner("WF01", "wf01-owner@example.test")
    gym.timezone = "Asia/Yangon"
    gym.save(update_fields=["timezone"])
    _enable(gym, "members")
    _enable(gym, "memberships")
    active, inactive = _plans(gym, owner.id)
    script = _Script()
    script.plan_id = str(active.id)
    script.inactive_id = str(inactive.id)
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}

    script.mode = "create"
    proposed = _post(client, headers, {"message": "Kyaw Swar ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်"})
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    step_id = str(pending["step_id"])
    conversation_id = str(proposed["conversation_id"])
    assert _members(gym) == []

    created = _confirm(client, headers, step_id)
    assert created["outcome"] == "committed"
    body = created["message"]["body"]
    assert isinstance(body, str)
    members = _members(gym)
    assert len(members) == 1
    member = members[0]
    assert member.first_name == "Kyaw"
    assert member.last_name == "Swar"
    assert member.join_date.isoformat() == "2026-11-12"
    assert member.member_number in body
    script.member_id = str(member.id)

    replay = _confirm(client, headers, step_id)
    assert replay["outcome"] == "existing"
    assert len(_members(gym)) == 1

    script.mode = "fail"
    failed = _post(client, headers, {"message": "ဆက်ပြောပါ", "conversation_id": conversation_id})
    failed_body = failed["message"]["body"]
    assert isinstance(failed_body, str)
    assert member.member_number in failed_body
    assert "The assistant is unavailable" not in failed_body
    assert "ဘာမှ အသစ် မသိမ်းရသေးပါ" not in failed_body
    assert len(_members(gym)) == 1

    calls_before_next = script.calls
    nxt = _post(client, headers, {"message": "ဘာဆက်လုပ်ရမလဲ", "conversation_id": conversation_id})
    assert script.calls == calls_before_next
    next_body = nxt["message"]["body"]
    assert isinstance(next_body, str)
    assert member.member_number in next_body
    assert "အသင်းဝင်" in next_body

    script.mode = "inactive"
    refused = _post(
        client,
        headers,
        {"message": "YOGA_2 ကို တပ်ပေး", "conversation_id": conversation_id},
    )
    refused_body = refused["message"]["body"]
    assert isinstance(refused_body, str)
    assert "အသုံးမပြုနိုင်ပါ" in refused_body
    assert refused["pending_confirmation"] is None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Membership.objects.filter(gym=gym).count() == 0

    sale = search_membership_plans(
        ReadCall(
            gym=gym,
            actor=owner,
            payload={"name": "Yoga", "eligible_for_sale": True, "limit": 25},
            request_id="req_plans",
            ip_address=None,
            user_agent="",
            permission_codes=frozenset({"memberships.view"}),
        )
    )
    codes = [row["code"] for row in sale["results"]]  # type: ignore[index]
    assert codes == ["YOGA"]
    catalog = search_membership_plans(
        ReadCall(
            gym=gym,
            actor=owner,
            payload={"name": "Yoga", "limit": 25},
            request_id="req_catalog",
            ip_address=None,
            user_agent="",
            permission_codes=frozenset({"memberships.view"}),
        )
    )
    catalog_rows = {row["code"]: row["status"] for row in catalog["results"]}  # type: ignore[index]
    assert catalog_rows == {"YOGA": "ACTIVE", "YOGA_2": "INACTIVE"}

    script.mode = "assign"
    assignment = _post(
        client,
        headers,
        {"message": "YOGA အစီအစဉ်ကို ဒီနေ့ တပ်ပေး", "conversation_id": conversation_id},
    )
    assignment_pending = assignment["pending_confirmation"]
    assert isinstance(assignment_pending, dict), assignment
    assigned = _confirm(client, headers, str(assignment_pending["step_id"]))
    assert assigned["outcome"] == "committed"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = Membership.objects.select_related("member").get(gym=gym)
    assert membership.member_id == member.id
    assert membership.plan_id == active.id
    assert membership.start_date.isoformat() == "2026-11-12"
    assigned_body = assigned["message"]["body"]
    assert isinstance(assigned_body, str)
    assert "Yoga" in assigned_body or "YOGA" in assigned_body

    calls_before_number = script.calls
    number = _post(
        client,
        headers,
        {"message": "Kyaw Swar ရဲ့ အဖွဲ့ဝင်နံပါတ် ပြပါ", "conversation_id": conversation_id},
    )
    assert script.calls == calls_before_number
    number_body = number["message"]["body"]
    assert isinstance(number_body, str)
    assert member.member_number in number_body

    reloaded = APIClient()
    detail = reloaded.get(
        f"/api/v1/assistant/conversations/{conversation_id}/",
        **headers,
    )
    assert detail.status_code == 200, detail.json()
    stored = detail.json()["data"]["messages"]
    assert any(member.member_number in item["body"] for item in stored)
    assert all(not item["body"].startswith("workflow-state v1") for item in stored)

    script.mode = "second"
    another = _post(
        client,
        headers,
        {"message": "နောက်ထပ် Kyaw Swar တစ်ယောက် ဖွင့်ချင်တယ်", "conversation_id": conversation_id},
    )
    another_pending = another["pending_confirmation"]
    assert isinstance(another_pending, dict), another
    _confirm(client, headers, str(another_pending["step_id"]))
    both = _members(gym)
    assert len(both) == 2
    assert both[0].member_number != both[1].member_number
    assert {row.first_name + " " + row.last_name for row in both} == {"Kyaw Swar"}


@override_settings(**_settings())
def test_tomorrow_membership_starts_the_next_gym_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """ဒီနေ့ is the join date. မနက်ဖြန်စမယ် is the membership start, one day later."""

    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner("WF04", "wf04-owner@example.test")
    gym.timezone = "Asia/Yangon"
    gym.save(update_fields=["timezone"])
    _enable(gym, "members")
    _enable(gym, "memberships")
    active, _inactive = _plans(gym, owner.id)
    script = _Script()
    script.plan_id = str(active.id)
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}

    script.mode = "create"
    proposed = _post(client, headers, {"message": "Kyaw Swar ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်"})
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    created = _confirm(client, headers, str(pending["step_id"]))
    assert created["outcome"] == "committed"
    member = _members(gym)[0]
    assert member.join_date.isoformat() == "2026-11-12"
    script.member_id = str(member.id)

    script.mode = "tomorrow"
    assignment = _post(
        client,
        headers,
        {
            "message": "YOGA ကို မနက်ဖြန်စမယ်",
            "conversation_id": proposed["conversation_id"],
        },
    )
    assignment_pending = assignment["pending_confirmation"]
    assert isinstance(assignment_pending, dict), assignment
    shown = {
        field["name"]: field["value"]
        for field in assignment_pending["fields"]
        if isinstance(field, dict)
    }
    assert shown.get("start_date") == "2026-11-13"
    assigned = _confirm(client, headers, str(assignment_pending["step_id"]))
    assert assigned["outcome"] == "committed"
    body = assigned["message"]["body"]
    assert isinstance(body, str)
    assert "2026-11-13" in body
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = Membership.objects.select_related("member", "plan").get(gym=gym)
    assert membership.member_id == member.id
    assert membership.plan_id == active.id
    assert membership.start_date.isoformat() == "2026-11-13"
    assert membership.expiry_date.isoformat() == "2026-12-12"
    assert membership.status == Membership.Status.PENDING
    assert membership.revenue_source == Membership.RevenueSource.NEW_MEMBER


@override_settings(**_settings())
def test_a_pending_write_stays_inside_its_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner("WF02", "wf02-owner@example.test")
    _enable(gym, "members")
    other = add_staff(gym, email="wf02-desk@example.test", role_key="RECEPTIONIST")
    script = _Script()
    script.mode = "create"
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    proposed = _post(client, headers, {"message": "Kyaw Swar ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်"})
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    step_id = str(pending["step_id"])
    assert _members(gym) == []

    script.mode = "text"
    other_turn = _post(client, headers, {"message": "မင်္ဂလာပါ"})
    assert other_turn["conversation_id"] != proposed["conversation_id"]
    assert other_turn["pending_confirmation"] is None
    assert _members(gym) == []

    hidden = client.get(
        f"/api/v1/assistant/conversations/{proposed['conversation_id']}/",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(other)}",
    )
    assert hidden.status_code == 404

    foreign = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {staff_token(other)}",
    )
    assert foreign.status_code == 404
    assert _members(gym) == []

    _confirm(client, headers, step_id)
    created = _members(gym)
    assert len(created) == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        other_messages = Message.objects.filter(
            gym=gym,
            conversation_id=other_turn["conversation_id"],
        )
        assert other_messages.exists()
        assert not other_messages.filter(body__contains=created[0].member_number).exists()


@override_settings(**_settings())
def test_assistant_turns_are_limited_by_membership_and_gym(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner("WF03", "wf03-owner@example.test")
    _enable(gym, "members")
    other = add_staff(gym, email="wf03-desk@example.test", role_key="RECEPTIONIST")
    script = _Script()
    script.mode = "text"
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    membership_limited = _throttle_rates(assistant_turn="2/60", assistant_gym_turn="20/60")
    gym_limited = _throttle_rates(assistant_turn="20/60", assistant_gym_turn="2/60")
    client = APIClient()
    owner_headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    other_headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(other)}"}
    cache.clear()
    try:
        with override_settings(REST_FRAMEWORK=membership_limited):
            api_settings.reload()
            for _attempt in range(2):
                allowed = _greeting(client, owner_headers)
                assert allowed.status_code == 200, allowed.json()
            limited = _greeting(client, owner_headers)
            assert limited.status_code == 429
            colleague = _greeting(client, other_headers)
            assert colleague.status_code == 200, colleague.json()
        cache.clear()
        with override_settings(REST_FRAMEWORK=gym_limited):
            api_settings.reload()
            for _attempt in range(2):
                allowed = _greeting(client, owner_headers)
                assert allowed.status_code == 200, allowed.json()
            blocked = _greeting(client, other_headers)
            assert blocked.status_code == 429
    finally:
        api_settings.reload()
        cache.clear()
    assert _members(gym) == []


def _throttle_rates(**rates: str) -> dict[str, object]:
    framework = {**settings.REST_FRAMEWORK}
    framework["DEFAULT_THROTTLE_RATES"] = {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        **rates,
    }
    return framework


def _greeting(client: APIClient, headers: dict[str, str]) -> object:
    return client.post(
        "/api/v1/assistant/turns/",
        {"message": "မင်္ဂလာပါ"},
        format="json",
        **headers,
    )
