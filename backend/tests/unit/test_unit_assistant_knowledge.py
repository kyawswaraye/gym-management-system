from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.knowledge import (
    DOMAIN_MAP,
    assistant_context,
    exact_code,
    gate_sentence,
    grounded_read_reply,
    included_sections,
    knowledge_capability,
    public_activity,
    refuse_write_for_lookup,
    staff_notice,
    stated_amount,
    stored_listing_reply,
    unnamed_member_payment_reply,
    unsupported_lookup_reply,
    visible_reply,
    widen_list_filters,
)
from apps.assistant.lookups import _keep_stated_dates
from apps.assistant.registry import production_registry
from apps.assistant.turns import _SYSTEM, _handle_call, _read


def test_domain_map_covers_the_gym_records() -> None:
    text = " ".join(DOMAIN_MAP.split()).casefold()
    for phrase in (
        "member",
        "membership",
        "check-in",
        "ACTIVE, EXPIRED, SUSPENDED, CANCELLED, or ARCHIVED",
        "trainer assignment",
        "service package",
        "PT booking",
        "class type",
        "class booking",
        "DRAFT, then PENDING_APPROVAL",
        "cash session",
        "receipt",
        "salary period",
        "retail",
        "stock movements",
        "announcements",
        "does not send mail",
        "do not invent",
        "no daily shift roster",
        "cannot be retrieved",
        "not a substitute for a read",
        "not supported",
        "not saved until the server confirms",
    ):
        assert phrase.casefold() in text
    assert "do not say a tool is missing" not in text


def test_turn_answers_lists_from_the_brief() -> None:
    text = " ".join(_SYSTEM.split()).casefold()
    assert "live gym brief" in text
    assert "cannot retrieve" in text
    assert "do not call a write tool" in text
    assert "before the server confirms" in text
    assert "no daily shift roster" in text
    assert "date_unconfirmed" in text
    assert "time_unconfirmed" in text
    assert "do not invent a date" in text
    assert "do not choose one" in text
    assert "when the staff write in burmese" in text
    assert "myanmar digits have the" in text
    assert "never say a tool is missing" not in text


def test_live_sections_follow_permissions() -> None:
    assert included_sections(frozenset(), False) == frozenset()
    assert included_sections(frozenset({"members.view"}), False) == frozenset({"members"})
    assert "payments" not in included_sections(frozenset({"members.view"}), False)
    assert included_sections(frozenset(), True) == included_sections(
        frozenset({"members.view", "payments.view", "inventory.view"}),
        True,
    )
    assert "members" in included_sections(frozenset(), True)
    assert "cash" in included_sections(frozenset({"cashbook.view"}), False)


