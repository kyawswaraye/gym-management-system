"""Date retention and onboarding state. These tests do not touch a gym database."""

from __future__ import annotations

from datetime import date

from apps.assistant.knowledge import (
    grounded_information_reply,
    information_question,
    staff_notice,
)
from apps.assistant.lookups import (
    _date_problem_body,
    _keep_stated_dates,
    calendar_day,
)
from apps.assistant.workflow_state import (
    absorb_read,
    confirmation_reply,
    stored_date_reply,
)

_TODAY = date(2026, 11, 12)


def test_a_definition_keeps_the_class_package_sentence() -> None:
    kept = grounded_information_reply(
        "Membership is gym access for a period. Class Package covers 10 sessions.",
        brief="Today is 2026-11-12.",
        staff_text="Membership နဲ့ class package ဘာကွာလဲ?",
    )
    assert "membership" in kept.casefold()
    assert "class" in kept.casefold()
    assert "10" not in kept


_SUPPLIED = "Wint War, 13-11-2026, Monthly_plan ဝယ်မယ်။"


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


def test_a_stored_date_is_repeated_instead_of_asked_again() -> None:
    state = {
        "member_name": "Wint War",
        "plan_code": "Monthly_plan",
        "start_date": "2026-11-13",
    }
    burmese = stored_date_reply("ဘယ်ရက်လဲ။", state)
    english = stored_date_reply("Which date?", state)
    assert burmese is not None and "2026-11-13" in burmese and "13 November 2026" in burmese
    assert "ဘယ်ရက်လဲ" not in burmese
    assert english is not None and english.startswith("The date is")
    assert "Which date" not in english


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


def test_an_open_registration_is_not_replaced_by_a_lookup() -> None:
    state = {
        "workflow": "member",
        "member_name": "Wint War",
        "start_date": "2026-11-13",
    }
    assert information_question("How much is the Monthly Plan?")
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
    assert invented == ""
