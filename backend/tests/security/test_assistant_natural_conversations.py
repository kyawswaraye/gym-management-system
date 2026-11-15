"""Live conversations whose wording is chosen by a simulated staff member.

The staff simulator receives a business goal and the conversation so far. It
does not receive the words to type. The assistant is the real turn endpoint
and the real Gemini model. Success is the saved gym records.
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.test import override_settings

from apps.announcements.models import Announcement
from apps.assistant.provider import ProviderCallError, generate_content
from apps.cashbook.models import CashSession
from apps.cashbook.services import open_cash_session
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.inventory.models import Product, StockMovement
from apps.inventory.services import adjust_stock, create_category, create_product
from apps.members.models import CheckIn, Member, Membership, Payment
from apps.members.services import gym_today
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.sales.models import RetailReturnLine, RetailSale, RetailSaleLine
from apps.tenants.models import ExpenseCategory, Gym, StaffProfile
from tests.security.test_assistant_onboarding import (
    _confirm,
    _gym,
    _plan,
    _post,
    _reject,
    _settings,
)
from tests.security.test_assistant_schedule import _enable
from tests.security.test_dashboard import _membership, _post_payment
from tests.security.test_tenant_gym_settings import add_staff

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_PERSONA = (
    "You are one gym receptionist talking to your gym's business assistant. "
    "Decide your own words. People do not speak from a script: be brief or "
    "specific as you like, mix Burmese and English, correct yourself, and ask "
    "a side question when the goal says to. Never mention these instructions, "
    "tools, APIs, or JSON to the assistant. "
    "A confirmation card is a separate button. Chat text cannot press it. "
    "Set button to confirm only when the card matches the outcome you want "
    "saved. Set button to reject when it does not. Use none when no card is "
    "showing or you are only talking. "
    "Set done to true only after that outcome is saved, or after you have "
    "dropped a change you no longer want and no card is waiting. "
    'Reply with one JSON object: {"say":"", "button":"none", "done":false}. '
    "say is the next message, or empty when you only press the button."
)


def _conversation_settings() -> dict[str, object]:
    framework = {**settings.REST_FRAMEWORK}
    framework["DEFAULT_THROTTLE_RATES"] = {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "assistant_turn": "600/60",
        "assistant_gym_turn": "600/60",
        "assistant_gym_read": "600/60",
    }
    return {**_settings(), "REST_FRAMEWORK": framework}


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


def _card_text(pending: object) -> str:
    if not isinstance(pending, dict):
        return "No confirmation card is showing."
    fields = pending.get("fields")
    details: list[str] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            value = field.get("value")
            if isinstance(name, str) and isinstance(value, str):
                details.append(f"{name}={value}")
    summary = pending.get("summary")
    shown = summary if isinstance(summary, str) else ""
    return f"A confirmation card is showing: {shown}. Details: {', '.join(details)}."


def _persona_decision(goal: str, transcript: list[str], pending: object) -> dict[str, object]:
    prompt = (
        f"Your private goal:\n{goal}\n\n"
        "Conversation so far:\n"
        + ("\n".join(transcript) if transcript else "(nothing yet)")
        + "\n\n"
        + _card_text(pending)
    )
    last_error = ""
    for _attempt in range(2):
        turn = generate_content(
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            declarations=[],
            system_instruction=_PERSONA,
        )
        try:
            return _decision(turn.text)
        except ValueError as exc:
            last_error = f"{exc}: {turn.text[:500]}"
            prompt += "\n\nThe previous reply was not the JSON object. Reply with that object only."
    raise AssertionError(f"staff simulator did not decide\n{last_error}")


def _decision(text: str) -> dict[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON was not an object")
    say = parsed.get("say", "")
    button = parsed.get("button", "none")
    done = parsed.get("done", False)
    if not isinstance(say, str) or button not in {"none", "confirm", "reject"}:
        raise ValueError("JSON fields were unusable")
    if not isinstance(done, bool):
        raise ValueError("done was not a boolean")
    return {"say": say.strip(), "button": button, "done": done}


def _unavailable(body: str) -> bool:
    folded = body.casefold()
    return "unavailable" in folded or "ဆက်မပြောနိုင်" in body or "အသုံးမပြုနိုင်" in body


def _talk(
    client: object,
    headers: dict[str, str],
    payload: dict[str, object],
    live: dict[str, str],
) -> dict[str, object]:
    last: dict[str, object] = {}
    for attempt in range(3):
        with override_settings(**live):
            try:
                last = _post(client, headers, payload)
            except AssertionError as exc:
                if "503" in str(exc) or "unavailable" in str(exc).casefold():
                    time.sleep(20)
                    continue
                raise
        body = str(last["message"]["body"])
        if not _unavailable(body) or attempt == 2:
            return last
        time.sleep(20)
    return last


def _run_goal(
    *,
    client: object,
    headers: dict[str, str],
    live: dict[str, str],
    goal: str,
    limit: int,
) -> list[str]:
    transcript: list[str] = []
    conversation = ""
    pending: object = None
    for _step in range(limit):
        with override_settings(**live):
            try:
                decision = _persona_decision(goal, transcript, pending)
            except ProviderCallError as exc:
                transcript.append(f"QUOTA staff simulator: {exc.failure.value}")
                break
        button = str(decision["button"])
        say = str(decision["say"])
        if button == "confirm" and isinstance(pending, dict):
            transcript.append(f"CARD: {_card_text(pending)}")
            try:
                with override_settings(**live):
                    confirmed = _confirm(client, headers, str(pending["step_id"]))
            except AssertionError as exc:
                transcript.append(f"CONFIRM FAILED: {exc}")
                break
            transcript.append(f"BUTTON confirm outcome={confirmed.get('outcome')}")
            message = confirmed.get("message")
            if isinstance(message, dict):
                transcript.append(f"ASSISTANT: {message.get('body')}")
            pending = confirmed.get("pending_confirmation")
        elif button == "reject" and isinstance(pending, dict):
            with override_settings(**live):
                _reject(client, headers, str(pending["step_id"]))
            transcript.append("BUTTON reject")
            pending = None
        if say:
            payload: dict[str, object] = {"message": say}
            if conversation:
                payload["conversation_id"] = conversation
            try:
                turn = _talk(client, headers, payload, live)
            except ProviderCallError as exc:
                transcript.append(f"QUOTA assistant: {exc.failure.value}")
                break
            conversation = str(turn["conversation_id"])
            body = str(turn["message"]["body"]).replace("\n", " ")
            transcript.append(f"STAFF: {say}")
            transcript.append(f"ASSISTANT: {body[:800]}")
            transcript.append(f"TOOLS: {turn.get('tool_activity')}")
            pending = turn.get("pending_confirmation")
            if isinstance(pending, dict):
                transcript.append(f"CARD waiting: {_card_text(pending)}")
            if _unavailable(body):
                transcript.append("QUOTA assistant reply")
                break
        progressed = any(line.startswith(("STAFF:", "BUTTON")) for line in transcript)
        if decision["done"] and progressed and pending is None:
            break
    return transcript


def _fail(transcript: list[str], reason: str) -> None:
    raise AssertionError(reason + "\n" + "\n".join(transcript))


@override_settings(**_conversation_settings())
def test_a_staff_member_corrects_the_name_sells_and_pays_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC01")
    _plan(gym, owner.id)
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        open_cash_session(
            gym=gym,
            actor=owner,
            register_code="FRONT",
            business_date=today,
            opening_cash=Decimal("80000.00"),
            request_id="req_nc_till",
            ip_address=None,
            user_agent="",
        )
    goal = (
        "You type to the gym assistant. The member is not in this chat. "
        "Her first name is Hla and her last name is Hla. She joins today and "
        "has no phone. You briefly thought the first name was Mya, and you "
        "correct that before anything is saved. "
        "A person is saved before a membership, and a membership before a "
        "payment. Confirm a card that saves Hla Hla even when the plan and "
        "payment are not on it. Then ask the Monthly plan price and sell it "
        "starting today only if you are told 50000. She pays 20000 in cash "
        "and leaves the rest unpaid. Confirm that payment card when the amount "
        "is 20000 in cash. "
        "Once, while work is unfinished, ask how many people checked in today, "
        "then return to the unfinished work. Reject a card that would save "
        "Mya, a different price, or a payment that is not 20000 cash."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=16)
    if any(line.startswith("QUOTA") for line in transcript):
        _fail(transcript, "NOT VERIFIED: provider quota or availability stopped the conversation")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        members = list(Member.objects.filter(gym=gym))
        wrong = [item for item in members if item.first_name == "Mya"]
        saved = [item for item in members if item.first_name == "Hla" and item.last_name == "Hla"]
        memberships = list(
            Membership.objects.filter(gym=gym, member__in=saved).select_related("plan")
        )
        payments = list(Payment.objects.filter(gym=gym, member__in=saved))
    if len(saved) != 1 or wrong:
        _fail(transcript, "the saved member was not Hla Hla alone")
    member = saved[0]
    if member.join_date != today:
        _fail(transcript, f"join date was {member.join_date}, gym today is {today}")
    matching = [
        item
        for item in memberships
        if item.plan.code == "Monthly_plan" and item.start_date == today
    ]
    if len(matching) != 1:
        _fail(transcript, "the Monthly plan was not saved for today")
    paid = [
        item
        for item in payments
        if item.amount == Decimal("20000.00")
        and item.method == Payment.Method.CASH
        and item.status
        in {Payment.Status.DRAFT, Payment.Status.PENDING_APPROVAL, Payment.Status.POSTED}
    ]
    if len(paid) != 1:
        _fail(transcript, "the 20000 cash payment was not saved")
    if paid[0].membership_id != matching[0].id:
        _fail(transcript, "the payment is not attached to that membership")


@override_settings(**_conversation_settings())
def test_a_staff_member_drops_an_expense_after_changing_their_mind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC02")
    _category(gym, owner.id)
    goal = (
        "First ask the assistant to record a cash expense of 15000 today for "
        "drinking water in the Utilities category. When a confirmation card "
        "for that expense is showing, change your mind. You do not want it "
        "saved. Say so in your own words and reject that card. Stop only "
        "after the card is gone and nothing was saved."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=8)
    if any(line.startswith("QUOTA") for line in transcript):
        _fail(transcript, "NOT VERIFIED: provider quota or availability stopped the conversation")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        count = Expense.objects.filter(gym=gym).count()
    if not any(line.startswith("CARD waiting:") for line in transcript):
        _fail(transcript, "the assistant never prepared the expense")
    if any("outcome=committed" in line for line in transcript):
        _fail(transcript, "a prepared change was saved")
    if count != 0:
        _fail(transcript, f"{count} expense rows were saved")


@override_settings(**_conversation_settings())
def test_chat_agreement_does_not_save_or_claim_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, _owner, client, headers = _gym(monkeypatch, "NC03")
    goal = (
        "Ask the assistant to register a new member. First name Ko, last name "
        "Ko, joining today, no phone. When a confirmation card is showing, "
        "reply in chat that you agree, and do not press the confirm button. "
        "Then stop."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=6)
    if any(line.startswith("QUOTA") for line in transcript):
        _fail(transcript, "NOT VERIFIED: provider quota or availability stopped the conversation")
    if not any(line.startswith("CARD waiting:") for line in transcript):
        _fail(transcript, "the assistant never prepared the member")
    if any("outcome=committed" in line for line in transcript):
        _fail(transcript, "chat agreement saved the member")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        count = Member.objects.filter(gym=gym).count()
    if count != 0:
        _fail(transcript, f"chat agreement created {count} members")
    replies = [line for line in transcript if line.startswith("ASSISTANT:")]
    if replies:
        folded = replies[-1].casefold()
        claimed = ("အောင်မြင်", "ပါပြီ", "successfully", "registered", " is saved")
        if any(token in folded for token in claimed):
            _fail(transcript, "the assistant claimed the member was registered")


def _category(gym: Gym, actor_id: object) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ExpenseCategory.objects.create(
            gym=gym,
            code="UTIL",
            name="Utilities",
            normalized_name="UTILITIES",
            is_active=True,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _finance(gym: Gym) -> None:
    _enable(gym, "finance")


def _quota(transcript: list[str]) -> None:
    if any(line.startswith("QUOTA") for line in transcript):
        _fail(transcript, "NOT VERIFIED: provider quota or availability stopped the conversation")


def _on_hand(gym: Gym, product: Product) -> Decimal:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        total = StockMovement.objects.filter(gym=gym, product=product).aggregate(
            total=Sum("quantity")
        )["total"]
    return Decimal(total or 0)


@override_settings(**_conversation_settings())
def test_a_desk_receives_stock_and_can_say_what_is_on_hand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, _owner, client, headers = _gym(monkeypatch, "NC04")
    _finance(gym)
    goal = (
        "The shop has no protein bars yet. Add a product category called Snacks. "
        "Add one product named Protein Bar, SKU BAR, unit PIECE, sale price 4000, "
        "current cost 2000, default cost 2000, and a low-stock threshold of 2. "
        "Then record an opening count of 6 today, with a short reason that this "
        "is the first count. Confirm each card that matches those facts. "
        "After the stock is saved, ask how many Protein Bars are on hand and "
        "stop when the answer says 6."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=14)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        products = list(Product.objects.filter(gym=gym))
    if len(products) != 1:
        _fail(transcript, f"{len(products)} products were saved")
    product = products[0]
    if product.sale_price != Decimal("4000.00"):
        _fail(transcript, f"sale price was {product.sale_price}")
    held = _on_hand(gym, product)
    if held != Decimal("6.000"):
        _fail(transcript, f"on hand was {held}")
    if not any("6" in line or "၆" in line for line in transcript if line.startswith("ASSISTANT:")):
        _fail(transcript, "the assistant did not report the quantity it had saved")


@override_settings(**_conversation_settings())
def test_a_desk_sells_two_bars_and_takes_one_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC05")
    _finance(gym)
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=owner,
            fields={"code": "SNACKS", "name": "Snacks"},
            request_id="req_nc_category",
            ip_address=None,
            user_agent="",
        )
        product = create_product(
            gym=gym,
            actor=owner,
            fields={
                "sku": "BAR",
                "name": "Protein Bar",
                "category_id": category.id,
                "unit": "PIECE",
                "sale_price": "4000.00",
                "current_unit_cost": "2000.00",
                "default_unit_cost": "2000.00",
                "low_stock_threshold": "2.000",
            },
            request_id="req_nc_product",
            ip_address=None,
            user_agent="",
        )
        adjust_stock(
            gym=gym,
            actor=owner,
            fields={
                "product_id": product.id,
                "quantity": "10.000",
                "movement_type": "OPENING",
                "business_date": today,
                "reason": "Opening count",
            },
            request_id="req_nc_opening",
            ip_address=None,
            user_agent="",
        )
    goal = (
        "Protein Bar, SKU BAR, is already in stock at 4000 each. Sell two of "
        "them today to a walk-in customer by bank transfer. This gym requires "
        "a separate approval, so the sale is not finished until it is posted. "
        "Then the customer brings one bar back today. Record that return. "
        "Confirm cards that sell two and return one. Reject a card for a "
        "different quantity or a different product."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=16)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sales = list(RetailSale.objects.filter(gym=gym))
        posted = [sale for sale in sales if sale.status == RetailSale.Status.POSTED]
        sold = list(RetailSaleLine.objects.filter(gym=gym, product=product))
        returned = list(RetailReturnLine.objects.filter(gym=gym, sale_line__product=product))
    if len(posted) != 1:
        _fail(transcript, f"{len(posted)} posted retail sales, {len(sales)} sales in total")
    quantities = [line.quantity for line in sold]
    if Decimal("2.000") not in quantities:
        _fail(transcript, f"sold quantities were {quantities}")
    if sum((line.quantity for line in returned), Decimal("0")) != Decimal("1.000"):
        _fail(transcript, f"returned quantities were {[line.quantity for line in returned]}")
    held = _on_hand(gym, product)
    if held != Decimal("9.000"):
        _fail(transcript, f"on hand was {held}")


@override_settings(**_conversation_settings())
def test_a_manager_posts_one_salary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC06")
    _finance(gym)
    today = gym_today(gym)
    trainer = add_staff(gym, email="nc06-su@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        profile = StaffProfile.objects.create(
            gym=gym,
            gym_membership=trainer,
            staff_code="SU-SU",
            display_name="Su Su",
            joined_date=today,
            status=StaffProfile.Status.ACTIVE,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    goal = (
        "Pay Su Su for 1 November 2026 through 12 November 2026. The wages are "
        "100000 in the gym currency, by bank transfer, described as November "
        "desk wages, and the business date is today. This gym requires a "
        "separate approval, so the salary is not finished until the period is "
        "posted. Confirm cards for Su Su and 100000. Reject a card for someone "
        "else or a different amount."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=16)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        periods = list(SalaryPeriod.objects.filter(gym=gym))
        posted = [period for period in periods if period.status == SalaryPeriod.Status.POSTED]
        lines = list(StaffSalaryLine.objects.filter(gym=gym, staff_profile=profile))
    if len(posted) != 1:
        _fail(transcript, f"{len(posted)} posted salary periods, {len(periods)} in total")
    if len(lines) != 1 or lines[0].amount != Decimal("100000.00"):
        shown = [(line.amount, str(line.staff_profile_id)) for line in lines]
        _fail(transcript, f"salary lines were {shown}")


@override_settings(**_conversation_settings())
def test_a_desk_publishes_a_notice_and_opens_the_till(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, _owner, client, headers = _gym(monkeypatch, "NC07")
    _finance(gym)
    goal = (
        "Two separate jobs. First, publish a general announcement for every "
        "active member. The title is Saturday class and the message is that "
        "the morning class starts at seven. Publishing is what sends it; a "
        "draft is not enough. Second, open today's cash till with 25000 "
        "opening cash. Confirm cards that match. Do not close the till."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=12)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        notices = list(Announcement.objects.filter(gym=gym))
        published = [notice for notice in notices if notice.status == Announcement.Status.PUBLISHED]
        tills = list(CashSession.objects.filter(gym=gym, status=CashSession.Status.OPEN))
    if len(published) != 1:
        _fail(transcript, f"{len(published)} published announcements, {len(notices)} in total")
    notice = published[0]
    if notice.audience != Announcement.Audience.ALL_ACTIVE_MEMBERS:
        _fail(transcript, f"audience was {notice.audience}")
    if notice.type != Announcement.Type.GENERAL:
        _fail(transcript, f"announcement type was {notice.type}")
    if len(tills) != 1 or tills[0].opening_cash != Decimal("25000.00"):
        shown = [(till.status, till.opening_cash) for till in tills]
        _fail(transcript, f"open tills were {shown}")


@override_settings(**_conversation_settings())
def test_a_desk_refunds_part_of_a_posted_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC08")
    _finance(gym)
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.create(
            gym=gym,
            member_number="NC08-1",
            first_name="Aye",
            last_name="Aye",
            join_date=today,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    original_id = _post_payment(
        client,
        headers,
        headers,
        {
            "purpose": "OTHER",
            "description": "Visitor pass",
            "amount": "40000.00",
            "currency": gym.currency,
            "method": "BANK_TRANSFER",
            "business_date": today.isoformat(),
            "member_id": str(member.id),
        },
    )
    goal = (
        "Aye Aye, member number NC08-1, already has a posted 40000 bank "
        "transfer for a visitor pass. She overpaid. Refund 10000 of that "
        "payment, by bank transfer, today, and say the reason is an overpayment. "
        "This gym requires a separate approval, so the refund is not finished "
        "until it is posted. Do not create another charge and do not refund "
        "the whole 40000. Confirm only a 10000 refund card."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=12)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        refunds = list(
            Payment.objects.filter(
                gym=gym,
                member=member,
                entry_type=Payment.EntryType.REFUND,
            )
        )
    posted = [
        refund
        for refund in refunds
        if refund.amount == Decimal("10000.00")
        and refund.status == Payment.Status.POSTED
        and str(refund.original_payment_id) == original_id
    ]
    if len(posted) != 1:
        shown = [(refund.amount, refund.status, refund.original_payment_id) for refund in refunds]
        _fail(transcript, f"posted 10000 refunds were {len(posted)}; rows {shown}")


@override_settings(**_conversation_settings())
def test_a_desk_checks_someone_in_and_then_pauses_the_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "NC09")
    plan = _plan(gym, owner.id)
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.create(
            gym=gym,
            member_number="NC09-1",
            first_name="Min",
            last_name="Min",
            join_date=today,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    membership = _membership(
        gym,
        owner,
        member=member,
        plan=plan,
        start=today,
        expiry=today.replace(year=today.year + 1),
        amount="50000.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    goal = (
        "Min Min, member number NC09-1, is here. Check her in today. After "
        "that visit is recorded, she asks to pause her membership. Suspend "
        "that membership. Do not cancel it and do not check her out unless "
        "she asks. Confirm the check-in and the suspension."
    )
    transcript = _run_goal(client=client, headers=headers, live=live, goal=goal, limit=10)
    _quota(transcript)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        visits = CheckIn.objects.filter(gym=gym, member=member).count()
        stored = Membership.objects.get(gym=gym, id=membership.id)
    if visits != 1:
        _fail(transcript, f"{visits} check-ins were saved")
    if stored.status != Membership.Status.SUSPENDED:
        _fail(transcript, f"membership status was {stored.status}")
