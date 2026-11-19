"""Workflow catalog and the router's first call, selection, and fallback."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.configuration import ProviderFailure
from apps.assistant.dispatch import tool_contracts
from apps.assistant.knowledge import DOMAIN_MAP
from apps.assistant.provider import FunctionCall, ModelTurn, ProviderCallError
from apps.assistant.registry import production_registry
from apps.assistant.turns import (
    _ANSWER_NOW,
    _REQUIRE_READ,
    _answer_from_results,
    _name_only_member,
    _plain_change,
    _remembered_saved_change,
    _reply,
    _safe_followup,
    _same_saved_change,
    _select_declaration,
    _select_workflows,
    _system_instruction,
    _workflow_declarations,
)
from apps.assistant.workflow_state import _KEYS, _public_state, note_saved_change
from apps.assistant.workflows import (
    WORKFLOWS,
    catalog_text,
    get_workflow,
    guide_text,
    modules_for,
    sections_for,
    workflow_ids,
    workflows_for_module,
)

_TOOL_NAME = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")
_NOT_TOOLS = frozenset(
    {
        "new_member_revenue",
        "renewal_revenue",
        "pt_revenue",
        "class_revenue",
        "product_revenue",
        "other_revenue",
        "total_revenue",
        "net_profit",
        "first_name",
        "last_name",
    }
)


def test_every_registered_module_belongs_to_a_workflow() -> None:
    registry = production_registry()
    registered = {registry.get(name).module for name in registry.names()}
    covered: set[str] = set()
    for item in WORKFLOWS:
        covered.update(item.modules)
    assert registered == covered
    assert len(workflow_ids()) == len(WORKFLOWS)
    assert len(set(workflow_ids())) == len(WORKFLOWS)


def test_guides_name_only_registered_tools_and_stay_short() -> None:
    registered = set(production_registry().names())
    allowed = registered | _NOT_TOOLS | set(workflow_ids())
    assert 1500 <= len(DOMAIN_MAP) <= 2500
    for item in WORKFLOWS:
        assert 400 <= len(item.guide) <= 900, item.id
        assert item.summary
        mentioned = set(_TOOL_NAME.findall(item.guide))
        assert mentioned <= allowed, item.id
        assert mentioned & registered
    lines = catalog_text().splitlines()
    assert len(lines) == len(WORKFLOWS)
    assert lines[0].startswith(f"{WORKFLOWS[0].id}: ")
    assert get_workflow("missing") is None
    assert workflows_for_module("payments")[0].id == "membership_payment"
    assert "payments" in modules_for(("membership_payment",))
    assert sections_for(()) == frozenset({"settings"})
    assert "cash" in sections_for(("membership_payment",))


def test_active_workflows_round_trip_in_public_state() -> None:
    assert "active_workflows" in _KEYS
    public = _public_state({"active_workflows": "check_in,reports", "member_id": "m1"})
    assert public["active_workflows"] == "check_in,reports"
    assert public["member_id"] == "m1"
    assert set(public) == set(_KEYS)


def _message(role: str, body: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, body=body)


def _saved(**kwargs: object) -> SimpleNamespace:
    body = kwargs["body"]
    assert isinstance(body, str)
    return SimpleNamespace(id=uuid4(), role=kwargs["role"], body=body, created_at=datetime.now(UTC))


def _names(declarations: object) -> set[str]:
    assert isinstance(declarations, list)
    return {
        item["name"]
        for item in declarations
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _texts(contents: object) -> list[str]:
    found: list[str] = []
    if not isinstance(contents, list):
        return found
    for item in contents:
        if not isinstance(item, dict):
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                found.append(part["text"])
    return found


def _responses(contents: object) -> bool:
    if not isinstance(contents, list):
        return False
    for item in contents:
        if not isinstance(item, dict):
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        if any(isinstance(part, dict) and "functionResponse" in part for part in parts):
            return True
    return False


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    staff_text: str,
    generate: object,
    state: dict[str, str] | None = None,
    brief: str = "Live gym brief\nGym: Test.\nToday is 2026-11-15.",
) -> dict[str, str]:
    stored = {} if state is None else state
    history = [_message("staff", staff_text)]
    monkeypatch.setattr("apps.assistant.turns._waiting", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("apps.assistant.turns._history", lambda *_args, **_kwargs: history)
    monkeypatch.setattr(
        "apps.assistant.turns._offered",
        lambda _request: frozenset(tool_contracts()),
    )
    monkeypatch.setattr(
        "apps.assistant.turns.assistant_context",
        lambda *_args, **_kwargs: brief,
    )
    monkeypatch.setattr("apps.assistant.turns.assistant_full_access", lambda _request: True)
    monkeypatch.setattr("apps.assistant.turns.append_message", _saved)
    monkeypatch.setattr("apps.assistant.turns.load_state", lambda *_args, **_kwargs: dict(stored))
    monkeypatch.setattr(
        "apps.assistant.turns.remember_sale_package", lambda *_args, **_kwargs: None
    )

    def save(*args: object, **kwargs: object) -> None:
        incoming = kwargs.get("state", args[2] if len(args) > 2 else {})
        assert isinstance(incoming, dict)
        stored.clear()
        stored.update(incoming)

    monkeypatch.setattr("apps.assistant.turns.save_state", save)
    monkeypatch.setattr("apps.assistant.turns.generate_content", generate)
    return stored


def _run(*, cue: str = "") -> dict[str, object]:
    return _reply(
        request=cast(Request, SimpleNamespace(permission_codes=frozenset())),
        view=APIView(),
        gym=cast(object, SimpleNamespace(id=uuid4(), currency="THB", name="Test")),  # type: ignore[arg-type]
        actor=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        conversation=cast(object, SimpleNamespace(id=uuid4())),  # type: ignore[arg-type]
        instruction="You are the gym assistant.",
        cue=cue,
        provider_fallback="The assistant is unavailable.",
    )


def test_the_first_call_declares_only_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[set[str], str, list[str]]] = []

    def generate(**kwargs: object) -> ModelTurn:
        instruction = kwargs["system_instruction"]
        assert isinstance(instruction, str)
        seen.append((_names(kwargs["declarations"]), instruction, _texts(kwargs["contents"])))
        return ModelTurn("Hello.", (), ({"text": "Hello."},))

    _install(monkeypatch, staff_text="Hello", generate=generate)
    result = _run(cue="Ask one question.")

    names, instruction, texts = seen[0]
    assert names == {"select_workflow", "cancel_pending_confirmation"}
    assert "check_in:" in instruction
    assert "create_payment" not in instruction
    assert "Live gym brief" not in instruction
    assert "Ask one question." not in instruction
    assert "none yet" not in instruction
    joined = "\n".join(texts)
    assert "Live gym brief" in joined
    assert "Today is 2026-11-15." in joined
    assert "Ask one question." in joined
    assert "Verified references" in joined
    assert result["message"]["body"] == "Hello."


def test_an_active_workflow_keeps_the_full_catalog() -> None:
    instruction = _system_instruction(
        "You are the gym assistant.",
        pending=False,
        active=["check_in"],
    )
    catalog = "membership_payment: Draft, submit, and approve"
    guide = "create_manual_check_in records the visit."

    assert "Workflows (call select_workflow to switch):" in instruction
    assert catalog in instruction
    assert "Active workflow steps:" in instruction
    assert guide in instruction
    assert instruction.index(catalog) < instruction.index("Active workflow steps:")
    assert instruction.index("Active workflow steps:") < instruction.index(guide)


def test_select_workflow_exposes_only_that_workflows_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        if not _responses(kwargs["contents"]):
            return ModelTurn(
                "",
                (FunctionCall("select_workflow", {"workflows": ["check_in"]}),),
                (
                    {
                        "functionCall": {
                            "name": "select_workflow",
                            "args": {"workflows": ["check_in"]},
                        }
                    },
                ),
            )
        return ModelTurn("Ready.", (), ({"text": "Ready."},))

    stored = _install(monkeypatch, staff_text="Check in a member", generate=generate)
    monkeypatch.setattr(
        "apps.assistant.turns._handle_call",
        lambda **_kwargs: (None, {"error": "unknown_tool"}),
    )
    result = _run()

    assert seen[0] == {"select_workflow", "cancel_pending_confirmation"}
    assert "create_manual_check_in" in seen[1]
    assert "search_check_ins" in seen[1]
    assert "create_payment" not in seen[1]
    assert "select_workflow" in seen[1]
    assert stored["active_workflows"] == "check_in"
    assert "Ready." in str(result["message"]["body"])


def test_an_unknown_workflow_returns_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(**kwargs: object) -> ModelTurn:
        if not _responses(kwargs["contents"]):
            return ModelTurn(
                "",
                (FunctionCall("select_workflow", {"workflows": ["not_a_workflow"]}),),
                (
                    {
                        "functionCall": {
                            "name": "select_workflow",
                            "args": {"workflows": ["not_a_workflow"]},
                        }
                    },
                ),
            )
        contents = kwargs["contents"]
        assert isinstance(contents, list)
        blob = str(contents)
        assert "unknown_workflow" in blob
        assert "check_in" in blob
        return ModelTurn("Which task?", (), ({"text": "Which task?"},))

    stored = _install(monkeypatch, staff_text="Do something", generate=generate)
    _run()
    assert stored.get("active_workflows", "") == ""


def test_a_hidden_offered_tool_activates_its_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []
    called: list[str] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        if not _responses(kwargs["contents"]):
            return ModelTurn(
                "",
                (FunctionCall("search_payments", {}),),
                ({"functionCall": {"name": "search_payments", "args": {}}},),
            )
        return ModelTurn("One payment.", (), ({"text": "One payment."},))

    def handle(**kwargs: object) -> tuple[None, dict[str, object]]:
        name = kwargs["name"]
        assert isinstance(name, str)
        called.append(name)
        return None, {"result": {"results": [{"status": "POSTED"}]}}

    stored = _install(monkeypatch, staff_text="List payments", generate=generate)
    monkeypatch.setattr("apps.assistant.turns._handle_call", handle)
    result = _run()

    assert "search_payments" not in seen[0]
    assert called == ["search_payments"]
    assert "search_payments" in seen[1]
    assert "create_class_schedule" not in seen[1]
    assert stored["active_workflows"] == "membership_payment"
    assert "One payment." in str(result["message"]["body"])


def test_a_stored_workflow_skips_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[set[str]] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(_names(kwargs["declarations"]))
        return ModelTurn("Still here.", (), ({"text": "Still here."},))

    _install(
        monkeypatch,
        staff_text="Next",
        generate=generate,
        state={"active_workflows": "reports"},
    )
    _run()

    assert "get_gym_dashboard" in seen[0]
    assert "get_gym_report" in seen[0]
    assert "create_member" not in seen[0]
    assert len(seen) == 1


def test_answer_from_results_asks_once_then_uses_stored_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(kwargs.get("declarations"))
        assert _ANSWER_NOW in str(kwargs.get("contents"))
        return ModelTurn("Ada is active, MEM-1.", (), ({"text": "Ada is active, MEM-1."},))

    monkeypatch.setattr("apps.assistant.turns.generate_content", generate)
    text, calls = _answer_from_results(
        contents=[{"role": "user", "parts": [{"text": "Find Ada"}]}],
        instruction="static",
        results=[{"results": [{"member_number": "MEM-1", "status": "ACTIVE"}]}],
        brief="Live gym brief",
        staff_text="Find Ada",
        deadline=monotonic() + 30,
    )

    assert calls == 1
    assert seen == [[]]
    assert "MEM-1" in text
    assert "Ada is active" in text

    def fail(**_kwargs: object) -> ModelTurn:
        raise ProviderCallError(ProviderFailure.PROVIDER)

    monkeypatch.setattr("apps.assistant.turns.generate_content", fail)
    rendered, failed_calls = _answer_from_results(
        contents=[{"role": "user", "parts": [{"text": "Find Ada"}]}],
        instruction="static",
        results=[{"results": [{"member_number": "MEM-1", "status": "ACTIVE"}]}],
        brief="Live gym brief",
        staff_text="Find Ada",
        deadline=monotonic() + 30,
    )

    assert failed_calls == 0
    assert "MEM-1" in rendered
    assert "ACTIVE" in rendered
    assert guide_text(("reports",))


def test_reports_offers_every_registered_read() -> None:
    contracts = tool_contracts()
    names = _names(_workflow_declarations(frozenset(contracts), contracts, ["reports"]))
    registry = production_registry()
    reads = {name for name in registry.names() if registry.get(name).operation == "read"}

    assert reads <= names
    assert "search_membership_expiries" in names
    assert "search_trainers" in names
    assert "search_products" in names
    assert "create_member" not in names


def test_select_workflow_keeps_both_ids() -> None:
    declaration = _select_declaration()
    parameters = declaration["parameters"]
    assert isinstance(parameters, dict)
    workflows = parameters["properties"]
    assert isinstance(workflows, dict)
    field = workflows["workflows"]
    assert isinstance(field, dict)
    assert field["maxItems"] == 2
    state: dict[str, str] = {}
    body = _select_workflows(state, {"workflows": ["expenses", "reports"]})

    assert body["workflows"] == ["expenses", "reports"]
    assert state["active_workflows"] == "expenses,reports"


def test_a_whole_myanmar_name_matches_a_split_saved_member() -> None:
    guide = get_workflow("member_registration")
    assert guide is not None
    assert "leave last_name empty" in guide.guide
    assert 'do not split a name like "Hla Hla"' in guide.guide
    saved = (
        "create_member",
        {"first_name": "Hla", "last_name": "Hla", "join_date": "2026-11-16"},
    )
    assert _same_saved_change(
        "create_member",
        {"first_name": "Hla Hla", "last_name": "", "join_date": "2026-11-16"},
        saved,
    )
    assert _same_saved_change(
        "create_member",
        {"first_name": "  hla   hla  ", "join_date": "2026-11-16"},
        saved,
    )
    assert not _same_saved_change(
        "create_member",
        {"first_name": "Nwe Nwe", "last_name": "", "join_date": "2026-11-16"},
        saved,
    )
    assert not _same_saved_change(
        "update_member",
        {"first_name": "Hla Hla", "last_name": ""},
        ("update_member", {"first_name": "Hla", "last_name": "Hla"}),
    )


def test_equivalent_amounts_are_the_same_saved_change() -> None:
    assert _plain_change({"opening_cash": "50000"}) == _plain_change({"opening_cash": "50000.00"})
    assert _plain_change({"opening_cash": "50,000"}) == _plain_change({"opening_cash": "50000"})
    saved = ("open_cash_session", {"opening_cash": "50000", "register_code": "FRONT"})
    assert _same_saved_change(
        "open_cash_session",
        {"opening_cash": "50000.00", "register_code": "FRONT"},
        saved,
    )
    assert not _same_saved_change(
        "open_cash_session",
        {"opening_cash": "100.00"},
        saved,
    )


def test_a_name_alone_is_not_a_registration() -> None:
    assert _name_only_member("Wint War")
    assert not _name_only_member("Add Ada Lovelace")
    assert not _name_only_member("Wint War, Monthly_plan ဝယ်မယ်။")


def test_a_saved_category_is_remembered_on_a_later_turn() -> None:
    state: dict[str, str] = {}
    note_saved_change(state, "create_product_category", _plain_change({"name": "Snacks"}))
    assert _remembered_saved_change("create_product_category", {"name": "Snacks"}, state)
    assert not _remembered_saved_change("create_product", {"name": "Snacks"}, state)


def test_a_router_only_step_does_not_spend_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelTurn(
                "",
                (FunctionCall("select_workflow", {"workflows": ["reports"]}),),
                (
                    {
                        "functionCall": {
                            "name": "select_workflow",
                            "args": {"workflows": ["reports"]},
                        }
                    },
                ),
            )
        return ModelTurn("Done.", (), ({"text": "Done."},))

    _install(monkeypatch, staff_text="Any protein left?", generate=generate)
    monkeypatch.setattr("apps.assistant.turns.max_steps", lambda: 1)
    result = _run()

    assert calls["count"] == 2
    assert result["message"]["body"] == "Done."


def test_a_stored_answer_without_a_read_asks_for_one_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    called: list[str] = []

    def generate(**kwargs: object) -> ModelTurn:
        seen.append(str(kwargs.get("contents")))
        if len(seen) == 1:
            text = "There are currently no trainer assignments in the system."
            return ModelTurn(text, (), ({"text": text},))
        if len(seen) == 2:
            assert _REQUIRE_READ in seen[-1]
            return ModelTurn(
                "",
                (FunctionCall("search_trainer_assignments", {"summary": True}),),
                (
                    {
                        "functionCall": {
                            "name": "search_trainer_assignments",
                            "args": {"summary": True},
                        }
                    },
                ),
            )
        return ModelTurn(
            "There are currently no trainer assignments.",
            (),
            ({"text": "There are currently no trainer assignments."},),
        )

    def handle(**kwargs: object) -> tuple[None, dict[str, object]]:
        name = kwargs["name"]
        assert isinstance(name, str)
        called.append(name)
        return None, {"result": {"results": []}}

    _install(
        monkeypatch,
        staff_text="Which trainer is assigned how many members?",
        generate=generate,
        state={"active_workflows": "personal_training"},
    )
    monkeypatch.setattr("apps.assistant.turns._handle_call", handle)
    result = _run()

    assert called == ["search_trainer_assignments"]
    assert len(seen) == 3
    activity = result["tool_activity"]
    assert isinstance(activity, list)
    assert any("assignment" in str(item.get("name", "")).casefold() for item in activity)


def test_a_brief_fact_does_not_add_a_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        text = "Spring Water on hand is 5.000."
        return ModelTurn(text, (), ({"text": text},))

    _install(
        monkeypatch,
        staff_text="How many Spring Water are on hand?",
        generate=generate,
        state={"active_workflows": "personal_training"},
        brief="Live gym brief\nProducts: Spring Water sku 5.000",
    )
    result = _run()

    assert calls["count"] == 1
    assert "5.000" in str(result["message"]["body"])


def test_definition_prose_does_not_add_a_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        text = "A membership is a purchased term for one member."
        return ModelTurn(text, (), ({"text": text},))

    _install(
        monkeypatch,
        staff_text="What is a membership?",
        generate=generate,
        state={"active_workflows": "reports"},
    )
    result = _run()

    assert calls["count"] == 1
    assert "purchased term" in str(result["message"]["body"])


def test_a_stored_answer_is_nudged_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def generate(**_kwargs: object) -> ModelTurn:
        calls["count"] += 1
        text = "There are currently no trainer assignments in the system."
        return ModelTurn(text, (), ({"text": text},))

    _install(
        monkeypatch,
        staff_text="Which trainer is assigned how many members?",
        generate=generate,
        state={"active_workflows": "personal_training"},
    )
    _run()

    assert calls["count"] == 2


def test_a_burmese_conversation_localizes_the_summary_and_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "step_id": "11111111-1111-1111-1111-111111111111",
        "goal_id": "22222222-2222-2222-2222-222222222222",
        "tool_name": "Create a member",
        "summary": "Create a member.",
        "fields": [],
        "approval": None,
    }

    def generate(**_kwargs: object) -> ModelTurn:
        return ModelTurn("Create a member.", (), ({"text": "Create a member."},))

    _install(
        monkeypatch,
        staff_text="Wint War၊ 13-11-2026၊ Monthly_plan ဝယ်မယ်။",
        generate=generate,
    )
    monkeypatch.setattr("apps.assistant.turns._waiting", lambda *_args, **_kwargs: pending)
    created = _run()
    created_body = str(created["message"]["body"])
    created_card = created["pending_confirmation"]

    assert isinstance(created_card, dict)
    assert "Create a member" not in created_body
    assert "Create a member" not in str(created_card["summary"])
    assert "အဖွဲ့ဝင်" in created_body
    assert "အဖွဲ့ဝင်" in str(created_card["summary"])
    assert created_card["tool_name"] == "Create a member"
    assert "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ" in created_body
    assert "That change is not saved until you confirm." not in created_body

    def side_question(**_kwargs: object) -> ModelTurn:
        text = "ဒီနေ့အတွက် အသုံးစရိတ် မရှိပါ။ That change is not saved until you confirm."
        return ModelTurn(text, (), ({"text": text},))

    _install(monkeypatch, staff_text="Any expenses today?", generate=side_question)
    monkeypatch.setattr(
        "apps.assistant.turns._history",
        lambda *_args, **_kwargs: [
            _message("staff", "လူသစ်တစ်ယောက် Member လုပ်မယ်။"),
            _message("assistant", "နာမည်ပြောပါ။"),
            _message("staff", "Any expenses today?"),
        ],
    )
    monkeypatch.setattr("apps.assistant.turns._waiting", lambda *_args, **_kwargs: dict(pending))
    interrupted = _run()
    interrupted_body = str(interrupted["message"]["body"])

    assert "That change is not saved until you confirm." not in interrupted_body
    assert interrupted_body.count("ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ") == 1
    assert "အသုံးစရိတ်" in interrupted_body


def _tool_turn(name: str, arguments: dict[str, object]) -> ModelTurn:
    return ModelTurn(
        "",
        (FunctionCall(name, arguments),),
        ({"functionCall": {"name": name, "args": arguments}},),
    )


def test_a_pending_submit_continues_to_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def handle(**kwargs: object) -> tuple[dict[str, object] | None, dict[str, object]]:
        name = kwargs["name"]
        assert isinstance(name, str)
        called.append(name)
        if name == "submit_payment":
            return None, {"error": "finance_state", "status": "PENDING_APPROVAL"}
        assert name == "approve_payment"
        return (
            {"summary": "Approve the payment.", "tool_name": "approve_payment"},
            {"status": "confirmation_required"},
        )

    def generate(**kwargs: object) -> ModelTurn:
        if not _responses(kwargs["contents"]):
            return _tool_turn("submit_payment", {"payment_id": "pay-1"})
        return ModelTurn("", (), ())

    _install(monkeypatch, staff_text="ငွေချေမယ်", generate=generate)
    monkeypatch.setattr("apps.assistant.turns._handle_call", handle)
    monkeypatch.setattr("apps.assistant.turns._blocks_own_approval", lambda *_args: False)
    result = _run()
    body = str(result["message"]["body"])

    assert called == ["submit_payment", "approve_payment"]
    assert result["pending_confirmation"] == {
        "summary": "Approve the payment.",
        "tool_name": "approve_payment",
    }
    assert "ဆက်လုပ်ဖို့ အတည်ပြုချက်ကို အတည်ပြုပါ။" in body
    assert "ဒီအခြေအနေမှာ ဒီအဆင့်ကို လုပ်လို့ မရပါ" not in body


def test_a_posted_record_stops_without_another_card(monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(**kwargs: object) -> tuple[None, dict[str, object]]:
        assert kwargs["name"] == "submit_payment"
        return None, {"error": "finance_state", "status": "POSTED"}

    def generate(**kwargs: object) -> ModelTurn:
        if not _responses(kwargs["contents"]):
            return _tool_turn("submit_payment", {"payment_id": "pay-1"})
        return ModelTurn("", (), ())

    _install(monkeypatch, staff_text="ငွေချေမယ်", generate=generate)
    monkeypatch.setattr("apps.assistant.turns._handle_call", handle)
    result = _run()
    body = str(result["message"]["body"])

    assert result["pending_confirmation"] is None
    assert body == "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ ထပ်သိမ်းစရာ မလိုပါ။"


def test_a_missing_amount_asks_without_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(**_kwargs: object) -> tuple[None, dict[str, object]]:
        return None, {"error": "amount_unconfirmed"}

    def generate(**kwargs: object) -> ModelTurn:
        if not _responses(kwargs["contents"]):
            return _tool_turn("create_payment", {})
        return ModelTurn("I cannot do that.", (), ({"text": "I cannot do that."},))

    _install(monkeypatch, staff_text="ငွေချေမယ်", generate=generate)
    monkeypatch.setattr("apps.assistant.turns._handle_call", handle)
    result = _run()

    assert result["pending_confirmation"] is None
    assert result["message"]["body"] == "ပမာဏ ဘယ်လောက်လဲ။"


def test_confirmation_followups_stay_on_the_safe_next_write() -> None:
    pending = _safe_followup(
        "submit_pt_sale",
        {"error": "finance_state", "status": "PENDING_APPROVAL"},
        {"pt_sale_id": "sale-1"},
    )
    posted = _safe_followup(
        "approve_payment",
        {"error": "not_pending", "status": "POSTED"},
        {"payment_id": "pay-1"},
    )
    draft = _safe_followup(
        "approve_payment",
        {"error": "not_pending", "status": "DRAFT"},
        {"payment_id": "pay-1"},
    )
    void = _safe_followup(
        "void_payment",
        {"error": "finance_state", "status": "POSTED"},
        {"payment_id": "pay-1"},
    )

    assert pending == ("approve_pt_sale", {"pt_sale_id": "sale-1"})
    assert posted is None
    assert draft == ("submit_payment", {"payment_id": "pay-1"})
    assert void is None
