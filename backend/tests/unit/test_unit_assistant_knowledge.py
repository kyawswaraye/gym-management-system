from __future__ import annotations

import re
from datetime import date
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
    localized_summary,
    public_activity,
    recovery_sentence,
    reply_needs_fresh_read,
    staff_notice,
    states_stored_facts,
    unconfirmed_notice,
    visible_reply,
)
from apps.assistant.lookups import _keep_stated_dates
from apps.assistant.registry import production_registry
from apps.assistant.turns import _SYSTEM, _handle_call, _read
from apps.assistant.workflows import guide_text


def test_domain_map_covers_the_gym_records() -> None:
    text = " ".join(DOMAIN_MAP.split()).casefold()
    for phrase in (
        "member",
        "membership",
        "ACTIVE, EXPIRED, SUSPENDED, CANCELLED, or ARCHIVED",
        "DRAFT, then PENDING_APPROVAL",
        "cash session",
        "one-off email",
        "do not invent",
        "no daily shift roster",
        "cannot be retrieved",
        "not a substitute for a read",
        "not supported",
        "not saved until the server confirms",
        "not a member membership",
        "pending_payment_amount",
        "myanmar digits",
    ):
        assert phrase.casefold() in text
    assert "do not say a tool is missing" not in text
    guides = guide_text(
        (
            "check_in",
            "personal_training",
            "class_lifecycle",
            "retail_inventory",
            "salaries",
            "announcements",
            "reports",
        )
    ).casefold()
    for phrase in (
        "check-in",
        "create_pt_booking",
        "create_class_type",
        "create_class_booking",
        "create_salary_period",
        "retail",
        "stock",
        "announcement",
        "get_gym_report",
        "do not invent a cause",
    ):
        assert phrase.casefold() in guides


def test_turn_answers_lists_from_the_brief() -> None:
    text = " ".join(_SYSTEM.split()).casefold()
    assert "live gym brief" in text
    assert "cannot retrieve" in text
    assert "do not call a write tool" in text
    assert "before the server confirms" in text
    assert "no daily shift roster" in text
    assert "do not invent" in text
    assert "do not choose" in text
    assert "reply in the staff member's language" in text
    reports = guide_text(("reports",)).casefold()
    assert "get_gym_report" in reports
    assert "get_trainer_dashboard" in reports
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

    assert {"search_payments", "get_payment", "search_members"} <= reads
    assert "search_check_ins" in reads
    assert "search_membership_expiries" in reads
    assert "search_low_stock" in reads
    assert "search_staff_access" in reads
    assert "search_service_packages" in reads
    assert len(writes) == 98
    assert len(reads) == 36


