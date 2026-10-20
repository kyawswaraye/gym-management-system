"""Cash-book and profit/loss range parsing and profit formula."""

import time
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.core import signing
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from apps.reports.inventory_reports import (
    INVENTORY_QUERY_COUNT,
    INVENTORY_SOURCE_STATUS_POLICY,
    PRODUCT_SALES_QUERY_COUNT,
    PRODUCT_SALES_SOURCE_STATUS_POLICY,
    STOCK_MOVEMENT_QUERY_COUNT,
    STOCK_MOVEMENT_SOURCE_STATUS_POLICY,
    _decode_position,
)
from apps.reports.ranges import inclusive_days
from apps.reports.selectors import (
    _SOURCES,
    CHECK_IN_QUERY_COUNT,
    CHECK_IN_SOURCE_STATUS_POLICY,
    CLASS_REPORT_QUERY_COUNT,
    CLASS_SOURCE_STATUS_POLICY,
    MEMBERSHIP_EXPIRY_QUERY_COUNT,
    MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY,
    PT_SESSION_QUERY_COUNT,
    PT_SESSION_SOURCE_STATUS_POLICY,
    _ExpenseDay,
    _ProductMetric,
    _profit_row,
    _remaining_capacity,
)
from apps.reports.serializers import (
    InventoryReportQuerySerializer,
    ProductSalesReportQuerySerializer,
    ReportRangeQuerySerializer,
)


def _query(text: str) -> ReportRangeQuerySerializer:
    return ReportRangeQuerySerializer(data=QueryDict(text))


def test_range_query_accepts_one_day_a_leap_day_and_366_days() -> None:
    one = _query("date_from=2026-10-01&date_to=2026-10-01")
    assert one.is_valid(), one.errors
    leap = _query("date_from=2024-02-28&date_to=2024-03-01")
    assert leap.is_valid(), leap.errors
    assert inclusive_days(date(2024, 2, 28), date(2024, 3, 1)) == 3
    year = _query("date_from=2024-01-01&date_to=2024-12-31")
    assert year.is_valid(), year.errors
    assert inclusive_days(date(2024, 1, 1), date(2024, 12, 31)) == 366


def test_range_query_rejects_unknown_duplicate_empty_and_unbounded_values() -> None:
    missing = _query("date_from=2026-10-01")
    assert missing.is_valid() is False
    blank = _query("date_from=&date_to=2026-10-01")
    assert blank.is_valid() is False
    duplicate = _query("date_from=2026-10-01&date_from=2026-10-02&date_to=2026-10-02")
    assert duplicate.is_valid() is False
    unknown = _query("date_from=2026-10-01&date_to=2026-10-01&gym_id=other&status=OPEN")
    assert unknown.is_valid() is False
    reversed_range = _query("date_from=2026-10-02&date_to=2026-10-01")
    assert reversed_range.is_valid() is False
    too_long = _query("date_from=2024-01-01&date_to=2025-01-01")
    assert too_long.is_valid() is False
    assert inclusive_days(date(2024, 1, 1), date(2025, 1, 1)) == 367
    early = _query("date_from=1899-12-31&date_to=1899-12-31")
    assert early.is_valid() is False
    late = _query("date_from=2100-01-01&date_to=2100-01-01")
    assert late.is_valid() is False


def test_operational_reports_publish_fixed_bounds_and_current_status_policy() -> None:
    assert MEMBERSHIP_EXPIRY_QUERY_COUNT == 2
    assert CHECK_IN_QUERY_COUNT == 4
    assert "not_historical_point_in_time_status" in MEMBERSHIP_EXPIRY_SOURCE_STATUS_POLICY
    assert "mutually_exhaustive" in CHECK_IN_SOURCE_STATUS_POLICY
    assert "independent_of_selected_range" in CHECK_IN_SOURCE_STATUS_POLICY


def test_session_reports_publish_current_status_and_capacity_clamp() -> None:
    assert PT_SESSION_QUERY_COUNT == 2
    assert CLASS_REPORT_QUERY_COUNT == 4
    assert "current_status_at_generation" in PT_SESSION_SOURCE_STATUS_POLICY
    assert "not_status_transition_date_or_history" in PT_SESSION_SOURCE_STATUS_POLICY
    assert "metered_and_unmetered_include_every_status" in PT_SESSION_SOURCE_STATUS_POLICY
    assert "current_occurrence_and_booking_status" in CLASS_SOURCE_STATUS_POLICY
    assert "clamped_at_zero" in CLASS_SOURCE_STATUS_POLICY
    assert "not_status_transition_dates" in CLASS_SOURCE_STATUS_POLICY
    assert _remaining_capacity(4, 2) == 2
    assert _remaining_capacity(1, 4) == 0
    assert _remaining_capacity(0, 0) == 0


