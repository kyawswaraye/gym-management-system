"""A question during registration must not drop the member, date, or plan."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from django.db import transaction
from django.test import override_settings

from apps.assistant.provider import FunctionCall, ModelTurn
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym
from apps.trainers.models import ServicePackage
from tests.security.test_assistant_onboarding import (
    _counts,
    _field,
    _gym,
    _plan,
    _post,
    _settings,
)
from tests.security.test_assistant_schedule import _enable

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_REGISTER = "Register a new member named Wint War, starting 13-11-2026."
_PRICE = "How much is the Monthly Plan?"
_PACKAGE = "What about the PT package?"
_RESUME = "Okay, register her and sell the Monthly Plan."


def _responds(contents: list[dict[str, object]]) -> bool:
    if not contents:
        return False
    parts = contents[-1].get("parts")
    return isinstance(parts, list) and any(
        isinstance(part, dict) and "functionResponse" in part for part in parts
    )


class _Script:
    def __init__(self) -> None:
        self.calls = 0
        self.phase = "register"

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
        if self.phase == "price":
            if _responds(contents):
                return _text("The price is 1.00.")
            return _call("search_membership_plans", {"code": "Monthly_plan"}, "")
        if self.phase == "package":
            return _text("The package is Fantasy Pack for 99999.")
        if self.phase == "resume":
            return _call(
                "create_member",
                {"first_name": "Wint", "last_name": "War", "join_date": "2026-01-01"},
                "သိမ်းပြီးပါပြီ။",
            )
        return _text("Wint War can be registered when you are ready.")


def _call(name: str, arguments: dict[str, object], text: str) -> ModelTurn:
    return ModelTurn(
        text,
        (FunctionCall(name, arguments),),
        ({"functionCall": {"name": name, "args": arguments}}, {"text": text}),
    )


def _text(body: str) -> ModelTurn:
    return ModelTurn(body, (), ({"text": body},))


def _package(gym: Gym, actor_id: object) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ServicePackage.objects.create(
            gym=gym,
            type=ServicePackage.PackageType.PT,
            code="PT_MORNING",
            name="Morning PT",
            normalized_name="MORNING PT",
            session_count=8,
            validity_value=1,
            validity_unit=ServicePackage.ValidityUnit.MONTH,
            price=Decimal("80000.00"),
            currency=gym.currency,
            is_active=True,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


@override_settings(**_settings())
def test_a_price_and_package_question_keeps_the_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, client, headers = _gym(monkeypatch, "SQ01")
    _enable(gym, "members")
    _enable(gym, "memberships")
    _plan(gym, owner.id)
    _package(gym, owner.id)
    script = _Script()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    opened = _post(client, headers, {"message": _REGISTER})
    conversation = str(opened["conversation_id"])
    assert opened["pending_confirmation"] is None
    assert _counts(gym) == (0, 0, 0, 0)

    script.phase = "price"
    calls = script.calls
    priced = _post(client, headers, {"message": _PRICE, "conversation_id": conversation})
    price_body = str(priced["message"]["body"])
    assert script.calls > calls
    assert "50000" in price_body
    assert "1.00" not in price_body
    assert "Monthly_plan" in price_body
    assert priced["pending_confirmation"] is None

    calls = script.calls
    dated = _post(client, headers, {"message": "Which date?", "conversation_id": conversation})
    assert script.calls == calls
    assert "2026-11-13" in str(dated["message"]["body"])
    assert "13 November 2026" in str(dated["message"]["body"])
    assert "Which date" not in str(dated["message"]["body"])

    script.phase = "package"
    packaged = _post(client, headers, {"message": _PACKAGE, "conversation_id": conversation})
    package_body = str(packaged["message"]["body"])
    assert "Morning PT" in package_body
    assert "Fantasy" not in package_body
    assert "99999" not in package_body
    assert packaged["pending_confirmation"] is None

    calls = script.calls
    still = _post(client, headers, {"message": "ဘယ်ရက်လဲ။", "conversation_id": conversation})
    assert script.calls == calls
    assert "Wint War" in str(still["message"]["body"])
    assert "2026-11-13" in str(still["message"]["body"])

    script.phase = "resume"
    proposed = _post(client, headers, {"message": _RESUME, "conversation_id": conversation})
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    assert _field(pending, "join_date") == "2026-11-13"
    body = str(proposed["message"]["body"])
    assert "Wint War" in body
    assert "Monthly_plan" in body
    assert "မသိမ်းရသေး" in body
    assert "သိမ်းပြီး" not in body
    assert _counts(gym) == (0, 0, 0, 0)

    script.phase = "price"
    during = _post(client, headers, {"message": _PRICE, "conversation_id": conversation})
    during_body = str(during["message"]["body"])
    assert during["pending_confirmation"] is not None
    assert "50000" in during_body
    assert "1.00" not in during_body
    assert _counts(gym) == (0, 0, 0, 0)


class _WriteInstead:
    def __init__(self) -> None:
        self.phase = "register"

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del declarations, system_instruction, transport
        if self.phase == "write":
            if _responds(contents):
                return _text("The price is 1.00.")
            return _call(
                "create_membership",
                {"plan_id": "Monthly_plan"},
                "သိမ်းပြီးပါပြီ။",
            )
        if self.phase == "resume":
            return _call(
                "create_member",
                {"first_name": "Wint", "last_name": "War", "join_date": "2026-01-01"},
                "သိမ်းပြီးပါပြီ။",
            )
        return _text("Wint War can be registered when you are ready.")


@override_settings(**_settings())
def test_a_price_question_rejects_a_write_and_reads_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, client, headers = _gym(monkeypatch, "SQ02")
    _enable(gym, "members")
    _enable(gym, "memberships")
    _plan(gym, owner.id)
    script = _WriteInstead()
    monkeypatch.setattr("apps.assistant.turns.generate_content", script)

    opened = _post(client, headers, {"message": _REGISTER})
    conversation = str(opened["conversation_id"])
    script.phase = "resume"
    proposed = _post(client, headers, {"message": _RESUME, "conversation_id": conversation})
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    assert _counts(gym) == (0, 0, 0, 0)

    script.phase = "write"
    priced = _post(
        client,
        headers,
        {"message": "Monthly_plan ဘယ်လောက်လဲ?", "conversation_id": conversation},
    )
    body = str(priced["message"]["body"])
    still = priced["pending_confirmation"]
    assert isinstance(still, dict), priced
    assert still["step_id"] == pending["step_id"]
    assert "membership" not in str(still["tool_name"]).casefold()
    assert "50000" in body
    assert "1.00" not in body
    assert "သိမ်းပြီး" not in body
    assert _counts(gym) == (0, 0, 0, 0)