def test_a_question_does_not_hide_a_write_tool(monkeypatch) -> None:
    def resolve(
        *_args: object, **_kwargs: object
    ) -> tuple[dict[str, object], dict[str, str], None]:
        return {}, {}, None

    def absent(*_args: object, **_kwargs: object) -> None:
        return None

    def opened(**_kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        return {"summary": "Check in"}, {"status": "confirmation_required"}

    monkeypatch.setattr("apps.assistant.turns.resolve_references", resolve)
    monkeypatch.setattr("apps.assistant.turns.unmetered_gate", absent)
    monkeypatch.setattr("apps.assistant.turns.assignment_gap", absent)
    monkeypatch.setattr("apps.assistant.turns.existing_assignment", absent)
    monkeypatch.setattr("apps.assistant.turns.booking_package_gap", absent)
    monkeypatch.setattr("apps.assistant.turns.proposal_block", absent)
    monkeypatch.setattr("apps.assistant.turns.missing_fields", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("apps.assistant.turns._open_proposal", opened)
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

    assert proposal is not None
    assert body["status"] == "confirmation_required"


def test_permission_denial_and_raw_exceptions_stay_off_the_reply(monkeypatch) -> None:
    def denied(**_kwargs: object) -> dict[str, object]:
        raise PermissionDenied("payments.view missing for gym row")

    monkeypatch.setattr("apps.assistant.turns.execute_offered_read", denied)
    hidden = _read(cast(Request, SimpleNamespace()), APIView(), "search_payments", {})
    notice = staff_notice(hidden, "ငွေပေးချေမှု ပြပါ")

    assert hidden == {"error": "permission_denied"}
    assert notice == "ဒီအချက်အလက်ကို ကြည့်ခွင့် မရှိပါ။ ခွင့်ရှိတဲ့ အချက်ကို မေးပါ။"
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

    priced = visible_reply(
        "Call search_membership_plans for Monthly_plan. Monthly_plan is 50,000.00 THB.",
        confirmed=False,
        proposed=False,
        staff_text="How much is Monthly_plan",
    )
    assert "Monthly_plan" in priced
    assert "search_membership_plans" not in priced
    assert "50,000.00" in priced


def test_burmese_digits_in_a_code_keep_the_same_value() -> None:
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

    assert time_field is None
    assert missing_time["starts_at"] == "2026-11-12T09:00:00+06:30"


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
        "member_inactive": "ဒီအဖွဲ့ဝင်က အသုံးမပြုနိုင်ပါ။ အသုံးပြုနိုင်တဲ့ အဖွဲ့ဝင်ကို ပြောပါ။",
        "plan_inactive": "ဒီအစီအစဉ်က အသုံးမပြုနိုင်ပါ။ အသုံးပြုနိုင်တဲ့ အစီအစဉ်ကို ပြောပါ။",
        "class_full": "ဒီအတန်းမှာ နေရာမကျန်ပါ။ အခြားအတန်းကို ရွေးပါ။",
        "duplicate_booking": "ဒီအဖွဲ့ဝင်က ဒီအတန်းမှာ ဘိုကင်ရှိပြီးပါပြီ။ အခြားအတန်းကို ရွေးပါ။",
        "class_package_required": ("ဒီအတန်းအတွက် လက်ကျန်ရှိတဲ့ အတန်းပက်ကေ့ လိုပါတယ်။ အတန်းပက်ကေ့ကို အရင် ရောင်းပါ။"),
        "stock_short": ("တောင်းတဲ့ အရေအတွက်အတွက် လက်ကျန်မလုံလောက်ပါ။ လက်ကျန်ထက် မပိုတဲ့ အရေအတွက်ကို ပြောပါ။"),
        "quantity_unconfirmed": "အရေအတွက် ဘယ်လောက်လဲ။",
        "quantity_invalid": "အရေအတွက်ကို ကိန်းပြည့် အပေါင်းနဲ့ ပြောပေးပါ။",
        "refund_exceeds": ("ပြန်အမ်းငွေက ပြန်အမ်းနိုင်တဲ့ ပမာဏထက် များနေပါတယ်။ အဲဒီပမာဏထက် မပိုအောင် ပြောပါ။"),
        "payment_state": ("ဒီငွေပေးချေမှုကို အခုအခြေအနေမှာ ပြင်လို့ မရပါ။ အခြေအနေနဲ့ ကိုက်တဲ့ နောက်တစ်ဆင့်ကို ပြောပါ။"),
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
    assert english == "That class has no remaining places. Choose another class."
    assert "create_class_booking" not in english


def test_a_failed_step_names_the_state_and_the_next_action() -> None:
    burmese = "ငွေချေမယ်"
    pending = recovery_sentence(
        {"error": "finance_state", "status": "PENDING_APPROVAL"},
        burmese,
    )
    own = recovery_sentence(
        {"error": "finance_state", "status": "PENDING_APPROVAL"},
        burmese,
        can_continue=False,
    )
    posted = recovery_sentence(
        {"error": "not_pending", "status": "POSTED"},
        burmese,
    )
    draft = staff_notice({"error": "not_pending", "status": "DRAFT"}, burmese)
    amount = staff_notice({"error": "amount_unconfirmed"}, burmese)
    assignment = staff_notice({"error": "assignment_required"}, burmese)
    unknown = recovery_sentence({"error": "something_else"}, burmese)
    rejected = recovery_sentence({"error": "rejected"}, "Confirm the payment")

    assert pending == "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ ဆက်လုပ်ဖို့ အတည်ပြုချက်ကို အတည်ပြုပါ။"
    assert own == "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ အတည်ပြုခွင့်ရှိတဲ့ အခြားဝန်ထမ်းက အတည်ပြုရပါမယ်။"
    assert posted == "ဒီမှတ်တမ်းကို တင်ပြီးပါပြီ။ ထပ်သိမ်းစရာ မလိုပါ။"
    assert draft == "ဒီမှတ်တမ်းက မူကြမ်းဖြစ်နေပါသေးတယ်။ အတည်မပြုခင် အရင် တင်သွင်းပါ။"
    assert amount == "ပမာဏ ဘယ်လောက်လဲ။"
    assert "မရပါ" not in (amount or "")
    assert assignment == "ဒီရက်မှာ သင်တန်းဆရာကို အဖွဲ့ဝင်နဲ့ တာဝန်မပေးရသေးပါ။ အရောင်းမတိုင်ခင် တာဝန်ပေးပါ။"
    assert unknown == "ဒီအဆင့်ကို မလုပ်နိုင်သေးပါ။ ပြောထားတဲ့ အချက်ကို ပြန်ပြောပေးပါ။"
    assert rejected == (
        "That change was not saved. Say the step again with the record it applies to."
    )
    for sentence in (pending, own, posted, draft, amount, assignment, unknown, rejected):
        assert sentence is not None
        assert "submit_payment" not in sentence
        assert "approve_payment" not in sentence


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
    registered = visible_reply(
        "အဖွဲ့ဝင်အသစ် Nwe Nwe ကို အောင်မြင်စွာ မှတ်ပုံတင်လိုက်ပါပြီ။",
        confirmed=False,
        proposed=True,
        staff_text="yes",
    )
    finished = visible_reply(
        "အပြီးသတ်ပေးလိုက်ပါပြီ။",
        confirmed=False,
        proposed=True,
        staff_text="yes",
    )

    assert waiting == "That change is not saved until you confirm."
    assert "is saved" not in waiting
    assert done == "That change is not saved until you confirm."
    assert kept == "The member is saved."
    assert "မသိမ်းရသေး" in burmese
    assert "မသိမ်းရသေး" in registered
    assert "အောင်မြင်" not in registered
    assert "မသိမ်းရသေး" in finished
    assert "ပါပြီ" not in finished
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
    mixed = grounded_read_reply(
        "Aung Aung paid 80000. Hla Hla paid 99999.",
        results=stored,
        brief="Live gym brief. Today is 2026-11-12.",
        staff_text="Show payments",
    )
    formatted = grounded_read_reply(
        "Revenue is 1,500.00 THB.",
        results=[{"financial": {"total_revenue": "1500.00"}}],
        brief="Currency THB.",
        staff_text="Explain revenue",
    )
    assert formatted == "Revenue is 1,500.00 THB."
    scaled = grounded_read_reply(
        "The price is 1.00.",
        results=[{"code": "Monthly_plan", "price": "50000.00", "duration_value": 1}],
        brief="Live gym brief.",
        staff_text="How much is the Monthly Plan?",
    )
    assert "1.00" not in scaled
    assert "50000" in scaled
    assert mixed.startswith("Aung Aung paid 80000")
    assert "99999" not in mixed
    assert "Hla" not in mixed
    assert "Stored records" not in mixed
    assert counted == "There is 1 ACTIVE member."
    assert empty == "No stored record matches that."
    assert "Aung" not in empty
    assert "50000" not in empty
    assert burmese == "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။"
    assert "50000" not in burmese
    assert "၅၀၀၀၀" not in burmese


def test_invented_person_with_no_rows_is_not_shown() -> None:
    invented = "Invented Person paid 99999 on 1999-01-01."
    english = grounded_read_reply(
        invented,
        results=[{"results": []}],
        brief="Live gym brief. Today is 2026-11-16.",
        staff_text="Search members.",
    )
    burmese = grounded_read_reply(
        invented,
        results=[{"results": []}],
        brief="Today is 2026-11-16.",
        staff_text="အဖွဲ့ဝင် စာရင်း ရှာ",
    )

    assert english == "No stored record matches that."
    assert burmese == "သိမ်းထားတဲ့ မှတ်တမ်း မတွေ့ပါ။"
    for hidden in ("Invented Person", "99999", "1999-01-01"):
        assert hidden not in english
        assert hidden not in burmese


_NOVEMBER = {
    "range": {"date_from": "2026-11-01", "date_to": "2026-11-14"},
    "operational": {"new_members": 6, "check_ins": 4},
    "financial": {
        "new_member_revenue": "1500.00",
        "renewal_revenue": "0.00",
        "pt_revenue": "8000.00",
        "class_revenue": "0.00",
        "product_revenue": "0.00",
        "other_revenue": "0.00",
        "total_revenue": "9500.00",
        "total_expenses": "5825.00",
        "net_profit": "3675.00",
    },
}


def test_total_revenue_is_not_relabeled_as_membership_sales() -> None:
    membership = grounded_read_reply(
        "Membership sales are 9,500.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How much are membership sales?",
    )
    total = grounded_read_reply(
        "Total revenue is 9,500.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How much is total revenue?",
    )
    training = grounded_read_reply(
        "PT sales are 8,000.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How much are PT sales?",
    )
    classes = grounded_read_reply(
        "Class revenue is 9,500.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How much is class revenue?",
    )
    burmese = grounded_read_reply(
        "အသင်းဝင်အရောင်း 9500 ဖြစ်တယ်။",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="အသင်းဝင်အရောင်း ဘယ်လောက်လဲ",
    )
    counted = grounded_read_reply(
        "There are 6 new members.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How many new members?",
    )
    miscounted = grounded_read_reply(
        "New member revenue is 6.",
        results=[_NOVEMBER],
        brief="Currency THB.",
        staff_text="How much is new member revenue?",
    )

    assert "1500.00" in membership
    assert "9,500" not in membership
    assert "9500" not in membership
    assert total == "Total revenue is 9,500."
    assert training == "PT sales are 8,000."
    assert "0.00" in classes
    assert "9,500" not in classes
    assert "1500.00" in burmese
    assert "9500" not in burmese
    assert counted == "There are 6 new members."
    assert "1500.00" in miscounted
    assert re.search(r"\b6\b", miscounted) is None


def test_a_derived_percent_stays_and_a_wrong_percent_does_not_leave_a_sign() -> None:
    stored = [
        {
            "results": [
                {"name": "Maya Htun", "posted_amount": "8000.00", "assignments": 3},
                {"name": "Hla Hla", "posted_amount": "6400.00", "assignments": 2},
            ]
        }
    ]
    derived = grounded_read_reply(
        "Maya Htun is 25% ahead of Hla Hla.",
        results=stored,
        brief="Trainer comparison.",
        staff_text="Compare the trainers",
    )
    wrong = grounded_read_reply(
        "Maya Htun is 30% ahead of Hla Hla.",
        results=stored,
        brief="Trainer comparison.",
        staff_text="Compare the trainers",
    )
    added = grounded_read_reply(
        "Together that is 14400.",
        results=stored,
        brief="Trainer comparison.",
        staff_text="Compare the trainers",
    )
    invented = grounded_read_reply(
        "Together that is 9999.",
        results=stored,
        brief="Trainer comparison.",
        staff_text="Compare the trainers",
    )
    duration = grounded_read_reply(
        "The class lasts 45 minutes.",
        results=[{"name": "Yoga", "default_duration_minutes": 60}],
        brief="Classes.",
        staff_text="How long is the class?",
    )
    pending = visible_reply(
        grounded_read_reply(
            "Maya Htun is 30% ahead of Hla Hla. The payment is saved.",
            results=stored,
            brief="Trainer comparison.",
            staff_text="Compare the trainers",
        ),
        confirmed=False,
        proposed=True,
        staff_text="Compare the trainers",
    )

    assert derived == "Maya Htun is 25% ahead of Hla Hla."
    assert "%" not in wrong
    assert "30" not in wrong
    assert "Maya Htun" in wrong
    assert "Hla Hla" in wrong
    assert "14400" in added
    assert "9999" not in invented
    assert "45" not in duration
    assert "%" not in pending
    assert "is saved" not in pending.casefold()
    assert "not saved" in pending.casefold()


def test_grounding_drops_a_removed_list_item_in_burmese_and_english() -> None:
    brief = "Live gym brief. Today is 2026-11-12."
    burmese = grounded_read_reply(
        "အဆင့်တွေက ၁။ Member အသစ် စာရင်းသွင်းခြင်း။ "
        "၂။ Hla Hla ကို 1999-01-01 မှာ စာရင်းသွင်းမယ်။ "
        "၃။ Class တက်ဖို့ အတန်းရွေးမယ်။",
        results=(),
        brief=brief,
        staff_text="ဘာလုပ်ရမလဲ?",
        fallback=False,
    )
    english = grounded_read_reply(
        "1. Register the member. 2. Hla Hla is not stored. 3. Choose a class.",
        results=(),
        brief=brief,
        staff_text="What are the steps?",
        fallback=False,
    )
    bullets = grounded_read_reply(
        "- Register the member.\n- Hla Hla is not stored.\n- Choose a class.",
        results=(),
        brief=brief,
        staff_text="What are the steps?",
        fallback=False,
    )
    example = grounded_read_reply(
        "ပြီးမှ Class နဲ့ PT ကို ဆက်လုပ်လို့ရပါတယ်။ (ဥပမာ - 2026-11-12)",
        results=(),
        brief=brief,
        staff_text="ဘာလုပ်ရမလဲ?",
        fallback=False,
    )

    assert "၁။" in burmese
    assert "Member အသစ် စာရင်းသွင်းခြင်း" in burmese
    assert "၃။" in burmese
    assert "Class တက်ဖို့" in burmese
    assert "၂။" not in burmese
    assert "Hla" not in burmese
    assert "1999" not in burmese
    assert "။ ။" not in burmese
    assert burmese.strip("။ ") != ""
    assert "1." in english
    assert "Register the member" in english
    assert "3." in english
    assert "Choose a class" in english
    assert "2." not in english
    assert "Hla" not in english
    assert "- Register the member." in bullets
    assert "- Choose a class." in bullets
    assert "Hla" not in bullets
    assert "ဥပမာ" not in example
    assert "2026-11-12" not in example
    assert "ဆက်လုပ်လို့ရပါတယ်" in example


def test_product_field_labels_stay_with_their_values_in_english_and_burmese() -> None:
    product = {
        "results": [
            {
                "sku": "LOCAL1",
                "name": "Local Bottle",
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "quantity_on_hand": "0.000",
                "low_stock_threshold": "5.000",
                "status": "ACTIVE",
            }
        ]
    }
    labeled = (
        "**Name:** Local Bottle **SKU:** LOCAL1 **Status:** ACTIVE "
        "**Quantity on Hand:** 0.000 BTL **Sale Price:** 10.00 THB "
        "**Current Unit Cost:** 4.00 THB **Low Stock Threshold:** 5.000 BTL"
    )
    english = visible_reply(
        grounded_read_reply(
            f"There is one product in the system: {labeled} Call search_products. "
            "Hla Hla paid 999.00.",
            results=[product],
            brief="Products.",
            staff_text="Search products.",
        ),
        confirmed=False,
        proposed=False,
        staff_text="Search products.",
    )
    burmese = visible_reply(
        grounded_read_reply(
            f"ပစ္စည်းတစ်ခု ရှိပါတယ်။ {labeled} "
            "**sale_price:** 10.00 THB **unit_cost:** 4.00 THB **reorder_level:** 5.000 BTL",
            results=[product],
            brief="Products.",
            staff_text="ပစ္စည်း ရှာပါ။",
        ),
        confirmed=False,
        proposed=False,
        staff_text="ပစ္စည်း ရှာပါ။",
    )

    for shown in (english, burmese):
        assert "Local Bottle" in shown
        assert "LOCAL1" in shown
        assert "ACTIVE" in shown
        assert "0.000" in shown
        assert "10.00" in shown
        assert "4.00" in shown
        assert "5.000" in shown
        assert "**:**" not in shown
        assert "Sale Price" in shown or "Sale price" in shown
        assert "Current Unit Cost" in shown or "Unit cost" in shown
        assert "Low Stock Threshold" in shown or "Reorder level" in shown
    assert "search_products" not in english
    assert "Hla" not in english
    assert "999" not in english
    assert "Sale price" in burmese
    assert "Unit cost" in burmese
    assert "Reorder level" in burmese
    assert "ရှိပါတယ်" in burmese


def test_burmese_proposal_summary_and_notice_follow_the_conversation() -> None:
    summary = localized_summary("Create a member.", burmese=True)
    priced = localized_summary("Create a retail sale draft. Amount 30.00.", burmese=True)
    english = localized_summary("Create a member.", burmese=False)
    side_question = unconfirmed_notice("Any expenses today?")
    burmese_turn = unconfirmed_notice("မြန်မာ")

    assert "Create a member" not in summary
    assert summary.endswith("။")
    assert "အဖွဲ့ဝင်" in summary
    assert english == "Create a member."
    assert "Create a retail sale" not in priced
    assert "30.00" in priced
    assert "ပမာဏ" in priced
    assert side_question == "That change is not saved until you confirm."
    assert burmese_turn == "ဒီပြောင်းလဲမှုကို မသိမ်းရသေးပါ။ အတည်ပြုပြီးမှ သိမ်းပါမယ်။"
    assert states_stored_facts("There are currently no trainer assignments in the system.")
    assert not states_stored_facts("A membership is a purchased term for one member.")
    brief = "Active trainer assignments: 0.\nProducts: Spring Water sku 5.000"
    assert reply_needs_fresh_read(
        "Which trainer is assigned how many members?",
        "There are currently no trainer assignments in the system.",
        brief,
    )
    assert not reply_needs_fresh_read(
        "Spring Water လက်ကျန် ဘယ်လောက်လဲ",
        "Spring Water လက်ကျန် 5။",
        brief,
    )
    assert not reply_needs_fresh_read(
        "Which date?",
        "Wint War, 13 November 2026 (2026-11-13).",
        "Wint War, 13-11-2026, Monthly_plan",
    )
    assert not reply_needs_fresh_read(
        "What is a membership?",
        "A membership is a purchased term for one member.",
        brief,
    )
