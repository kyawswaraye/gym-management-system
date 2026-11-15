"""Real Gemini coverage for every registered assistant tool.

Staff messages describe a business outcome. They do not name tools. Coverage
is the tool Gemini selected, taken from the turn log, plus a committed write
row when the stored confirmation card matched that outcome.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.test import override_settings

from apps.assistant.configuration import ProviderFailure
from apps.assistant.models import ToolExecution, WorkflowStep
from apps.assistant.provider import ProviderCallError
from apps.assistant.registry import production_registry
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.models import Member, Membership
from apps.members.payment_services import create_payment_draft
from apps.members.services import gym_today
from apps.tenants.models import Gym
from tests.security.test_assistant_natural_conversations import (
    _card_text,
    _category,
    _conversation_settings,
    _live_settings,
    _unavailable,
)
from tests.security.test_assistant_onboarding import _confirm, _gym, _plan, _post, _reject
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_dashboard import _membership
from tests.security.test_tenant_gym_settings import PASSWORD, add_staff, staff_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_REPORT = "/tmp/gympilot-assistant-coverage.json"


@dataclass
class Ask:
    message: str
    confirm: tuple[str, ...] = ()
    needles: tuple[str, ...] = ()
    hold: tuple[str, ...] = ()


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.events: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        start = message.find("tools=")
        if start < 0:
            return
        chunk = message[start + 6 :].split(" ", 1)[0]
        if chunk == "none":
            return
        for piece in chunk.split(","):
            name, _, status = piece.partition(":")
            if name and name != "assistant":
                self.events.append((name, status or "unknown"))


class _Session:
    def __init__(
        self,
        client: object,
        headers: dict[str, str],
        live: dict[str, str],
        gym: Gym,
        owner: object,
    ):
        self.client = client
        self.headers = headers
        self.live = live
        self.gym = gym
        self.owner = owner
        self.conversation = ""
        self.capture = _Capture()
        self.notes: list[str] = []
        self.committed: set[str] = set()
        self.ask_hit = False
        self.stopped = ""
        logger = logging.getLogger("apps.assistant.turns")
        logger.setLevel(logging.INFO)
        logger.addHandler(self.capture)

    def close(self) -> None:
        logging.getLogger("apps.assistant.turns").removeHandler(self.capture)

    def refresh(self) -> None:
        self.headers["HTTP_AUTHORIZATION"] = f"Bearer {staff_token(self.owner)}"

    def as_staff(self, membership: object) -> None:
        self.headers["HTTP_AUTHORIZATION"] = f"Bearer {staff_token(membership)}"
        self.conversation = ""

    def as_owner(self) -> None:
        self.refresh()
        self.conversation = ""

    def fresh(self) -> None:
        self.refresh()
        self.conversation = ""

    def say(self, message: str) -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if self.conversation:
            payload["conversation_id"] = self.conversation
        last_error = ""
        for attempt in range(3):
            with override_settings(**self.live):
                try:
                    turn = _post(self.client, self.headers, payload)
                except ProviderCallError as exc:
                    self.stopped = f"provider {exc.failure.value}"
                    return {}
                except AssertionError as exc:
                    last_error = str(exc)
                    if "ACCESS_TOKEN_EXPIRED" in last_error:
                        self.refresh()
                        continue
                    if "503" in last_error or "429" in last_error:
                        time.sleep(15)
                        continue
                    self.stopped = f"turn failed: {exc}"
                    return {}
            body = _body(turn)
            if _provider_down(body) and attempt < 2:
                time.sleep(15)
                continue
            self.conversation = str(turn.get("conversation_id") or "")
            if _provider_down(body):
                self.stopped = "provider unavailable"
            return turn
        self.stopped = last_error or "provider unavailable"
        return {}

    def ask(self, item: Ask) -> None:
        if self.stopped:
            return
        self.ask_hit = False
        turn = self._once(item)
        if self.stopped or not turn:
            return
        if (item.confirm or item.hold) and not self.ask_hit:
            pending = turn.get("pending_confirmation")
            if isinstance(pending, dict):
                self._reject(str(pending["step_id"]))
            turn = self._once(item)
            if self.stopped or not turn:
                return
        pending = turn.get("pending_confirmation")
        if isinstance(pending, dict):
            tool = self._tool(str(pending["step_id"]))
            if tool in item.hold:
                self.notes.append(f"holding {tool}")
                return
            self._reject(str(pending["step_id"]))

    def _once(self, item: Ask) -> dict[str, object]:
        turn = self.say(item.message)
        if self.stopped or not turn:
            return turn
        return self._settle(turn, item)

    def _settle(self, turn: dict[str, object], item: Ask) -> dict[str, object]:
        pending = turn.get("pending_confirmation")
        while isinstance(pending, dict) and not self.stopped:
            step_id = str(pending["step_id"])
            tool = self._tool(step_id)
            blob = _card_text(pending).casefold()
            matched = all(needle.casefold() in blob for needle in item.needles)
            if tool in item.hold and matched:
                self.ask_hit = True
                return turn
            if tool in item.confirm and matched:
                try:
                    with override_settings(**self.live):
                        confirmed = _confirm(self.client, self.headers, step_id)
                except AssertionError as exc:
                    self.notes.append(f"confirm {tool} failed: {exc}")
                    self._reject(step_id)
                    return turn
                outcome = str(confirmed.get("outcome") or "")
                if outcome in {"committed", "existing"}:
                    self.committed.add(tool)
                    self.ask_hit = True
                    self.notes.append(f"committed {tool} outcome={outcome}")
                else:
                    self.notes.append(f"confirm {tool} outcome={outcome}")
                pending = confirmed.get("pending_confirmation")
                turn = confirmed
                continue
            break
        return turn

    def _reject(self, step_id: str) -> None:
        try:
            with override_settings(**self.live):
                _reject(self.client, self.headers, step_id)
        except AssertionError as exc:
            self.notes.append(f"reject failed: {exc}")

    def _tool(self, step_id: str) -> str:
        with transaction.atomic():
            set_gym_context(gym_id=self.gym.id)
            step = WorkflowStep.objects.filter(gym=self.gym, id=step_id).first()
        return step.tool_name if step is not None else ""


def _slow_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one timestamp moving without burning the 10-minute access token."""

    from django.utils import timezone as django_timezone

    start = django_timezone.now()

    class _Slow:
        def __init__(self) -> None:
            self.current = start

        def __call__(self):
            self.current += timedelta(milliseconds=1)
            return self.current

    monkeypatch.setattr(django_timezone, "now", _Slow())


