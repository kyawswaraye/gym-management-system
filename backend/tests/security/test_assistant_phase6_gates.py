"""Pre-proposal business gates for membership, booking, retail, and payments."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.configuration import ProviderFailure
from apps.assistant.lookups import proposal_block, unmetered_gate
from apps.assistant.models import Message
from apps.assistant.provider import ProviderCallError
from apps.assistant.records import append_message, open_confirmation, open_conversation
from apps.assistant.turns import confirm_step
from apps.classes.booking_services import create_class_booking
from apps.common.api.exceptions import RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.inventory.models import Product, StockMovement
from apps.inventory.services import adjust_stock, create_category, create_product, update_product
from apps.members.models import Member, Payment, Receipt
from apps.members.payment_services import (
    RetailCorrectionUnavailable,
    approve_payment,
    create_payment_draft,
    refund_payment,
    submit_payment,
)
from apps.members.services import (
    archive_plan,
    create_membership,
    create_plan,
    gym_today,
)
from apps.sales.models import RetailSale
from apps.sales.services import approve_sale, create_sale, submit_sale
from apps.tenants.models import Gym, GymMembership
from apps.trainers.models import PTBooking
from apps.trainers.pt_booking_services import create_pt_booking
from apps.trainers.services import create_assignment, create_service_package
from tests.security.test_assistant_booking_commands import (
    _grant,
    _member,
    _occurrence,
    _schedule,
    _type,
)
from tests.security.test_assistant_payment_reads import _owner
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_ZONE = ZoneInfo("Asia/Yangon")


def _payments(gym: Gym) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Payment.objects.filter(gym=gym).count()


def _plan(gym: Gym, actor: GymMembership, code: str, *, price: str = "100.00"):
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_plan(
            gym=gym,
            actor=actor,
            code=code,
            name=code,
            description="",
            duration_value=1,
            duration_unit="MONTH",
            price=Decimal(price),
        )


def _sell(gym: Gym, actor: GymMembership, member: Member, plan, start: date):
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_membership(
            gym=gym,
            actor=actor,
            member=member,
            plan=plan,
            start_date=start,
            discount_amount=Decimal("0.00"),
        )


def _status(gym: Gym, member: Member, status: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.filter(gym=gym, id=member.id).update(status=status)


def _draft_payment(gym: Gym, actor: GymMembership, **fields: object) -> Payment:
    payload: dict[str, object] = {
        "purpose": "OTHER",
        "description": "Day pass",
        "amount": Decimal("40.00"),
        "currency": gym.currency,
        "method": "BANK_TRANSFER",
        "business_date": gym_today(gym),
    }
    payload.update(fields)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_payment_draft(
            gym=gym,
            actor=actor,
            fields=payload,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )


def _post(gym: Gym, actor: GymMembership, payment: Payment) -> Payment:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        submit_payment(
            gym=gym,
            actor=actor,
            payment_id=payment.id,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        approved, _receipt, _session, _movement, _duplicate = approve_payment(
            gym=gym,
            actor=actor,
            payment_id=payment.id,
            cash_session_id=None,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    return approved


def _receipt(gym: Gym, payment: Payment) -> str:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Receipt.objects.get(gym=gym, payment_id=payment.id).receipt_number


def _request(gym: Gym, actor: GymMembership) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/confirmations/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    request.request_id = "req_phase6"
    return request


def _waiting(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    payload: dict[str, object],
    staff_text: str,
):
    conversation = open_conversation(gym=gym, actor=actor, title="Phase 6")
    append_message(
        gym=gym,
        conversation=conversation,
        actor=actor,
        role=Message.Role.STAFF,
        body=staff_text,
    )
    opened = open_confirmation(
        gym=gym,
        actor=actor,
        conversation=conversation,
        intent=tool_name,
        tool_name=tool_name,
        required_input={"actor_membership_id": str(actor.id), "payload": payload},
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert opened is not None
    return opened[1]


def _product(
    gym: Gym,
    actor: GymMembership,
    *,
    sku: str,
    name: str,
    opening: str,
) -> Product:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=actor,
            fields={"code": sku, "name": name},
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        product = create_product(
            gym=gym,
            actor=actor,
            fields={
                "category_id": category.id,
                "sku": sku,
                "name": name,
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "default_unit_cost": "4.00",
                "low_stock_threshold": "0.000",
            },
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        if Decimal(opening) > 0:
            adjust_stock(
                gym=gym,
                actor=actor,
                fields={
                    "product_id": product.id,
                    "quantity": opening,
                    "movement_type": "OPENING",
                    "business_date": gym_today(gym),
                    "reason": "Opening count",
                },
                request_id=f"req_{uuid4()}",
                ip_address=None,
                user_agent="",
            )
    return product


@override_settings(**jwt_settings())
def test_membership_proposal_checks_member_plan_dates_and_overlap() -> None:
    gym, owner = _owner("P6M1", "p6m1-owner@example.test")
    _enable(gym, "members")
    active = _member(gym, owner, "Active")
    paused = _member(gym, owner, "Paused")
    _status(gym, paused, Member.Status.SUSPENDED)
    monthly = _plan(gym, owner, "MONTHLY")
    closed = _plan(gym, owner, "CLOSED")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        archive_plan(gym=gym, actor=owner, plan_id=closed.id)
    start = gym_today(gym).isoformat()
    before = _payments(gym)
    sold = _sell(gym, owner, active, monthly, gym_today(gym))

    inactive_member = proposal_block(
        gym,
        owner,
        "create_membership",
        {"member_id": str(paused.id), "plan_id": str(monthly.id), "start_date": start},
        f"{start} membership",
    )
    inactive_plan = proposal_block(
        gym,
        owner,
        "create_membership",
        {"member_id": str(active.id), "plan_id": str(closed.id), "start_date": start},
        f"{start} membership",
    )
    overlap = proposal_block(
        gym,
        owner,
        "create_membership",
        {"member_id": str(active.id), "plan_id": str(monthly.id), "start_date": start},
        f"{start} membership",
    )
    later = (gym_today(gym) + timedelta(days=40)).isoformat()
    clear = proposal_block(
        gym,
        owner,
        "create_membership",
        {"member_id": str(active.id), "plan_id": str(monthly.id), "start_date": later},
        f"{later} membership",
    )

    assert inactive_member == {"error": "member_inactive"}
    assert inactive_plan == {"error": "plan_inactive"}
    assert overlap is not None
    assert overlap["error"] == "membership_overlap"
    assert overlap["plan"] == sold.plan_name
    assert overlap["start"] == sold.start_date.isoformat()
    assert clear is None
    assert _payments(gym) == before


@override_settings(**jwt_settings())
def test_membership_payment_checks_owner_currency_amount_and_balance() -> None:
    gym, owner = _owner("P6M2", "p6m2-owner@example.test")
    other_gym, other_owner = _owner("P6M3", "p6m3-owner@example.test")
    _enable(gym, "finance")
    _enable(other_gym, "finance")
    member = _member(gym, owner, "Aung")
    other_member = _member(gym, owner, "Hla")
    bare = _member(gym, owner, "Bare")
    foreign_member = _member(other_gym, other_owner, "Foreign")
    plan = _plan(gym, owner, "MONTHLY", price="100.00")
    foreign_plan = _plan(other_gym, other_owner, "MONTHLY", price="100.00")
    sold = _sell(gym, owner, member, plan, gym_today(gym))
    foreign = _sell(other_gym, other_owner, foreign_member, foreign_plan, gym_today(other_gym))
    other_sold = _sell(gym, owner, other_member, plan, gym_today(gym) + timedelta(days=40))
    day = gym_today(gym).isoformat()
    base = {
        "purpose": "MEMBERSHIP",
        "member_id": str(member.id),
        "method": "BANK_TRANSFER",
        "business_date": day,
        "currency": gym.currency,
    }

    missing = proposal_block(
        gym,
        owner,
        "create_payment",
        {**base, "member_id": str(bare.id), "amount": "40.00"},
        f"pay 40.00 on {day}",
    )
    wrong_member = proposal_block(
        gym,
        owner,
        "create_payment",
        {
            **base,
            "member_id": str(other_member.id),
            "membership_id": str(sold.id),
            "amount": "40.00",
        },
        f"pay 40.00 on {day}",
    )
    foreign_id = proposal_block(
        gym,
        owner,
        "create_payment",
        {**base, "membership_id": str(foreign.id), "amount": "40.00"},
        f"pay 40.00 on {day}",
    )
    currency = proposal_block(
        gym,
        owner,
        "create_payment",
        {**base, "membership_id": str(sold.id), "amount": "40.00", "currency": "USD"},
        f"pay 40.00 on {day}",
    )
    mismatch = proposal_block(
        gym,
        owner,
        "create_payment",
        {**base, "membership_id": str(sold.id), "amount": "40.00"},
        f"pay 55.00 on {day}",
    )
    over = proposal_block(
        gym,
        owner,
        "create_payment",
        {**base, "membership_id": str(sold.id), "amount": "150.00"},
        f"pay 150.00 on {day}",
    )
    payload = {**base, "amount": "40.00"}
    accepted = proposal_block(gym, owner, "create_payment", payload, f"pay 40.00 on {day}")

    assert missing is not None and missing["error"] == "membership_required"
    assert wrong_member == {"error": "membership_mismatch"}
    assert foreign_id == {"error": "membership_mismatch"}
    assert currency == {"error": "currency_mismatch"}
    assert mismatch == {"error": "amount_mismatch"}
    assert over is not None and over["error"] == "balance_exceeded"
    assert accepted is None
    assert payload["membership_id"] == str(sold.id)
    assert other_sold.id != sold.id

    first = _draft_payment(
        gym,
        owner,
        purpose="MEMBERSHIP",
        member_id=member.id,
        membership_id=sold.id,
        amount=Decimal("80.00"),
        description="First fee",
    )
    second = _draft_payment(
        gym,
        owner,
        purpose="MEMBERSHIP",
        member_id=member.id,
        membership_id=sold.id,
        amount=Decimal("40.00"),
        description="Second fee",
    )
    _post(gym, owner, first)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        submit_payment(
            gym=gym,
            actor=owner,
            payment_id=second.id,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        with pytest.raises(Exception) as blocked:
            approve_payment(
                gym=gym,
                actor=owner,
                payment_id=second.id,
                cash_session_id=None,
                request_id=f"req_{uuid4()}",
                ip_address=None,
                user_agent="",
            )
    assert blocked.value.__class__.__name__ == "PaymentPostingConflict"


@override_settings(**jwt_settings())
def test_membership_payment_binds_the_one_named_member() -> None:
    gym, owner = _owner("P6M4", "p6m4-owner@example.test")
    _enable(gym, "finance")
    member = _member(gym, owner, "Nwe")
    other = _member(gym, owner, "Hla")
    plan = _plan(gym, owner, "MONTHLY", price="100.00")
    sold = _sell(gym, owner, member, plan, gym_today(gym))
    day = gym_today(gym).isoformat()
    named = {
        "purpose": "MEMBERSHIP",
        "method": "BANK_TRANSFER",
        "business_date": day,
        "currency": gym.currency,
        "amount": "40.00",
    }
    both = dict(named)

    bound = proposal_block(gym, owner, "create_payment", named, f"Nwe Client pay 40.00 on {day}")
    ambiguous = proposal_block(
        gym,
        owner,
        "create_payment",
        both,
        f"Nwe Client and Hla Client pay 40.00 on {day}",
    )

    assert bound is None
    assert named["member_id"] == str(member.id)
    assert named["membership_id"] == str(sold.id)
    assert "member_id" not in both
    assert ambiguous == {"error": "missing_fields", "fields": ["member_id"]}
    assert other.id != member.id


@override_settings(**jwt_settings())
def test_class_proposal_checks_capacity_package_and_unmetered_intent() -> None:
    gym, owner = _owner("P6C1", "p6c1-owner@example.test")
    _enable(gym, "pt")
    receptionist = add_staff(gym, email="p6c1-desk@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="p6c1-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="YOGA1", name="Yoga Trainer")
    kind = _type(gym, owner)
    on = gym_today(gym) + timedelta(days=3)
    full_schedule = _schedule(
        gym,
        owner,
        kind.id,
        profile.id,
        on=on,
        start=time(9, 0),
        end=time(10, 0),
        capacity=1,
        location="Room A",
    )
    open_schedule = _schedule(
        gym,
        owner,
        kind.id,
        profile.id,
        on=on + timedelta(days=7),
        start=time(9, 0),
        end=time(10, 0),
        capacity=4,
        location="Room B",
    )
    full = _occurrence(full_schedule.id)
    opened = _occurrence(open_schedule.id)
    first = _member(gym, owner, "First")
    second = _member(gym, owner, "Second")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=full.id,
            member_id=first.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="The desk approved a guest trial",
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    day = on.isoformat()
    full_gate = proposal_block(
        gym,
        owner,
        "create_class_booking",
        {"member_id": str(second.id), "class_occurrence_id": str(full.id)},
        f"book the yoga class on {day}",
    )
    silent_package = {
        "member_id": str(second.id),
        "class_occurrence_id": str(opened.id),
        "unmetered": True,
    }
    unmetered_gate(
        gym,
        owner,
        "create_class_booking",
        silent_package,
        f"book the yoga class on {day}",
    )
    missing_package = proposal_block(
        gym,
        owner,
        "create_class_booking",
        silent_package,
        f"book the yoga class on {day}",
    )
    silent = {"member_id": str(second.id), "class_occurrence_id": str(opened.id), "unmetered": True}
    stripped = unmetered_gate(gym, owner, "create_class_booking", silent, f"book yoga on {day}")
    denied = unmetered_gate(
        gym,
        receptionist,
        "create_class_booking",
        {"unmetered": True},
        "unmetered booking because the guest has no package",
    )
    allowed = {"member_id": str(first.id), "class_occurrence_id": str(opened.id), "unmetered": True}
    permitted = unmetered_gate(
        gym,
        owner,
        "create_class_booking",
        allowed,
        "unmetered booking because the guest has no package",
    )
    booked = proposal_block(
        gym,
        owner,
        "create_class_booking",
        allowed,
        "unmetered booking because the guest has no package",
    )

    assert full_gate == {"error": "class_full"}
    assert missing_package is not None and missing_package["error"] == "class_package_required"
    assert stripped is None and "unmetered" not in silent
    assert denied == {"error": "unmetered_denied"}
    assert permitted is None and allowed["unmetered"] is True
    assert booked is None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=opened.id,
            member_id=first.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="The desk approved a guest trial",
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    duplicate = proposal_block(
        gym,
        owner,
        "create_class_booking",
        {
            "member_id": str(first.id),
            "class_occurrence_id": str(opened.id),
            "unmetered": True,
            "unmetered_reason": "The desk approved a guest trial",
        },
        "unmetered booking because the guest already came",
    )
    assert duplicate == {"error": "duplicate_booking"}

    fading = _schedule(
        gym,
        owner,
        kind.id,
        profile.id,
        on=on + timedelta(days=14),
        start=time(9, 0),
        end=time(10, 0),
        capacity=1,
        location="Room C",
    )
    fading_occurrence = _occurrence(fading.id)
    still_open = proposal_block(
        gym,
        owner,
        "create_class_booking",
        {
            "member_id": str(second.id),
            "class_occurrence_id": str(fading_occurrence.id),
            "unmetered": True,
            "unmetered_reason": "The desk approved a guest trial",
        },
        "unmetered booking because the guest has no package",
    )
    assert still_open is None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=fading_occurrence.id,
            member_id=first.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="The last seat was taken after the proposal",
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    step = _waiting(
        gym,
        owner,
        "create_class_booking",
        {
            "member_id": str(second.id),
            "class_occurrence_id": str(fading_occurrence.id),
            "unmetered": True,
            "unmetered_reason": "The desk approved a guest trial",
        },
        "ဒီအတန်းကို ဘိုကင်လုပ်ပါ",
    )
    with pytest.raises(RequestRejected) as refused:
        confirm_step(request=_request(gym, owner), view=APIView(), step_id=step.id)
    text = " ".join(refused.value.error_fields.get("confirmation", []))
    assert "နေရာမကျန်ပါ" in text
    assert "PT" not in text
    assert "ClassFull" not in text
    assert "create_class_booking" not in text


@override_settings(**jwt_settings())
def test_pt_proposal_checks_clock_order_and_trainer_overlap() -> None:
    gym, owner = _owner("P6P1", "p6p1-owner@example.test")
    _enable(gym, "pt")
    member = _member(gym, owner, "Aung")
    trainer = add_staff(gym, email="p6p1-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="PT1", name="Maya")
    start = datetime(2026, 11, 18, 9, 0, tzinfo=_ZONE)
    end = datetime(2026, 11, 18, 10, 0, tzinfo=_ZONE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=profile.id,
            member_id=member.id,
            effective_from=date(2026, 11, 1),
            effective_until=None,
            reason="Private sessions",
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        catalog = create_service_package(
            gym=gym,
            actor=owner,
            package_type="PT",
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=60,
            validity_unit="DAY",
            price=Decimal("100.00"),
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    grant = _grant(gym, owner, member.id, catalog.id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_pt_booking(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=profile.id,
            member_id=member.id,
            starts_at=start,
            ends_at=end,
            member_service_package_id=grant.id,
            unmetered=False,
            unmetered_reason="",
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    base = {
        "member_id": str(member.id),
        "trainer_staff_profile_id": str(profile.id),
    }
    inverted = proposal_block(
        gym,
        owner,
        "create_pt_booking",
        {**base, "starts_at": end.isoformat(), "ends_at": start.isoformat()},
        "2026-11-18 10:00 to 9:00",
    )
    overlap = proposal_block(
        gym,
        owner,
        "create_pt_booking",
        {
            **base,
            "starts_at": datetime(2026, 11, 18, 9, 30, tzinfo=_ZONE).isoformat(),
            "ends_at": datetime(2026, 11, 18, 10, 30, tzinfo=_ZONE).isoformat(),
        },
        "2026-11-18 9:30 to 10:30",
    )
    later = proposal_block(
        gym,
        owner,
        "create_pt_booking",
        {
            **base,
            "starts_at": datetime(2026, 11, 18, 11, 0, tzinfo=_ZONE).isoformat(),
            "ends_at": datetime(2026, 11, 18, 12, 0, tzinfo=_ZONE).isoformat(),
        },
        "2026-11-18 11:00 to 12:00",
    )
    invented = {"unmetered": True, "unmetered_reason": "model"}
    unmetered_gate(gym, owner, "create_pt_booking", invented, "2026-11-18 11:00 to 12:00")

    assert inverted == {"error": "time_order"}
    assert overlap == {"error": "trainer_unavailable"}
    assert later is None
    assert "unmetered" not in invented
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTBooking.objects.filter(gym=gym).count() == 1


@override_settings(**jwt_settings())
def test_retail_proposal_checks_product_quantity_and_stock() -> None:
    gym, owner = _owner("P6R1", "p6r1-owner@example.test")
    _enable(gym, "finance")
    water = _product(gym, owner, sku="WATER", name="Water", opening="5.000")
    towel = _product(gym, owner, sku="TOWEL", name="Towel", opening="0.000")
    day = gym_today(gym).isoformat()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        update_product(
            gym=gym,
            actor=owner,
            product_id=water.id,
            fields={"is_active": False},
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    inactive = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [{"product_id": str(water.id), "quantity": "1"}],
        },
        f"sell 1 water on {day}",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        update_product(
            gym=gym,
            actor=owner,
            product_id=water.id,
            fields={"is_active": True},
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    zero = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [{"product_id": str(water.id), "quantity": "0"}],
        },
        f"sell 0 water on {day}",
    )
    negative = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [{"product_id": str(water.id), "quantity": "-1"}],
        },
        f"sell water on {day}",
    )
    fraction = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [{"product_id": str(water.id), "quantity": "1.5"}],
        },
        f"sell 1.5 water on {day}",
    )
    myanmar = {
        "business_date": day,
        "payment_method": "CARD",
        "lines": [{"product_id": str(water.id), "quantity": "၂"}],
    }
    from apps.assistant.lookups import _normalize_line_numbers

    _normalize_line_numbers(myanmar)
    accepted = proposal_block(gym, owner, "create_retail_sale", myanmar, f"sell 2 water on {day}")
    short = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [{"product_id": str(water.id), "quantity": "6"}],
        },
        f"sell 6 water on {day}",
    )
    mixed = proposal_block(
        gym,
        owner,
        "create_retail_sale",
        {
            "business_date": day,
            "payment_method": "CARD",
            "lines": [
                {"product_id": str(water.id), "quantity": "1"},
                {"product_id": str(towel.id), "quantity": "1"},
            ],
        },
        f"sell 1 water and 1 towel on {day}",
    )

    assert inactive is not None and inactive["error"] == "product_inactive"
    assert zero == {"error": "quantity_invalid"}
    assert negative == {"error": "quantity_invalid"}
    assert fraction == {"error": "quantity_invalid"}
    assert accepted is None
    assert myanmar["lines"][0]["quantity"] == "2"
    assert "unit_price" not in myanmar["lines"][0]
    assert short == {"error": "stock_short", "quantity": "6"}
    assert mixed == {"error": "stock_short", "quantity": "1"}

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = create_sale(
            gym=gym,
            actor=owner,
            fields={
                "business_date": gym_today(gym),
                "payment_method": "CARD",
                "lines": [{"product_id": water.id, "quantity": "2.000", "discount": "0.00"}],
            },
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        adjust_stock(
            gym=gym,
            actor=owner,
            fields={
                "product_id": water.id,
                "quantity": "-4.000",
                "movement_type": "ADJUSTMENT",
                "business_date": gym_today(gym),
                "reason": "Count correction",
            },
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        submit_sale(
            gym=gym,
            actor=owner,
            sale_id=sale.id,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        with pytest.raises(ValidationError) as shortage:
            approve_sale(
                gym=gym,
                actor=owner,
                sale_id=sale.id,
                cash_session_id=None,
                request_id=f"req_{uuid4()}",
                ip_address=None,
                user_agent="",
            )
        assert "not enough" in str(shortage.value.detail).casefold()
        assert (
            StockMovement.objects.filter(
                gym=gym,
                movement_type=StockMovement.Type.SALE,
                retail_sale_line__sale_id=sale.id,
            ).count()
            == 0
        )
        assert RetailSale.objects.get(id=sale.id).status == RetailSale.Status.PENDING_APPROVAL


@override_settings(**jwt_settings())
def test_payment_update_refund_and_void_use_the_gym_payment() -> None:
    gym, owner = _owner("P6Y1", "p6y1-owner@example.test")
    other_gym, other_owner = _owner("P6Y2", "p6y2-owner@example.test")
    _enable(gym, "finance")
    _enable(other_gym, "finance")
    member = _member(gym, owner, "Aung")
    bare = _member(gym, owner, "Bare")
    plan = _plan(gym, owner, "MONTHLY", price="100.00")
    sold = _sell(gym, owner, member, plan, gym_today(gym))
    draft = _draft_payment(gym, owner, amount=Decimal("40.00"))
    foreign = _draft_payment(other_gym, other_owner, amount=Decimal("80.00"))
    posted = _post(
        gym,
        owner,
        _draft_payment(gym, owner, amount=Decimal("60.00"), description="Posted"),
    )
    day = gym_today(gym).isoformat()
    future = (gym_today(gym) + timedelta(days=3)).isoformat()

    not_draft = proposal_block(
        gym,
        owner,
        "update_payment",
        {"payment_id": str(posted.id), "amount": "60.00"},
        "change 60.00",
    )
    foreign_update = proposal_block(
        gym,
        owner,
        "update_payment",
        {"payment_id": str(foreign.id), "amount": "80.00"},
        "change 80.00",
    )
    missing_membership = proposal_block(
        gym,
        owner,
        "update_payment",
        {
            "payment_id": str(draft.id),
            "purpose": "MEMBERSHIP",
            "member_id": str(bare.id),
            "amount": "40.00",
        },
        f"change the 40.00 payment on {day}",
    )
    bad_amount = proposal_block(
        gym,
        owner,
        "update_payment",
        {"payment_id": str(draft.id), "amount": "25.00", "business_date": day},
        f"change 40.00 on {day}",
    )
    future_date = proposal_block(
        gym,
        owner,
        "update_payment",
        {"payment_id": str(draft.id), "business_date": future},
        f"move the 40.00 payment to {future}",
    )
    kept = {"amount": "40.00", "business_date": day}
    updated = proposal_block(gym, owner, "update_payment", kept, f"change 40.00 on {day}")

    assert not_draft == {"error": "payment_state"}
    assert foreign_update == {"error": "not_found", "field": "payment_id"}
    assert missing_membership is not None and missing_membership["error"] == "membership_required"
    assert bad_amount == {"error": "amount_mismatch"}
    assert future_date == {"error": "date_invalid"}
    assert updated is None
    assert kept["payment_id"] == str(draft.id)

    draft_refund = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(draft.id), "amount": "40.00", "business_date": day},
        f"refund 40.00 on {day}",
    )
    foreign_refund = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(foreign.id), "amount": "80.00", "business_date": day},
        f"refund 80.00 on {day}",
    )
    number = _receipt(gym, posted)
    over_refund = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(posted.id), "amount": "90.00", "business_date": day},
        f"refund 90.00 {number} on {day}",
    )
    stated_mismatch = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(posted.id), "amount": "20.00", "business_date": day},
        f"refund 30.00 {number} on {day}",
    )
    valid = {"amount": "20.00", "business_date": day}
    accepted = proposal_block(
        gym, owner, "refund_payment", valid, f"refund 20.00 {number} on {day}"
    )
    voided: dict[str, object] = {}
    can_void = proposal_block(
        gym, owner, "void_payment", voided, f"void the 60.00 payment {number} on {day}"
    )

    assert draft_refund == {"error": "payment_state"}
    assert foreign_refund == {"error": "not_found", "field": "payment_id"}
    assert over_refund is not None and over_refund["error"] == "refund_exceeds"
    assert stated_mismatch == {"error": "amount_mismatch"}
    assert accepted is None
    assert valid["payment_id"] == str(posted.id)
    assert can_void is None
    assert voided["payment_id"] == str(posted.id)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        refund_payment(
            gym=gym,
            actor=owner,
            payment_id=posted.id,
            amount=Decimal("20.00"),
            reason="Partial return",
            method="BANK_TRANSFER",
            business_date=gym_today(gym),
            reference_number="",
            cash_session_id=None,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    blocked_void = proposal_block(
        gym,
        owner,
        "void_payment",
        {"payment_id": str(posted.id)},
        f"void the 60.00 payment on {day}",
    )
    above_remaining = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(posted.id), "amount": "50.00", "business_date": day},
        f"refund 50.00 {number} on {day}",
    )
    assert blocked_void is not None and blocked_void["error"] == "void_blocked"
    assert above_remaining is not None and above_remaining["error"] == "refund_exceeds"

    water = _product(gym, owner, sku="WATER", name="Water", opening="2.000")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = create_sale(
            gym=gym,
            actor=owner,
            fields={
                "business_date": gym_today(gym),
                "payment_method": "CARD",
                "lines": [{"product_id": water.id, "quantity": "1.000", "discount": "0.00"}],
            },
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        submit_sale(
            gym=gym,
            actor=owner,
            sale_id=sale.id,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
        _sale, product_charge, sale_receipt, _movement = approve_sale(
            gym=gym,
            actor=owner,
            sale_id=sale.id,
            cash_session_id=None,
            request_id=f"req_{uuid4()}",
            ip_address=None,
            user_agent="",
        )
    retail = proposal_block(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(product_charge.id), "amount": "10.00", "business_date": day},
        f"refund 10.00 {sale_receipt.receipt_number} on {day}",
    )
    assert retail == {"error": "retail_charge"}
    assert sold.member_id == member.id

    step = _waiting(
        gym,
        owner,
        "refund_payment",
        {"payment_id": str(posted.id), "amount": "50.00", "business_date": day},
        "၅၀ ပြန်အမ်းပါ",
    )
    text = ""
    from apps.assistant import turns as turns_module

    def _retail(**_kwargs: object) -> None:
        raise RetailCorrectionUnavailable()

    original = turns_module.execute_confirmed_write
    turns_module.execute_confirmed_write = _retail
    try:
        with pytest.raises(RequestRejected) as retail_error:
            confirm_step(request=_request(gym, owner), view=APIView(), step_id=step.id)
        text = " ".join(retail_error.value.error_fields.get("confirmation", []))
    finally:
        turns_module.execute_confirmed_write = original
    assert "ပြန်အမ်းလမ်းကြောင်း" in text
    assert "stock and destination-session" not in text
    assert "RetailCorrectionUnavailable" not in text


@override_settings(**jwt_settings())
def test_confirmation_describes_a_class_package_failure_in_burmese(monkeypatch) -> None:
    gym, owner = _owner("P6C2", "p6c2-owner@example.test")
    _enable(gym, "pt")
    trainer = add_staff(gym, email="p6c2-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="YOGA2", name="Yoga Trainer")
    kind = _type(gym, owner)
    on = gym_today(gym) + timedelta(days=4)
    schedule = _schedule(
        gym,
        owner,
        kind.id,
        profile.id,
        on=on,
        start=time(11, 0),
        end=time(12, 0),
        capacity=4,
        location="Room C",
    )
    occurrence = _occurrence(schedule.id)
    member = _member(gym, owner, "Aung")
    step = _waiting(
        gym,
        owner,
        "create_class_booking",
        {"member_id": str(member.id), "class_occurrence_id": str(occurrence.id)},
        "ဒီအတန်းကို ဘိုကင်လုပ်ပါ",
    )

    def _fail(**_kwargs: object) -> None:
        raise ProviderCallError(ProviderFailure.PROVIDER)

    monkeypatch.setattr("apps.assistant.turns.generate_content", _fail)
    result = confirm_step(request=_request(gym, owner), view=APIView(), step_id=step.id)

    message = result["message"]
    assert isinstance(message, dict)
    assert "အတန်းပက်ကေ့" in str(message["body"])
    assert "PT" not in str(message["body"])
    assert "PackageNotEligible" not in str(message["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        from apps.assistant.models import Goal

        assert Goal.objects.get(id=step.goal_id).status == Goal.Status.CANCELLED
