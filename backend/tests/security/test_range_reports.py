"""Cash-book and profit/loss reports, isolation, and bounded reads."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.services import record_cash_movement
from apps.common.db.tenant_context import set_gym_context
from apps.reports.selectors import (
    CASH_BOOK_QUERY_COUNT,
    CASH_BOOK_SOURCE_STATUS_POLICY,
    PROFIT_LOSS_QUERY_COUNT,
    SOURCE_STATUS_POLICY,
    cash_book_report,
    period_report,
    profit_loss_report,
)
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
)
from apps.tenants.sequence_services import EXPENSE_CATEGORY_CATALOG
from tests.security.test_dashboard import (
    FROZEN,
    _category,
    _expense,
    _headers,
    _legacy_class_payment,
    _member,
    _staff,
    _void_legacy,
    _zone,
)
from tests.security.test_reports import _grant_financial
from tests.security.test_retail import _enable, _gym
from tests.security.test_tenant_gym_settings import jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

BUCKETS = (
    "new_member_revenue",
    "renewal_revenue",
    "pt_revenue",
    "class_revenue",
    "product_revenue",
    "other_revenue",
)
PROFIT_FIELDS = (
    *BUCKETS,
    "total_revenue",
    "product_cogs",
    "product_gross_profit",
    "total_expenses",
    "net_profit",
)


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _report(api: Any, headers: dict[str, str], path: str) -> dict[str, Any]:
    response = api.get(path, **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _row(report: dict[str, Any], day: str) -> dict[str, Any]:
    matches = [row for row in report["rows"] if row["date"] == day]
    assert len(matches) == 1
    return matches[0]


def _open_session(
    gym: Gym,
    actor: GymMembership,
    *,
    day: date,
    register: str,
    opening: str,
) -> CashSession:
    amount = Decimal(opening)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return CashSession.objects.create(
            gym=gym,
            business_date=day,
            register_code=register,
            currency=gym.currency,
            opening_cash=amount,
            cash_sales=Decimal("0.00"),
            cash_expenses=Decimal("0.00"),
            manual_net=Decimal("0.00"),
            expected_closing=amount,
            status=CashSession.Status.OPEN,
            opened_by_membership=actor,
        )


def _move(
    gym: Gym,
    actor: GymMembership,
    session: CashSession,
    *,
    direction: str,
    source_type: str,
    amount: str,
    source_id: Any = None,
    reversal_of_id: Any = None,
    reason: str = "",
) -> CashTransaction:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return record_cash_movement(
            gym=gym,
            actor=actor,
            session_id=session.id,
            direction=direction,
            source_type=source_type,
            source_id=source_id,
            amount=Decimal(amount),
            currency=gym.currency,
            business_date=session.business_date,
            description=source_type,
            reason=reason,
            reversal_of_id=reversal_of_id,
        )


def _close(
    gym: Gym,
    actor: GymMembership,
    session: CashSession,
    *,
    actual: str,
    reason: str,
) -> None:
    counted = Decimal(actual)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        locked = CashSession.objects.select_for_update().get(gym_id=gym.id, id=session.id)
        locked.actual_closing = counted
        locked.difference = counted - locked.expected_closing
        locked.difference_reason = reason
        locked.status = CashSession.Status.CLOSED
        locked.closed_by_membership = actor
        locked.closed_at = timezone.now()
        locked.save(
            update_fields=[
                "actual_closing",
                "difference",
                "difference_reason",
                "status",
                "closed_by_membership",
                "closed_at",
                "updated_at",
            ]
        )


@override_settings(**jwt_settings())
def test_range_reports_reject_closed_callers_and_unbounded_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, _owner_headers = _gym("CBGATE")
    _enable(gym, "reports")
    staff = _staff(gym)
    cash = "/api/v1/reports/cash-book/?date_from=2024-02-28&date_to=2024-03-01"
    profit = "/api/v1/reports/profit-loss/?date_from=2024-02-28&date_to=2024-03-01"
    accountant = _headers(staff["accountant"])
    assert api.get("/api/v1/reports/cash-book/", **accountant).status_code == 400
    assert (
        api.get("/api/v1/reports/profit-loss/?date_from=2026-10-01", **accountant).status_code
        == 400
    )
    overridden = f"{cash}&gym_id={gym.id}&status=OPEN&formula=cash"
    assert api.get(overridden, **accountant).status_code == 400
    duplicate = (
        "/api/v1/reports/cash-book/?date_from=2026-10-01&date_from=2026-10-02&date_to=2026-10-02"
    )
    assert api.get(duplicate, **accountant).status_code == 400
    assert (
        api.get(
            "/api/v1/reports/profit-loss/?date_from=&date_to=2026-10-01",
            **accountant,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/cash-book/?date_from=2026-10-02&date_to=2026-10-01",
            **accountant,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/profit-loss/?date_from=2024-01-01&date_to=2025-01-01",
            **accountant,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/cash-book/?date_from=1899-12-31&date_to=1899-12-31",
            **accountant,
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/reports/profit-loss/?date_from=2100-01-01&date_to=2100-01-01",
            **accountant,
        ).status_code
        == 400
    )
    assert api.get(cash, **_headers(staff["reception"])).status_code == 403
    assert api.get(profit, **_headers(staff["trainer"])).status_code == 403
    _grant_financial(gym, owner, staff["trainer"])
    assert api.get(profit, **_headers(staff["trainer"])).status_code == 404

    leap = _report(api, accountant, cash)
    assert leap["range"] == {"date_from": "2024-02-28", "date_to": "2024-03-01"}
    assert [row["date"] for row in leap["rows"]] == ["2024-02-28", "2024-02-29", "2024-03-01"]
    assert all(
        row["session_count"] == 0 and row["actual_closing"] == "0.00" for row in leap["rows"]
    )
    profit_leap = _report(api, accountant, profit)
    assert len(profit_leap["rows"]) == 3
    assert all(
        row["total_revenue"] == "0.00" and row["net_profit"] == "0.00"
        for row in profit_leap["rows"]
    )
    full_year = _report(
        api,
        accountant,
        "/api/v1/reports/cash-book/?date_from=2024-01-01&date_to=2024-12-31",
    )
    assert len(full_year["rows"]) == 366
    assert full_year["rows"][0]["date"] == "2024-01-01"
    assert full_year["rows"][-1]["date"] == "2024-12-31"
    GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(enabled=False)
    assert api.get(cash, **accountant).status_code == 403


@override_settings(**jwt_settings())
def test_cash_book_reconciles_directions_and_stays_query_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, _owner_headers = _gym("CBFIN")
    _enable(gym, "reports")
    staff = _staff(gym)
    day = date(2026, 10, 1)
    front = _open_session(gym, owner, day=day, register="FRONT", opening="100.00")
    payment_in = _move(
        gym,
        owner,
        front,
        direction="IN",
        source_type="PAYMENT",
        amount="40.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="OUT",
        source_type="PAYMENT",
        amount="5.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="OUT",
        source_type="EXPENSE",
        amount="10.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="IN",
        source_type="EXPENSE",
        amount="2.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="OUT",
        source_type="COMMISSION",
        amount="7.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="IN",
        source_type="COMMISSION",
        amount="1.00",
        source_id=uuid4(),
    )
    _move(
        gym,
        owner,
        front,
        direction="IN",
        source_type="MANUAL",
        amount="3.00",
        reason="Float",
    )
    _move(
        gym,
        owner,
        front,
        direction="OUT",
        source_type="MANUAL",
        amount="1.00",
        reason="Drawer",
    )
    _move(
        gym,
        owner,
        front,
        direction="OUT",
        source_type="REVERSAL",
        amount="4.00",
        reversal_of_id=payment_in.id,
        reason="Correct payment",
    )
    _close(gym, owner, front, actual="120.50", reason="Counted over")
    _open_session(gym, staff["reception"], day=day, register="SIDE", opening="10.00")
    _other_api, other, other_owner, _other_headers = _gym("CBOTH")
    _open_session(other, other_owner, day=day, register="OTHER", opening="999.00")

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        front.refresh_from_db()
    assert front.expected_closing == Decimal("119.00")
    assert front.difference == Decimal("1.50")
    assert front.status == CashSession.Status.CLOSED

    report = _report(
        api,
        _headers(staff["accountant"]),
        "/api/v1/reports/cash-book/?date_from=2026-10-01&date_to=2026-10-02",
    )
    assert report["gym"]["id"] == str(gym.id)
    assert report["gym"]["code"] == gym.code
    assert report["source_status_policy"] == CASH_BOOK_SOURCE_STATUS_POLICY
    assert [row["date"] for row in report["rows"]] == ["2026-10-01", "2026-10-02"]
    booked = _row(report, "2026-10-01")
    assert booked["session_count"] == 2
    assert booked["open_session_count"] == 1
    assert booked["closed_session_count"] == 1
    assert booked["void_session_count"] == 0
    assert booked["opening_cash"] == "110.00"
    assert booked["payment_in"] == "40.00"
    assert booked["payment_out"] == "5.00"
    assert booked["expense_in"] == "2.00"
    assert booked["expense_out"] == "10.00"
    assert booked["commission_in"] == "1.00"
    assert booked["commission_out"] == "7.00"
    assert booked["manual_in"] == "3.00"
    assert booked["manual_out"] == "1.00"
    assert booked["reversal_in"] == "0.00"
    assert booked["reversal_out"] == "4.00"
    assert booked["cash_in"] == "46.00"
    assert booked["cash_out"] == "27.00"
    assert booked["net_cash_flow"] == "19.00"
    assert booked["expected_closing"] == "129.00"
    assert booked["actual_closing"] == "120.50"
    assert booked["difference"] == "1.50"
    empty = _row(report, "2026-10-02")
    assert empty["session_count"] == 0
    assert empty["cash_in"] == "0.00"
    assert empty["actual_closing"] == "0.00"
    assert empty["difference"] == "0.00"
    assert report["totals"]["session_count"] == 2
    assert report["totals"]["opening_cash"] == "110.00"
    assert report["totals"]["net_cash_flow"] == "19.00"
    assert report["totals"]["difference"] == "1.50"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            cash_book_report(
                gym=loaded,
                date_from=day,
                date_to=day,
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_year:
            cash_book_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    one_sql = [item["sql"] for item in one_day.captured_queries]
    year_sql = [item["sql"] for item in full_year.captured_queries]
    assert len(one_sql) == len(year_sql) == CASH_BOOK_QUERY_COUNT, (one_sql, year_sql)


@override_settings(**jwt_settings())
def test_profit_loss_matches_period_report_and_stays_query_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("PLFIN")
    for feature in ("reports", "finance", "members"):
        _enable(gym, feature)
    _zone(gym, "Asia/Bangkok", 1)
    staff = _staff(gym)
    member = _member(gym, owner, number="PL-A", name="Ada", joined=date(2026, 10, 1))
    charge = _legacy_class_payment(
        gym, owner, member, amount="30.00", day=date(2026, 10, 1), sequence=11
    )
    _void_legacy(gym, owner, charge, day=date(2026, 10, 2), sequence=12)
    _expense(
        api,
        _headers(staff["reception"]),
        _headers(staff["manager"]),
        category_id=str(_category(gym, "RENT").id),
        amount="40.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    utilities = _expense(
        api,
        _headers(staff["reception"]),
        _headers(staff["manager"]),
        category_id=str(_category(gym, "UTILITIES").id),
        amount="5.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    voided = api.post(
        f"/api/v1/expenses/{utilities}/void/",
        {"reason": "Wrong meter", "business_date": "2026-10-03"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="pl-expense-void-001",
        **owner_headers,
    )
    assert voided.status_code == 200, voided.content
    pending = _expense(
        api,
        _headers(staff["reception"]),
        _headers(staff["manager"]),
        category_id=str(_category(gym, "RENT").id),
        amount="99.00",
        day="2026-10-01",
        gym=gym,
        post=False,
    )
    assert pending
    other_api, other, other_owner, _other_headers = _gym("PLOTH")
    _enable(other, "reports")
    other_member = _member(other, other_owner, number="PL-Z", name="Zed", joined=date(2026, 10, 1))
    _legacy_class_payment(
        other, other_owner, other_member, amount="999.00", day=date(2026, 10, 1), sequence=21
    )

    path = "/api/v1/reports/profit-loss/?date_from=2026-10-01&date_to=2026-10-05"
    report = _report(api, _headers(staff["accountant"]), path)
    assert report["source_status_policy"] == SOURCE_STATUS_POLICY
    assert [row["date"] for row in report["rows"]] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
        "2026-10-05",
    ]
    first = _row(report, "2026-10-01")
    second = _row(report, "2026-10-02")
    third = _row(report, "2026-10-03")
    assert first["class_revenue"] == "30.00"
    assert first["total_revenue"] == "30.00"
    assert first["total_expenses"] == "45.00"
    assert first["net_profit"] == "-15.00"
    assert first["product_cogs"] == "0.00"
    assert second["class_revenue"] == "-30.00"
    assert second["net_profit"] == "-30.00"
    assert third["total_expenses"] == "-5.00"
    assert third["net_profit"] == "5.00"
    assert _row(report, "2026-10-05")["total_revenue"] == "0.00"
    for row in report["rows"]:
        buckets = sum((Decimal(str(row[name])) for name in BUCKETS), Decimal("0.00"))
        assert buckets == Decimal(str(row["total_revenue"]))
        assert Decimal(str(row["total_revenue"])) - Decimal(str(row["total_expenses"])) == Decimal(
            str(row["net_profit"])
        )
    totals = report["totals"]
    assert totals["class_revenue"] == "0.00"
    assert totals["total_expenses"] == "40.00"
    assert totals["net_profit"] == "-40.00"
    assert totals["staff_salary_expense"] == "0.00"
    assert totals["pt_commission_expense"] == "0.00"
    categories = {item["system_key"]: item["amount"] for item in totals["expenses_by_category"]}
    assert list(categories) == [code for code, _name in EXPENSE_CATEGORY_CATALOG]
    assert categories["RENT"] == "40.00"
    assert categories["UTILITIES"] == "0.00"
    assert len(categories) == 10

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        daily = period_report(
            gym=loaded,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 5),
            generated_at=FROZEN,
        )
        profit = profit_loss_report(
            gym=loaded,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 5),
            generated_at=FROZEN,
        )
    assert isinstance(daily["rows"], list)
    assert isinstance(profit["rows"], list)
    for period_row, profit_row in zip(daily["rows"], profit["rows"], strict=True):
        assert isinstance(period_row, dict)
        assert isinstance(profit_row, dict)
        for field in PROFIT_FIELDS:
            assert profit_row[field] == period_row[field]
    assert isinstance(daily["totals"], dict)
    assert isinstance(profit["totals"], dict)
    for field in (*PROFIT_FIELDS, "staff_salary_expense", "pt_commission_expense"):
        assert profit["totals"][field] == daily["totals"][field]
    assert profit["totals"]["expenses_by_category"] == daily["totals"]["expenses_by_category"]

    isolated = _report(
        other_api,
        _headers(_staff(other)["accountant"]),
        "/api/v1/reports/profit-loss/?date_from=2026-10-01&date_to=2026-10-01",
    )
    assert isolated["gym"]["code"] == other.code
    assert isolated["rows"][0]["class_revenue"] == "999.00"
    assert report["rows"][0]["class_revenue"] == "30.00"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            profit_loss_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_year:
            profit_loss_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    one_sql = [item["sql"] for item in one_day.captured_queries]
    year_sql = [item["sql"] for item in full_year.captured_queries]
    assert len(one_sql) == len(year_sql) == PROFIT_LOSS_QUERY_COUNT, (one_sql, year_sql)