def _features(gym: Gym) -> None:
    for code in ("members", "memberships", "finance", "pt", "reports", "classes", "announcements"):
        _enable(gym, code)


def _seed(gym: Gym, owner: object) -> tuple[Member, object]:
    _features(gym)
    trainer = add_staff(gym, email="cv01-trainer@example.test", role_key="TRAINER")
    spare = add_staff(gym, email="cv01-spare@example.test", role_key="TRAINER")
    su = _profile(gym, trainer, code="TRN", name="Su Su")
    _profile(gym, spare, code="SPR", name="Mya Mya")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        su.default_commission_percent = Decimal("20.00")
        su.save(update_fields=["default_commission_percent"])
    plan = _plan(gym, owner.id)
    _category(gym, owner.id)
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.create(
            gym=gym,
            member_number="CV01-1",
            first_name="Min",
            last_name="Min",
            join_date=today,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
        create_payment_draft(
            gym=gym,
            actor=owner,
            fields={
                "purpose": "OTHER",
                "description": "Day pass",
                "amount": Decimal("15000.00"),
                "currency": gym.currency,
                "method": "CASH",
                "business_date": today,
            },
            request_id="req_cv_day_pass",
            ip_address=None,
            user_agent="",
        )
    _membership(
        gym,
        owner,
        member=member,
        plan=plan,
        start=today,
        expiry=today + timedelta(days=30),
        amount="50000.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    return member, trainer


def _reads(day: str) -> list[Ask]:
    questions = (
        "How many members are stored, and which of them are named Min?",
        "Open the record for member number CV01-1 and tell me the join date.",
        "Which membership plans are active, and what does the Monthly plan cost?",
        "Which memberships are active, and which ones expire within 30 days?",
        "Which PT packages and service packages are offered?",
        "Which trainers work here, and who is assigned to a member?",
        "Which staff profiles are active?",
        "Which roles exist, and which permission codes can a role hold?",
        "Which permission codes does Su Su hold?",
        "Which announcements are stored?",
        f"Give me the gym dashboard for {day}, including revenue and expenses.",
        f"Give me the official gym report for {day}.",
        "Give me the trainer dashboard.",
        "Which payments are stored, including the day pass?",
        "Open the day pass payment and tell me its amount and status.",
        "Which expenses are stored?",
        "Which retail sales are stored?",
        "Which salary periods are stored?",
        "Which cash sessions are stored?",
        "Which PT commissions are stored?",
        "Which products are in the catalog?",
        "Which stock movements are stored?",
        "Which products are at or below their low-stock threshold?",
        "Which products have sold?",
        "Which class types exist?",
        "Which class schedules exist?",
        f"Which class occurrences are on {day}?",
        "Which class bookings are stored?",
        f"Which check-ins are stored for {day}?",
        "Which PT sales are stored?",
        "Which PT bookings are stored?",
        "Which PT sales are grouped by trainer?",
    )
    return [Ask(question) for question in questions]


def _writes(day: str, currency: str) -> list[tuple[str, list[Ask]]]:
    money = currency
    return [
        (
            "members",
            [
                Ask(
                    f"Register a new member. First name Nyein, last name Nyein, "
                    f"join date {day}, no phone.",
                    ("create_member",),
                    ("Nyein", day),
                ),
                Ask(
                    "Update Nyein Nyein's phone to +66812345001. Do not change the name.",
                    ("update_member",),
                    ("+66812345001",),
                ),
                Ask(
                    f"Create a membership plan. Code WEEKDAY, name Weekday, duration 1 month, "
                    f"price 30000 {money}.",
                    ("create_plan",),
                    ("WEEKDAY", "30000"),
                ),
                Ask(
                    f"Change the Weekday plan price to 32000 {money}. Keep the name Weekday.",
                    ("update_plan",),
                    ("32000",),
                ),
                Ask(
                    "Archive the Weekday plan. Do not archive the Monthly plan.",
                    ("archive_plan",),
                    ("Weekday",),
                ),
                Ask(
                    f"Sell Nyein Nyein the Monthly plan starting {day}. No discount.",
                    ("create_membership",),
                    ("Nyein", day),
                ),
                Ask(
                    "Add the note 'front desk sale' on Nyein Nyein's Monthly membership.",
                    ("update_membership_note",),
                    ("front desk sale",),
                ),
                Ask(
                    "Min Min wants to pause her membership. Suspend it. Do not cancel it.",
                    ("suspend_membership",),
                    ("Min",),
                ),
                Ask(
                    "Min Min is back. Resume her membership.",
                    ("resume_membership",),
                    ("Min",),
                ),
                Ask(
                    "Cancel Nyein Nyein's membership. Reason: she asked to stop. "
                    "Do not cancel Min Min.",
                    ("cancel_membership",),
                    ("Nyein",),
                ),
                Ask(
                    "Archive member Nyein Nyein.",
                    ("archive_member",),
                    ("Nyein",),
                ),
                Ask(
                    "Restore member Nyein Nyein.",
                    ("restore_member",),
                    ("Nyein",),
                ),
            ],
        ),
        (
            "announcements",
            [
                Ask(
                    "Create a draft announcement. Type GENERAL, title Pool hours, "
                    "body The pool closes at 8, audience ALL_ACTIVE_MEMBERS.",
                    ("create_announcement",),
                    ("Pool hours",),
                ),
                Ask(
                    "Change the Pool hours announcement body to The pool closes at 9.",
                    ("update_announcement",),
                    ("closes at 9",),
                ),
                Ask(
                    "Publish the Pool hours announcement now.",
                    ("publish_announcement",),
                    ("Pool hours",),
                ),
                Ask(
                    "Create a draft announcement. Type GENERAL, title Staff meeting, "
                    "body Meet at noon, audience ALL_ACTIVE_MEMBERS. Do not publish it.",
                    ("create_announcement",),
                    ("Staff meeting",),
                ),
                Ask(
                    "Cancel the Staff meeting announcement.",
                    ("cancel_announcement",),
                    ("Staff meeting",),
                ),
                Ask(
                    "Min Min, member number CV01-1, is at the desk. Check her in.",
                    ("create_manual_check_in",),
                    ("Min",),
                ),
                Ask(
                    "Check Min Min out of the visit that is still open.",
                    ("check_out_check_in",),
                    ("Min",),
                ),
            ],
        ),
        (
            "inventory",
            [
                Ask(
                    f"Open the front till for {day} with 25000 {money}. Register code FRONT.",
                    ("open_cash_session",),
                    ("25000",),
                ),
                Ask(
                    "Add a product category named Snacks.",
                    ("create_product_category",),
                    ("Snacks",),
                ),
                Ask(
                    f"Add a product named Protein Bar, sku BAR, unit PIECE, in Snacks. "
                    f"Sale price 4000 {money}, current cost 2000, default cost 2000, "
                    f"low-stock threshold 2.",
                    ("create_product",),
                    ("Protein Bar", "4000"),
                ),
                Ask(
                    f"Change the Protein Bar sale price to 4500 {money}.",
                    ("update_product",),
                    ("4500",),
                ),
                Ask(
                    f"Record an opening stock count of 6 Protein Bars on {day}. "
                    f"Reason: first count.",
                    ("adjust_stock",),
                    ("6",),
                ),
                Ask(
                    f"Sell two Protein Bars for cash on {day}. Prepare the sale. "
                    f"Do not submit it yet.",
                    ("create_retail_sale",),
                    ("Protein",),
                ),
                Ask(
                    "Change that Protein Bar sale to one bar. It is still a draft.",
                    ("update_retail_sale",),
                    ("1",),
                ),
                Ask(
                    "Submit the Protein Bar sale.",
                    ("submit_retail_sale",),
                    (),
                ),
                Ask(
                    "Approve the submitted Protein Bar sale.",
                    ("approve_retail_sale",),
                    (),
                ),
                Ask(
                    "The member returned that one Protein Bar. Record the return. "
                    "Reason: unopened.",
                    ("return_retail_sale",),
                    (),
                ),
                Ask(
                    f"Prepare another cash sale of one Protein Bar on {day}. Leave it as a draft.",
                    ("create_retail_sale",),
                    ("Protein",),
                ),
                Ask(
                    "Cancel that Protein Bar draft. Reason: the member changed their mind.",
                    ("cancel_retail_sale",),
                    (),
                ),
                Ask(
                    f"Prepare another cash sale of one Protein Bar on {day}. Leave it as a draft.",
                    ("create_retail_sale",),
                    ("Protein",),
                ),
                Ask("Submit that Protein Bar draft.", ("submit_retail_sale",), ()),
                Ask(
                    "Reject that submitted Protein Bar sale. Reason: wrong member.",
                    ("reject_retail_sale",),
                    (),
                ),
                Ask(
                    f"Prepare another cash sale of one Protein Bar on {day}. Leave it as a draft.",
                    ("create_retail_sale",),
                    ("Protein",),
                ),
                Ask("Submit that Protein Bar draft.", ("submit_retail_sale",), ()),
                Ask("Approve that submitted Protein Bar sale.", ("approve_retail_sale",), ()),
                Ask(
                    "Void that posted Protein Bar sale. Reason: entered twice.",
                    ("void_retail_sale",),
                    (),
                ),
            ],
        ),
        (
            "expenses",
            [
                Ask(
                    "Create an expense category named Supplies.",
                    ("create_expense_category",),
                    ("Supplies",),
                ),
                Ask(
                    "Rename the Supplies category to Shop supplies.",
                    ("update_expense_category",),
                    ("Shop supplies",),
                ),
                Ask(
                    f"Record a cash expense of 1800 {money} on {day} for towels "
                    f"in Shop supplies. Leave it as a draft.",
                    ("create_expense",),
                    ("1800", "towel"),
                ),
                Ask(
                    "Change that towel expense description to Desk towels.",
                    ("update_expense",),
                    ("Desk towels",),
                ),
                Ask("Submit the Desk towels expense.", ("submit_expense",), ()),
                Ask("Approve the submitted Desk towels expense.", ("approve_expense",), ()),
                Ask(
                    f"Record a cash expense of 900 {money} on {day} for balloons in Shop supplies. "
                    f"Leave it as a draft.",
                    ("create_expense",),
                    ("900",),
                ),
                Ask(
                    "Cancel that balloons draft. Reason: not buying them.",
                    ("cancel_expense",),
                    (),
                ),
                Ask(
                    f"Record a cash expense of 600 {money} on {day} for cups in Shop supplies. "
                    f"Leave it as a draft.",
                    ("create_expense",),
                    ("600",),
                ),
                Ask("Submit the cups expense.", ("submit_expense",), ()),
                Ask(
                    "Reject that submitted cups expense. Reason: missing receipt.",
                    ("reject_expense",),
                    (),
                ),
                Ask(
                    f"Record a cash expense of 700 {money} on {day} for tape in Shop supplies. "
                    f"Leave it as a draft.",
                    ("create_expense",),
                    ("700",),
                ),
                Ask("Submit the tape expense.", ("submit_expense",), ()),
                Ask("Approve the submitted tape expense.", ("approve_expense",), ()),
                Ask(
                    "Void that posted tape expense. Reason: duplicate.",
                    ("void_expense",),
                    (),
                ),
                Ask(
                    f"Add 500 {money} into the open FRONT till on {day}. "
                    f"Direction IN. Reason: float correction.",
                    ("adjust_cash_session",),
                    ("500",),
                ),
            ],
        ),
        (
            "payments",
            [
                Ask(
                    f"Min Min, member number CV01-1, pays 20000 {money} in cash on {day} "
                    f"toward her Monthly membership. Description: partial membership. "
                    f"Leave it as a draft.",
                    ("create_payment",),
                    ("20000",),
                ),
                Ask(
                    "Change that payment description to November partial.",
                    ("update_payment",),
                    ("November partial",),
                ),
                Ask("Submit the November partial payment.", ("submit_payment",), ()),
                Ask("Approve the submitted November partial payment.", ("approve_payment",), ()),
                Ask(
                    f"Refund 5000 {money} of that posted payment in cash on {day}. "
                    f"Reason: goodwill. Do not refund the rest.",
                    ("refund_payment",),
                    ("5000",),
                ),
                Ask(
                    "Void the receipt that was issued for the November partial payment. "
                    "Reason: reprinted.",
                    ("void_receipt",),
                    (),
                ),
                Ask(
                    f"Record an other cash payment of 3000 {money} on {day} for a guest pass. "
                    f"Leave it as a draft.",
                    ("create_payment",),
                    ("3000",),
                ),
                Ask(
                    "Cancel that guest pass draft. Reason: guest left.",
                    ("cancel_payment",),
                    (),
                ),
                Ask(
                    f"Record an other cash payment of 4000 {money} on {day} for a locker. "
                    f"Leave it as a draft.",
                    ("create_payment",),
                    ("4000",),
                ),
                Ask("Submit the locker payment.", ("submit_payment",), ()),
                Ask("Approve the submitted locker payment.", ("approve_payment",), ()),
                Ask(
                    "Void that posted locker payment. Reason: wrong amount.",
                    ("void_payment",),
                    (),
                ),
                Ask(
                    f"Record an other cash payment of 2500 {money} on {day} for a towel hire. "
                    f"Leave it as a draft.",
                    ("create_payment",),
                    ("2500",),
                ),
                Ask("Submit the towel hire payment.", ("submit_payment",), ()),
                Ask(
                    "Reject that submitted towel hire payment. Reason: no receipt.",
                    ("reject_payment",),
                    (),
                ),
                Ask(
                    "Close the FRONT till. Count the cash in the drawer. "
                    "The count equals the expected closing, so there is no difference.",
                    ("reconcile_cash_session",),
                    (),
                ),
            ],
        ),
        (
            "salary",
            [
                Ask(
                    f"Create a salary period from 2026-11-01 through 2026-11-30, "
                    f"business date {day}, description November wages.",
                    ("create_salary_period",),
                    ("2026-11-01", "2026-11-30"),
                ),
                Ask(
                    f"Put one wage line on that November period for Su Su: 100000 {money} "
                    f"by bank transfer, description November coaching.",
                    ("replace_salary_lines",),
                    ("100000",),
                ),
                Ask(
                    "Change the November wages description to November coaching payroll.",
                    ("update_salary_period",),
                    ("November coaching payroll",),
                ),
                Ask("Submit the November salary period.", ("submit_salary_period",), ()),
                Ask(
                    "Approve the submitted November salary period.",
                    ("approve_salary_period",),
                    (),
                ),
                Ask(
                    f"Create a salary period from 2026-10-01 through 2026-10-31, "
                    f"business date {day}, description October draft.",
                    ("create_salary_period",),
                    ("2026-10-01",),
                ),
                Ask(
                    "Cancel the October draft. Reason: wrong month.",
                    ("cancel_salary_period",),
                    (),
                ),
                Ask(
                    f"Create a salary period from 2026-09-01 through 2026-09-30, "
                    f"business date {day}, description September wages.",
                    ("create_salary_period",),
                    ("2026-09-01",),
                ),
                Ask(
                    f"Put one wage line on the September period for Su Su: 100000 {money} "
                    f"by bank transfer, description September coaching.",
                    ("replace_salary_lines",),
                    ("100000",),
                ),
                Ask("Submit the September salary period.", ("submit_salary_period",), ()),
                Ask(
                    "Reject that submitted September salary period. Reason: hours not confirmed.",
                    ("reject_salary_period",),
                    (),
                ),
                Ask(
                    f"Create a salary period from 2026-08-01 through 2026-08-31, "
                    f"business date {day}, description August wages.",
                    ("create_salary_period",),
                    ("2026-08-01",),
                ),
                Ask(
                    f"Put one wage line on the August period for Su Su: 100000 {money} "
                    f"by bank transfer, description August coaching.",
                    ("replace_salary_lines",),
                    ("100000",),
                ),
                Ask("Submit the August salary period.", ("submit_salary_period",), ()),
                Ask("Approve the submitted August salary period.", ("approve_salary_period",), ()),
                Ask(
                    "Void that posted August salary period. Reason: paid outside this gym.",
                    ("void_salary_period",),
                    (),
                ),
            ],
        ),
        (
            "classes",
            [
                Ask(
                    "Create a class type named Yoga, 45 minutes, capacity 8.",
                    ("create_class_type",),
                    ("Yoga",),
                ),
                Ask(
                    "Change the Yoga class type capacity to 10.",
                    ("update_class_type",),
                    ("10",),
                ),
                Ask(
                    f"Schedule Yoga with trainer Su Su on the weekday of {day}, "
                    f"from 09:00 to 09:45, effective only on {day}, location Studio A, "
                    f"capacity 8.",
                    ("create_class_schedule",),
                    ("Studio A",),
                ),
                Ask(
                    "Change that Yoga schedule capacity to 9. Keep Studio A.",
                    ("update_class_schedule",),
                    ("9",),
                ),
                Ask(
                    f"Cancel the Yoga class on {day}. Reason: trainer sick.",
                    ("cancel_class_occurrence",),
                    ("sick",),
                ),
                Ask(
                    f"Create another Yoga schedule with Su Su on the weekday of {day}, "
                    f"from 08:00 to 08:45, effective only on {day}, location Studio B, "
                    f"capacity 8.",
                    ("create_class_schedule",),
                    ("Studio B",),
                ),
                Ask(
                    "Book Min Min, member number CV01-1, into the Studio B Yoga class "
                    "without a package. Reason: drop-in.",
                    ("create_class_booking",),
                    ("Min",),
                ),
                Ask(
                    "Mark Min Min attended that Yoga booking.",
                    ("attend_class_booking",),
                    (),
                ),
                Ask(
                    f"Create another Yoga schedule with Su Su on the weekday of {day}, "
                    f"from 07:00 to 07:45, effective only on {day}, location Studio C, "
                    f"capacity 8.",
                    ("create_class_schedule",),
                    ("Studio C",),
                ),
                Ask(
                    "Book Nyein Nyein into the Studio C Yoga class without a package. "
                    "Reason: drop-in.",
                    ("create_class_booking",),
                    ("Nyein",),
                ),
                Ask(
                    "Mark Nyein Nyein's Studio C Yoga booking as a no-show.",
                    ("mark_class_booking_no_show",),
                    (),
                ),
                Ask(
                    f"Create another Yoga schedule with Su Su on the weekday of {day}, "
                    f"from 12:00 to 12:45, effective only on {day}, location Studio D, "
                    f"capacity 8.",
                    ("create_class_schedule",),
                    ("Studio D",),
                ),
                Ask(
                    "Book Min Min into the Studio D Yoga class without a package. Reason: drop-in.",
                    ("create_class_booking",),
                    ("Min",),
                ),
                Ask(
                    "Cancel Min Min's Studio D Yoga booking.",
                    ("cancel_class_booking",),
                    (),
                ),
            ],
        ),
        (
            "training",
            [
                Ask(
                    f"Create a PT package. Type PT, code MORNING, name Morning PT, "
                    f"8 sessions, valid 30 days, price 80000 {money}.",
                    ("create_service_package",),
                    ("Morning PT", "80000"),
                ),
                Ask(
                    f"Assign trainer Su Su to Min Min starting {day}.",
                    ("create_assignment",),
                    ("Su Su", day),
                ),
                Ask(
                    f"Assign trainer Su Su to Nyein Nyein starting {day}.",
                    ("create_assignment",),
                    ("Nyein", day),
                ),
                Ask(
                    "Cancel Su Su's assignment to Nyein Nyein. Reason: wrong trainer.",
                    ("cancel_assignment",),
                    (),
                ),
                Ask(
                    f"Grant Min Min the Morning PT package on {day} as complimentary. "
                    f"Reason: trial.",
                    ("grant_member_package",),
                    ("Morning PT",),
                ),
                Ask(
                    f"Book Su Su with Min Min for PT on {day} from 09:00 to 09:45, "
                    f"using the Morning PT package.",
                    ("create_pt_booking",),
                    ("09:00",),
                ),
                Ask("Mark that PT booking attended.", ("attend_pt_booking",), ()),
                Ask(
                    f"Book Su Su with Min Min for PT on {day} from 06:15 to 06:45, "
                    f"using the Morning PT package.",
                    ("create_pt_booking",),
                    ("06:15",),
                ),
                Ask(
                    "Mark that 06:15 PT booking as a no-show.",
                    ("mark_pt_booking_no_show",),
                    (),
                ),
                Ask(
                    f"Book Su Su with Min Min for PT on {day} from 14:00 to 14:45, "
                    f"using the Morning PT package.",
                    ("create_pt_booking",),
                    ("14:00",),
                ),
                Ask("Cancel that 14:00 PT booking.", ("cancel_pt_booking",), ()),
                Ask(
                    f"Sell Min Min Morning PT on {day}. Trainer Su Su. Gross fee 80000 {money}. "
                    f"Leave the sale as a draft.",
                    ("create_pt_sale",),
                    ("80000",),
                ),
                Ask(
                    f"Change that PT sale gross fee to 75000 {money}.",
                    ("update_pt_sale",),
                    ("75000",),
                ),
                Ask("Submit the Morning PT sale.", ("submit_pt_sale",), ()),
                Ask("Approve the submitted Morning PT sale.", ("approve_pt_sale",), ()),
                Ask(
                    f"Record the commission payout for that PT sale, 10000 {money} "
                    f"by bank transfer on {day}.",
                    ("record_pt_commission_payment",),
                    ("10000",),
                ),
                Ask(
                    "Correct the Morning PT package ledger by reversing the latest entry. "
                    "Reason: session counted twice.",
                    ("adjust_package_ledger",),
                    (),
                ),
                Ask(
                    f"Create another Morning PT sale for Min Min on {day} with Su Su "
                    f"at 80000 {money}. Leave it as a draft.",
                    ("create_pt_sale",),
                    ("80000",),
                ),
                Ask(
                    "Cancel that Morning PT draft. Reason: duplicate sale.",
                    ("cancel_pt_sale",),
                    (),
                ),
                Ask(
                    f"Create another Morning PT sale for Min Min on {day} with Su Su "
                    f"at 80000 {money}. Leave it as a draft.",
                    ("create_pt_sale",),
                    ("80000",),
                ),
                Ask("Submit that Morning PT draft.", ("submit_pt_sale",), ()),
                Ask(
                    "Reject that submitted Morning PT sale. Reason: price not agreed.",
                    ("reject_pt_sale",),
                    (),
                ),
                Ask(
                    f"Create another Morning PT sale for Min Min on {day} with Su Su "
                    f"at 80000 {money}. Leave it as a draft.",
                    ("create_pt_sale",),
                    ("80000",),
                ),
                Ask("Submit that Morning PT draft.", ("submit_pt_sale",), ()),
                Ask("Approve that submitted Morning PT sale.", ("approve_pt_sale",), ()),
                Ask(
                    f"Void that posted Morning PT sale on {day}. Reason: sale was a test.",
                    ("void_pt_sale",),
                    (),
                ),
                Ask(
                    f"End Su Su's assignment to Min Min on {day}. Reason: season finished.",
                    ("end_assignment",),
                    ("Min",),
                ),
            ],
        ),
        (
            "staff",
            [
                Ask(
                    "Change Mya Mya's job title to Coach.",
                    ("update_staff_profile",),
                    ("Coach",),
                ),
                Ask(
                    "Suspend Mya Mya. Reason: leave.",
                    ("suspend_staff",),
                    ("Mya",),
                ),
                Ask(
                    "Activate Mya Mya. Reason: leave ended.",
                    ("activate_staff",),
                    ("Mya",),
                ),
                Ask(
                    "Create a custom role named Floor helper.",
                    ("create_role",),
                    ("Floor helper",),
                ),
                Ask(
                    "Change the Floor helper role description to Desk cover.",
                    ("update_role",),
                    ("Desk cover",),
                ),
                Ask(
                    "Set the Floor helper role permissions to members.view only.",
                    ("replace_role_permissions",),
                    ("members.view",),
                ),
                Ask(
                    "Give Mya Mya the Floor helper role and remove her other custom roles. "
                    "Keep her trainer access if the role list must include the trainer role.",
                    ("replace_membership_roles",),
                    (),
                ),
                Ask(
                    "Archive the Floor helper role.",
                    ("archive_role",),
                    ("Floor helper",),
                ),
                Ask(
                    "Update the gym phone to 020000001. Do not change the gym name.",
                    ("update_gym_profile",),
                    ("020000001",),
                ),
                Ask(
                    "Set the member number prefix to CV. Do not change other settings.",
                    ("update_gym_settings",),
                    ("CV",),
                ),
                Ask(
                    "Revoke the other owner, Ada Owner. Reason: left the gym.",
                    ("revoke_owner",),
                    ("Ada",),
                ),
                Ask(
                    "Cancel the pending owner invitation for Iva Invitee.",
                    ("cancel_owner_invitation",),
                    ("Iva",),
                ),
            ],
        ),
    ]


def _coverage(gym: Gym, session: _Session) -> dict[str, object]:
    registry = production_registry()
    selected: dict[str, list[str]] = {}
    for name, status in session.capture.events:
        selected.setdefault(name, [])
        if status not in selected[name]:
            selected[name].append(status)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        steps = list(WorkflowStep.objects.filter(gym=gym).values_list("tool_name", "status"))
        executed = set(ToolExecution.objects.filter(gym=gym).values_list("tool_name", flat=True))
    succeeded = {name for name, status in steps if status == WorkflowStep.Status.SUCCEEDED}
    committed = set(session.committed) | executed | succeeded
    rows = []
    missing = []
    for name in registry.names():
        tool = registry.get(name)
        statuses = selected.get(name, [])
        step_status = sorted({status for step_name, status in steps if step_name == name})
        if tool.operation == "read":
            done = "completed" in statuses
        else:
            done = name in committed
        if not done:
            missing.append(name)
        rows.append(
            {
                "name": name,
                "operation": tool.operation,
                "statuses": statuses,
                "steps": step_status,
                "committed": name in committed,
                "covered": done,
            }
        )
    covered = [row["name"] for row in rows if row["covered"]]
    return {
        "model": os.environ.get("GEMINI_MODEL", ""),
        "stopped": session.stopped,
        "registered": len(rows),
        "covered": covered,
        "missing": missing,
        "notes": session.notes,
        "tools": rows,
    }


def _write_report(report: dict[str, object], path: str = _REPORT) -> None:
    """Keep a richer real-Gemini report when a later run is stopped by the provider."""

    target = path
    stopped = str(report.get("stopped") or "")
    if "provider" in stopped or "unavailable" in stopped or "quota" in stopped:
        try:
            with open(path, encoding="utf-8") as handle:
                previous = json.loads(handle.read())
        except (OSError, json.JSONDecodeError):
            previous = {}
        previous_count = len(previous.get("covered") or [])
        if previous_count > len(report.get("covered") or []):
            target = path + ".aborted"
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")


@override_settings(**_conversation_settings())
def test_a_committed_write_survives_a_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "RC01")
    _slow_clock(monkeypatch)
    _features(gym)
    _category(gym, owner.id)
    day = gym_today(gym).isoformat()
    session = _Session(client, headers, live, gym, owner)
    try:
        session.ask(
            Ask(
                f"Register a new member. First name Nyein, last name Nyein, "
                f"join date {day}, no phone.",
                ("create_member",),
                ("Nyein", day),
            )
        )
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            saved = list(Member.objects.filter(gym=gym, first_name="Nyein", last_name="Nyein"))
        assert len(saved) == 1, session.notes
        step = _succeeded_step(gym, "create_member")
        with override_settings(**live):
            replay = _confirm(client, headers, str(step.id))
        assert replay["outcome"] == "existing"
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            assert Member.objects.filter(gym=gym, first_name="Nyein").count() == 1

        import apps.assistant.turns as turns

        original = turns.generate_content

        def _quota(**_kwargs: object) -> None:
            raise ProviderCallError(ProviderFailure.QUOTA)

        turns.generate_content = _quota
        try:
            interrupted = session.say("How many members are stored?")
        finally:
            turns.generate_content = original
        body = _body(interrupted)
        assert "could not continue" in body.casefold() or _unavailable(body), body
        assert _member_count(gym) == 1

        session.stopped = ""
        session.ask(
            Ask(
                f"Register Nyein Nyein again with join date {day}.",
                (),
                (),
            )
        )
        assert _member_count(gym) == 1, session.notes

        session.fresh()
        session.stopped = ""
        before = _expense_count(gym)
        session.ask(
            Ask(
                f"Record a cash expense of 4242 {gym.currency} on {day} for packing tape "
                f"in Utilities. Prepare it and do not submit it.",
                hold=("create_expense",),
                needles=("4242",),
            )
        )
        waiting = _waiting_step(gym, "create_expense")
        assert waiting is not None, session.notes
        assert _expense_count(gym) == before
        turns.generate_content = _quota
        try:
            interrupted = session.say("Are there any expenses today?")
        finally:
            turns.generate_content = original
        waiting.refresh_from_db()
        assert waiting.status == WorkflowStep.Status.WAITING_CONFIRMATION
        notice = _body(interrupted).casefold()
        assert "could not continue" in notice or _unavailable(_body(interrupted)), notice
        assert _expense_count(gym) == before
        with override_settings(**live):
            saved_expense = _confirm(client, headers, str(waiting.id))
            again = _confirm(client, headers, str(waiting.id))
        assert saved_expense["outcome"] == "committed"
        assert again["outcome"] == "existing"
        assert _expense_count(gym) == before + 1
    finally:
        session.close()


def _succeeded_step(gym: Gym, tool_name: str) -> WorkflowStep:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step = (
            WorkflowStep.objects.filter(
                gym=gym,
                tool_name=tool_name,
                status=WorkflowStep.Status.SUCCEEDED,
            )
            .order_by("-created_at")
            .first()
        )
    if step is None:
        raise AssertionError(f"no succeeded {tool_name} step")
    return step


def _waiting_step(gym: Gym, tool_name: str) -> WorkflowStep | None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            WorkflowStep.objects.filter(
                gym=gym,
                tool_name=tool_name,
                status=WorkflowStep.Status.WAITING_CONFIRMATION,
            )
            .order_by("-created_at")
            .first()
        )


