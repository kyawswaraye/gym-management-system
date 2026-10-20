"""Period-report windows and shared row assembly."""

from datetime import date
from decimal import Decimal

from apps.reports.ranges import (
    REPORT_MAX_DATE,
    REPORT_MIN_DATE,
    date_in_report_window,
    inclusive_days,
    iter_dates,
    month_bounds,
)
from apps.reports.selectors import (
    _CATEGORY_KEYS,
    _SOURCES,
    SOURCE_STATUS_POLICY,
    _ExpenseDay,
    _OperationDay,
    _ProductMetric,
    _report_row,
    _report_totals,
)
from apps.reports.serializers import DailyReportQuerySerializer, MonthlyReportQuerySerializer


def test_reporting_window_matches_calendar_months() -> None:
    assert date_in_report_window(REPORT_MIN_DATE) is True
    assert date_in_report_window(REPORT_MAX_DATE) is True
    assert date_in_report_window(date(1899, 12, 31)) is False
    assert date_in_report_window(date(2100, 1, 1)) is False
    start, end = month_bounds(2026, 10)
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert inclusive_days(start, end) == 31
    assert len(iter_dates(start, end)) == 31
    assert iter_dates(start, end)[0] == start
    assert iter_dates(start, end)[-1] == end
    february = month_bounds(2026, 2)
    assert inclusive_days(*february) == 28
    assert inclusive_days(*month_bounds(2024, 2)) == 29


def test_report_queries_reject_overrides_and_dates_outside_the_window() -> None:
    daily = DailyReportQuerySerializer(data={"date": "2026-10-01"})
    assert daily.is_valid(), daily.errors
    missing = DailyReportQuerySerializer(data={})
    assert not missing.is_valid()
    malformed = DailyReportQuerySerializer(data={"date": "2026-02-31"})
    assert not malformed.is_valid()
    early = DailyReportQuerySerializer(data={"date": "1899-12-31"})
    assert not early.is_valid()
    late = DailyReportQuerySerializer(data={"date": "2100-01-01"})
    assert not late.is_valid()
    overridden = DailyReportQuerySerializer(
        data={"date": "2026-10-01", "gym_id": "gym", "status": "POSTED", "formula": "cash"}
    )
    assert not overridden.is_valid()
    assert set(overridden.errors) >= {"gym_id", "status", "formula"}

    monthly = MonthlyReportQuerySerializer(data={"year": 2026, "month": 2})
    assert monthly.is_valid(), monthly.errors
    assert not MonthlyReportQuerySerializer(data={"year": 2026}).is_valid()
    assert not MonthlyReportQuerySerializer(data={"year": 1899, "month": 1}).is_valid()
    assert not MonthlyReportQuerySerializer(data={"year": 2100, "month": 1}).is_valid()
    assert not MonthlyReportQuerySerializer(data={"year": 2026, "month": 0}).is_valid()
    assert not MonthlyReportQuerySerializer(data={"year": 2026, "month": 13}).is_valid()
    extra = MonthlyReportQuerySerializer(data={"year": 2026, "month": 10, "gym_id": "gym"})
    assert not extra.is_valid()
    assert "gym_id" in extra.errors


def test_rows_zero_fill_and_totals_do_not_sum_unique_attendees() -> None:
    first = date(2026, 10, 1)
    second = date(2026, 10, 2)
    revenue = {source: Decimal("0.00") for source in _SOURCES}
    revenue["CLASS"] = Decimal("30.00")
    revenue["PRODUCT"] = Decimal("20.00")
    expense = _ExpenseDay()
    expense.add("RENT", Decimal("40.00"), 1)
    expense.add(None, Decimal("7.00"), 1)
    product = _ProductMetric()
    product.add(
        quantity=Decimal("2.000"),
        revenue=Decimal("20.00"),
        cogs=Decimal("8.00"),
        profit=Decimal("12.00"),
        sign=1,
    )
    first_ops = _OperationDay()
    first_ops.new_members = 1
    first_ops.unique_attendees = 1
    second_ops = _OperationDay()
    second_ops.unique_attendees = 1
    rows = [
        _report_row(
            first,
            {first: revenue},
            {first: expense},
            {first: product},
            {first: first_ops},
        ),
        _report_row(second, {}, {}, {}, {second: second_ops}),
    ]
    assert rows[1]["total_revenue"] == "0.00"
    assert rows[1]["product_quantity"] == "0.000"
    assert rows[1]["net_profit"] == "0.00"
    totals = _report_totals(rows, {first: expense}, 1)
    assert totals["unique_attendees"] == 1
    assert totals["new_members"] == 1
    assert totals["total_revenue"] == "50.00"
    assert totals["class_revenue"] == "30.00"
    assert totals["product_revenue"] == "20.00"
    assert totals["product_quantity"] == "2.000"
    assert totals["total_expenses"] == "47.00"
    assert totals["net_profit"] == "3.00"
    assert totals["staff_salary_expense"] == "0.00"
    assert totals["pt_commission_expense"] == "0.00"
    categories = totals["expenses_by_category"]
    assert isinstance(categories, list)
    assert [item["system_key"] for item in categories] == list(_CATEGORY_KEYS)
    assert categories[0]["system_key"] == "RENT"
    assert categories[0]["amount"] == "40.00"
    assert all(item["amount"] == "0.00" for item in categories if item["system_key"] != "RENT")
    assert set(SOURCE_STATUS_POLICY) == {"revenue", "expenses", "operations"}
