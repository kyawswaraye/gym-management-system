"""PostgreSQL coverage for retail sale and expense reads."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.finance_reads import execute_registered_read
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.expenses.services import create_category, create_expense_draft
from apps.inventory.services import create_category as create_product_category
from apps.inventory.services import create_product
from apps.members.payment_serializers import money_text
from apps.members.services import gym_today
from apps.sales.models import RetailSale
from apps.sales.services import create_sale
from apps.tenants.models import (
    ExpenseCategory,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    IdempotencyRecord,
)
from tests.security.test_assistant_member_reads import _owner, _person
from tests.security.test_assistant_schedule import _goal
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _request(
    gym: Gym,
    actor: GymMembership,
    codes: frozenset[str],
    roles: frozenset[str] = frozenset(),
) -> Request:
    request = Request(APIRequestFactory().get("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.role_keys = roles
    request.request_id = "req_finance_read"
    return request


def _read(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    payload: dict[str, object],
    *,
    codes: frozenset[str],
    roles: frozenset[str] = frozenset(),
):
    return execute_registered_read(
        request=_request(gym, actor, codes, roles),
        view=APIView(),
        tool_name=tool_name,
        payload=payload,
    )


def _sales(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_sales", payload, codes=frozenset({"retail.view"}))


def _expenses(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_expenses", payload, codes=frozenset({"expenses.view"}))


def _ids(page: dict[str, object]) -> set[str]:
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows}


def _counts(gym: Gym, goal: Goal) -> tuple[int, ...]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            RetailSale.objects.filter(gym=gym).count(),
            Expense.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


def _clock_after_yangon_offset() -> datetime:
    """Pick a UTC instant whose previous Asia/Yangon day is a different date.

    The gym is 6 hours 30 minutes ahead of UTC. From 06:30 local onward, the
    UTC calendar date matches the gym date, so yesterday cannot equal it.
    The instant stays at or after the real clock so setup entitlements remain
    effective.
    """

    zone = ZoneInfo("Asia/Yangon")
    current = datetime.now(zone)
    opening = current.replace(hour=6, minute=30, second=0, microsecond=0)
    chosen = current if current >= opening else opening
    return chosen.astimezone(ZoneInfo("UTC"))


@override_settings(**jwt_settings())
def test_sale_and_expense_reads_use_stored_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner = _owner("SL01", "sl01-owner@example.test")
    other_gym, other_owner = _owner("SL02", "sl02-owner@example.test")
    receptionist = add_staff(gym, email="sl01-desk@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="sl01-trainer@example.test", role_key="TRAINER")
    accountant = add_staff(gym, email="sl01-accountant@example.test", role_key="ACCOUNTANT")
    frozen = _clock_after_yangon_offset()
    monkeypatch.setattr("django.utils.timezone.now", lambda: frozen)
    today = gym_today(gym)
    yesterday = today - timedelta(days=1)
    buyer = _person(gym, owner, "Hla", "Win", phone="+959111111111", notes="secret-note")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        product_category = create_product_category(
            gym=gym,
            actor=owner,
            fields={"code": "WATER", "name": "Water"},
            request_id="req_sale_category",
            ip_address=None,
            user_agent="",
        )
        product = create_product(
            gym=gym,
            actor=owner,
            fields={
                "category_id": product_category.id,
                "sku": "WATER1",
                "name": "Water",
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "default_unit_cost": "4.00",
                "low_stock_threshold": "0.000",
            },
            request_id="req_sale_product",
            ip_address=None,
            user_agent="",
        )
        owner_sale = create_sale(
            gym=gym,
            actor=owner,
            fields={
                "business_date": yesterday,
                "payment_method": "CARD",
                "reference_number": "POS-1",
                "member_id": buyer.id,
                "lines": [
                    {"product_id": product.id, "quantity": "2.000", "discount": "0.00"},
                ],
            },
            request_id="req_owner_sale",
            ip_address=None,
            user_agent="",
        )
        desk_sale = create_sale(
            gym=gym,
            actor=receptionist,
            fields={
                "business_date": today,
                "payment_method": "CASH",
                "reference_number": "POS-2",
                "lines": [
                    {"product_id": product.id, "quantity": "1.000", "discount": "0.00"},
                ],
            },
            request_id="req_desk_sale",
            ip_address=None,
            user_agent="",
        )
        expense_category = create_category(
            gym=gym,
            actor=owner,
            fields={"code": "SUPPLIES", "name": "Supplies"},
            request_id="req_expense_category",
            ip_address=None,
            user_agent="",
        )
        owner_expense = create_expense_draft(
            gym=gym,
            actor=owner,
            fields={
                "category_id": expense_category.id,
                "business_date": yesterday,
                "description": "Towels",
                "amount": Decimal("15.00"),
                "currency": gym.currency,
                "payment_method": "CASH",
                "policy_note": "secret-policy",
            },
            request_id="req_owner_expense",
            ip_address=None,
            user_agent="",
        )
        desk_expense = create_expense_draft(
            gym=gym,
            actor=receptionist,
            fields={
                "category_id": expense_category.id,
                "business_date": today,
                "description": "Cups",
                "amount": Decimal("8.50"),
                "currency": gym.currency,
                "payment_method": "CARD",
                "policy_note": "secret-policy",
            },
            request_id="req_desk_expense",
            ip_address=None,
            user_agent="",
        )
        salary = ExpenseCategory.objects.get(gym=gym, system_key="STAFF_SALARY")
        salary_expense = Expense.objects.create(
            gym=gym,
            category=salary,
            business_date=today,
            description="Salary",
            amount=Decimal("100.00"),
            currency=gym.currency,
            payment_method="BANK_TRANSFER",
            status=Expense.Status.DRAFT,
            policy_note="secret-policy",
            created_with_reception_scope=True,
            created_by_membership_id=receptionist.id,
            updated_by_membership_id=receptionist.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        foreign_category = create_product_category(
            gym=other_gym,
            actor=other_owner,
            fields={"code": "WATER", "name": "Water"},
            request_id="req_foreign_category",
            ip_address=None,
            user_agent="",
        )
        foreign_product = create_product(
            gym=other_gym,
            actor=other_owner,
            fields={
                "category_id": foreign_category.id,
                "sku": "WATER1",
                "name": "Water",
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "default_unit_cost": "4.00",
                "low_stock_threshold": "0.000",
            },
            request_id="req_foreign_product",
            ip_address=None,
            user_agent="",
        )
        foreign_sale = create_sale(
            gym=other_gym,
            actor=other_owner,
            fields={
                "business_date": gym_today(other_gym),
                "payment_method": "CARD",
                "reference_number": "OTHER",
                "lines": [
                    {
                        "product_id": foreign_product.id,
                        "quantity": "1.000",
                        "discount": "0.00",
                    },
                ],
            },
            request_id="req_foreign_sale",
            ip_address=None,
            user_agent="",
        )
        foreign_expense_category = create_category(
            gym=other_gym,
            actor=other_owner,
            fields={"code": "SUPPLIES", "name": "Supplies"},
            request_id="req_foreign_expense_category",
            ip_address=None,
            user_agent="",
        )
        foreign_expense = create_expense_draft(
            gym=other_gym,
            actor=other_owner,
            fields={
                "category_id": foreign_expense_category.id,
                "business_date": gym_today(other_gym),
                "description": "Other",
                "amount": Decimal("3.00"),
                "currency": other_gym.currency,
                "payment_method": "CASH",
            },
            request_id="req_foreign_expense",
            ip_address=None,
            user_agent="",
        )
    goal = _goal(gym, owner)
    before = _counts(gym, goal)
    zone = ZoneInfo(gym.timezone)
    assert gym.timezone == "Asia/Yangon"
    assert today == frozen.astimezone(zone).date()
    assert owner_sale.business_date == yesterday
    created_utc = owner_sale.created_at.astimezone(ZoneInfo("UTC")).date()
    assert created_utc == frozen.date()
    assert created_utc != owner_sale.business_date

    listed = _sales(gym, owner, {})
    assert _ids(listed) == {str(owner_sale.id), str(desk_sale.id)}
    by_id = {str(row["id"]): row for row in listed["results"]}
    stored = by_id[str(owner_sale.id)]
    assert stored["business_date"] == owner_sale.business_date.isoformat()
    assert stored["business_date"] != created_utc.isoformat()
    assert stored["total_amount"] == money_text(owner_sale.total_amount) == "20.00"
    assert stored["subtotal"] == money_text(owner_sale.subtotal)
    assert stored["status"] == RetailSale.Status.DRAFT
    assert stored["currency"] == owner_sale.currency
    assert stored["payment_method"] == "CARD"
    assert stored["member_id"] == str(buyer.id)
    assert stored["member_number"] == buyer.member_number
    assert stored["member_name"] == "Hla Win"
    line = stored["lines"][0]
    assert line["sku"] == "WATER1"
    assert line["product_name"] == "Water"
    assert line["quantity"] == "2.000"
    assert line["line_revenue"] == "20.00"
    assert "unit_cost" not in line
    dated = _sales(gym, owner, {"business_date": yesterday.isoformat()})
    assert _ids(dated) == {str(owner_sale.id)}
    assert _sales(gym, owner, {"business_date": today.isoformat()})["results"][0]["id"] == str(
        desk_sale.id
    )
    assert _ids(_sales(gym, owner, {"business_date": "2020-01-01"})) == set()
    assert _ids(_sales(gym, owner, {"member_id": str(buyer.id)})) == {str(owner_sale.id)}
    assert _sales(gym, owner, {"member_id": str(uuid4())})["results"] == []
    exact = _sales(gym, owner, {"sale_id": str(owner_sale.id)})
    assert _ids(exact) == {str(owner_sale.id)}
    desk_page = _read(
        gym,
        receptionist,
        "search_sales",
        {},
        codes=frozenset(),
        roles=frozenset({"RECEPTIONIST"}),
    )
    assert _ids(desk_page) == {str(desk_sale.id)}
    with pytest.raises(ExecutionRejected) as foreign_sale_miss:
        _sales(gym, owner, {"sale_id": str(foreign_sale.id)})
    assert foreign_sale_miss.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as unknown_sale:
        _sales(gym, owner, {"sale_id": str(uuid4())})
    assert unknown_sale.value.code == "not_found"
    with pytest.raises(ValidationError):
        _sales(gym, owner, {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _sales(gym, owner, {"sale_id": "not-a-sale"})
    with pytest.raises(ValidationError):
        _sales(gym, owner, {"date_from": yesterday.isoformat()})
    with pytest.raises(ExecutionRejected) as limited:
        _sales(gym, owner, {"limit": 26})
    assert limited.value.code == "invalid_limit"

    seen: set[str] = set()
    cursor: object = None
    for _page_number in range(4):
        payload: dict[str, object] = {"limit": 1}
        if isinstance(cursor, str):
            payload["after"] = cursor
        page = _sales(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        seen |= found
        if page["has_more"] is False:
            break
        cursor = page["next_after"]
    assert seen == {str(owner_sale.id), str(desk_sale.id)}
    with pytest.raises(ValidationError):
        _sales(other_gym, other_owner, {"limit": 1, "after": cursor})

    expense_page = _expenses(gym, owner, {})
    assert _ids(expense_page) == {
        str(owner_expense.id),
        str(desk_expense.id),
        str(salary_expense.id),
    }
    expense_by_id = {str(row["id"]): row for row in expense_page["results"]}
    owner_row = expense_by_id[str(owner_expense.id)]
    assert owner_row["amount"] == money_text(owner_expense.amount) == "15.00"
    assert owner_row["business_date"] == yesterday.isoformat()
    assert owner_row["category_code"] == "SUPPLIES"
    assert owner_row["category_name"] == "Supplies"
    assert owner_row["description"] == "Towels"
    assert owner_row["status"] == Expense.Status.DRAFT
    assert owner_row["currency"] == owner_expense.currency
    assert _ids(_expenses(gym, owner, {"business_date": yesterday.isoformat()})) == {
        str(owner_expense.id)
    }
    assert _ids(_expenses(gym, owner, {"payment_method": "CARD"})) == {str(desk_expense.id)}
    assert _ids(_expenses(gym, owner, {"category_id": str(expense_category.id)})) == {
        str(owner_expense.id),
        str(desk_expense.id),
    }
    assert _expenses(gym, owner, {"business_date": "2020-01-01"})["results"] == []
    desk_expenses = _read(
        gym,
        receptionist,
        "search_expenses",
        {},
        codes=frozenset(),
        roles=frozenset({"RECEPTIONIST"}),
    )
    assert _ids(desk_expenses) == {str(desk_expense.id)}
    leaked = str(listed) + str(expense_page) + str(desk_expenses)
    assert "secret-note" not in leaked
    assert "secret-policy" not in leaked
    assert "+959111111111" not in leaked
    with pytest.raises(ExecutionRejected) as foreign_expense_miss:
        _expenses(gym, owner, {"expense_id": str(foreign_expense.id)})
    assert foreign_expense_miss.value.code == "not_found"
    with pytest.raises(ValidationError):
        _expenses(gym, owner, {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _expenses(gym, owner, {"payment_method": "GOLD"})
    with pytest.raises(ValidationError):
        _expenses(gym, owner, {"date_to": today.isoformat()})

    expense_seen: set[str] = set()
    expense_cursor: object = None
    for _page_number in range(5):
        payload = {"limit": 1}
        if isinstance(expense_cursor, str):
            payload["after"] = expense_cursor
        page = _expenses(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        expense_seen |= found
        if page["has_more"] is False:
            break
        expense_cursor = page["next_after"]
    assert expense_seen == {
        str(owner_expense.id),
        str(desk_expense.id),
        str(salary_expense.id),
    }
    with pytest.raises(ValidationError):
        _expenses(other_gym, other_owner, {"limit": 1, "after": expense_cursor})

    with pytest.raises(PermissionDenied):
        _read(
            gym,
            accountant,
            "search_sales",
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"ACCOUNTANT"}),
        )
    with pytest.raises(PermissionDenied):
        _read(
            gym,
            accountant,
            "search_expenses",
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"ACCOUNTANT"}),
        )
    with pytest.raises(Http404):
        _read(
            gym,
            trainer,
            "search_sales",
            {},
            codes=frozenset({"retail.view"}),
            roles=frozenset({"TRAINER"}),
        )
    with pytest.raises(Http404):
        _read(
            gym,
            trainer,
            "search_expenses",
            {},
            codes=frozenset({"expenses.view"}),
            roles=frozenset({"TRAINER"}),
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="finance").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _sales(gym, owner, {})
    with pytest.raises(PermissionDenied):
        _expenses(gym, owner, {})

    assert _counts(gym, goal) == before
    assert isinstance(owner_sale.id, UUID)
    assert isinstance(owner_expense.id, UUID)
