"""Deterministic RFC 4180 CSV for one already-calculated report payload.

The file repeats gym identity on every row, then one ``ROW`` per report row,
then one ``TOTALS`` row. Values come from the same serializers as the JSON
report. Cells that start with ``=``, ``+``, ``-``, or ``@`` gain one leading
single quote so a spreadsheet does not treat them as formulas.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from rest_framework import serializers

from apps.reports.serializers import (
    CashBookDaySerializer,
    CashBookReportSerializer,
    CashBookTotalsSerializer,
    CheckInReportDaySerializer,
    CheckInReportSerializer,
    CheckInReportTotalsSerializer,
    ClassReportDaySerializer,
    ClassReportSerializer,
    ClassReportTotalsSerializer,
    InventoryReportRowSerializer,
    InventoryReportSerializer,
    InventoryReportTotalsSerializer,
    MembershipExpiryDaySerializer,
    MembershipExpiryReportSerializer,
    MembershipExpiryTotalsSerializer,
    PeriodReportSerializer,
    ProductSalesReportRowSerializer,
    ProductSalesReportSerializer,
    ProductSalesReportTotalsSerializer,
    ProfitLossDaySerializer,
    ProfitLossReportSerializer,
    ProfitLossTotalsSerializer,
    PTSessionReportDaySerializer,
    PTSessionReportSerializer,
    PTSessionReportTotalsSerializer,
    ReportDaySerializer,
    ReportTotalsSerializer,
    StockMovementReportRowSerializer,
    StockMovementReportSerializer,
    StockMovementReportTotalsSerializer,
)

_FORMULA_STARTS = frozenset("=+-@")
_IDENTITY = (
    "gym_id",
    "gym_code",
    "gym_name",
    "timezone",
    "currency",
    "date_from",
    "date_to",
    "generated_at",
)
_CATEGORY_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FILENAME = re.compile(
    r"^(?:daily|monthly|cash-book|profit-loss|product-sales|membership-expiry|"
    r"check-ins|pt-sessions|classes|stock-movements|inventory)"
    r"-[A-Z0-9][A-Z0-9-]{1,31}"
    r"(?:-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2})?\.csv$"
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_Spec = tuple[
    type[serializers.Serializer[Any]],
    type[serializers.Serializer[Any]],
    type[serializers.Serializer[Any]],
]
_SPECS: dict[str, _Spec] = {
    "daily": (PeriodReportSerializer, ReportDaySerializer, ReportTotalsSerializer),
    "monthly": (PeriodReportSerializer, ReportDaySerializer, ReportTotalsSerializer),
    "cash-book": (CashBookReportSerializer, CashBookDaySerializer, CashBookTotalsSerializer),
    "profit-loss": (
        ProfitLossReportSerializer,
        ProfitLossDaySerializer,
        ProfitLossTotalsSerializer,
    ),
    "membership-expiry": (
        MembershipExpiryReportSerializer,
        MembershipExpiryDaySerializer,
        MembershipExpiryTotalsSerializer,
    ),
    "check-ins": (
        CheckInReportSerializer,
        CheckInReportDaySerializer,
        CheckInReportTotalsSerializer,
    ),
    "pt-sessions": (
        PTSessionReportSerializer,
        PTSessionReportDaySerializer,
        PTSessionReportTotalsSerializer,
    ),
    "classes": (ClassReportSerializer, ClassReportDaySerializer, ClassReportTotalsSerializer),
    "product-sales": (
        ProductSalesReportSerializer,
        ProductSalesReportRowSerializer,
        ProductSalesReportTotalsSerializer,
    ),
    "stock-movements": (
        StockMovementReportSerializer,
        StockMovementReportRowSerializer,
        StockMovementReportTotalsSerializer,
    ),
    "inventory": (
        InventoryReportSerializer,
        InventoryReportRowSerializer,
        InventoryReportTotalsSerializer,
    ),
}


def export_filename(
    *,
    kind: str,
    gym_code: str,
    date_from: date | None,
    date_to: date | None,
) -> str:
    """Return the attachment name, or ``report.csv`` when the parts are unsafe."""

    if date_from is None or date_to is None:
        name = f"{kind}-{gym_code}.csv"
    else:
        name = f"{kind}-{gym_code}-{date_from.isoformat()}-{date_to.isoformat()}.csv"
    if _FILENAME.fullmatch(name) is None:
        return "report.csv"
    return name


def render_report_csv(kind: str, payload: Mapping[str, object]) -> str:
    """Serialize ``payload`` with the JSON report serializer and write CSV."""

    spec = _SPECS.get(kind)
    if spec is None:
        raise ValueError("Unsupported report export.")
    report_cls, row_cls, totals_cls = spec
    rendered = report_cls(_serializable_payload(kind, payload)).data
    if not isinstance(rendered, Mapping):
        raise ValueError("Unsupported report export.")
    rows = _mappings(_sequence(rendered.get("rows")))
    totals = _mapping(rendered.get("totals"))
    row_fields = _field_names(row_cls)
    extra_fields = tuple(
        name
        for name in _field_names(totals_cls)
        if name not in row_fields and name != "expenses_by_category"
    )
    categories = _categories(totals.get("expenses_by_category"))
    policy_names, policy_values = _policy(rendered.get("source_status_policy"))
    identity = _identity(rendered)
    header = [
        "record_type",
        *_IDENTITY,
        *policy_names,
        *row_fields,
        *extra_fields,
        *[f"expense_{key}" for key, _amount in categories],
    ]
    table = [header]
    for row in rows:
        table.append(
            _line(
                "ROW",
                identity,
                policy_values,
                [_cell(row.get(name, "")) for name in row_fields],
                ["" for _name in extra_fields],
                ["" for _category in categories],
            )
        )
    table.append(
        _line(
            "TOTALS",
            identity,
            policy_values,
            [_cell(totals[name]) if name in totals else "" for name in row_fields],
            [_cell(totals.get(name, "")) for name in extra_fields],
            [amount for _key, amount in categories],
        )
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerows(table)
    return buffer.getvalue()


def _serializable_payload(kind: str, payload: Mapping[str, object]) -> Mapping[str, object]:
    if kind not in {"product-sales", "inventory"} or "meta" in payload:
        return payload
    return {**payload, "meta": {"next_after": None, "has_more": False}}


def _field_names(serializer_cls: type[serializers.Serializer[Any]]) -> tuple[str, ...]:
    return tuple(serializer_cls().get_fields())


def _identity(rendered: Mapping[str, object]) -> list[str]:
    gym = _mapping(rendered.get("gym"))
    report_range = _mapping(rendered.get("range"))
    return [
        _cell(gym.get("id", "")),
        _cell(gym.get("code", "")),
        _cell(gym.get("name", "")),
        _cell(rendered.get("timezone", "")),
        _cell(rendered.get("currency", "")) if "currency" in rendered else "",
        _cell(report_range.get("date_from", "")) if report_range else "",
        _cell(report_range.get("date_to", "")) if report_range else "",
        _cell(rendered.get("generated_at", "")),
    ]


def _policy(value: object) -> tuple[list[str], list[str]]:
    if isinstance(value, Mapping):
        names: list[str] = []
        cells: list[str] = []
        for key, item in value.items():
            names.append(f"source_status_policy.{key}")
            cells.append(_cell(item))
        return names, cells
    return ["source_status_policy"], [_cell(value)]


def _categories(value: object) -> list[tuple[str, str]]:
    categories: list[tuple[str, str]] = []
    for item in _sequence(value):
        entry = _mapping(item)
        key = entry.get("system_key")
        if not isinstance(key, str) or _CATEGORY_KEY.fullmatch(key) is None:
            continue
        categories.append((key, _cell(entry.get("amount", ""))))
    return categories


def _line(
    record_type: str,
    identity: list[str],
    policy: list[str],
    row_cells: list[str],
    extra_cells: list[str],
    category_cells: list[str],
) -> list[str]:
    return [record_type, *identity, *policy, *row_cells, *extra_cells, *category_cells]


def _cell(value: object) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if text[:1] in _FORMULA_STARTS:
        return f"'{text}"
    return text


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _sequence(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _mappings(values: list[object]) -> list[Mapping[str, object]]:
    return [_mapping(value) for value in values]


def range_dates(payload: Mapping[str, object]) -> tuple[date | None, date | None]:
    """Return inclusive dates when both values are dates or YYYY-MM-DD strings."""

    report_range = payload.get("range")
    if not isinstance(report_range, Mapping):
        return None, None
    start = _as_date(report_range.get("date_from"))
    end = _as_date(report_range.get("date_to"))
    if start is None or end is None:
        return None, None
    return start, end


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
