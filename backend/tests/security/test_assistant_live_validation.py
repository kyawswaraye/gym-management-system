"""Live Gemini checks for revenue categories and confirmed writes.

This module calls the configured model. It does not run in CI unless
ASSISTANT_LIVE_GEMINI=1. The gym is created inside the test transaction and
is not Local Test Gym.
"""

from __future__ import annotations

import os
import re
import time
from datetime import time as clock_time
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction
from django.db.models import Sum
from django.test import override_settings

from apps.assistant.provider import ProviderCallError, ProviderFailure, generate_content
from apps.classes.models import ClassBooking
from apps.classes.services import create_class_schedule, create_class_type
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, Payment
from apps.members.services import gym_today
from apps.reports.selectors import tenant_dashboard
from apps.tenants.models import Gym, StaffProfile
from apps.trainers.models import MemberServicePackage, PTSale, ServicePackage
from apps.trainers.package_services import grant_package_admin
from tests.security.test_assistant_onboarding import (
    _confirm,
    _gym,
    _plan,
    _post,
    _reject,
    _settings,
)
from tests.security.test_assistant_schedule import _enable
from tests.security.test_assistant_side_questions import _package
from tests.security.test_dashboard import _category, _expense, _membership, _post_payment
from tests.security.test_tenant_gym_settings import add_staff, staff_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


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


def _money(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        found.add(Decimal(token.replace(",", "")).quantize(Decimal("0.01")))
    return found


def _charge(
    gym: Gym,
    purpose: str,
    amount: str,
    day: str,
    member_id: object,
    membership_id: object | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "purpose": purpose,
        "description": f"{purpose} {amount}",
        "amount": amount,
        "currency": gym.currency,
        "method": "BANK_TRANSFER",
        "business_date": day,
        "member_id": str(member_id),
    }
    if membership_id is not None:
        body["membership_id"] = str(membership_id)
    return body


def _rows(gym: Gym, model: type) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return model.objects.filter(gym=gym).count()


def _one(gym: Gym, model: type):
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return model.objects.get(gym=gym)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?။])\s+", text)
    return [part for part in parts if part.strip()]