def test_inventory_report_queries_keep_fixed_bounds() -> None:
    assert PRODUCT_SALES_QUERY_COUNT <= 9
    assert STOCK_MOVEMENT_QUERY_COUNT == 1
    assert INVENTORY_QUERY_COUNT <= 3
    assert "immutable_line_snapshots" in PRODUCT_SALES_SOURCE_STATUS_POLICY
    assert "not_current_product_cost" in PRODUCT_SALES_SOURCE_STATUS_POLICY
    assert "stored_signed_quantity" in STOCK_MOVEMENT_SOURCE_STATUS_POLICY
    assert "not_rewritten_absolute_quantity" in STOCK_MOVEMENT_SOURCE_STATUS_POLICY
    assert "current_same_gym_movement_sum" in INVENTORY_SOURCE_STATUS_POLICY
    assert "includes_archived_products" in INVENTORY_SOURCE_STATUS_POLICY
    sales = ProductSalesReportQuerySerializer(
        data=QueryDict("date_from=2026-10-01&date_to=2026-10-03")
    )
    assert sales.is_valid(), sales.errors
    assert sales.validated_data["limit"] == 25
    inventory = InventoryReportQuerySerializer(data=QueryDict(""))
    assert inventory.is_valid(), inventory.errors
    assert inventory.validated_data["limit"] == 25
    rejected = (
        "date_from=2026-10-01&date_to=2026-10-01&limit=0",
        "date_from=2026-10-01&date_to=2026-10-01&limit=101",
        "date_from=2026-10-01&date_to=2026-10-01&limit=",
        "date_from=2026-10-01&date_to=2026-10-01&limit=25&limit=25",
        "date_from=2026-10-01&date_to=2026-10-01&after=",
        "date_from=2026-10-01&date_to=2026-10-01&gym_id=other",
    )
    for text in rejected:
        assert ProductSalesReportQuerySerializer(data=QueryDict(text)).is_valid() is False
    dated = InventoryReportQuerySerializer(data=QueryDict("date_from=2026-10-01"))
    assert dated.is_valid() is False
    assert InventoryReportQuerySerializer(data=QueryDict("limit=25&limit=10")).is_valid() is False


def test_report_cursors_bind_route_scope_and_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    row_id = uuid4()
    scope = {
        "route": "product-sales",
        "gym_id": str(uuid4()),
        "actor_membership_id": str(uuid4()),
        "date_from": "2026-10-01",
        "date_to": "2026-10-04",
        "limit": "25",
        "ordering": "sku ASC, id ASC",
    }
    issued = time.time()
    token = signing.dumps(
        {"position_sku": "PRO", "id": str(row_id), **scope},
        salt="report-product-sales",
        compress=True,
    )
    sku, decoded = _decode_position(token, scope=scope, salt="report-product-sales")
    assert sku == "PRO"
    assert decoded == row_id
    loaded = signing.loads(token, salt="report-product-sales", max_age=24 * 60 * 60)
    assert set(loaded) == {"position_sku", "id", *scope}
    assert "email" not in loaded
    assert "cost" not in loaded
    inventory_scope = {
        "route": "inventory",
        "gym_id": scope["gym_id"],
        "actor_membership_id": scope["actor_membership_id"],
        "limit": "25",
        "ordering": scope["ordering"],
    }
    with pytest.raises(ValidationError):
        _decode_position(token, scope=inventory_scope, salt="report-inventory")
    changed = dict(scope)
    changed["limit"] = "10"
    with pytest.raises(ValidationError):
        _decode_position(token, scope=changed, salt="report-product-sales")
    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    with pytest.raises(ValidationError):
        _decode_position(tampered, scope=scope, salt="report-product-sales")
    monkeypatch.setattr(
        "django.core.signing.time.time",
        lambda: issued + 24 * 60 * 60 + 5,
    )
    with pytest.raises(ValidationError):
        _decode_position(token, scope=scope, salt="report-product-sales")


def test_profit_row_keeps_net_profit_as_revenue_minus_expenses() -> None:
    day = date(2026, 10, 1)
    amounts = {source: Decimal("0.00") for source in _SOURCES}
    amounts["PRODUCT"] = Decimal("10.00")
    amounts["CLASS"] = Decimal("30.00")
    expense = _ExpenseDay()
    expense.add("RENT", Decimal("3.00"), 1)
    product = _ProductMetric()
    product.cogs = Decimal("4.00")
    product.profit = Decimal("6.00")
    row = _profit_row(day, {day: amounts}, {day: expense}, {day: product})
    assert row["class_revenue"] == "30.00"
    assert row["product_revenue"] == "10.00"
    assert row["total_revenue"] == "40.00"
    assert row["product_cogs"] == "4.00"
    assert row["product_gross_profit"] == "6.00"
    assert row["total_expenses"] == "3.00"
    assert row["net_profit"] == "37.00"
