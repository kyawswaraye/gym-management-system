"""Retail sale and expense reads stay on stored values and existing filters."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.finance_reads import (
    finance_read_tool_contracts,
    search_expenses,
    search_sales,
)
from apps.assistant.proposals import declaration
from apps.tenants.models import Gym, GymMembership

_SALE_KEYS = {
    "id",
    "member_id",
    "member_number",
    "member_name",
    "business_date",
    "currency",
    "payment_method",
    "reference_number",
    "status",
    "subtotal",
    "discount_total",
    "total_amount",
    "lines",
}
_LINE_KEYS = {
    "id",
    "product_id",
    "sku",
    "product_name",
    "quantity",
    "unit_price",
    "discount",
    "line_revenue",
}
_EXPENSE_KEYS = {
    "id",
    "category_id",
    "category_code",
    "category_name",
    "business_date",
    "description",
    "amount",
    "currency",
    "payment_method",
    "status",
    "original_expense_id",
    "submitted_at",
    "posted_at",
    "voided_at",
    "cancelled_at",
}
_HIDDEN = {
    "phone",
    "email",
    "notes",
    "policy_note",
    "reason",
    "rejection_reason",
    "void_reason",
    "unit_cost_snapshot",
    "cost_of_goods_sold",
    "gross_profit",
    "created_by_membership_id",
    "receipt_file_asset_id",
}


class _Lines:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self.rows


def _gym() -> Gym:
    return Gym(
        id=uuid4(),
        code="sale",
        name="Sale",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
        currency="MMK",
    )


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE),
        payload=payload,
        request_id="req_finance_read",
        ip_address=None,
        user_agent="",
    )


def _sale() -> SimpleNamespace:
    member_id = uuid4()
    line = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        sku_snapshot="WATER1",
        name_snapshot="Water",
        quantity=Decimal("2.000"),
        unit_price_snapshot=Decimal("10.00"),
        discount=Decimal("0.00"),
        line_revenue=Decimal("20.00"),
        unit_cost_snapshot=Decimal("4.00"),
        cost_of_goods_sold=Decimal("8.00"),
        gross_profit=Decimal("12.00"),
    )
    return SimpleNamespace(
        id=uuid4(),
        member_id=member_id,
        member=SimpleNamespace(
            id=member_id,
            member_number="MEM-000004",
            first_name="Hla",
            last_name="Win",
            phone="+959111111111",
            email="secret@example.test",
            notes="secret-note",
        ),
        business_date=date(2026, 11, 10),
        currency="MMK",
        payment_method="CARD",
        reference_number="POS-1",
        status="DRAFT",
        subtotal=Decimal("20.00"),
        discount_total=Decimal("0.00"),
        total_amount=Decimal("20.00"),
        cogs_total=Decimal("8.00"),
        gross_profit_total=Decimal("12.00"),
        reason="secret-reason",
        lines=_Lines([line]),
    )


def _expense() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        category_id=uuid4(),
        category=SimpleNamespace(code="SUPPLIES", name="Supplies"),
        business_date=date(2026, 11, 11),
        description="Towels",
        amount=Decimal("15.00"),
        currency="MMK",
        payment_method="CASH",
        status="DRAFT",
        original_expense_id=None,
        submitted_at=None,
        posted_at=None,
        voided_at=None,
        cancelled_at=None,
        policy_note="secret-policy",
        reason="secret-reason",
        rejection_reason="secret-reason",
        created_by_membership_id=uuid4(),
    )


def test_finance_reads_advertise_only_supported_filters() -> None:
    sales = declaration(finance_read_tool_contracts()["search_sales"])
    expenses = declaration(finance_read_tool_contracts()["search_expenses"])
    sale_parameters = sales["parameters"]
    expense_parameters = expenses["parameters"]
    assert isinstance(sale_parameters, dict)
    assert isinstance(expense_parameters, dict)
    assert sale_parameters["required"] == []
    assert expense_parameters["required"] == []
    sale_properties = sale_parameters["properties"]
    expense_properties = expense_parameters["properties"]
    assert isinstance(sale_properties, dict)
    assert isinstance(expense_properties, dict)
    assert set(sale_properties) == {
        "sale_id",
        "status",
        "business_date",
        "member_id",
        "limit",
        "after",
    }
    assert set(expense_properties) == {
        "expense_id",
        "status",
        "category_id",
        "payment_method",
        "business_date",
        "limit",
        "after",
    }
    assert "date_from" not in sale_properties
    assert "date_to" not in expense_properties
    sale_contract = finance_read_tool_contracts()["search_sales"]
    expense_contract = finance_read_tool_contracts()["search_expenses"]
    assert sale_contract.mode == ExecutionMode.READ
    assert expense_contract.mode == ExecutionMode.READ
    assert sale_contract.finance_closed is True
    assert expense_contract.finance_closed is True
    assert sale_contract.definition.permissions == ("retail.view",)
    assert expense_contract.definition.permissions == ("expenses.view",)
    assert sale_contract.definition.features == ("finance",)
    assert expense_contract.definition.features == ("finance",)
    assert sale_contract.definition.confirmation_required is False
    assert expense_contract.definition.confirmation_required is False


def test_search_sales_returns_stored_totals(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _sale()
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.finance_reads.page_sales", page)
    monkeypatch.setattr("apps.assistant.finance_reads._detailed_sales", lambda _gym, rows: rows)
    result = search_sales(
        _call(
            gym,
            {
                "business_date": "2026-11-10",
                "status": "DRAFT",
                "member_id": str(row.member_id),
            },
        )
    )
    assert captured["gym_id"] == gym.id
    assert captured["business_date"] == date(2026, 11, 10)
    assert captured["status"] == "DRAFT"
    assert captured["member_id"] == row.member_id
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["business_date"] == "2026-11-10"
    assert found[0]["total_amount"] == "20.00"
    assert found[0]["subtotal"] == "20.00"
    assert found[0]["discount_total"] == "0.00"
    assert found[0]["status"] == "DRAFT"
    assert found[0]["member_number"] == "MEM-000004"
    line = found[0]["lines"][0]
    assert line["quantity"] == "2.000"
    assert line["unit_price"] == "10.00"
    assert line["line_revenue"] == "20.00"
    assert set(found[0]) == _SALE_KEYS
    assert set(line) == _LINE_KEYS
    assert _HIDDEN.isdisjoint(found[0])
    assert _HIDDEN.isdisjoint(line)
    assert "+959111111111" not in str(found)


def test_sale_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_sales(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_sales(_call(gym, {"sale_id": "not-a-sale"}))
    with pytest.raises(ValidationError):
        search_sales(_call(gym, {"business_date": "yesterday"}))
    with pytest.raises(ValidationError):
        search_sales(_call(gym, {"date_from": "2026-11-10"}))
    with pytest.raises(ValidationError):
        search_sales(_call(gym, {"limit": 26}))


def test_missing_sale_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(**_kwargs: object) -> SimpleNamespace:
        raise Http404

    monkeypatch.setattr("apps.assistant.finance_reads.get_sale", missing)
    with pytest.raises(Http404):
        search_sales(_call(_gym(), {"sale_id": str(uuid4())}))


def test_sale_page_can_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.finance_reads.page_sales",
        lambda **_kwargs: ([], {"has_more": False, "next_after": None}),
    )
    result = search_sales(_call(_gym(), {"business_date": "2020-01-01"}))
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_sale_total_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _sale()
    row.total_amount = None
    monkeypatch.setattr(
        "apps.assistant.finance_reads.page_sales",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr("apps.assistant.finance_reads._detailed_sales", lambda _gym, rows: rows)
    with pytest.raises(ExecutionRejected) as missing:
        search_sales(_call(_gym(), {}))
    assert missing.value.code == "read_result"


def test_search_expenses_returns_the_stored_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _expense()
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": True, "next_after": "cursor"}

    monkeypatch.setattr("apps.assistant.finance_reads.page_expenses", page)
    monkeypatch.setattr(
        "apps.assistant.finance_reads._detailed_expenses",
        lambda _gym, rows: rows,
    )
    call = _call(
        gym,
        {
            "business_date": "2026-11-11",
            "status": "DRAFT",
            "category_id": str(row.category_id),
            "payment_method": "CASH",
        },
    )
    result = search_expenses(call)
    assert captured["gym_id"] == gym.id
    assert captured["actor_membership_id"] == call.actor.id
    assert captured["business_date"] == date(2026, 11, 11)
    assert captured["category_id"] == row.category_id
    assert captured["payment_method"] == "CASH"
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["amount"] == "15.00"
    assert found[0]["business_date"] == "2026-11-11"
    assert found[0]["category_code"] == "SUPPLIES"
    assert found[0]["status"] == "DRAFT"
    assert found[0]["description"] == "Towels"
    assert result["has_more"] is True
    assert set(found[0]) == _EXPENSE_KEYS
    assert _HIDDEN.isdisjoint(found[0])
    assert "secret-policy" not in str(found)


def test_expense_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_expenses(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_expenses(_call(gym, {"payment_method": "GOLD"}))
    with pytest.raises(ValidationError):
        search_expenses(_call(gym, {"expense_id": "not-an-expense"}))
    with pytest.raises(ValidationError):
        search_expenses(_call(gym, {"date_to": "2026-11-11"}))
