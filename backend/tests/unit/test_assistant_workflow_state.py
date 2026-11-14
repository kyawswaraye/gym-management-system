"""Workflow facts, dates, ownership, and throttle scopes without a provider call."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from django.conf import settings

from apps.assistant.knowledge import offered_operations, short_reply_kind
from apps.assistant.lookups import _keep_stated_dates, relative_day, relative_window
from apps.assistant.queries import may_read_conversation
from apps.assistant.registry import closed_production_names
from apps.assistant.throttling import (
    AssistantGymTurnThrottle,
    AssistantReadThrottle,
    AssistantTurnThrottle,
)
from apps.assistant.workflow_state import (
    absorb_read,
    capability_brief,
    completion_sentence,
    dashboard_arguments,
    direct_reply,
    provider_notice,
    remember_intent,
    same_created_member,
    selling_membership,
)
from apps.tenants.models import GymMembership

_TODAY = date(2026, 11, 12)


def test_a_member_lookup_does_not_turn_a_pt_date_into_a_membership() -> None:
    state: dict[str, str] = {}
    remember_intent(state, "ဝင့်ဝါကို PT ထည့်မယ်")
    absorb_read(
        state,
        "search_members",
        {
            "results": [
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "member_number": "MEM-000005",
                    "first_name": "ဝင့်",
                    "last_name": "ဝါ",
                }
            ]
        },
    )

    assert state["workflow"] == "pt"
    assert capability_brief("ဒီနေ့", state).startswith("Relevant operation. Personal training")
    assert capability_brief("Soe Soe", state).startswith("Relevant operation. Personal training")


def test_one_unambiguous_read_keeps_that_record_id() -> None:
    state: dict[str, str] = {"workflow": "payment"}
    payment = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    absorb_read(
        state,
        "search_payments",
        {"results": [{"id": payment, "purpose": "MEMBERSHIP"}]},
    )
    assert state["payment_id"] == payment
    assert state["workflow"] == "payment"
    absorb_read(
        state,
        "search_products",
        {"results": [{"id": payment}, {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc"}]},
    )
    assert "product_id" not in state
    absorb_read(
        state,
        "get_payment",
        {"record_id": payment, "member_id": "dddddddd-dddd-dddd-dddd-dddddddddddd"},
    )
    assert state["member_id"] == "dddddddd-dddd-dddd-dddd-dddddddddddd"
    absorb_read(
        state,
        "search_service_packages",
        {"results": [{"code": "PT3", "name": "Three sessions"}]},
    )
    assert state["package_code"] == "PT3"
    assert state["package_name"] == "Three sessions"


def test_short_reply_keeps_the_operation_named_by_the_previous_question() -> None:
    assert short_reply_kind("ထည့်မယ်။") == "affirm"
    assert short_reply_kind("အင်း") == "affirm"
    assert short_reply_kind("ဟုတ်တယ်") == "affirm"
    assert short_reply_kind("ရတယ်") == "affirm"
    assert short_reply_kind("လုပ်ပါ") == "affirm"
    assert short_reply_kind("yes") == "affirm"
    assert short_reply_kind("okay.") == "affirm"
    assert short_reply_kind("OK") == "affirm"
    assert short_reply_kind("မထည့်တော့ဘူး") == "decline"
    assert short_reply_kind("မလုပ်တော့ဘူး") == "decline"
    assert short_reply_kind("မဟုတ်ဘူး") == "decline"
    assert short_reply_kind("အဖွဲ့ဝင် အသစ် ထည့်မယ်") == ""
    assert offered_operations("PT3 package ထည့်ပေးရမလား။") == ("create_pt_sale",)
    assert offered_operations("ပက်ကေ့ ရောင်းမလား၊ ဘိုကင် လုပ်မလား။") == (
        "create_pt_sale",
        "create_pt_booking",
    )
    state = {"workflow": "pt", "next": "create_pt_sale"}
    remember_intent(state, "ထည့်မယ်")
    remember_intent(state, "ဒီနေ့")
    remember_intent(state, "100")
    assert state["workflow"] == "pt"
    assert capability_brief("ထည့်မယ်", state).startswith("Relevant operation. Personal training")


def test_yangon_relative_days_and_a_later_confirmation_keep_today() -> None:
    assert relative_day("ဒီနေ့ ဝင်မယ်", _TODAY) == date(2026, 11, 12)
    assert relative_day("မနက်ဖြန်", _TODAY) == date(2026, 11, 13)
    assert relative_day("မနေ့က", _TODAY) == date(2026, 11, 11)
    assert relative_day("နောက်တစ်ပတ်", _TODAY) == date(2026, 11, 19)
    window = relative_window("ဒီလ", _TODAY)
    assert window == (date(2026, 11, 1), date(2026, 11, 30))
    payload = {"start_date": "2026-01-01"}
    assert _keep_stated_dates(payload, "ဟုတ်ကဲ့", _TODAY, history=("ဒီနေ့ join date",)) is None
    assert payload["start_date"] == "2026-01-01"


def test_saved_member_answers_the_number_and_the_next_step() -> None:
    state = {
        "member_name": "Kyaw Swar",
        "member_number": "M-1042",
        "member_id": "8c2c0b4e-1c2a-4f4a-9c2a-1c2a4f4a9c2a",
        "last_tool": "create_member",
        "last_outcome": "saved",
        "next": "create_membership",
    }
    number = direct_reply("Kyaw Swar ရဲ့ အဖွဲ့ဝင်နံပါတ် ပြပါ", state)
    assert number == "Kyaw Swar ရဲ့ အဖွဲ့ဝင်နံပါတ်က M-1042 ပါ။"
    nxt = direct_reply("ဘာဆက်လုပ်ရမလဲ", state)
    assert nxt is not None
    assert "M-1042" in nxt
    assert "အသင်းဝင်" in nxt
    done = completion_sentence(state, "ဟုတ်ကဲ့")
    assert "M-1042" in done
    assert "သိမ်းပြီးပါပြီ" in done
    notice = provider_notice("ဟုတ်ကဲ့", state)
    assert "M-1042" in notice
    assert "Nothing new was saved" not in notice
    assert "ဘာမှ အသစ် မသိမ်းရသေးပါ" not in notice
    assert not same_created_member(
        state,
        {"first_name": "Kyaw", "last_name": "Swar"},
    )
    assert not same_created_member(state, {"first_name": "Other", "last_name": "Person"})


def test_a_sale_offers_active_plans_and_a_catalog_question_does_not() -> None:
    state = {"next": "create_membership"}
    assert selling_membership("YOGA တပ်ပေး", state)
    assert not selling_membership("Which membership plan is named Yoga?", None)
    assert not selling_membership("inactive plans ပြပါ", state)
    arguments = dashboard_arguments({}, "ဒီလအတွင်း ဝင်ငွေဘယ်လောက်ရလဲ", _TODAY)
    assert arguments["range"] == "this_month"
    replaced = dashboard_arguments(
        {"range": "last_month"},
        "ဒီလ ဝင်ငွေ ဘယ်လောက်လဲ",
        _TODAY,
    )
    assert replaced["range"] == "this_month"
    assert "date_from" not in replaced
    submitted = completion_sentence(
        {"last_tool": "submit_salary_period", "last_outcome": "saved", "next": "approval"},
        "လစာ တင်ပေး",
    )
    assert "အတည်ပြု" in submitted
    assert "သိမ်းပြီးပါပြီ" not in submitted


def test_conversation_read_is_the_author_or_the_active_owner() -> None:
    author = SimpleNamespace(id="author", gym_id="gym", is_owner=False, status="ACTIVE")
    owner = SimpleNamespace(id="owner", gym_id="gym", is_owner=True, status="ACTIVE")
    other = SimpleNamespace(id="other", gym_id="gym", is_owner=False, status="ACTIVE")
    suspended = SimpleNamespace(id="old", gym_id="gym", is_owner=True, status="SUSPENDED")
    conversation = SimpleNamespace(gym_id="gym", created_by_membership_id="author")
    assert may_read_conversation(actor=author, conversation=conversation)
    assert may_read_conversation(actor=owner, conversation=conversation)
    assert not may_read_conversation(actor=other, conversation=conversation)
    assert not may_read_conversation(actor=suspended, conversation=conversation)
    assert GymMembership.Status.ACTIVE == "ACTIVE"


def test_assistant_routes_have_membership_and_gym_rates() -> None:
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["assistant_read"] == "60/60"
    assert rates["assistant_turn"] == "20/60"
    assert rates["assistant_gym_read"] == "180/60"
    assert rates["assistant_gym_turn"] == "60/60"
    assert AssistantReadThrottle.scope == "assistant_read"
    assert AssistantTurnThrottle.scope == "assistant_turn"
    assert AssistantGymTurnThrottle.scope == "assistant_gym_turn"
    names = closed_production_names()
    assert "get_gym_dashboard" in names
    assert "search_announcements" in names