@override_settings(**_settings())
def test_live_gemini_keeps_revenue_categories_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "AV01")
    for feature in ("members", "memberships", "reports", "finance", "expenses", "pt"):
        _enable(gym, feature)
    today = gym_today(gym)
    reception = add_staff(gym, email="av01-desk@example.test", role_key="RECEPTIONIST")
    creator = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(reception)}"}
    approver = headers
    plan = _plan(gym, owner.id)
    day = today.isoformat()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        for index in range(6):
            Member.objects.create(
                gym=gym,
                member_number=f"AV-{index + 1:04d}",
                first_name="Seed",
                last_name=str(index + 1),
                join_date=today,
                status=Member.Status.ACTIVE,
                created_by_membership_id=owner.id,
                updated_by_membership_id=owner.id,
            )
        member = Member.objects.get(gym=gym, member_number="AV-0001")
    sold = _membership(
        gym,
        owner,
        member=member,
        plan=plan,
        start=today,
        expiry=today + timedelta(days=30),
        amount="1500.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    _post_payment(
        client,
        creator,
        approver,
        _charge(gym, "MEMBERSHIP", "1500.00", day, member.id, sold.id),
    )
    coach = add_staff(gym, email="av01-coach@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        profile = StaffProfile.objects.create(
            gym=gym,
            gym_membership=coach,
            staff_code="AV-COACH",
            display_name="Ko Min",
            joined_date=today,
            status=StaffProfile.Status.ACTIVE,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    assigned = client.post(
        "/api/v1/trainer-assignments/",
        {
            "trainer_staff_profile_id": str(profile.id),
            "member_id": str(member.id),
            "effective_from": day,
        },
        format="json",
        **approver,
    )
    assert assigned.status_code == 201, assigned.content
    posted_sale = client.post(
        "/api/v1/pt-sales/",
        {
            "member_id": str(member.id),
            "trainer_staff_profile_id": str(profile.id),
            "service_date": day,
            "description": "Private session",
            "gross_fee": "8000.00",
            "currency": gym.currency,
            "payment": {
                "description": "PT session",
                "amount": "8000.00",
                "method": "BANK_TRANSFER",
                "business_date": day,
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pt-{uuid4().hex}",
        **creator,
    )
    assert posted_sale.status_code == 201, posted_sale.content
    sale_id = str(posted_sale.json()["data"]["id"])
    submitted_sale = client.post(
        f"/api/v1/pt-sales/{sale_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"ptsub-{uuid4().hex}",
        **creator,
    )
    assert submitted_sale.status_code == 200, submitted_sale.content
    approved_sale = client.post(
        f"/api/v1/pt-sales/{sale_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"ptapr-{uuid4().hex}",
        **approver,
    )
    assert approved_sale.status_code == 200, approved_sale.content
    _expense(
        client,
        creator,
        approver,
        category_id=str(_category(gym, "RENT").id),
        amount="5825.00",
        day=day,
        gym=gym,
        post=True,
    )
    stored = tenant_dashboard(
        gym=gym,
        date_from=today.replace(day=1),
        date_to=today,
        today=today,
        include_financial=True,
    )
    financial = stored["financial"]
    assert financial is not None
    assert financial["new_member_revenue"] == "1500.00"
    assert financial["pt_revenue"] == "8000.00"
    assert financial["class_revenue"] == "0.00"
    assert financial["total_revenue"] == "9500.00"
    assert financial["total_expenses"] == "5825.00"
    assert financial["net_profit"] == "3675.00"
    assert stored["operational"]["new_members"] == 6

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
        transcript.append(f"ASSISTANT: {body[:800]} TOOLS: {_tools(turn) or 'none'}")
        return turn

    try:
        burmese = say("ဒီလ အသင်းဝင်အရောင်း၊ PT အရောင်း၊ အတန်းဝင်ငွေ၊ စုစုပေါင်းဝင်ငွေ၊ အသုံးစရိတ်၊ အမြတ် ဘယ်လောက်လဲ။")
        conversation = str(burmese["conversation_id"])
        _assert_categories(str(burmese["message"]["body"]), financial)
        english = say(
            "For this month, what are membership sales, PT sales, class revenue, "
            "total revenue, expenses, and profit? Keep each category separate.",
            conversation,
        )
        _assert_categories(str(english["message"]["body"]), financial)
        mixed = say(
            "Compare this month with last month. Total revenue နဲ့ membership sales ကို သပ်သပ်ပြပါ။",
            conversation,
        )
        _assert_no_false_membership(str(mixed["message"]["body"]), financial)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n" + "\n".join(transcript)) from None


def _assert_categories(body: str, financial: dict[str, object]) -> None:
    _assert_no_false_membership(body, financial)
    amounts = _money(body)
    assert Decimal(str(financial["pt_revenue"])) in amounts
    assert Decimal(str(financial["total_revenue"])) in amounts
    assert Decimal(str(financial["total_expenses"])) in amounts
    assert Decimal(str(financial["net_profit"])) in amounts
    assert Decimal(str(financial["new_member_revenue"])) in amounts


def _assert_no_false_membership(body: str, financial: dict[str, object]) -> None:
    membership = Decimal(str(financial["new_member_revenue"])) + Decimal(
        str(financial["renewal_revenue"])
    )
    total = Decimal(str(financial["total_revenue"]))
    for sentence in _sentences(body):
        folded = sentence.casefold()
        about_membership = any(
            word in folded or word in sentence
            for word in ("membership", "အသင်းဝင်", "new member", "new-member", "လူသစ်")
        )
        about_total = any(word in folded or word in sentence for word in ("total", "စုစုပေါင်း"))
        if not about_membership or about_total:
            continue
        amounts = _money(sentence)
        if not amounts:
            continue
        assert membership in amounts, sentence
        if membership != total:
            assert total not in amounts, sentence


@override_settings(**_settings())
def test_live_gemini_confirms_member_sale_booking_and_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_settings()
    gym, owner, client, headers = _gym(monkeypatch, "AV02")
    for feature in ("members", "memberships", "pt", "classes", "finance", "reports"):
        _enable(gym, feature)
    plan = _plan(gym, owner.id)
    _package(gym, owner.id)
    today = gym_today(gym)
    trainer = add_staff(gym, email="av02-coach@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        profile = StaffProfile.objects.create(
            gym=gym,
            gym_membership=trainer,
            staff_code="KO-MIN",
            display_name="Ko Min",
            joined_date=today,
            status=StaffProfile.Status.ACTIVE,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
        class_type = create_class_type(
            gym=gym,
            actor=owner,
            code="YOGA",
            name="Yoga",
            description="",
            default_duration_minutes=60,
            default_capacity=12,
            request_id="req_av_class",
            ip_address=None,
            user_agent="",
        )
        create_class_schedule(
            gym=gym,
            actor=owner,
            class_type_id=class_type.id,
            trainer_staff_profile_id=profile.id,
            weekdays=[today.weekday()],
            local_start_time=clock_time(18, 0),
            local_end_time=clock_time(19, 0),
            effective_from=today,
            effective_until=today,
            location="Studio",
            location_is_exclusive=False,
            capacity=12,
            request_id="req_av_schedule",
            ip_address=None,
            user_agent="",
        )
        yoga_package = ServicePackage.objects.create(
            gym=gym,
            type=ServicePackage.PackageType.CLASS,
            code="YOGA_PACK",
            name="Yoga pack",
            normalized_name="YOGA PACK",
            session_count=4,
            validity_value=1,
            validity_unit=ServicePackage.ValidityUnit.MONTH,
            price=Decimal("20000.00"),
            currency=gym.currency,
            class_type=class_type,
            is_active=True,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    transcript: list[str] = []
    real_generate = generate_content

    def say(message: str, conversation: str = "") -> dict[str, object]:
        payload: dict[str, object] = {"message": message}
        if conversation:
            payload["conversation_id"] = conversation
        turn: dict[str, object] = {}
        for attempt in range(4):
            with override_settings(**live):
                turn = _post(client, headers, payload)
            if "assistant" not in _tools(turn) or attempt == 3:
                break
            time.sleep(15)
        body = str(turn["message"]["body"]).replace("\n", " ")
        transcript.append(f"USER: {message}")
        transcript.append(f"ASSISTANT: {body[:800]} TOOLS: {_tools(turn) or 'none'}")
        pending = turn.get("pending_confirmation")
        if isinstance(pending, dict):
            transcript.append(f"PENDING: {pending.get('summary') or pending.get('tool_name')}")
        return turn

    def until_new_pending(
        message: str,
        conversation: str,
        previous_step: str,
        followups: tuple[str, ...],
    ) -> tuple[str, dict[str, object]]:
        prompts = (message, *followups)
        index = 0
        retries = 0
        while index < len(prompts) and retries < 4:
            turn = say(prompts[index], conversation)
            conversation = str(turn["conversation_id"])
            body = str(turn["message"]["body"])
            pending = turn.get("pending_confirmation")
            if isinstance(pending, dict) and str(pending.get("step_id")) != previous_step:
                return conversation, pending
            if "ဆက်မပြောနိုင်" in body or "unavailable" in body.casefold():
                retries += 1
                time.sleep(20)
                continue
            index += 1
            retries = 0
        raise AssertionError("no new confirmation\n" + "\n".join(transcript))

    try:
        conversation, pending = until_new_pending(
            "အဖွဲ့ဝင်အသစ် ဖွင့်ပါ။ နာမည် Nwe Nwe။ ဒီနေ့ join လုပ်မယ်။",
            "",
            "",
            ("ဖုန်းမရှိပါ။", "Nwe Nwe၊ ဒီနေ့။"),
        )
        agreed = say("yes", conversation)
        assert agreed["pending_confirmation"] is not None
        assert _rows(gym, Member) == 0
        with override_settings(**live):
            created = _confirm(client, headers, str(pending["step_id"]))
        assert created["outcome"] == "committed"
        with override_settings(**live):
            replay = _confirm(client, headers, str(pending["step_id"]))
        assert replay["outcome"] == "existing"
        member = _one(gym, Member)
        assert member.gym_id == gym.id
        assert "Nwe" in f"{member.first_name} {member.last_name}"
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            grant_package_admin(
                gym=gym,
                actor=owner,
                member_id=member.id,
                service_package_id=yoga_package.id,
                business_date=today,
                complimentary=True,
                reason="Booking coverage",
                request_id="req_av_grant",
                ip_address=None,
                user_agent="",
            )

        conversation, pending = until_new_pending(
            f"Nwe Nwe ကို {plan.code} ဒီနေ့ စတင်ပြီး ရောင်းပါ။",
            conversation,
            str(pending["step_id"]),
            (f"{plan.code} ပါ။ ဒီနေ့ စမယ်။",),
        )
        with override_settings(**live):
            sold = _confirm(client, headers, str(pending["step_id"]))
        assert sold["outcome"] == "committed"
        membership = _one(gym, Membership)
        assert membership.plan_code == plan.code
        assert membership.gym_id == gym.id

        previous_step = str(pending["step_id"])
        pt_prompts = (
            "Nwe Nwe ကို Morning PT package ဒီနေ့ ရောင်းပါ။ Trainer က Ko Min ပါ။ "
            "ငွေပေးချေမှု bank transfer ပါ။",
            "ရှိပြီးသား PT sale ကို submit လုပ်ပါ။ အသစ် မဖွင့်ပါနဲ့။",
            "submit လုပ်ပြီးသား PT sale ကို approve လုပ်ပါ။",
        )

        def confirm_card(card: dict[str, object]) -> dict[str, object]:
            with override_settings(**live):
                confirmed = _confirm(client, headers, str(card["step_id"]))
            transcript.append(f"CONFIRM: {card.get('summary')} {confirmed.get('outcome')}")
            return confirmed

        for index in range(6):
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                posted = PTSale.objects.filter(gym=gym, status=PTSale.Status.POSTED).exists()
            if posted:
                break
            conversation, pending = until_new_pending(
                pt_prompts[min(index, len(pt_prompts) - 1)],
                conversation,
                previous_step,
                (),
            )
            summary = str(pending.get("summary") or "")
            folded = summary.casefold()
            another_draft = (
                _rows(gym, PTSale) > 0
                and "submit" not in folded
                and "approve" not in folded
                and "sale" in folded
            )
            previous_step = str(pending["step_id"])
            if another_draft:
                with override_settings(**live):
                    declined = _reject(client, headers, previous_step)
                transcript.append(f"REJECT extra draft: {summary} {declined.get('outcome')}")
                continue
            confirmed = confirm_card(pending)
            follow = confirmed.get("pending_confirmation")
            for _chain in range(3):
                if not isinstance(follow, dict):
                    break
                follow_summary = str(follow.get("summary") or "").casefold()
                if "submit" not in follow_summary and "approve" not in follow_summary:
                    break
                previous_step = str(follow["step_id"])
                confirmed = confirm_card(follow)
                follow = confirmed.get("pending_confirmation")
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            sale = PTSale.objects.get(gym=gym, status=PTSale.Status.POSTED)
        assert sale.gym_id == gym.id
        assert sale.member_id == member.id
        assert sale.gross_fee == Decimal("80000.00")
        assert sale.status == PTSale.Status.POSTED

        _class_conversation, pending = until_new_pending(
            "Nwe Nwe ကို ဒီနေ့ Yoga class မှာ book လုပ်ပေးပါ။",
            "",
            "",
            ("Yoga pack နဲ့ book လုပ်ပါ။ ဒီနေ့။",),
        )
        with override_settings(**live):
            booked = _confirm(client, headers, str(pending["step_id"]))
        transcript.append(f"CONFIRM: {pending.get('summary')} {booked.get('outcome')}")
        assert booked["outcome"] == "committed"
        with override_settings(**live):
            booked_again = _confirm(client, headers, str(pending["step_id"]))
        assert booked_again["outcome"] == "existing"
        booking = _one(gym, ClassBooking)
        assert booking.gym_id == gym.id
        assert booking.member_id == member.id
        assert booking.status == ClassBooking.Status.BOOKED
        assert booking.unmetered is False
        assert booking.member_service_package_id is not None
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            remaining = MemberServicePackage.objects.get(
                gym=gym, member=member, package_type="CLASS"
            ).ledger_entries.aggregate(total=Sum("session_delta"))["total"]
        assert int(remaining) == 4

        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            payment_ids = set(Payment.objects.filter(gym=gym).values_list("id", flat=True))
        _payment_conversation, pending = until_new_pending(
            "Nwe Nwe membership အတွက် 50000 ကို bank transfer နဲ့ ဒီနေ့ ငွေသွင်းမယ်။",
            "",
            "",
            (
                "Nwe Nwe ပါ။ ပမာဏ 50000။ bank transfer။ ဒီနေ့။",
                "Member Nwe Nwe, amount 50000, bank transfer, today.",
            ),
        )
        paid = confirm_card(pending)
        assert paid["outcome"] == "committed"
        with override_settings(**live):
            paid_again = _confirm(client, headers, str(pending["step_id"]))
        assert paid_again["outcome"] == "existing"
        follow = paid.get("pending_confirmation")
        for _chain in range(3):
            if not isinstance(follow, dict):
                break
            follow_summary = str(follow.get("summary") or "").casefold()
            if "submit" not in follow_summary and "approve" not in follow_summary:
                transcript.append(f"SKIP follow-up: {follow.get('summary')}")
                break
            paid = confirm_card(follow)
            follow = paid.get("pending_confirmation")
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            created_payments = list(Payment.objects.filter(gym=gym).exclude(id__in=payment_ids))
        if not created_payments:
            _payment_conversation, pending = until_new_pending(
                "ဒီ membership ငွေသွင်းမှုကို submit လုပ်ပြီး approve လုပ်ပါ။",
                _payment_conversation,
                str(pending["step_id"]),
                (),
            )
            with override_settings(**live):
                paid = _confirm(client, headers, str(pending["step_id"]))
            assert paid["outcome"] == "committed"
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                created_payments = list(Payment.objects.filter(gym=gym).exclude(id__in=payment_ids))
        assert len(created_payments) == 1
        payment = created_payments[0]
        assert payment.gym_id == gym.id
        assert payment.member_id == member.id
        assert payment.amount == Decimal("50000.00")
        assert payment.purpose == Payment.Purpose.MEMBERSHIP

        before_members = _rows(gym, Member)
        _reject_conversation, rejected_pending = until_new_pending(
            "အဖွဲ့ဝင်အသစ် ဖွင့်ပါ။ နာမည် Hla Hla။ ဒီနေ့ join လုပ်မယ်။",
            "",
            "",
            ("ဖုန်းမရှိပါ။",),
        )
        declined: dict[str, object] | None = None
        for attempt in range(4):
            try:
                with override_settings(**live):
                    declined = _reject(client, headers, str(rejected_pending["step_id"]))
                break
            except AssertionError as exc:
                if "THROTTLED" not in str(exc) or attempt == 3:
                    raise
                time.sleep(3)
        assert declined is not None
        assert declined["outcome"] == "cancelled"
        assert _rows(gym, Member) == before_members
        payment_count = _rows(gym, Payment)

        fail_next = {"armed": True}

        def fail_once(**kwargs: object) -> object:
            if fail_next["armed"]:
                fail_next["armed"] = False
                raise ProviderCallError(ProviderFailure.NETWORK, reached_provider=False)
            return real_generate(**kwargs)

        monkeypatch.setattr("apps.assistant.turns.generate_content", fail_once)
        with override_settings(**live):
            failed = _post(
                client,
                headers,
                {"message": "ဒီနေ့ check-in ဘယ်နှစ်ယောက်လဲ", "conversation_id": conversation},
            )
        transcript.append(f"NETWORK: {failed['message']['body']}")
        assert _rows(gym, Payment) == payment_count
        monkeypatch.setattr("apps.assistant.turns.generate_content", real_generate)
        resumed = say("Nwe Nwe ရဲ့ member number ပြပါ။", conversation)
        assert member.member_number in str(resumed["message"]["body"])
        assert _rows(gym, Payment) == payment_count
    except Exception as exc:
        raise AssertionError(f"{type(exc).__name__}: {exc}\n" + "\n".join(transcript)) from None
