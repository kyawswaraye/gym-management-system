"""CSV cell escaping for a report payload that is already calculated."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from uuid import UUID

from apps.reports.csv_export import export_filename, range_dates, render_report_csv

GYM_ID = UUID("11111111-1111-4111-8111-111111111111")
PRODUCT_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_csv_prefixes_formula_characters_and_quotes_rfc4180() -> None:
    body = render_report_csv(
        "product-sales",
        {
            "gym": {"id": GYM_ID, "code": "YGN1", "name": '=Gym "North", Ltd'},
            "timezone": "Asia/Yangon",
            "currency": "MMK",
            "range": {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 3)},
            "generated_at": datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC),
            "source_status_policy": "immutable_line_snapshots",
            "rows": [
                {
                    "product_id": PRODUCT_ID,
                    "sku": "EQ1",
                    "name": "=1+1",
                    "is_active": True,
                    "quantity_sold": "1.000",
                    "revenue": "-1.00",
                    "cogs": "+2.00",
                    "gross_profit": "@SUM(A1)",
                }
            ],
            "totals": {
                "quantity_sold": "1.000",
                "revenue": "-1.00",
                "cogs": "+2.00",
                "gross_profit": "@SUM(A1)",
                "product_count": 1,
                "active_product_count": 0,
            },
        },
    )
    rows = list(csv.reader(io.StringIO(body)))
    assert body.splitlines()[0].startswith("record_type,gym_id,gym_code,gym_name,")
    assert "\r\n" in body
    header = rows[0]
    data = dict(zip(header, rows[1], strict=True))
    totals = dict(zip(header, rows[2], strict=True))
    assert data["record_type"] == "ROW"
    assert totals["record_type"] == "TOTALS"
    assert data["gym_name"] == '\'=Gym "North", Ltd'
    assert data["name"] == "'=1+1"
    assert data["revenue"] == "'-1.00"
    assert data["cogs"] == "'+2.00"
    assert data["gross_profit"] == "'@SUM(A1)"
    assert data["is_active"] == "true"
    assert totals["active_product_count"] == "0"
    assert totals["revenue"] == "'-1.00"
    assert "meta" not in header
    assert "after" not in header
    assert export_filename(
        kind="product-sales",
        gym_code="YGN1",
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 3),
    ) == ("product-sales-YGN1-2026-10-01-2026-10-03.csv")
    assert (
        export_filename(kind="inventory", gym_code="YGN1", date_from=None, date_to=None)
        == "inventory-YGN1.csv"
    )
    assert (
        export_filename(kind="daily", gym_code="bad name", date_from=None, date_to=None)
        == "report.csv"
    )
    start, end = range_dates({"range": {"date_from": "2026-10-01", "date_to": "2026-10-04"}})
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 4))
    assert (
        export_filename(kind="check-ins", gym_code="YGN1", date_from=start, date_to=end)
        == "check-ins-YGN1-2026-10-01-2026-10-04.csv"
    )
    assert range_dates(
        {"range": {"date_from": "2026-10-01T00:00:00", "date_to": "2026-10-04"}}
    ) == (None, None)
    assert range_dates({}) == (None, None)
