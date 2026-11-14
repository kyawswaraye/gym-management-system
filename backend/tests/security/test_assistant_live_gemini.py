"""Live Gemini conversations. This file calls the configured model when opted in.

Scripted provider tests do not import this module's assertions. A missing key
skips the test instead of substituting a fake model.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from datetime import time as clock_time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.test import override_settings

from apps.common.db.tenant_context import set_gym_context
from apps.inventory.services import create_category as create_product_category
from apps.inventory.services import create_product
from apps.members.models import CheckIn, Membership, MembershipPlan
from apps.members.services import create_member, gym_today
from apps.tenants.models import Gym, GymMembership
from tests.security.test_assistant_onboarding import (
    _confirm,
    _counts,
    _field,
    _gym,
    _members,
    _plan,
    _post,
    _settings,
)
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_assistant_side_questions import _package
from tests.security.test_tenant_gym_settings import add_staff

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_OPENING = "လူသစ်တစ်ယောက် Member လုပ်မယ်။ Class တက်မယ်။ PT လည်းလုပ်မယ်။ ဘာလုပ်ရမလဲ?"
_DETAILS = "Wint War, 13-11-2026, Monthly_plan ဝယ်မယ်။"
_PRICE = "Monthly_plan ဘယ်လောက်လဲ?"
_PACKAGES = "PT package တွေ ဘယ်လောက်ရှိလဲ?"
_INCOME = "ဒီနေ့ Payment ဘယ်လောက်ဝင်ထားလဲ?"
_RESUME = "အရင် Member Registration ကို ဆက်လုပ်မယ်။"


def _live_settings() -> dict[str, str]:
    key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "")
    if os.environ.get("ASSISTANT_LIVE_GEMINI") != "1" or len(key) < 20 or not model:
        pytest.skip(
            "NOT VERIFIED: set ASSISTANT_LIVE_GEMINI=1 with GEMINI_API_KEY and GEMINI_MODEL"
        )
    return {
        "GEMINI_API_KEY": key,
        "GEMINI_MODEL": model,
        "ASSISTANT_ENABLED": "true",
        "GEMINI_TIMEOUT": "90",
        "ASSISTANT_MAX_EXECUTION_TIME": "90",
    }


def _tools(turn: dict[str, object]) -> str:
    activity = turn.get("tool_activity")
    if not isinstance(activity, list):
        return ""
    return " ".join(str(item.get("name")) for item in activity if isinstance(item, dict)).casefold()


@override_settings(**_settings())
def test_live_gemini_keeps_registration_and_answers_from_the_gym(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "GV01")
    for feature in ("members", "memberships", "pt", "reports", "finance", "classes"):
        _enable(gym, feature)
    plan = _plan(gym, owner.id)
    _package(gym, owner.id)
    transcript: list[str] = []

    def say(message: str, conversation: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if conversation:
            payload["conversation_id"] = conversation
        with override_settings(**live):
            turn = _post(client, headers, payload)
        body = str(turn["message"]["body"]).replace("\n", " ")
        transcript.append(f"USER: {message}")
        transcript.append(f"ASSISTANT: {body[:500]} TOOLS: {_tools(turn) or 'none'}")
        return turn

    try:
        opened = say(_OPENING)
        conversation = str(opened["conversation_id"])
        assert opened["pending_confirmation"] is None
        assert _counts(gym) == (0, 0, 0, 0)

        supplied = say(_DETAILS, conversation)
        assert _counts(gym) == (0, 0, 0, 0)

        priced = say(_PRICE, conversation)
        assert "50000" in str(priced["message"]["body"])
        assert priced["pending_confirmation"] is not None
        assert _counts(gym) == (0, 0, 0, 0)
        assert "membership plan" in _tools(priced) or "50000" in str(priced["message"]["body"])

        packaged = say(_PACKAGES, conversation)
        package_body = str(packaged["message"]["body"])
        assert "Morning PT" in package_body
        assert "80000" in package_body.replace(",", "")
        assert "package" in _tools(packaged) or "Morning PT" in package_body
        assert packaged["pending_confirmation"] is not None
        assert _counts(gym) == (0, 0, 0, 0)

        income = say(_INCOME, conversation)
        assert _counts(gym) == (0, 0, 0, 0)
        assert income["pending_confirmation"] is not None
        income_tools = _tools(income)
        assert "dashboard" in income_tools or "payment" in income_tools

        resumed = say(_RESUME, conversation)
        pending = resumed["pending_confirmation"] or supplied["pending_confirmation"]
        assert isinstance(pending, dict), "no confirmation after resume"
        dated = _field(pending, "join_date") or _field(pending, "start_date")
        if dated:
            assert dated == "2026-11-13"
        assert _counts(gym) == (0, 0, 0, 0)

        during_payment = say("Today's payments.", conversation)
        during_members = say("Active member count.", conversation)
        during_classes = say("Available classes.", conversation)
        assert during_payment["pending_confirmation"] is not None
        assert during_members["pending_confirmation"] is not None
        assert during_classes["pending_confirmation"] is not None
        assert _counts(gym) == (0, 0, 0, 0)
        assert "dashboard" in _tools(during_members) or _empty_or_zero(
            str(during_members["message"]["body"])
        )
        assert "class" in _tools(during_classes) or _empty_or_zero(
            str(during_classes["message"]["body"])
        )

        with override_settings(**live):
            created = _confirm(client, headers, str(pending["step_id"]))
        assert created["outcome"] == "committed"
        members = _members(gym)
        assert len(members) == 1
        assert members[0].first_name == "Wint"
        assert members[0].last_name == "War"
        assert members[0].join_date.isoformat() == "2026-11-13"
        assert _counts(gym)[0] == 1
        assert plan.code == "Monthly_plan"
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n" + "\n".join(transcript)) from None


def _empty_or_zero(body: str) -> bool:
    folded = body.casefold()
    return "no " in folded or "none" in folded or "မတွေ့" in body or "မရှိ" in body or "0" in body


@override_settings(**_settings())
def test_live_gemini_answers_modules_and_resumes_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "GV02")
    for feature in ("members", "memberships", "pt", "reports", "finance", "classes"):
        _enable(gym, feature)
    _plan(gym, owner.id)
    _package(gym, owner.id)
    transcript: list[str] = []

    def say(message: str, conversation: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if conversation:
            payload["conversation_id"] = conversation
        turn: dict[str, object] = {}
        for attempt in range(3):
            with override_settings(**live):
                turn = _post(client, headers, payload)
            if "assistant" not in _tools(turn) or attempt == 2:
                break
            time.sleep(5)
        body = str(turn["message"]["body"]).replace("\n", " ")
        transcript.append(f"USER: {message}")
        transcript.append(f"ASSISTANT: {body[:700]} TOOLS: {_tools(turn) or 'none'}")
        return turn

    try:
        catalog = say("How much is Monthly_plan, and what PT packages are available?")
        conversation = str(catalog["conversation_id"])
        catalog_body = str(catalog["message"]["body"]).replace(",", "")
        assert "50000" in catalog_body
        assert "Morning PT" in catalog_body
        assert "80000" in catalog_body
        assert "membership plan" in _tools(catalog)
        assert "package" in _tools(catalog)
        assert catalog["pending_confirmation"] is None
        assert _counts(gym) == (0, 0, 0, 0)

        members = say("Active member count.", conversation)
        assert "dashboard" in _tools(members)
        assert "0" in str(members["message"]["body"])
        payments = say("Today's payments.", conversation)
        classes = say("Available classes.", conversation)
        expenses = say("Any expenses today?", conversation)
        cash = say("Any open cash sessions?", conversation)
        announcements = say("Any announcements?", conversation)
        salary = say("Any salary periods?", conversation)
        retail = say("Any retail sales?", conversation)
        for turn in (payments, classes, expenses, cash, announcements, salary, retail):
            assert turn["pending_confirmation"] is None
            assert _empty_or_zero(str(turn["message"]["body"]))
        assert _counts(gym) == (0, 0, 0, 0)

        opened = say(_OPENING, conversation)
        assert opened["pending_confirmation"] is None
        assert _counts(gym) == (0, 0, 0, 0)
        named = say("Wint War", conversation)
        assert named["pending_confirmation"] is None
        assert "သိမ်းပြီး" not in str(named["message"]["body"])
        assert _counts(gym) == (0, 0, 0, 0)

        supplied = say("13-11-2026, Monthly_plan ဝယ်မယ်။", conversation)
        assert _counts(gym) == (0, 0, 0, 0)
        interrupted = say("Any expenses today?", conversation)
        assert interrupted["pending_confirmation"] is not None
        assert _empty_or_zero(str(interrupted["message"]["body"]))
        assert _counts(gym) == (0, 0, 0, 0)

        resumed = say(_RESUME, conversation)
        pending = resumed["pending_confirmation"] or supplied["pending_confirmation"]
        assert isinstance(pending, dict), "no confirmation after resume"
        dated = _field(pending, "join_date") or _field(pending, "start_date")
        if dated:
            assert dated == "2026-11-13"
        assert _counts(gym) == (0, 0, 0, 0)
        with override_settings(**live):
            created = _confirm(client, headers, str(pending["step_id"]))
        assert created["outcome"] == "committed"
        stored = _members(gym)
        assert len(stored) == 1
        assert stored[0].first_name == "Wint"
        assert stored[0].last_name == "War"
        assert stored[0].join_date.isoformat() == "2026-11-13"
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n" + "\n".join(transcript)) from None


def _seed_business_rows(gym: Gym, owner: GymMembership) -> None:
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = create_member(
            gym=gym,
            actor=owner,
            member_number="",
            first_name="Wint",
            last_name="War",
            phone=None,
            email=None,
            gender=None,
            date_of_birth=None,
            address={},
            join_date=today,
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
        )
        plan = MembershipPlan.objects.create(
            gym=gym,
            code="READPLAN",
            name="Read Plan",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("100.00"),
            currency=gym.currency,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
        membership = Membership.objects.create(
            gym=gym,
            member=member,
            plan=plan,
            plan_code=plan.code,
            plan_name=plan.name,
            duration_value=1,
            duration_unit=plan.duration_unit,
            start_date=today,
            expiry_date=today.replace(day=30),
            list_price=plan.price,
            discount_amount=Decimal("0.00"),
            final_amount=plan.price,
            currency=gym.currency,
            payment_status=Membership.PaymentStatus.PAID,
            status=Membership.Status.ACTIVE,
            revenue_source=Membership.RevenueSource.NEW_MEMBER,
            created_by_membership_id=owner.id,
        )
        CheckIn.objects.create(
            gym=gym,
            member=member,
            membership=membership,
            check_in_at=datetime.combine(today, clock_time(9, 0), tzinfo=ZoneInfo(gym.timezone)),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        category = create_product_category(
            gym=gym,
            actor=owner,
            fields={"code": "BOTTLE", "name": "Bottles"},
            request_id="req_live_category",
            ip_address=None,
            user_agent="",
        )
        create_product(
            gym=gym,
            actor=owner,
            fields={
                "category_id": category.id,
                "sku": "LOCAL1",
                "name": "Local Bottle",
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "default_unit_cost": "4.00",
                "low_stock_threshold": "5.000",
            },
            request_id="req_live_product",
            ip_address=None,
            user_agent="",
        )
    desk = add_staff(gym, email="gv03-desk@example.test", role_key="RECEPTIONIST")
    _profile(gym, desk, code="DESK", name="Desk One")


@override_settings(**_settings())
def test_live_gemini_answers_business_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "GV03")
    for feature in ("members", "memberships", "pt", "reports", "finance", "classes"):
        _enable(gym, feature)
    assert isinstance(owner, GymMembership)
    _seed_business_rows(gym, owner)
    transcript: list[str] = []

    def say(message: str, conversation: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if conversation:
            payload["conversation_id"] = conversation
        turn: dict[str, object] = {}
        for attempt in range(3):
            with override_settings(**live):
                turn = _post(client, headers, payload)
            if "assistant" not in _tools(turn) or attempt == 2:
                break
            time.sleep(5)
        body = str(turn["message"]["body"]).replace("\n", " ")
        transcript.append(f"USER: {message}")
        transcript.append(f"ASSISTANT: {body[:700]} TOOLS: {_tools(turn) or 'none'}")
        return turn

    try:
        arrived = say("ဒီနေ့ Gym ကို Member ဘယ်နှယောက်လာပြီးပြီလဲ။")
        conversation = str(arrived["conversation_id"])
        arrived_body = str(arrived["message"]["body"])
        assert "check-in" in _tools(arrived) or "1" in arrived_body or "၁" in arrived_body
        assert "Wint" in arrived_body or "1" in arrived_body or "၁" in arrived_body
        assert arrived["pending_confirmation"] is None
        assert _counts(gym) == (1, 1, 0, 0)

        expiring = say("ဒီလ Membership သက်တမ်းကုန်မယ့်သူတွေ ဘယ်သူတွေလဲ။", conversation)
        expiring_body = str(expiring["message"]["body"])
        assert "expir" in _tools(expiring) or "membership" in _tools(expiring)
        assert "Wint" in expiring_body
        assert "2026-11-30" in expiring_body or "30" in expiring_body or "၃၀" in expiring_body
        assert expiring["pending_confirmation"] is None

        yesterday = say("မနေ့က Check-in ဝင်ထားတဲ့သူတွေ ပြပေးပါ။", conversation)
        yesterday_body = str(yesterday["message"]["body"])
        assert "check-in" in _tools(yesterday)
        assert _empty_or_zero(yesterday_body)
        assert "Wint" not in yesterday_body

        stock = say("Stock နည်းနေတဲ့ Product တွေ ဘာတွေရှိလဲ။", conversation)
        assert "low-stock" in _tools(stock) or "low stock" in _tools(stock)
        assert "Local Bottle" in str(stock["message"]["body"])
        assert stock["pending_confirmation"] is None

        english = say("Which memberships expire this month?", conversation)
        assert "Wint" in str(english["message"]["body"])
        assert english["pending_confirmation"] is None

        access = say("Staff တစ်ယောက်ချင်းစီ ဘာ Permission တွေရထားလဲ။", conversation)
        access_body = str(access["message"]["body"])
        assert "permission" in _tools(access) or "staff" in _tools(access)
        assert "Desk One" in access_body
        assert "gv03-desk@example.test" not in access_body
        assert access["pending_confirmation"] is None

        money = say("ဒီလ ဝင်ငွေ၊ အသုံးစရိတ်၊ အမြတ် ဘယ်လောက်ရှိလဲ။", conversation)
        assert "dashboard" in _tools(money)
        assert money["pending_confirmation"] is None
        assert _counts(gym) == (1, 1, 0, 0)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n" + "\n".join(transcript)) from None


@override_settings(**_settings())
def test_live_gemini_answers_the_remaining_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "GV04")
    for feature in ("members", "memberships", "pt", "reports", "finance", "classes"):
        _enable(gym, feature)
    assert isinstance(owner, GymMembership)
    _seed_business_rows(gym, owner)
    transcript: list[str] = []

    def say(message: str, conversation: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if conversation:
            payload["conversation_id"] = conversation
        turn: dict[str, object] = {}
        for attempt in range(3):
            with override_settings(**live):
                turn = _post(client, headers, payload)
            if "assistant" not in _tools(turn) or attempt == 2:
                break
            time.sleep(5)
        body = str(turn["message"]["body"]).replace("\n", " ")
        transcript.append(f"USER: {message}")
        transcript.append(f"ASSISTANT: {body[:500]} TOOLS: {_tools(turn) or 'none'}")
        return turn

    try:
        trainers = say("List the trainers.")
        conversation = str(trainers["conversation_id"])
        assert "trainer" in _tools(trainers)
        assert trainers["pending_confirmation"] is None

        assigned = say("Which trainer is assigned how many members?", conversation)
        assert "assignment" in _tools(assigned)
        assert _empty_or_zero(str(assigned["message"]["body"]))

        granted = say("Who bought a PT package?", conversation)
        assert (
            "granted" in _tools(granted)
            or "package" in _tools(granted)
            or "pt sales" in _tools(granted)
        )
        assert _empty_or_zero(str(granted["message"]["body"]))

        sales = say("Show PT sales.", conversation)
        assert "pt sales" in _tools(sales)
        assert _empty_or_zero(str(sales["message"]["body"]))

        classes = say("How many classes are there today?", conversation)
        assert "class" in _tools(classes)
        assert _empty_or_zero(str(classes["message"]["body"]))

        schedules = say("Show the class schedule definitions.", conversation)
        assert "schedule" in _tools(schedules)

        products = say("Search products.", conversation)
        assert "product" in _tools(products)
        assert "Local Bottle" in str(products["message"]["body"])

        movements = say("Show stock movements.", conversation)
        assert "stock movement" in _tools(movements)

        ranked = say("Which products are the best selling this month?", conversation)
        assert "product sales" in _tools(ranked) or "retail" in _tools(ranked)
        assert _empty_or_zero(str(ranked["message"]["body"]))

        roles = say("List the roles.", conversation)
        assert "role" in _tools(roles)
        assert roles["pending_confirmation"] is None

        trainers_sales = say(
            "ဘယ် Trainer က PT အများဆုံးရောင်းထားလဲ? Answer from posted sales only.",
            conversation,
        )
        assert "search_pt_trainer_sales" in _tools(trainers_sales)
        assert _empty_or_zero(str(trainers_sales["message"]["body"]))
        assert trainers_sales["pending_confirmation"] is None
        assert _counts(gym) == (1, 1, 0, 0)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n" + "\n".join(transcript)) from None
