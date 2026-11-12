"""Gemini request budget. These tests do not call the network or a database."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.dispatch import tool_contracts
from apps.assistant.knowledge import complete_stored_reply
from apps.assistant.provider import FunctionCall, ModelTurn
from apps.assistant.turns import _model_declarations, _reply

_OFFERED = frozenset(
    {
        "search_sales",
        "search_class_occurrences",
        "search_bookings",
        "search_members",
        "search_payments",
        "create_member",
        "create_payment",
        "approve_payment",
    }
)


def _message(role: str, body: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, body=body)


def _saved(**kwargs: object) -> SimpleNamespace:
    body = kwargs["body"]
    assert isinstance(body, str)
    return SimpleNamespace(
        id=uuid4(),
        role=kwargs["role"],
        body=body,
        created_at=datetime.now(UTC),
    )


def _call(name: str, arguments: dict[str, object] | None = None, text: str = "") -> ModelTurn:
    args = arguments or {}
    parts: list[dict[str, object]] = []
    if text:
        parts.append({"text": text})
    parts.append({"functionCall": {"name": name, "args": args}})
    return ModelTurn(text, (FunctionCall(name, args),), tuple(parts))


def _text(body: str) -> ModelTurn:
    return ModelTurn(body, (), ({"text": body},))


def _names(declarations: object) -> set[str]:
    assert isinstance(declarations, list)
    return {
        item["name"]
        for item in declarations
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _responses(contents: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if not isinstance(contents, list):
        return found
    for item in contents:
        if not isinstance(item, dict):
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and "functionResponse" in part:
                found.append(part)
    return found


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    staff_text: str,
    generate: object,
    handle: object | None = None,
) -> None:
    history = [_message("staff", staff_text)]
    monkeypatch.setattr("apps.assistant.turns._waiting", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("apps.assistant.turns._history", lambda *_args, **_kwargs: history)
    monkeypatch.setattr("apps.assistant.turns._offered", lambda _request: _OFFERED)
    monkeypatch.setattr(
        "apps.assistant.turns.assistant_context",
        lambda *_args, **_kwargs: "Live gym brief\nGym: Test.",
    )
    monkeypatch.setattr("apps.assistant.turns.assistant_full_access", lambda _request: False)
    monkeypatch.setattr("apps.assistant.turns.append_message", _saved)
    monkeypatch.setattr("apps.assistant.turns.load_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "apps.assistant.turns.remember_sale_package", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("apps.assistant.turns.save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("apps.assistant.turns.generate_content", generate)
    if handle is not None:
        monkeypatch.setattr("apps.assistant.turns._handle_call", handle)


def _run() -> dict[str, object]:
    return _reply(
        request=cast(Request, SimpleNamespace(permission_codes=frozenset())),
        view=APIView(),
        gym=cast(object, SimpleNamespace(id=uuid4(), currency="THB")),  # type: ignore[arg-type]
        actor=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        conversation=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        instruction="You are the gym assistant.",
        cue="",
        provider_fallback="The assistant is unavailable.",
    )


def test_read_only_turns_omit_write_declarations_and_still_refuse_a_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        if _responses(kwargs["contents"]):
            return _text("I created the payment.")
        return _call("create_payment")

    _install(monkeypatch, staff_text="List payments", generate=generate)
    result = _run()

    assert seen
    assert "search_payments" in seen[0]
    assert "create_payment" not in seen[0]
    assert "approve_payment" not in seen[0]
    assert "create_member" not in seen[0]
    contracts = tool_contracts()
    assert all(contracts[name].definition.operation == "read" for name in seen[0])
    assert result["pending_confirmation"] is None
    assert result["message"]["body"] == "I can look that up. I will not make a change to answer it."


def test_a_write_request_keeps_write_declarations_and_the_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        if not _responses(kwargs["contents"]):
            return _call("search_members", {"query": "Ada"})
        return _call(
            "create_member",
            {"first_name": "Ada", "last_name": "Lovelace"},
            text="Review the change before confirming.",
        )

    def handle(**kwargs: object) -> tuple[dict[str, object] | None, dict[str, object]]:
        name = kwargs["name"]
        if name == "search_members":
            return None, {"result": {"results": [{"member_number": "MEM-1", "status": "ACTIVE"}]}}
        if name == "create_member":
            return (
                {
                    "summary": "Create a member.",
                    "tool_name": "Create a member",
                    "step_id": "step",
                    "goal_id": "goal",
                },
                {"status": "confirmation_required", "summary": "Create a member."},
            )
        return None, {"error": "unknown_tool"}

    _install(monkeypatch, staff_text="Add Ada Lovelace", generate=generate, handle=handle)
    result = _run()

    assert len(seen) == 2
    assert "create_member" in seen[0]
    assert "search_members" in seen[0]
    assert result["pending_confirmation"] is not None
    assert result["message"]["body"] == "Review the change before confirming."
    assert "saved" not in str(result["message"]["body"]).casefold()


def test_a_saved_change_follow_up_still_receives_write_declarations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        instruction = kwargs["system_instruction"]
        assert isinstance(instruction, str)
        assert "previous change is saved" in instruction
        return _text("The member is saved.")

    _install(monkeypatch, staff_text="Add Ada Lovelace", generate=generate)
    result = _reply(
        request=cast(Request, SimpleNamespace(permission_codes=frozenset())),
        view=APIView(),
        gym=cast(object, SimpleNamespace(id=uuid4(), currency="THB")),  # type: ignore[arg-type]
        actor=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        conversation=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        instruction="You are the gym assistant. The previous change is saved.",
        cue="The change is saved. Continue only if a different step is still required.",
        provider_fallback=None,
        saved=("create_member", {"first_name": "Ada"}),
    )

    assert result is not None
    assert seen and "create_member" in seen[0]
    assert result["message"]["body"] == "The member is saved."
    assert len(seen) == 1


def test_a_complete_list_does_not_ask_gemini_to_phrase_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        return _call("search_sales")

    def handle(**kwargs: object) -> tuple[None, dict[str, object]]:
        assert kwargs["name"] == "search_sales"
        return None, {
            "result": {
                "results": [
                    {"status": "POSTED", "total_amount": "10.00", "business_date": "2026-11-12"}
                ]
            }
        }

    _install(monkeypatch, staff_text="List sales", generate=generate, handle=handle)
    result = _run()

    assert calls["count"] == 1
    body = str(result["message"]["body"])
    assert "Sales." in body
    assert "POSTED" in body
    assert "10.00" in body
    assert result["pending_confirmation"] is None


def test_a_multi_tool_list_still_calls_each_read_and_skips_the_final_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def generate(**kwargs: object) -> ModelTurn:
        answered = _responses(kwargs["contents"])
        if len(answered) >= 2:
            raise AssertionError("final phrasing call")
        if not answered:
            calls.append("search_class_occurrences")
            return _call("search_class_occurrences")
        calls.append("search_bookings")
        return _call("search_bookings")

    def handle(**kwargs: object) -> tuple[None, dict[str, object]]:
        name = kwargs["name"]
        if name == "search_class_occurrences":
            return None, {"result": {"results": [{"name": "Yoga", "status": "SCHEDULED"}]}}
        if name == "search_bookings":
            return None, {
                "result": {
                    "results": [{"member_number": "MEM-000101", "status": "BOOKED"}],
                }
            }
        return None, {"error": "unknown_tool"}

    _install(monkeypatch, staff_text="List classes and bookings", generate=generate, handle=handle)
    result = _run()

    assert calls == ["search_class_occurrences", "search_bookings"]
    body = str(result["message"]["body"])
    assert "Yoga" in body
    assert "MEM-000101" in body
    assert "BOOKED" in body


def test_a_single_record_read_still_asks_the_model_to_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def generate(**kwargs: object) -> ModelTurn:
        calls["count"] += 1
        if _responses(kwargs["contents"]):
            return _text("MEM-000101 is active.")
        return _call("search_members", {"query": "MEM-000101"})

    def handle(**_kwargs: object) -> tuple[None, dict[str, object]]:
        return None, {"result": {"results": [{"member_number": "MEM-000101", "status": "ACTIVE"}]}}

    _install(
        monkeypatch,
        staff_text="What is member MEM-000101's phone",
        generate=generate,
        handle=handle,
    )
    result = _run()

    assert calls["count"] == 2
    assert result["message"]["body"] == "MEM-000101 is active."


def test_declarations_for_a_lookup_exclude_writes_and_a_change_keeps_them() -> None:
    contracts = tool_contracts()
    lookup = _names(_model_declarations(_OFFERED, contracts, "List payments"))
    change = _names(_model_declarations(_OFFERED, contracts, "Add Ada Lovelace"))
    assert "search_payments" in lookup
    assert "create_payment" not in lookup
    assert "approve_payment" not in lookup
    assert "create_member" in change
    assert "search_members" in change


def test_a_partial_list_is_not_treated_as_finished() -> None:
    classes = {"results": [{"name": "Yoga", "status": "SCHEDULED"}]}
    bookings = {"results": [{"member_number": "MEM-000101", "status": "BOOKED"}]}
    staff = "List classes and bookings"
    assert complete_stored_reply(staff, {"search_class_occurrences": classes}) is None
    finished = complete_stored_reply(
        staff,
        {"search_class_occurrences": classes, "search_bookings": bookings},
    )
    assert finished is not None
    assert "Yoga" in finished
    assert "MEM-000101" in finished
    ambiguous = complete_stored_reply(
        "What did the member pay",
        {
            "search_members": {
                "ambiguous": True,
                "results": [{"member_number": "MEM-000101"}, {"member_number": "MEM-000102"}],
            }
        },
    )
    assert ambiguous is not None
    assert "MEM-000101" in ambiguous
    assert "MEM-000102" in ambiguous


def test_the_step_limit_stops_without_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        return _call("search_members", {"query": "Ada"})

    def handle(**_kwargs: object) -> tuple[None, dict[str, object]]:
        return None, {"result": {"results": [{"member_number": "MEM-1", "status": "ACTIVE"}]}}

    _install(monkeypatch, staff_text="Find Ada", generate=generate, handle=handle)
    monkeypatch.setattr("apps.assistant.turns.max_steps", lambda: 4)
    result = _run()

    assert calls["count"] == 4
    assert result["message"]["body"] == "I could not finish that within the assistant limit."
    assert result["pending_confirmation"] is None
    assert "saved" not in str(result["message"]["body"]).casefold()
