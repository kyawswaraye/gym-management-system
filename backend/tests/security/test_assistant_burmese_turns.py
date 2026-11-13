"""Burmese conversation orchestration with a mocked provider.

The mock chooses the tool. These tests prove the Assistant API keeps context,
applies the existing rules, confirms writes, and reports the stored result.
They do not prove live Gemini language understanding.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.announcements.models import Announcement
from apps.assistant.configuration import ProviderFailure
from apps.assistant.provider import FunctionCall, ModelTurn, ProviderCallError
from apps.cashbook.models import CashSession
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.classes.services import create_class_schedule, create_class_type
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.inventory.models import Product
from apps.members.models import CheckIn, Member, Membership, Payment
from apps.notifications.models import Notification
from apps.salaries.models import SalaryPeriod
from apps.tenants.models import ExpenseCategory, Gym
from apps.trainers.models import PTBooking, PTSale, ServicePackage
from apps.trainers.services import create_assignment, create_service_package
from tests.security.test_assistant_member_workflow import _plans
from tests.security.test_assistant_payment_reads import _owner
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings, staff_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_FIXED = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.step = 0

    def __call__(self) -> datetime:
        self.step += 1
        return _FIXED + timedelta(seconds=self.step)


def _settings() -> dict[str, str]:
    return {
        **jwt_settings(),
        "ASSISTANT_ENABLED": "true",
        "GEMINI_API_KEY": "k" * 20,
        "GEMINI_MODEL": "gemini-test",
    }


def _latest_parts(contents: list[dict[str, object]]) -> list[object]:
    if not contents:
        return []
    parts = contents[-1].get("parts")
    return parts if isinstance(parts, list) else []


class _Mock:
    """One queued tool call per staff turn. A saved-change cue stops the model."""

    def __init__(self) -> None:
        self.queue: list[FunctionCall] = []

    def push(self, name: str, arguments: dict[str, object]) -> None:
        self.queue.append(FunctionCall(name, arguments))

    def __call__(
        self,
        *,
        contents: list[dict[str, object]],
        declarations: list[dict[str, object]],
        system_instruction: str,
        transport: object = None,
    ) -> ModelTurn:
        del declarations, transport
        latest = " ".join(
            part["text"]
            for part in _latest_parts(contents)
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if latest.startswith("The change is saved"):
            raise ProviderCallError(ProviderFailure.QUOTA)
        responded = any(
            isinstance(part, dict) and "functionResponse" in part
            for part in _latest_parts(contents)
        )
        if responded or not self.queue:
            quantity = _brief_quantity(system_instruction, "Spring Water")
            echoed = _echo_result(contents)
            catalog = _brief_line(system_instruction, "Service packages:")
            if quantity and "လက်ကျန်" in latest:
                text = f"Spring Water လက်ကျန် {quantity}။"
            elif catalog and "ပက်ကေ့" in latest:
                text = catalog
            elif echoed:
                text = echoed
            else:
                text = "ရပါပြီ။"
            return ModelTurn(text, (), ({"text": text},))
        call = self.queue.pop(0)
        return ModelTurn(
            "အတည်ပြုပါ။",
            (call,),
            (
                {"functionCall": {"name": call.name, "args": call.arguments}},
                {"text": "အတည်ပြုပါ။"},
            ),
        )


def _echo_result(contents: list[dict[str, object]]) -> str:
    for item in reversed(contents):
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            response = part.get("functionResponse")
            if not isinstance(response, dict):
                continue
            body = response.get("response")
            if not isinstance(body, dict):
                continue
            result = body.get("result", body)
            if not isinstance(result, dict):
                continue
            bits = _stored_bits(result)
            if bits:
                return " ".join(bits)
    return ""


def _stored_bits(value: object) -> list[str]:
    """Quote stored facts the way a model does after it reads a tool result."""

    bits: list[str] = []

    def walk(item: object, *, depth: int) -> None:
        if len(bits) >= 12 or depth > 4:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if key.endswith("_id") or key in {"phone", "email", "notes"}:
                    continue
                walk(child, depth=depth + 1)
            return
        if isinstance(item, list):
            for child in item[:4]:
                walk(child, depth=depth + 1)
            return
        if isinstance(item, bool) or item is None:
            return
        text = str(item).strip()
        if text and len(text) <= 80:
            bits.append(text)

    walk(value, depth=0)
    return bits


def _brief_line(brief: str, prefix: str) -> str:
    for line in brief.splitlines():
        if line.startswith(prefix):
            return line
    return ""


def _brief_quantity(brief: str, name: str) -> str:
    for line in brief.splitlines():
        if name in line:
            parts = line.split()
            if parts:
                return parts[-1]
    return ""


def _gym(monkeypatch: pytest.MonkeyPatch, code: str) -> tuple[Gym, object]:
    monkeypatch.setattr(django_timezone, "now", _Clock())
    gym, owner = _owner(code, f"{code.lower()}-owner@example.test")
    gym.timezone = "Asia/Yangon"
    gym.save(update_fields=["timezone"])
    for feature in ("members", "memberships", "finance", "reports", "pt"):
        _enable(gym, feature)
    return gym, owner


def _client(owner: object) -> tuple[APIClient, dict[str, str]]:
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token(owner)}"}
    return client, headers


def _post(
    client: APIClient,
    headers: dict[str, str],
    message: str,
    conversation_id: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/assistant/turns/", payload, format="json", **headers)
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _confirm(client: APIClient, headers: dict[str, str], step_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/assistant/confirmations/{step_id}/confirm/",
        {"confirmed": True},
        format="json",
        **headers,
    )
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def _commit(
    mock: _Mock,
    client: APIClient,
    headers: dict[str, str],
    message: str,
    tool: str,
    arguments: dict[str, object],
    conversation_id: str = "",
) -> tuple[dict[str, object], str]:
    mock.push(tool, arguments)
    proposed = _post(client, headers, message, conversation_id)
    pending = proposed["pending_confirmation"]
    assert isinstance(pending, dict), proposed
    body = str(proposed["message"]["body"])
    assert "သိမ်းပြီး" not in body
    saved = _confirm(client, headers, str(pending["step_id"]))
    assert saved["outcome"] == "committed"
    return saved, str(proposed["conversation_id"])


@override_settings(**_settings())
def test_burmese_payment_cash_expense_and_salary(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner = _gym(monkeypatch, "BW01")
    mock = _Mock()
    monkeypatch.setattr("apps.assistant.turns.generate_content", mock)
    client, headers = _client(owner)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        profile = _profile(gym, owner, code="BW01OWN", name="Owner Desk")

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီနေ့ အခြား ငွေပေးချေမှု 50000 ငွေသားနဲ့ မှတ်ပေး",
        "create_payment",
        {
            "purpose": "OTHER",
            "description": "Walk-in day pass",
            "amount": "50000.00",
            "currency": gym.currency,
            "method": "CASH",
            "business_date": "2026-01-01",
        },
    )
    assert "သိမ်းပြီး" in str(_turn["message"]["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.get(gym=gym)
    assert payment.business_date.isoformat() == "2026-11-12"
    assert payment.status == Payment.Status.DRAFT
    assert payment.amount == Decimal("50000.00")

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီနေ့ ငွေသားဖွင့်ချင်တယ်",
        "open_cash_session",
        {"business_date": "2026-01-01", "opening_cash": "10000.00"},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        session = CashSession.objects.get(gym=gym)
    assert session.business_date.isoformat() == "2026-11-12"
    assert session.opening_cash == Decimal("10000.00")
    assert session.status == CashSession.Status.OPEN

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "လျှပ်စစ် အသုံးစရိတ် အမျိုးအစား ဖွင့်ချင်တယ်",
        "create_expense_category",
        {"name": "Utilities"},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = ExpenseCategory.objects.filter(gym=gym, name="Utilities").order_by("-id").get()

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီနေ့ လျှပ်စစ်ခ 8000 မှတ်ပေး",
        "create_expense",
        {
            "category_id": str(category.id),
            "business_date": "2026-01-01",
            "description": "Electricity",
            "amount": "8000.00",
            "currency": gym.currency,
            "payment_method": "CASH",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        expense = Expense.objects.get(gym=gym)
    assert expense.business_date.isoformat() == "2026-11-12"
    assert expense.status == Expense.Status.DRAFT
    assert expense.category_id == category.id

    mock.push("search_expenses", {})
    listed = _post(client, headers, "အသုံးစရိတ် စာရင်း ရှာ", conversation)
    assert listed["pending_confirmation"] is None
    assert "Electricity" in str(listed["message"]["body"])

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီလ လစာစာရင်း ဖွင့်ချင်တယ်",
        "create_salary_period",
        {
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "business_date": "2026-11-12",
            "description": "November desk",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = SalaryPeriod.objects.get(gym=gym)
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "လစာလိုင်း ထည့်ပေး",
        "replace_salary_lines",
        {
            "salary_period_id": str(period.id),
            "lines": [
                {
                    "staff_profile_id": str(profile.id),
                    "amount": "1500.00",
                    "currency": gym.currency,
                    "payment_method": "BANK_TRANSFER",
                    "description": "November pay",
                }
            ],
        },
        conversation,
    )
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "လစာ တင် လုပ်ပေး",
        "submit_salary_period",
        {"salary_period_id": str(period.id)},
        conversation,
    )
    assert "အတည်ပြု" in str(_turn["message"]["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period.refresh_from_db()
    assert period.status == SalaryPeriod.Status.PENDING_APPROVAL

    trainer = add_staff(gym, email="bw01-trainer@example.test", role_key="TRAINER")
    mock.push(
        "approve_salary_period",
        {
            "salary_period_id": str(period.id),
            "record_id": str(period.id),
            "amount": "1500.00",
        },
    )
    denied = _post(
        APIClient(),
        {"HTTP_AUTHORIZATION": f"Bearer {staff_token(trainer)}"},
        "လစာ အတည်ပြုချင်တယ်",
    )
    assert denied["pending_confirmation"] is None
    assert "သိမ်းပြီး" not in str(denied["message"]["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period.refresh_from_db()
    assert period.status == SalaryPeriod.Status.PENDING_APPROVAL


@override_settings(**_settings())
def test_burmese_check_in_follows_membership_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner = _gym(monkeypatch, "BW02")
    active, _inactive = _plans(gym, owner.id)
    mock = _Mock()
    monkeypatch.setattr("apps.assistant.turns.generate_content", mock)
    client, headers = _client(owner)

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Nila Win ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်",
        "create_member",
        {"first_name": "Nila", "last_name": "Win", "join_date": "2026-01-01"},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        nila = Member.objects.get(gym=gym, first_name="Nila")
    assert nila.join_date.isoformat() == "2026-11-12"
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Nila Win ကို ဒီနေ့ YOGA တပ်ပေး",
        "create_membership",
        {
            "member_id": str(nila.id),
            "plan_id": str(active.id),
            "start_date": "2026-01-01",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        nila_membership = Membership.objects.get(gym=gym, member=nila)
    assert nila_membership.start_date.isoformat() == "2026-11-12"
    assert nila_membership.expiry_date.isoformat() == "2026-12-11"
    assert nila_membership.status == Membership.Status.ACTIVE

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Hla Win ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်",
        "create_member",
        {"first_name": "Hla", "last_name": "Win", "join_date": "2026-11-12"},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        hla = Member.objects.get(gym=gym, first_name="Hla")
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Hla Win ကို မနက်ဖြန် YOGA စချင်တယ်",
        "create_membership",
        {
            "member_id": str(hla.id),
            "plan_id": str(active.id),
            "start_date": "2026-11-12",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        hla_membership = Membership.objects.get(gym=gym, member=hla)
    assert hla_membership.start_date.isoformat() == "2026-11-13"
    assert hla_membership.status == Membership.Status.PENDING

    mock.push("create_manual_check_in", {"member_id": str(hla.id)})
    refused = _post(client, headers, "Hla Win ကို ဒီနေ့ ဝင်ရောက် မှတ်ပေး", conversation)
    pending = refused["pending_confirmation"]
    if isinstance(pending, dict):
        response = client.post(
            f"/api/v1/assistant/confirmations/{pending['step_id']}/confirm/",
            {"confirmed": True},
            format="json",
            **headers,
        )
        assert response.status_code == 200, response.content
        denied = response.json()["data"]
        assert denied["outcome"] == "cancelled"
        assert "အသင်းဝင်ကြေး မရှိပါ" in str(denied["message"]["body"])
        assert "သိမ်းပြီး" not in str(denied["message"]["body"])
    else:
        assert "သိမ်းပြီး" not in str(refused["message"]["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, member=hla).count() == 0

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Nila Win ကို ဒီနေ့ ဝင်ရောက် မှတ်ပေး",
        "create_manual_check_in",
        {"member_id": str(nila.id)},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        visit = CheckIn.objects.get(gym=gym, member=nila)
    assert visit.membership_id == nila_membership.id


@override_settings(**_settings())
def test_burmese_announcements_reports_and_retail_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner = _gym(monkeypatch, "BW03")
    mock = _Mock()
    monkeypatch.setattr("apps.assistant.turns.generate_content", mock)
    client, headers = _client(owner)
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Mya Aye ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်",
        "create_member",
        {"first_name": "Mya", "last_name": "Aye", "join_date": "2026-01-01"},
    )
    receptionist = add_staff(gym, email="bw03-reception@example.test", role_key="RECEPTIONIST")

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ကြေညာချက် ဖွင့်ချင်တယ်",
        "create_announcement",
        {
            "type": "GENERAL",
            "title": "Holiday hours",
            "body": "The gym opens at nine.",
            "audience": "ALL_ACTIVE_MEMBERS",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        announcement = Announcement.objects.get(gym=gym)
    assert announcement.status == Announcement.Status.DRAFT
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ကြေညာချက် ပြင်ပေး",
        "update_announcement",
        {"announcement_id": str(announcement.id), "title": "Updated hours"},
        conversation,
    )
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ကြေညာချက် ထုတ် လုပ်ပေး",
        "publish_announcement",
        {"announcement_id": str(announcement.id)},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        announcement.refresh_from_db()
        notes = Notification.objects.filter(announcement=announcement)
    assert announcement.status == Announcement.Status.PUBLISHED
    assert announcement.title == "Updated hours"
    assert notes.filter(channel=Notification.Channel.IN_APP).exists()
    mock.push("search_announcements", {})
    listed = _post(client, headers, "ကြေညာချက် စာရင်း ရှာ", conversation)
    assert "Updated hours" in str(listed["message"]["body"])
    assert "PUBLISHED" in str(listed["message"]["body"])

    mock.push("get_gym_dashboard", {"range": "last_month"})
    dashboard = _post(
        client,
        headers,
        "ဒီလ ဝင်ငွေ ဘယ်လောက်လဲ",
        conversation,
    )
    assert "2026-11-01" in str(dashboard["message"]["body"])
    assert "2026-11-30" in str(dashboard["message"]["body"])
    receptionist_client = APIClient()
    mock.push("get_gym_dashboard", {})
    hidden = _post(
        receptionist_client,
        {"HTTP_AUTHORIZATION": f"Bearer {staff_token(receptionist)}"},
        "ဒီလ ဝင်ငွေ ဘယ်လောက်လဲ",
    )
    assert "ငွေကြေးပမာဏကို ကြည့်ခွင့် မရှိပါ" in str(hidden["message"]["body"])

    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ကုန်အမျိုးအစား ဖွင့်ချင်တယ်",
        "create_product_category",
        {"code": "DRINKS", "name": "Cold Drinks"},
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        from apps.inventory.models import ProductCategory

        category = ProductCategory.objects.get(gym=gym, code="DRINKS")
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ကုန်ပစ္စည်း ဖွင့်ချင်တယ်",
        "create_product",
        {
            "sku": "WATER-1",
            "name": "Spring Water",
            "category_id": str(category.id),
            "unit": "BTL",
            "sale_price": "25.00",
            "current_unit_cost": "10.00",
            "default_unit_cost": "9.00",
            "low_stock_threshold": "2.000",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        product = Product.objects.get(gym=gym)
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီနေ့ ကုန်လက်ကျန် 5 လုံး မှတ်ပေး",
        "adjust_stock",
        {
            "product_id": str(product.id),
            "quantity": "5.000",
            "movement_type": "OPENING",
            "business_date": "2026-01-01",
            "reason": "Opening count",
        },
        conversation,
    )
    mock.queue.clear()
    stock = _post(client, headers, "Spring Water လက်ကျန် ဘယ်လောက်လဲ", conversation)
    assert "5" in str(stock["message"]["body"])
    assert "999" not in str(stock["message"]["body"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        on_hand = Product.objects.filter(gym=gym, id=product.id).aggregate(
            total=Sum("stock_movements__quantity")
        )["total"]
    assert on_hand == Decimal("5.000")


@override_settings(**_settings())
def test_burmese_pt_package_and_class_booking(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner = _gym(monkeypatch, "BW04")
    active, _inactive = _plans(gym, owner.id)
    trainer_membership = add_staff(gym, email="bw04-ada@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        trainer = _profile(gym, trainer_membership, code="BW04ADA", name="Ada")
        package = create_service_package(
            gym=gym,
            actor=owner,
            package_type=ServicePackage.PackageType.PT,
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id="req_bw04",
            ip_address=None,
            user_agent="",
        )
        class_type = create_class_type(
            gym=gym,
            actor=owner,
            code="YOGA",
            name="Morning Yoga",
            description="",
            default_duration_minutes=60,
            default_capacity=10,
            request_id="req_bw04",
            ip_address=None,
            user_agent="",
        )
        create_class_schedule(
            gym=gym,
            actor=owner,
            class_type_id=class_type.id,
            trainer_staff_profile_id=trainer.id,
            weekdays=[date(2026, 11, 13).weekday()],
            local_start_time=time(9, 0),
            local_end_time=time(10, 0),
            effective_from=date(2026, 11, 13),
            effective_until=date(2026, 11, 13),
            location="Studio",
            location_is_exclusive=False,
            capacity=10,
            request_id="req_bw04",
            ip_address=None,
            user_agent="",
        )
    mock = _Mock()
    monkeypatch.setattr("apps.assistant.turns.generate_content", mock)
    client, headers = _client(owner)
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "Kyaw Swar ကို ဒီနေ့ အဖွဲ့ဝင်ဖွင့်ချင်တယ်",
        "create_member",
        {"first_name": "Kyaw", "last_name": "Swar", "join_date": "2026-01-01"},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(gym=gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=trainer.id,
            member_id=member.id,
            effective_from=date(2026, 11, 12),
            effective_until=None,
            reason="Desk request",
            request_id="req_bw04",
            ip_address=None,
            user_agent="",
        )
    found = _post(client, headers, "PT ပက်ကေ့ စာရင်း ရှာ", conversation)
    found_body = str(found["message"]["body"])
    assert "Three sessions" in found_body or "PT3" in found_body
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "ဒီနေ့ PT ရောင်းပေး",
        "create_pt_sale",
        {
            "member_id": str(member.id),
            "trainer_staff_profile_id": str(trainer.id),
            "service_package_id": str(package.id),
            "service_date": "2026-01-01",
            "description": "Private session",
            "gross_fee": "100.00",
            "currency": gym.currency,
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = PTSale.objects.get(gym=gym)
    assert sale.member_id == member.id
    assert sale.trainer_staff_profile_id == trainer.id
    assert sale.service_date.isoformat() == "2026-11-12"
    assert sale.status == PTSale.Status.DRAFT

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(gym=gym)
    mock.push("search_class_occurrences", {})
    classes = _post(client, headers, "အတန်း စာရင်း ရှာ", conversation)
    class_body = str(classes["message"]["body"])
    assert "Morning Yoga" in class_body or "YOGA" in class_body
    _turn, conversation = _commit(
        mock,
        client,
        headers,
        "မနက်ဖြန် အတန်းကို အခမဲ့ ဘိုကင်လုပ်ပေး",
        "create_class_booking",
        {
            "class_occurrence_id": str(occurrence.id),
            "member_id": str(member.id),
            "unmetered": True,
            "unmetered_reason": "Complimentary intro",
        },
        conversation,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        booking = ClassBooking.objects.get(gym=gym)
    assert booking.member_id == member.id
    assert booking.class_occurrence_id == occurrence.id
    assert PTBooking.objects.filter(gym=gym).count() == 0
    del active
