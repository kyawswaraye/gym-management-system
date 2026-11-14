"""PT sale package identity and short Burmese replies.

Scripted tests prove the server stores the package id and how it reads a short
reply. They do not prove that live Gemini selects the sale tool.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone as django_timezone

from apps.assistant.models import Goal, Message, WorkflowStep
from apps.assistant.records import append_message, open_conversation, stored_payload
from apps.tenants.models import GymMembership, StaffProfile
from apps.trainers.models import PTSale, ServicePackage
from apps.trainers.services import create_assignment, create_service_package
from tests.security.test_assistant_pt_regression import (
    _FIXED,
    _Clock,
    _confirm,
    _post,
    _reject,
    _Script,
    _settings,
    _world,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_STABLE: dict[str, str] | None = None


def _stable() -> dict[str, str]:
    global _STABLE
    if _STABLE is None:
        _STABLE = _settings()
    return _STABLE


@pytest.fixture
def configured():
    with override_settings(**_stable()):
        yield


_AFFIRM = ("ထည့်မယ်", "အင်း", "ဟုတ်တယ်", "ရတယ်", "လုပ်ပါ")
_DECLINE = ("မထည့်တော့ဘူး", "မလုပ်တော့ဘူး", "မဟုတ်ဘူး")


def _prepare(monkeypatch: pytest.MonkeyPatch, code: str):
    from django.db import transaction

    from apps.common.db.tenant_context import set_gym_context

    gym, client, member, headers = _world(monkeypatch, code)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        owner = GymMembership.objects.get(gym=gym, user__email=f"{code.lower()}-owner@example.test")
        trainer = StaffProfile.objects.get(gym=gym, staff_code=f"{code}SOE")
        package = ServicePackage.objects.get(gym=gym, code="PT3")
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=trainer.id,
            member_id=member.id,
            effective_from=date(2026, 11, 1),
            effective_until=None,
            reason="Package link test",
            request_id=f"req_{code.lower()}_assign",
            ip_address=None,
            user_agent="",
        )
    return gym, client, member, headers, owner, trainer, package


def _sales(gym) -> list[PTSale]:
    from django.db import transaction

    from apps.common.db.tenant_context import set_gym_context

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return list(PTSale.objects.filter(gym=gym).select_related("service_package"))


def _sale_arguments(member, trainer, gym, description: str) -> dict[str, object]:
    return {
        "member_id": str(member.id),
        "trainer_staff_profile_id": str(trainer.id),
        "service_date": "2026-11-12",
        "description": description,
        "gross_fee": "100.00",
        "currency": gym.currency,
    }


def _package_on_step(gym, step_id: str) -> str:
    from django.db import transaction

    from apps.common.db.tenant_context import set_gym_context

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step = WorkflowStep.objects.get(gym=gym, id=step_id)
        payload = stored_payload(step)
    value = payload.get("service_package_id")
    assert isinstance(value, str) and value
    return value


def _seed(owner, gym, question: str) -> str:
    conversation = open_conversation(gym=gym, actor=owner, title="package question")
    append_message(
        gym=gym,
        conversation=conversation,
        actor=None,
        role=Message.Role.ASSISTANT,
        body=question,
    )
    return str(conversation.id)


@pytest.mark.django_db(transaction=True)
def test_scripted_pt_sale_stores_the_selected_package(
    monkeypatch: pytest.MonkeyPatch,
    configured: None,
) -> None:
    from django.test import override_settings

    gym, client, member, headers, _owner, trainer, package = _prepare(monkeypatch, "PK01")
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    assert _sales(gym) == []
    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    with override_settings(**_stable()):
        opened = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
    pending = opened["pending_confirmation"]
    assert isinstance(pending, dict), opened["message"]
    assert _package_on_step(gym, str(pending["step_id"])) == str(package.id)
    assert any("PT3" in str(field.get("value")) for field in pending["fields"])
    with override_settings(**_stable()):
        status, saved = _confirm(client, headers, str(pending["step_id"]))
        status_again, again = _confirm(client, headers, str(pending["step_id"]))
    assert status == 200 and saved.get("outcome") in {"committed", "existing"}
    assert status_again == 200 and again.get("outcome") == "existing"
    sales = _sales(gym)
    assert len(sales) == 1
    assert sales[0].service_package_id == package.id
    assert sales[0].gym_id == gym.id
    assert sales[0].service_package is not None
    assert sales[0].service_package.gym_id == gym.id
    assert sales[0].service_package.code == "PT3"

    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    with override_settings(**_stable()):
        rejected_turn = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
        rejected_pending = rejected_turn["pending_confirmation"]
        assert isinstance(rejected_pending, dict)
        reject_status, rejected = _reject(client, headers, str(rejected_pending["step_id"]))
    assert reject_status == 200 and rejected.get("outcome") == "cancelled"
    assert len(_sales(gym)) == 1

    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "Three sessions"),
        text="အတည်ပြုပါ။",
    )
    script.push_text("ဘယ်ပက်ကေ့လဲ။")
    with override_settings(**_stable()):
        described = _post(client, headers, "ဒီနေ့ session ရောင်းပေး။ စုစုပေါင်း 100။")
    assert described["pending_confirmation"] is None
    assert "ဘယ်ပက်ကေ့လဲ" in str(described["message"]["body"])
    assert len(_sales(gym)) == 1


@pytest.mark.django_db(transaction=True)
def test_scripted_short_burmese_replies_follow_the_open_operation(
    monkeypatch: pytest.MonkeyPatch,
    configured: None,
) -> None:
    from django.core.cache import cache
    from django.db import transaction
    from django.test import override_settings

    from apps.common.db.tenant_context import set_gym_context

    gym, client, member, headers, owner, trainer, package = _prepare(monkeypatch, "PK02")
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    with override_settings(**_stable()):
        alone = _post(client, headers, "ထည့်မယ်")
    assert alone["pending_confirmation"] is None
    assert script.traces
    assert _sales(gym) == []

    for phrase in _AFFIRM:
        script.push_call(
            "create_pt_sale",
            _sale_arguments(member, trainer, gym, "session fee"),
            text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
        )
        conversation = _seed(owner, gym, "PT3 package ထည့်ပေးရမလား။")
        with override_settings(**_stable()):
            answered = _post(client, headers, phrase, conversation)
        pending = answered["pending_confirmation"]
        assert isinstance(pending, dict), (phrase, answered["message"])
        assert script.traces[-1]["create_pt_sale"] is True
        assert "ရှာကြည့်လို့ ရပါတယ်" not in str(answered["message"]["body"])
        assert _package_on_step(gym, str(pending["step_id"])) == str(package.id)
        with override_settings(**_stable()):
            _reject(client, headers, str(pending["step_id"]))
        assert _sales(gym) == []

    conversation = _seed(owner, gym, "PT3 package ထည့်ပေးရမလား။")
    script.push_text("ရက်ကို မှတ်ထားပါတယ်။")
    with override_settings(**_stable()):
        dated = _post(client, headers, "ဒီနေ့", conversation)
    assert script.traces[-1]["create_pt_sale"] is True
    assert "ရှာကြည့်လို့ ရပါတယ်" not in str(dated["message"]["body"])

    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    with override_settings(**_stable()):
        proposed = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
        pending = proposed["pending_confirmation"]
        assert isinstance(pending, dict)
        script.push_call("cancel_pending_confirmation", {})
        script.push_text("မသိမ်းပါ။")
        declined = _post(client, headers, "မလုပ်တော့ဘူး", str(proposed["conversation_id"]))
    assert declined["pending_confirmation"] is None
    assert "မသိမ်းပါ" in str(declined["message"]["body"])
    assert _sales(gym) == []

    cache.clear()
    for phrase in ("မထည့်တော့ဘူး", "မဟုတ်ဘူး"):
        script.push_text("စောင့်နေတဲ့ ပြောင်းလဲမှု မရှိပါ။")
        with override_settings(**_stable()):
            idle = _post(client, headers, phrase)
        assert "စောင့်နေတဲ့ ပြောင်းလဲမှု မရှိပါ" in str(idle["message"]["body"])
        assert _sales(gym) == []

    mixed = _seed(owner, gym, "ပက်ကေ့ ရောင်းမလား၊ ဘိုကင် လုပ်မလား။")
    script.push_text("ဘယ်အရာကို ဆက်လုပ်မလဲ။")
    with override_settings(**_stable()):
        clarify = _post(client, headers, "လုပ်ပါ", mixed)
    assert "ဘယ်အရာကို ဆက်လုပ်မလဲ" in str(clarify["message"]["body"])
    assert clarify["pending_confirmation"] is None

    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    with override_settings(**_stable()):
        waiting = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
    card = waiting["pending_confirmation"]
    assert isinstance(card, dict)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        goal = Goal.objects.get(gym=gym, id=card["goal_id"])
        goal.expires_at = _FIXED - timedelta(minutes=1)
        goal.save(update_fields=["expires_at"])
    script.push_text("ဒီအတည်ပြုချက် သက်တမ်းကုန်ပါပြီ။ ဘာမှ မသိမ်းပါ။")
    with override_settings(**_stable()):
        expired = _post(client, headers, "အင်း", str(waiting["conversation_id"]))
    assert expired["pending_confirmation"] is None
    assert "သက်တမ်းကုန်" in str(expired["message"]["body"])
    assert _sales(gym) == []

    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    script.push_text("သိမ်းပြီးပါပြီ။")
    with override_settings(**_stable()):
        done = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
        done_card = done["pending_confirmation"]
        assert isinstance(done_card, dict)
        status, _saved = _confirm(client, headers, str(done_card["step_id"]))
        again = _post(client, headers, "လုပ်ပါ", str(done["conversation_id"]))
    assert status == 200
    assert len(_sales(gym)) == 1
    assert again["pending_confirmation"] is None
    assert len(_sales(gym)) == 1


@pytest.mark.django_db(transaction=True)
def test_scripted_ambiguous_package_is_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
    configured: None,
) -> None:
    from decimal import Decimal

    from django.db import transaction
    from django.test import override_settings

    from apps.common.db.tenant_context import set_gym_context

    gym, client, member, headers, owner, trainer, _package = _prepare(monkeypatch, "PK03")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_service_package(
            gym=gym,
            actor=owner,
            package_type=ServicePackage.PackageType.PT,
            code="PT8",
            name="Eight sessions",
            session_count=8,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id="req_pk03_pt8",
            ip_address=None,
            user_agent="",
        )
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="အတည်ပြုပါ။",
    )
    script.push_text("ဘယ်ဟာလဲ။")
    with override_settings(**_stable()):
        opened = _post(
            client,
            headers,
            "PT3 နဲ့ PT8 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။",
        )
    assert opened["pending_confirmation"] is None
    assert "ဘယ်ဟာလဲ" in str(opened["message"]["body"])
    assert _sales(gym) == []


@pytest.mark.django_db(transaction=True)
def test_confirm_rejects_a_package_from_outside_the_sale(
    monkeypatch: pytest.MonkeyPatch,
    configured: None,
) -> None:
    from django.db import transaction
    from django.test import override_settings

    from apps.common.db.tenant_context import set_gym_context

    gym, client, member, headers, _owner, trainer, _package = _prepare(monkeypatch, "PK04")
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)
    script.push_call(
        "create_pt_sale",
        _sale_arguments(member, trainer, gym, "session fee"),
        text="ပက်ကေ့ အရောင်းကို အတည်ပြုပါ။",
    )
    with override_settings(**_stable()):
        opened = _post(
            client,
            headers,
            "PT3 ပက်ကေ့ ရောင်းပေး။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
        )
    pending = opened["pending_confirmation"]
    assert isinstance(pending, dict)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step = WorkflowStep.objects.get(gym=gym, id=pending["step_id"])
        raw = dict(step.required_input)
        payload = dict(raw["payload"])
        payload["service_package_id"] = str(uuid4())
        raw["payload"] = payload
        step.required_input = raw
        step.save(update_fields=["required_input", "updated_at"])
    monkeypatch.setattr(django_timezone, "now", _Clock())
    with override_settings(**_stable()):
        status, body = _confirm(client, headers, str(pending["step_id"]))
    assert status != 200
    assert _sales(gym) == []
    assert "ပက်ကေ့" in str(body)


def _load_gemini_env() -> None:
    """Read the configured model from the repo env file. Do not log the key."""

    if os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL"):
        return
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"GEMINI_API_KEY", "GEMINI_MODEL", "ASSISTANT_ENABLED"} and not os.environ.get(
            key
        ):
            os.environ[key] = value.strip().strip('"').strip("'")


def test_live_gemini_package_link_and_short_reply(
    monkeypatch: pytest.MonkeyPatch,
    configured: None,
) -> None:
    """Live Gemini only. A skipped or mocked run is not this result."""

    if os.environ.get("ASSISTANT_LIVE_GEMINI") != "1":
        pytest.skip("Set ASSISTANT_LIVE_GEMINI=1 to call the configured Gemini model")
    _load_gemini_env()
    key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "")
    if len(key) < 20 or not model:
        pytest.skip("NOT VERIFIED: GEMINI_API_KEY or GEMINI_MODEL is missing")
    from django.test import override_settings

    gym, client, _member, headers, owner, _trainer, package = _prepare(monkeypatch, "PK05")
    before = len(_sales(gym))
    with override_settings(GEMINI_API_KEY=key, GEMINI_MODEL=model, ASSISTANT_ENABLED="true"):
        opened = _post(
            client,
            headers,
            "ဝင့်ဝါကို PT3 ပက်ကေ့ ရောင်းပေး။ သင်တန်းဆရာ Soe Soe။ ဝန်ဆောင်မှုရက် ဒီနေ့။ စုစုပေါင်း 100။",
        )
        pending = opened.get("pending_confirmation")
        if not isinstance(pending, dict):
            opened = _post(
                client,
                headers,
                "PT3 ပက်ကေ့ပဲ။ ဝန်ဆောင်မှုရက် 2026-11-12။ စုစုပေါင်း 100။",
                str(opened["conversation_id"]),
            )
            pending = opened.get("pending_confirmation")
        assert isinstance(pending, dict), opened["message"]
        assert _package_on_step(gym, str(pending["step_id"])) == str(package.id)
        status, saved = _confirm(client, headers, str(pending["step_id"]))
        status_again, again = _confirm(client, headers, str(pending["step_id"]))
        assert status == 200 and saved.get("outcome") in {"committed", "existing"}, saved
        assert status_again == 200 and again.get("outcome") == "existing"
        sales = _sales(gym)
        assert len(sales) == before + 1
        assert sales[0].service_package_id == package.id
        assert sales[0].gym_id == gym.id
        assert sales[0].service_package is not None
        assert sales[0].service_package.gym_id == gym.id
        assert sales[0].service_package.code == "PT3"
        question = _seed(
            owner,
            gym,
            "PT3 package ကို ဝင့်ဝါ၊ Soe Soe၊ ဒီနေ့၊ ကြေး 100 နဲ့ ထည့်ပေးရမလား။",
        )
        short = _post(client, headers, "ထည့်မယ်", question)
        if "အသုံးမပြုနိုင်ပါ" in str(short["message"]["body"]):
            question = _seed(
                owner,
                gym,
                "PT3 package ကို ဝင့်ဝါ၊ Soe Soe၊ ဒီနေ့၊ ကြေး 100 နဲ့ ထည့်ပေးရမလား။",
            )
            short = _post(client, headers, "ထည့်မယ်", question)
        short_pending = short.get("pending_confirmation")
        if not isinstance(short_pending, dict) and "အသုံးမပြုနိုင်ပါ" not in str(
            short["message"]["body"]
        ):
            short = _post(
                client,
                headers,
                "ဝင့်ဝါ၊ Soe Soe၊ ဒီနေ့၊ 100",
                question,
            )
            short_pending = short.get("pending_confirmation")
        declined = _post(client, headers, "မလုပ်တော့ဘူး", question)
        assert "ရှာကြည့်လို့ ရပါတယ်" not in str(short["message"]["body"])
        assert isinstance(short_pending, dict), short["message"]
        assert _package_on_step(gym, str(short_pending["step_id"])) == str(package.id)
        assert declined["pending_confirmation"] is None
        assert len(_sales(gym)) == before + 1