def test_failed_brief_still_sends_the_domain_map(monkeypatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("database")

    monkeypatch.setattr("apps.assistant.knowledge.gym_facts", fail)
    text = assistant_context(SimpleNamespace(name="Brief Gym"), frozenset(), False)  # type: ignore[arg-type]
    assert "GymPilot is the staff application" in text
    assert "Live gym brief unavailable" in text
    assert "Brief Gym" in text


def test_capabilities_follow_registered_tools() -> None:
    registry = production_registry()
    names = registry.names()
    reads = {name for name in names if registry.get(name).operation == "read"}
    writes = [name for name in names if registry.get(name).operation == "write"]

    assert knowledge_capability("payments") == "read"
    assert knowledge_capability("members") == "read"
    assert {"search_payments", "get_payment", "search_members"} <= reads
    assert knowledge_capability("checkins") == "write_only"
    assert knowledge_capability("pt_sales") == "write_only"
    assert knowledge_capability("inventory") == "write_only"
    assert not any(registry.get(name).operation == "read" and "check_in" in name for name in reads)
    assert knowledge_capability("audit") == "unsupported"
    assert knowledge_capability("roster") == "unsupported"
    assert knowledge_capability("mail") == "unsupported"
    assert len(writes) == 98
    assert len(reads) == 14


def test_an_unsupported_read_does_not_invent_a_record() -> None:
    reply = unsupported_lookup_reply("Show the check-in history")

    assert reply is not None
    assert "cannot look up check-in history" in reply
    assert "ACTIVE" not in reply
    assert "2026-" not in reply
    assert "saved" not in reply.casefold()
    assert unsupported_lookup_reply("List payments") is None
    assert unsupported_lookup_reply("Add Ada Lovelace") is None
    assert unsupported_lookup_reply("Approve the payment") is None
    assert unsupported_lookup_reply("How many check-ins today") is None
    named = unsupported_lookup_reply("Show Aung Aung's check-ins")
    assert named is not None
    assert "cannot look up check-in history" in named
    assert "Aung Aung" in named
    assert "ACTIVE" not in named
    assert unsupported_lookup_reply("Who attended the class") is None


def test_a_burmese_unsupported_domain_keeps_exact_tokens() -> None:
    reply = unsupported_lookup_reply("Aung Aung ရဲ့ ဝင်ရောက်မှတ်တမ်း ပြပါ။ MEM-000101 2026-11-11")

    assert reply is not None
    assert "မဖတ်နိုင်" in reply
    assert "Aung Aung" in reply
    assert "MEM-000101" in reply
    assert "2026-11-11" in reply
    assert "MEM-000102" not in reply
    assert "create_manual_check_in" not in reply
    assert "ACTIVE" not in reply


def test_a_lookup_does_not_use_a_write_tool(monkeypatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("write was prepared")

    monkeypatch.setattr("apps.assistant.turns.resolve_references", fail)
    proposal, body = _handle_call(
        request=cast(Request, SimpleNamespace()),
        view=APIView(),
        gym=cast(object, SimpleNamespace()),  # type: ignore[arg-type]
        actor=cast(object, SimpleNamespace()),  # type: ignore[arg-type]
        conversation=cast(object, SimpleNamespace()),  # type: ignore[arg-type]
        name="create_manual_check_in",
        arguments={},
        offered=frozenset({"create_manual_check_in"}),
        proposal=None,
        staff_text="Aung Aung ရဲ့ ဝင်ရောက်မှတ်တမ်း ပြပါ",
    )

    assert proposal is None
    assert body == {"error": "not_a_read"}
    assert refuse_write_for_lookup("List payments", "write")
    assert not refuse_write_for_lookup("List payments", "read")
    assert not refuse_write_for_lookup("Add Ada Lovelace", "write")


def test_permission_denial_and_raw_exceptions_stay_off_the_reply(monkeypatch) -> None:
    def denied(**_kwargs: object) -> dict[str, object]:
        raise PermissionDenied("payments.view missing for gym row")

    monkeypatch.setattr("apps.assistant.turns.execute_offered_read", denied)
    hidden = _read(cast(Request, SimpleNamespace()), APIView(), "search_payments", {})
    notice = staff_notice(hidden, "ငွေပေးချေမှု ပြပါ")

    assert hidden == {"error": "permission_denied"}
    assert notice == "ဒီအချက်အလက်ကို ကြည့်ခွင့် မရှိပါ။"
    assert "payments.view" not in str(hidden)
    assert notice is not None
    assert "payments.view" not in notice

    def exploded(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("relation payments does not exist\nTraceback (most recent call last)")

    monkeypatch.setattr("apps.assistant.turns.execute_offered_read", exploded)
    failed = _read(cast(Request, SimpleNamespace()), APIView(), "search_payments", {})
    assert failed == {"error": "unavailable"}
    assert "relation" not in str(failed)
    assert "Traceback" not in str(failed)

    def leaked(**_kwargs: object) -> dict[str, object]:
        raise ExecutionRejected("SELECT * FROM payments", code="SELECT * FROM payments")

    monkeypatch.setattr("apps.assistant.turns.execute_offered_read", leaked)
    coded = _read(cast(Request, SimpleNamespace()), APIView(), "search_payments", {})
    assert coded == {"error": "unavailable"}
    assert "SELECT" not in str(coded)

    staff = visible_reply(
        "search_payments failed: OperationalError traceback",
        confirmed=False,
        proposed=False,
        staff_text="List payments",
    )
    assert "search_payments" not in staff
    assert "OperationalError" not in staff
    assert "Traceback" not in staff.casefold()


def test_burmese_amounts_keep_the_same_value() -> None:
    assert stated_amount("၅၀၀၀၀") == "50000"
    assert Decimal(stated_amount("၅၀၀၀၀") or "0") == Decimal("50000")
    assert stated_amount("၈၀,၀၀၀") == "80000"  # noqa: RUF001
    assert Decimal(stated_amount("၈၀,၀၀၀") or "0") == Decimal("80000")  # noqa: RUF001
    assert stated_amount("၁,၅၀၀.၅၀") == "1500.50"
    assert Decimal(stated_amount("၁,၅၀၀.၅၀") or "0") == Decimal("1500.50")
    assert exact_code("MEM-၀၀၀၁၀၁") == "MEM-000101"
    assert exact_code("WATER-B") == "WATER-B"


def test_burmese_dates_ask_instead_of_inventing_one() -> None:
    missing_day: dict[str, object] = {"starts_at": "2026-11-12T09:00:00+06:30"}
    field = _keep_stated_dates(missing_day, "မနက် ၉ နာရီ book လုပ်ပေးပါ", today=date(2026, 11, 11))
    notice = staff_notice({"error": "date_unconfirmed", "field": field}, "မနက် ၉ နာရီ book လုပ်ပေးပါ")

    assert field == "starts_at"
    assert notice == "ဘယ်ရက်လဲ။"
    assert "2026-11-12" not in (notice or "")

    missing_time: dict[str, object] = {"starts_at": "2026-11-12T09:00:00+06:30"}
    time_field = _keep_stated_dates(
        missing_time,
        "မနက်ဖြန် booking လုပ်ပေးပါ",
        today=date(2026, 11, 11),
    )
    time_notice = staff_notice(
        {"error": "time_unconfirmed", "field": "starts_at"},
        "မနက်ဖြန် booking လုပ်ပေးပါ",
    )

    assert time_field == "time:starts_at"
    assert time_notice == "အချိန် ဘယ်လောက်လဲ။"
    assert "09:00" not in (time_notice or "")


def test_missing_or_ambiguous_facts_ask_without_internal_names() -> None:
    missing = staff_notice(
        {"error": "missing_fields", "fields": ["member_id", "amount"]},
        "ငွေချေမယ်",
    )
    ambiguous = staff_notice(
        {
            "error": "ambiguous",
            "field": "member_id",
            "matches": [
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "name": "Aung Aung (MEM-000101)",
                    "date": "",
                },
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "Aung Aung (MEM-000102)",
                    "date": "2026-11-11",
                },
            ],
        },
        "Aung Aung ကို ရှာပါ",
    )

    assert missing == "ဘယ်အဖွဲ့ဝင်လဲ။"
    assert "member_id" not in (missing or "")
    assert ambiguous is not None
    assert "MEM-000101" in ambiguous
    assert "MEM-000102" in ambiguous
    assert "2026-11-11" in ambiguous
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" not in ambiguous
    assert "member_id" not in ambiguous


def test_known_validation_errors_are_staff_sentences() -> None:
    burmese = "အဖွဲ့ဝင်ကို ငွေချေမယ်"
    notices = {
        "member_inactive": "ဒီအဖွဲ့ဝင်က အသုံးမပြုနိုင်ပါ။",
        "plan_inactive": "ဒီအစီအစဉ်က အသုံးမပြုနိုင်ပါ။",
        "class_full": "ဒီအတန်းမှာ နေရာမကျန်ပါ။",
        "duplicate_booking": "ဒီအဖွဲ့ဝင်က ဒီအတန်းမှာ ဘိုကင်ရှိပြီးပါပြီ။",
        "class_package_required": "ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။",
        "stock_short": "တောင်းတဲ့ အရေအတွက်အတွက် လက်ကျန်မလုံလောက်ပါ။",
        "quantity_unconfirmed": "အရေအတွက် ဘယ်လောက်လဲ။",
        "quantity_invalid": "အရေအတွက်ကို ကိန်းပြည့် အပေါင်းနဲ့ ပြောပေးပါ။",
        "refund_exceeds": "ပြန်အမ်းငွေက ပြန်အမ်းနိုင်တဲ့ ပမာဏထက် များနေပါတယ်။",
        "payment_state": "ဒီငွေပေးချေမှုကို အခုအခြေအနေမှာ ပြင်လို့ မရပါ။",
        "retail_charge": "ဒီပစ္စည်းအရောင်းကို ပြန်အမ်းလမ်းကြောင်းကနေ လုပ်ပါ။",
        "amount_unconfirmed": "ပမာဏ ဘယ်လောက်လဲ။",
        "time_order": "အဆုံးအချိန်က စတင်အချိန်ထက် နောက်ကျရပါမယ်။",
    }
    for code, sentence in notices.items():
        assert staff_notice({"error": code}, burmese) == sentence
        assert "RetailCorrectionUnavailable" not in sentence
        assert code not in sentence

    overlap = staff_notice(
        {
            "error": "membership_overlap",
            "plan": "Monthly",
            "start": "2026-11-01",
            "end": "2026-11-30",
        },
        burmese,
    )
    package = staff_notice({"status": "package_required", "member_id": "hidden"}, burmese)
    missing_plan = staff_notice(
        {"error": "missing_fields", "fields": ["plan_id"]},
        burmese,
    )
    english = gate_sentence("class_full", "The class is full")

    assert overlap is not None
    assert "Monthly" in overlap
    assert "2026-11-01" in overlap
    assert "MembershipOverlap" not in overlap
    assert package == "ဒီဘိုကင်အတွက် လက်ကျန်ရှိတဲ့ PT ပက်ကေ့ လိုပါတယ်။ PT အရောင်းကို အရင်တင်ပါ။"
    assert "PT" not in (staff_notice({"error": "class_package_required"}, burmese) or "")
    assert missing_plan == "ဘယ်အစီအစဉ်လဲ။"
    assert english == "That class has no remaining places."
    assert "create_class_booking" not in english


def test_a_change_is_not_called_saved_before_the_server_confirms() -> None:
    waiting = visible_reply(
        "The payment is saved.",
        confirmed=False,
        proposed=True,
        staff_text="Record a payment",
    )
    done = visible_reply("Done.", confirmed=False, proposed=True, staff_text="Add Ada")
    kept = visible_reply(
        "The member is saved.",
        confirmed=True,
        proposed=False,
        staff_text="Add Ada",
    )
    burmese = visible_reply(
        "သိမ်းပြီးပါပြီ။",
        confirmed=False,
        proposed=True,
        staff_text="ငွေချေမယ်",
    )

    assert waiting == "That change is not saved until you confirm."
    assert "is saved" not in waiting
    assert done == "That change is not saved until you confirm."
    assert kept == "The member is saved."
    assert "မသိမ်းရသေး" in burmese
    leaked = visible_reply(
        "SELECT * FROM payments",
        confirmed=False,
        proposed=False,
        staff_text="List payments",
    )
    assert leaked == "I could not complete that."
    assert "SELECT" not in leaked


def test_public_activity_uses_the_description_and_a_safe_status() -> None:
    shown = public_activity("search_payments", "completed")
    hidden = public_activity("not_a_real_tool", "SELECT * FROM payments")

    assert shown == {"name": "Search payments in this gym", "status": "completed"}
    assert hidden == {"name": "Lookup", "status": "completed"}
    assert "search_payments" not in shown["name"]
    assert "_" not in hidden["name"]


def test_a_read_answer_keeps_only_stored_or_brief_facts() -> None:
    stored = [
        {
            "results": [
                {
                    "first_name": "Aung",
                    "last_name": "Aung",
                    "amount": "80000",
                    "status": "ACTIVE",
                    "business_date": "2026-11-11",
                    "member_number": "MEM-000101",
                }
            ]
        }
    ]
    invented = grounded_read_reply(
        "Hla Hla paid 99999 on 1999-01-01 and the status is POSTED.",
        results=stored,
        brief="Live gym brief. Today is 2026-11-12.",
        staff_text="Show payments",
    )
    faithful = grounded_read_reply(
        "Aung Aung, MEM-000101, is ACTIVE. Amount 80000 on 2026-11-11.",
        results=stored,
        brief="Live gym brief. Today is 2026-11-12.",
        staff_text="Show payments",
    )
    counted = grounded_read_reply(
        "There is 1 ACTIVE member.",
        results=[{"results": []}],
        brief="Members by status: ACTIVE 1.",
        staff_text="How many members",
    )
    empty = grounded_read_reply(
        "Aung Aung paid 50000.",
        results=[{"results": []}],
        brief="Live gym brief. Today is 2026-11-12.",
        staff_text="List payments",
    )
    burmese = grounded_read_reply(
        "Aung Aung က ၅၀၀၀၀ ပေးထားတယ်။",
        results=[{"results": []}],
        brief="Today is 2026-11-12.",
        staff_text="ငွေပေးချေမှု ပြပါ",
    )

    assert "Hla" not in invented
    assert "99999" not in invented
    assert "1999-01-01" not in invented
    assert "POSTED" not in invented
    assert "80000" in invented
    assert "Aung" in invented
    assert "ACTIVE" in invented
    assert "2026-11-11" in invented
    assert "MEM-000101" in invented
    assert faithful.startswith("Aung Aung")
    assert counted == "There is 1 ACTIVE member."
    assert empty == "No stored record matches that."
    assert "Aung" not in empty
    assert "50000" not in empty
    assert burmese == "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။"
    assert "50000" not in burmese
    assert "၅၀၀၀၀" not in burmese


def test_a_list_without_a_read_does_not_invent_rows() -> None:
    sales = "Search retail sales and expenses. List each amount and description."
    missed = stored_listing_reply(sales, {})
    assert missed is not None
    assert "will not guess" in missed
    assert "Cleaning" not in missed
    assert "500" not in missed
    assert "1000" not in missed
    assert "2000" not in missed

    stored = stored_listing_reply(
        sales,
        {
            "search_sales": {
                "results": [
                    {
                        "status": "POSTED",
                        "total_amount": "15.00",
                        "business_date": "2026-11-11",
                        "name": "Desk Water",
                    }
                ]
            },
            "search_expenses": {
                "results": [
                    {
                        "status": "POSTED",
                        "amount": "25.00",
                        "business_date": "2026-11-11",
                        "description": "Desk towels",
                    }
                ]
            },
        },
    )
    assert stored is not None
    assert "15.00" in stored
    assert "Desk Water" in stored
    assert "25.00" in stored
    assert "Desk towels" in stored
    assert "Cleaning" not in stored
    assert "Marketing" not in stored


def test_salary_and_cash_lists_keep_every_stored_row() -> None:
    question = "Search salary periods and cash sessions. List status and business dates only."
    missed = stored_listing_reply(question, {})
    assert missed is not None
    assert "2026-10-31" not in missed
    assert "will not guess" in missed

    stored = stored_listing_reply(
        question,
        {
            "search_salary_periods": {
                "results": [
                    {
                        "status": "POSTED",
                        "period_start": "2026-11-11",
                        "period_end": "2026-11-11",
                    }
                ]
            },
            "search_cash_sessions": {
                "results": [
                    {"status": "CLOSED", "business_date": "2026-11-11"},
                    {"status": "OPEN", "business_date": "2026-11-11"},
                ]
            },
        },
    )
    assert stored is not None
    assert "2026-11-11" in stored
    assert "2026-10-31" not in stored
    assert "CLOSED" in stored
    assert "OPEN" in stored


def test_a_general_class_list_does_not_keep_today_filter() -> None:
    question = "Search class occurrences and class bookings."
    opened = widen_list_filters(
        "search_class_occurrences",
        {"date_from": "2026-11-12", "date_to": "2026-11-12", "limit": 25},
        question,
        date(2026, 11, 12),
    )
    bookings = widen_list_filters(
        "search_bookings",
        {"date_from": "2026-11-12", "status": "BOOKED"},
        question,
        date(2026, 11, 12),
    )
    today = widen_list_filters(
        "search_class_occurrences",
        {"date_from": "2026-11-11", "limit": 25},
        "Which classes are scheduled today?",
        date(2026, 11, 12),
    )
    stated = widen_list_filters(
        "search_class_occurrences",
        {"date_from": "2026-11-12", "date_to": "2026-11-12"},
        "Search class occurrences on 2026-11-18.",
        date(2026, 11, 12),
    )
    cash = widen_list_filters(
        "search_cash_sessions",
        {"status": "OPEN", "business_date": "2026-10-31"},
        "Search salary periods and cash sessions.",
        date(2026, 11, 12),
    )

    assert "date_from" not in opened
    assert "date_to" not in opened
    assert opened["limit"] == 25
    assert "date_from" not in bookings
    assert today["date_from"] == "2026-11-12"
    assert today["date_to"] == "2026-11-12"
    assert stated["date_from"] == "2026-11-18"
    assert stated["date_to"] == "2026-11-18"
    assert "status" not in cash
    assert "business_date" not in cash
    today_reply = stored_listing_reply(
        "Which classes are scheduled today?",
        {"search_class_occurrences": {"results": []}},
    )
    assert today_reply is not None
    assert "limited to today" in today_reply


def test_duplicate_plan_names_ask_for_a_code() -> None:
    question = "Which membership plan is named Yoga?"
    reply = stored_listing_reply(
        question,
        {
            "search_membership_plans": {
                "ambiguous": True,
                "results": [
                    {"code": "YOGA", "name": "Yoga", "status": "ACTIVE", "price": "50000.00"},
                    {"code": "YOGA_2", "name": "Yoga", "status": "INACTIVE", "price": "50000.00"},
                ],
            }
        },
    )
    catalog = stored_listing_reply(
        "Search memberships and membership plans.",
        {
            "search_memberships": {
                "results": [
                    {
                        "plan_name": "Desk Month",
                        "status": "ACTIVE",
                        "member_number": "MEM-000001",
                    }
                ]
            },
            "search_membership_plans": {
                "results": [
                    {"code": "DESK_MONTH", "name": "Desk Month", "status": "ACTIVE"},
                    {"code": "PROBE", "name": "Probe", "status": "INACTIVE"},
                    {"code": "YOGA", "name": "Yoga", "status": "ACTIVE"},
                    {"code": "YOGA_2", "name": "Yoga", "status": "INACTIVE"},
                ]
            },
        },
    )

    assert reply is not None
    assert "Which plan code?" in reply
    assert "YOGA" in reply
    assert "YOGA_2" in reply
    assert "INACTIVE" in reply
    assert "only one" not in reply.casefold()
    assert catalog is not None
    assert "PROBE" in catalog
    assert "YOGA_2" in catalog
    assert "MEM-000001" in catalog


def test_an_unnamed_member_payment_asks_instead_of_listing_rows() -> None:
    reply = unnamed_member_payment_reply("What did the member pay?")
    named = unnamed_member_payment_reply("What did Hla Win pay?")
    numbered = unnamed_member_payment_reply("What did member MEM-000001 pay?")
    ambiguous = stored_listing_reply(
        "What did Aung Aung pay?",
        {
            "search_members": {
                "ambiguous": True,
                "results": [
                    {"member_number": "MEM-000002", "first_name": "aung"},
                    {"member_number": "MEM-000003", "first_name": "Aung"},
                    {"member_number": "MEM-000004", "first_name": "Aung"},
                ],
            },
            "search_payments": {"results": [{"amount": "15.00", "purpose": "PRODUCT"}]},
        },
    )

    assert reply == "Which member? Tell me the member number."
    assert named is None
    assert numbered is None
    assert ambiguous is not None
    assert "MEM-000002" in ambiguous
    assert "MEM-000003" in ambiguous
    assert "MEM-000004" in ambiguous
    assert "15.00" not in ambiguous
    assert "PRODUCT" not in ambiguous
