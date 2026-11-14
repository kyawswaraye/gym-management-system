"""Date retention and onboarding state. These tests do not touch a gym database."""

from __future__ import annotations

from datetime import date

from apps.assistant.knowledge import (
    fact_tools,
    grounded_information_reply,
    information_question,
    refuse_write_for_lookup,
    staff_notice,
)
from apps.assistant.lookups import (
    _date_problem_body,
    _keep_stated_dates,
    calendar_day,
)
from apps.assistant.workflow_state import (
    absorb_read,
    capability_brief,
    confirmation_reply,
    direct_reply,
    remember_facts,
    remember_intent,
    stored_date_reply,
)

_TODAY = date(2026, 11, 12)
_SUPPLIED = "Wint War, 13-11-2026, Monthly_plan ဝယ်မယ်။"
_OPENING = "လူတစ်ယောက်လာပြီး member လုပ်မယ်။class တက်မယ်။pt လုပ်မယ်တဲ့။ဘာတွေလုပ်ရမလဲ?"


def test_day_month_year_dates_normalize_to_13_november_2026() -> None:
    assert calendar_day("13-11-2026", _TODAY) == date(2026, 11, 13)
    assert calendar_day("13/11/2026", _TODAY) == date(2026, 11, 13)
    assert calendar_day("2026-11-13", _TODAY) == date(2026, 11, 13)
    assert calendar_day("2026/11/13", _TODAY) == date(2026, 11, 13)
    assert calendar_day("၁၃-၁၁-၂၀၂၆", _TODAY) == date(2026, 11, 13)
    assert calendar_day("13 November 2026", _TODAY) == date(2026, 11, 13)
    assert calendar_day("November 13, 2026", _TODAY) == date(2026, 11, 13)
    assert calendar_day("၁၃ နိုဝင်ဘာ ၂၀၂၆", _TODAY) == date(2026, 11, 13)
    assert calendar_day("11-13-2026", _TODAY) == date(2026, 11, 13)


def test_a_supplied_day_month_year_replaces_the_model_date() -> None:
    payload: dict[str, object] = {"join_date": "2026-01-01", "start_date": "2026-01-01"}

    assert _keep_stated_dates(payload, _SUPPLIED, today=_TODAY) is None

    assert payload["join_date"] == "2026-11-13"
    assert payload["start_date"] == "2026-11-13"


def test_a_later_message_does_not_copy_a_date_phrase_from_history() -> None:
    payload: dict[str, object] = {"start_date": "2026-01-01"}

    assert (
        _keep_stated_dates(
            payload,
            "ဘယ်ရက်လဲ။",
            today=_TODAY,
            history=(_SUPPLIED,),
        )
        is None
    )
    assert payload["start_date"] == "2026-01-01"

    english: dict[str, object] = {"start_date": "2025-01-01"}
    assert (
        _keep_stated_dates(
            english,
            "Which date?",
            today=_TODAY,
            history=("Wint War, 13/11/2026, Monthly_plan",),
        )
        is None
    )
    assert english["start_date"] == "2025-01-01"


def test_a_lookup_that_says_today_does_not_become_the_join_date() -> None:
    payload: dict[str, object] = {"join_date": "2026-01-01"}

    assert (
        _keep_stated_dates(
            payload,
            "Wint War",
            today=_TODAY,
            history=("Today's payments.", "Any expenses today?"),
            require_join=True,
        )
        is None
    )
    assert payload["join_date"] == "2026-01-01"


def test_a_stored_date_is_not_stamped_over_the_model_date() -> None:
    payload: dict[str, object] = {"start_date": "2026-01-01"}

    assert _keep_stated_dates(payload, "Monthly_plan", today=_TODAY, stored="2026-11-13") is None

    assert payload["start_date"] == "2026-01-01"


