"""Synchronous CSV report exports stay tenant-scoped and permission-closed."""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.reports.csv_export import render_report_csv
from apps.reports.inventory_reports import (
    INVENTORY_QUERY_COUNT,
    PRODUCT_SALES_QUERY_COUNT,
    STOCK_MOVEMENT_QUERY_COUNT,
    inventory_export,
    product_sales_export,
    stock_movement_report,
)
from apps.reports.selectors import PERIOD_REPORT_QUERY_COUNT, period_report
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from tests.security.test_dashboard import FROZEN, _headers, _staff
from tests.security.test_inventory_reports import _catalog
from tests.security.test_retail import _gym
from tests.security.test_tenant_gym_settings import jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

DAILY = "/api/v1/reports/daily/export/"
CHECK_INS = "/api/v1/reports/check-ins/export/"
SALES = "/api/v1/reports/product-sales/export/"
INVENTORY = "/api/v1/reports/inventory/export/"
DAY = {"date": "2026-10-01"}
RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-04"}
LEAP = {"date_from": "2024-01-01", "date_to": "2024-12-31"}


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _grant(gym: Gym, owner: GymMembership, membership: GymMembership, code: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        key = code.replace(".", "_").upper()
        role = Role.objects.filter(gym=gym, key=key).first()
        if role is None:
            role = Role.objects.create(
                gym=gym,
                key=key,
                name=code,
                created_by_membership_id=owner.id,
            )
            permission = Permission.objects.get(code=code)
            RolePermission.objects.create(
                gym=gym,
                role=role,
                permission=permission,
                created_by_membership_id=owner.id,
            )
        GymMembershipRole.objects.create(gym=gym, gym_membership=membership, role=role)


def _table(body: str) -> list[dict[str, str]]:
    parsed = list(csv.reader(io.StringIO(body)))
    header, *rows = parsed
    return [dict(zip(header, row, strict=True)) for row in rows]


def _plain(cell: str) -> str:
    if len(cell) >= 2 and cell[0] == "'" and cell[1] in "=+-@":
        return cell[1:]
    return cell


def _expected(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _assert_matches(csv_body: str, payload: dict[str, Any]) -> None:
    table = _table(csv_body)
    data_rows = [row for row in table if row["record_type"] == "ROW"]
    totals_rows = [row for row in table if row["record_type"] == "TOTALS"]
    assert len(totals_rows) == 1
    assert len(data_rows) == len(payload["rows"])
    identity = table[0]
    gym = payload["gym"]
    assert _plain(identity["gym_id"]) == str(gym["id"])
    assert _plain(identity["gym_code"]) == gym["code"]
    assert _plain(identity["gym_name"]) == gym["name"]
    assert identity["timezone"] == payload["timezone"]
    if "currency" in payload:
        assert identity["currency"] == payload["currency"]
    else:
        assert identity["currency"] == ""
    report_range = payload.get("range")
    if isinstance(report_range, dict):
        assert identity["date_from"] == report_range["date_from"]
        assert identity["date_to"] == report_range["date_to"]
    else:
        assert identity["date_from"] == ""
        assert identity["date_to"] == ""
    assert identity["generated_at"] == payload["generated_at"]
    policy = payload["source_status_policy"]
    if isinstance(policy, dict):
        for key, value in policy.items():
            assert identity[f"source_status_policy.{key}"] == value
    else:
        assert identity["source_status_policy"] == policy
    for csv_row, json_row in zip(data_rows, payload["rows"], strict=True):
        for key, value in json_row.items():
            assert _plain(csv_row[key]) == _expected(value)
    totals = totals_rows[0]
    for key, value in payload["totals"].items():
        if key == "expenses_by_category":
            assert isinstance(value, list)
            for category in value:
                assert _plain(totals[f"expense_{category['system_key']}"]) == category["amount"]
            continue
        assert _plain(totals[key]) == _expected(value)


def _csv(
    api: APIClient,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
) -> tuple[Any, str]:
    response = api.get(path, params, **headers)
    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "no-store, private"
    disposition = response["Content-Disposition"]
    assert disposition.startswith("attachment; filename=")
    return response, response.content.decode()


def _queries(gym: Gym, call: Any) -> int:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as captured:
            call(loaded)
    return len(captured.captured_queries)


@override_settings(**jwt_settings())
def test_report_exports_require_export_permission_and_close_trainers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("EXPGAT")
    staff = _staff(gym)
    reception = _headers(staff["reception"])
    trainer = _headers(staff["trainer"])
    assert api.get(DAILY, DAY, **reception).status_code == 403
    assert api.get(CHECK_INS, RANGE, **reception).status_code == 403
    _grant(gym, owner, staff["reception"], "reports.financial")
    assert api.get(DAILY, DAY, **reception).status_code == 403
    assert api.get(DAILY, DAY, **trainer).status_code == 403
    _grant(gym, owner, staff["trainer"], "reports.export")
    assert api.get(CHECK_INS, RANGE, **trainer).status_code == 404
    assert api.get(INVENTORY, **trainer).status_code == 404
    assert api.get(DAILY, DAY, **trainer).status_code == 403
    _grant(gym, owner, staff["trainer"], "reports.financial")
    assert api.get(DAILY, DAY, **trainer).status_code == 404
    _grant(gym, owner, staff["reception"], "reports.export")
    exported, _body = _csv(api, CHECK_INS, RANGE, reception)
    assert exported["Content-Disposition"] == (
        f'attachment; filename="check-ins-{gym.code}-2026-10-01-2026-10-04.csv"'
    )
    assert (
        api.get(
            f"{SALES}?date_from=2026-10-01&date_to=2026-10-04&after=cursor", **owner_headers
        ).status_code
        == 400
    )
    assert (
        api.get(
            f"{SALES}?date_from=2026-10-01&date_to=2026-10-04&limit=25", **owner_headers
        ).status_code
        == 400
    )
    assert api.get(f"{INVENTORY}?after=cursor", **owner_headers).status_code == 400
    assert api.get(f"{INVENTORY}?limit=25", **owner_headers).status_code == 400
    assert api.get(f"{DAILY}?date=2026-10-01&gym_id={gym.id}", **owner_headers).status_code == 400
    assert (
        api.get(
            CHECK_INS,
            {"date_from": "2024-01-01", "date_to": "2025-01-01"},
            **owner_headers,
        ).status_code
        == 400
    )
    missing = uuid4()
    assert (
        api.post("/api/v1/reports/exports/", {}, format="json", **owner_headers).status_code == 404
    )
    assert api.get(f"/api/v1/reports/exports/{missing}/", **owner_headers).status_code == 404
    assert api.get("/api/v1/trainer/dashboard/export/", **trainer).status_code == 404
    assert api.get("/api/v1/trainer/reports/sessions/export/", RANGE, **trainer).status_code == 404
    GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(enabled=False)
    assert api.get(DAILY, DAY, **owner_headers).status_code == 403


@override_settings(**jwt_settings())
def test_report_exports_match_json_escape_formulas_and_reuse_query_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, _owner, owner_headers = _gym("EXPMAT")
    other_api, other_gym, _other_owner, other_headers = _gym("EXPOTH")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Gym.objects.filter(id=gym.id).update(name="=Injection Gym")
    names = ("=1+1", "+PLUS", "-MINUS", "@AT")
    for sku, name in zip(("EQ1", "PLUS1", "MIN1", "AT1"), names, strict=True):
        _catalog(
            api,
            owner_headers,
            sku=sku,
            price="10.00",
            cost="4.00",
            threshold="1.000",
            name=name,
        )
    _catalog(
        other_api,
        other_headers,
        sku="OTHER1",
        price="10.00",
        cost="4.00",
        threshold="1.000",
        name="=OTHERGYM",
    )

    daily_json = api.get("/api/v1/reports/daily/", DAY, **owner_headers)
    assert daily_json.status_code == 200, daily_json.content
    daily_payload = daily_json.json()["data"]
    _response, daily_csv = _csv(api, DAILY, DAY, owner_headers)
    assert _response["Content-Disposition"] == (
        f'attachment; filename="daily-{gym.code}-2026-10-01-2026-10-01.csv"'
    )
    _assert_matches(daily_csv, daily_payload)
    assert "'=Injection Gym" in daily_csv

    sales_json = api.get(
        "/api/v1/reports/product-sales/",
        {"date_from": "2026-10-01", "date_to": "2026-10-04", "limit": "1"},
        **owner_headers,
    )
    assert sales_json.status_code == 200, sales_json.content
    sales_page = sales_json.json()["data"]
    assert len(sales_page["rows"]) == 1
    _sales_response, sales_csv = _csv(api, SALES, RANGE, owner_headers)
    sales_rows = [row for row in _table(sales_csv) if row["record_type"] == "ROW"]
    assert len(sales_rows) == 4
    assert [row["name"] for row in sales_rows] == ["'@AT", "'=1+1", "'-MINUS", "'+PLUS"]
    full_json = api.get(
        "/api/v1/reports/product-sales/",
        {"date_from": "2026-10-01", "date_to": "2026-10-04", "limit": "100"},
        **owner_headers,
    )
    assert full_json.status_code == 200, full_json.content
    full_payload = full_json.json()["data"]
    _assert_matches(sales_csv, full_payload)
    assert sales_page["totals"] == full_payload["totals"]
    assert all("OTHERGYM" not in row["name"] for row in sales_rows)
    assert other_gym.code not in sales_csv

    inventory_json = api.get("/api/v1/reports/inventory/", {"limit": "100"}, **owner_headers)
    assert inventory_json.status_code == 200, inventory_json.content
    inventory_payload = inventory_json.json()["data"]
    inventory_response, inventory_csv = _csv(api, INVENTORY, {}, owner_headers)
    assert inventory_response["Content-Disposition"] == (
        f'attachment; filename="inventory-{gym.code}.csv"'
    )
    _assert_matches(inventory_csv, inventory_payload)
    assert "OTHERGYM" not in inventory_csv

    assert (
        _queries(
            gym,
            lambda loaded: period_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                generated_at=FROZEN,
            ),
        )
        == PERIOD_REPORT_QUERY_COUNT
    )
    assert (
        _queries(
            gym,
            lambda loaded: period_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 31),
                generated_at=FROZEN,
            ),
        )
        == PERIOD_REPORT_QUERY_COUNT
    )
    assert (
        _queries(
            gym,
            lambda loaded: product_sales_export(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                generated_at=FROZEN,
            ),
        )
        == PRODUCT_SALES_QUERY_COUNT
    )
    assert (
        _queries(
            gym,
            lambda loaded: product_sales_export(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            ),
        )
        == PRODUCT_SALES_QUERY_COUNT
    )
    assert (
        _queries(
            gym,
            lambda loaded: stock_movement_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            ),
        )
        == STOCK_MOVEMENT_QUERY_COUNT
    )
    assert (
        _queries(
            gym,
            lambda loaded: inventory_export(gym=loaded, generated_at=FROZEN),
        )
        == INVENTORY_QUERY_COUNT
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        payload = inventory_export(gym=loaded, generated_at=FROZEN)
        with CaptureQueriesContext(connection) as captured:
            render_report_csv("inventory", payload)
    assert len(captured.captured_queries) == 0
    assert (
        api.get("/api/v1/reports/stock-movements/export/", LEAP, **owner_headers).status_code == 200
    )