def _expense_count(gym: Gym) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Expense.objects.filter(gym=gym).count()


def _member_count(gym: Gym) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.filter(gym=gym).count()


def _step_up_owner(client: object, headers: dict[str, str], owner: object) -> None:
    """Enroll the disposable owner and confirm with a current authenticator code."""

    import time

    from django.conf import settings

    from apps.users.mfa import _totp

    user = owner.user
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    trusted = {**headers, "HTTP_X_GYM_BFF_SECRET": settings.BFF_SHARED_SECRET}
    enrolled = client.post(
        "/api/v1/auth/mfa/enroll/",
        {"current_password": PASSWORD},
        format="json",
        **trusted,
    )
    assert enrolled.status_code == 200, enrolled.content
    data = enrolled.json()["data"]
    code = _totp(str(data["secret"]), int(time.time()) // 30)
    confirmed = client.post(
        "/api/v1/auth/mfa/enroll/confirm/",
        {"enrollment_token": data["enrollment_token"], "code": code},
        format="json",
        **trusted,
    )
    assert confirmed.status_code == 200, confirmed.content


def _seed_owner_records(gym: Gym) -> None:
    from datetime import date as date_cls

    from django.utils import timezone

    from apps.tenants.models import GymMembership, StaffInvitation
    from apps.users.models import User

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ada_user = User.objects.create_user("cv01-ada@example.test", PASSWORD)
        ada_user.first_name = "Ada"
        ada_user.last_name = "Owner"
        ada_user.save(update_fields=["first_name", "last_name"])
        GymMembership.objects.create(
            gym=gym,
            user=ada_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date_cls.today(),
            activated_at=timezone.now(),
        )
        iva_user = User.objects.create_user("cv01-iva@example.test", PASSWORD)
        iva_user.first_name = "Iva"
        iva_user.last_name = "Invitee"
        iva_user.save(update_fields=["first_name", "last_name"])
        invited = GymMembership.objects.create(
            gym=gym,
            user=iva_user,
            status=GymMembership.Status.INVITED,
            is_owner=True,
            joined_date=date_cls.today(),
        )
        StaffInvitation.objects.create(
            gym=gym,
            gym_membership=invited,
            email=iva_user.email,
            normalized_email=iva_user.email,
            token_hash=hashlib.sha256(b"cv01-iva-owner-invitation").hexdigest(),
            expires_at=timezone.now() + timedelta(days=2),
        )


def _body(turn: dict[str, object]) -> str:
    message = turn.get("message")
    if isinstance(message, dict):
        return str(message.get("body") or "")
    return ""


def _provider_down(body: str) -> bool:
    folded = body.casefold()
    return (
        "the assistant is unavailable" in folded
        or "could not continue just now" in folded
        or "ဆက်မပြောနိုင်" in body
        or "လက်ထောက်ကို အခု ခဏ အသုံးမပြုနိုင်" in body
    )


@override_settings(**_conversation_settings())
def test_real_gemini_tool_coverage_record(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "CV01")
    _slow_clock(monkeypatch)
    _member, trainer = _seed(gym, owner)
    day = gym_today(gym).isoformat()
    session = _Session(client, headers, live, gym, owner)
    report: dict[str, object] = {}
    try:
        session.fresh()
        print("coverage phase reads", flush=True)
        for item in _reads(day):
            trainer_turn = "trainer dashboard" in item.message
            if trainer_turn:
                session.as_staff(trainer)
            session.ask(item)
            if trainer_turn:
                session.as_owner()
            if session.stopped:
                break
        for name, items in _writes(day, gym.currency):
            if session.stopped:
                break
            print(f"coverage phase {name}", flush=True)
            session.fresh()
            if name == "staff":
                _step_up_owner(client, session.headers, owner)
                _seed_owner_records(gym)
            for item in items:
                session.ask(item)
                if session.stopped:
                    break
    finally:
        try:
            report = _coverage(gym, session)
            _write_report(report)
        finally:
            session.close()
    missing = report.get("missing", [])
    stopped = str(report.get("stopped") or "")
    covered = report.get("covered", [])
    assert not missing and not stopped, (
        f"covered {len(covered)} of {report.get('registered')} registered tools; "
        f"stopped={stopped or 'no'}; missing={missing}. Report: {_REPORT}"
    )