def test_a_message_without_a_date_keeps_the_model_date_for_confirmation() -> None:
    missing: dict[str, object] = {"start_date": "2026-11-13"}
    assert _keep_stated_dates(missing, "Monthly plan ရောင်းပေးပါ", today=_TODAY) is None
    assert missing["start_date"] == "2026-11-13"
    assert (
        staff_notice(
            {"error": "date_unconfirmed", "field": "start_date"}, "Monthly plan ရောင်းပေးပါ"
        )
        == "ဘယ်ရက်လဲ။"
    )

    registration: dict[str, object] = {"join_date": "2026-11-13"}
    assert _keep_stated_dates(registration, "Wint War, Monthly_plan ဝယ်မယ်။", today=_TODAY) is None
    assert registration["join_date"] == "2026-11-13"

    plain: dict[str, object] = {"join_date": "2026-11-11"}
    assert _keep_stated_dates(plain, "Add Ada Lovelace", today=_TODAY) is None
    assert plain["join_date"] == "2026-11-11"


def test_an_invalid_or_ambiguous_date_explains_the_problem() -> None:
    invalid_payload: dict[str, object] = {"start_date": "2026-11-13"}
    invalid = _keep_stated_dates(invalid_payload, "32-11-2026", today=_TODAY)
    assert invalid is not None
    invalid_notice = staff_notice(_date_problem_body(invalid, "2026-11-13"), "32-11-2026")
    assert invalid_notice is not None
    assert "32-11-2026" in invalid_notice
    assert "not a real calendar day" in invalid_notice
    assert "13 November 2026" in invalid_notice
    assert invalid_payload["start_date"] == "2026-11-13"

    ambiguous = _keep_stated_dates({"start_date": "2026-01-01"}, "02-03-2026", today=_TODAY)
    assert ambiguous is not None
    ambiguous_notice = staff_notice(_date_problem_body(ambiguous, ""), "02-03-2026")
    assert ambiguous_notice is not None
    assert "02-03-2026" in ambiguous_notice
    assert "2 March 2026" in ambiguous_notice
    assert "3 February 2026" in ambiguous_notice
    assert "Which date?" in ambiguous_notice

    assert calendar_day("31-02-2026", _TODAY) == "invalid"
    assert calendar_day("02/03/2026", _TODAY) == "ambiguous"


def test_onboarding_keeps_member_class_and_pt_and_the_supplied_facts() -> None:
    state: dict[str, str] = {}
    remember_intent(state, _OPENING)
    assert state["workflow"] == "member"
    assert state["goals"] == "member,membership,class,pt"
    assert capability_brief(_OPENING, state).startswith("Relevant operation. Members")
    assert "separate steps" in capability_brief(_OPENING, state)

    remember_intent(state, _SUPPLIED)
    remember_facts(state, _SUPPLIED, _TODAY)
    assert state["workflow"] == "member"
    assert state["member_name"] == "Wint War"
    assert state["plan_code"] == "Monthly_plan"
    assert state["start_date"] == "2026-11-13"

    remember_intent(state, "ဘယ်ရက်လဲ။")
    remember_facts(state, "Which date?", _TODAY)
    remember_facts(state, "32-11-2026", _TODAY)
    assert state["start_date"] == "2026-11-13"
    assert state["workflow"] == "member"

    burmese = stored_date_reply("ဘယ်ရက်လဲ။", state)
    english = stored_date_reply("Which date?", state)
    assert burmese is not None and "2026-11-13" in burmese and "13 November 2026" in burmese
    assert "ဘယ်ရက်လဲ" not in (burmese or "")
    assert english is not None and english.startswith("The date is")
    assert "Which date" not in (english or "")
    assert direct_reply("ဘယ်ရက်လဲ။", state) == burmese

    remember_intent(state, "class တက်မယ်")
    assert state["workflow"] == "member"
    state["member_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    remember_intent(state, "class တက်မယ်")
    assert state["workflow"] == "class"
    assert state["goals"] == "member,membership,class,pt"
    assert state["member_name"] == "Wint War"


def test_a_pt_only_request_stays_personal_training() -> None:
    state: dict[str, str] = {}
    remember_intent(state, "ဝင့်ဝါကို PT ထည့်မယ်")
    remember_intent(state, "ဒီနေ့")
    remember_intent(state, "100")
    assert state["workflow"] == "pt"
    assert state.get("goals", "") == ""
    assert capability_brief("ဒီနေ့", state).startswith("Relevant operation. Personal training")


def test_a_confirmation_reply_shows_the_collected_facts_without_claiming_a_save() -> None:
    state = {
        "member_name": "Wint War",
        "plan_code": "Monthly_plan",
        "start_date": "2026-11-13",
    }
    proposal = {
        "fields": [
            {"name": "first_name", "value": "Wint"},
            {"name": "last_name", "value": "War"},
            {"name": "join_date", "value": "2026-11-13"},
        ]
    }
    reply = confirmation_reply(
        "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ။ အတည်ပြုပြီးမှ သိမ်းပါမယ်။",
        proposal,
        _SUPPLIED,
        state,
    )

    assert "Wint War" in reply
    assert "Monthly_plan" in reply
    assert "13 November 2026" in reply
    assert "2026-11-13" in reply
    assert "မသိမ်းရသေး" in reply
    assert "သိမ်းပြီး" not in reply
    assert "ဘယ်ရက်လဲ" not in reply


def test_a_side_question_keeps_the_registration_and_can_return() -> None:
    state: dict[str, str] = {}
    opening = "Register a new member named Wint War, starting 13-11-2026."
    remember_intent(state, opening)
    remember_facts(state, opening, _TODAY)
    assert state["workflow"] == "member"
    assert state["member_name"] == "Wint War"
    assert state["start_date"] == "2026-11-13"

    for question in (
        "How much is the Monthly Plan?",
        "What about the PT package?",
        "Show me today's payments.",
    ):
        remember_intent(state, question)
        remember_facts(state, question, _TODAY)
    assert state["workflow"] == "member"
    assert state["member_name"] == "Wint War"
    assert state["start_date"] == "2026-11-13"
    assert state.get("parked", "") == ""
    asked = capability_brief("How much is the Monthly Plan?", state)
    assert "Answer only this question" in asked
    assert "separate steps" not in asked
    assert information_question("How much is the Monthly Plan?")
    assert fact_tools("How much is the Monthly Plan?") == ("search_membership_plans",)
    assert fact_tools("Monthly_plan ဘယ်လောက်လဲ?") == ("search_membership_plans",)
    assert fact_tools("PT package တွေ ဘယ်လောက်ရှိလဲ?") == ("search_service_packages",)
    assert "get_gym_dashboard" in fact_tools("ဒီနေ့ Payment ဘယ်လောက်ဝင်ထားလဲ?")
    assert "search_payments" in fact_tools("ဒီနေ့ Payment ဘယ်လောက်ဝင်ထားလဲ?")
    assert fact_tools("Active member count") == ("get_gym_dashboard",)
    assert fact_tools("Available classes") == ("search_class_occurrences",)
    remember_intent(state, "Today's payments.")
    remember_intent(state, "Available classes")
    assert state["workflow"] == "member"
    assert state["start_date"] == "2026-11-13"
    assert refuse_write_for_lookup("How much is the Monthly Plan?", "write")
    assert not refuse_write_for_lookup("Add Ada Lovelace", "write")

    remember_intent(state, "Create a PT booking")
    assert state["workflow"] == "pt"
    assert state["parked"] == "member"
    resume = "Okay, register her and sell Monthly_plan."
    remember_intent(state, resume)
    remember_facts(state, resume, _TODAY)
    assert state["workflow"] == "member"
    assert state.get("parked", "") == ""
    assert state["plan_code"] == "Monthly_plan"
    assert state["start_date"] == "2026-11-13"

    absorb_read(
        state,
        "search_members",
        {
            "results": [
                {
                    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "member_number": "M-9",
                    "first_name": "Other",
                    "last_name": "Person",
                }
            ]
        },
    )
    absorb_read(
        state,
        "search_memberships",
        {"results": [{"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "start_date": "2020-01-01"}]},
    )
    assert state["member_name"] == "Wint War"
    assert state.get("member_id", "") == ""
    assert state["start_date"] == "2026-11-13"

    invented = grounded_information_reply(
        "The package is Fantasy Pack for 99999.",
        brief="Service packages: Morning PT PT 8",
        staff_text="What about the PT package?",
    )
    assert "Morning PT" in invented
    assert "Fantasy" not in invented
    assert "99999" not in invented
