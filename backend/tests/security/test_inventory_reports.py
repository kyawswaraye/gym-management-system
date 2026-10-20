"""Product sales, stock movement, and inventory reports."""

from __future__ import annotations

import os
import time
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core import signing
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.reports.inventory_reports import (
    INVENTORY_QUERY_COUNT,
    INVENTORY_SOURCE_STATUS_POLICY,
    PRODUCT_SALES_QUERY_COUNT,
    PRODUCT_SALES_SOURCE_STATUS_POLICY,
    STOCK_MOVEMENT_QUERY_COUNT,
    STOCK_MOVEMENT_SOURCE_STATUS_POLICY,
    inventory_report,
    product_sales_report,
    stock_movement_report,
)
from apps.reports.selectors import period_report
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from tests.security.test_dashboard import FROZEN, _headers, _key, _sell, _staff
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

SALES = "/api/v1/reports/product-sales/"
STOCK = "/api/v1/reports/stock-movements/"
INVENTORY = "/api/v1/reports/inventory/"
RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-04"}
LEAP = {"date_from": "2024-02-28", "date_to": "2024-03-01"}


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _disable(gym: Gym, code: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        updated = GymFeatureEntitlement.objects.filter(gym=gym, feature__code=code).update(
            enabled=False
        )
    assert updated == 1


def _grant_financial(gym: Gym, owner: GymMembership, membership: GymMembership) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        role = Role.objects.create(
            gym=gym,
            key="REPORTS",
            name="Report reader",
            created_by_membership_id=owner.id,
        )
        permission = Permission.objects.get(code="reports.financial")
        RolePermission.objects.create(
            gym=gym,
            role=role,
            permission=permission,
            created_by_membership_id=owner.id,
        )
        GymMembershipRole.objects.create(gym=gym, gym_membership=membership, role=role)


def _report(
    api: APIClient,
    headers: dict[str, str],
    path: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = api.get(path, params or {}, **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    assert set(body) == {"success", "data", "message"}
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _catalog(
    api: APIClient,
    headers: dict[str, str],
    *,
    sku: str,
    price: str,
    cost: str,
    threshold: str,
    name: str,
) -> str:
    category = api.post(
        "/api/v1/product-categories/",
        {"code": sku, "name": name},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("cat"),
        **headers,
    )
    assert category.status_code == 201, category.content
    created = api.post(
        "/api/v1/products/",
        {
            "sku": sku,
            "name": name,
            "category_id": category.json()["data"]["id"],
            "unit": "BTL",
            "sale_price": price,
            "current_unit_cost": cost,
            "default_unit_cost": cost,
            "low_stock_threshold": threshold,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("prd"),
        **headers,
    )
    assert created.status_code == 201, created.content
    return str(created.json()["data"]["id"])


def _patch(
    api: APIClient,
    headers: dict[str, str],
    product_id: str,
    body: dict[str, object],
) -> None:
    response = api.patch(
        f"/api/v1/products/{product_id}/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("patch"),
        **headers,
    )
    assert response.status_code == 200, response.content


def _adjust(
    api: APIClient,
    headers: dict[str, str],
    product_id: str,
    *,
    quantity: str,
    movement_type: str,
    day: str,
) -> None:
    response = api.post(
        "/api/v1/stock-movements/adjust/",
        {
            "product_id": product_id,
            "quantity": quantity,
            "movement_type": movement_type,
            "business_date": day,
            "reason": movement_type,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("adj"),
        **headers,
    )
    assert response.status_code == 201, response.content


def _return_one(
    api: APIClient,
    headers: dict[str, str],
    sale_id: str,
    *,
    quantity: str,
    day: str,
) -> None:
    detail = api.get(f"/api/v1/retail-sales/{sale_id}/", **headers)
    assert detail.status_code == 200, detail.content
    line_id = detail.json()["data"]["lines"][0]["id"]
    returned = api.post(
        f"/api/v1/retail-sales/{sale_id}/return/",
        {
            "reason": "Partial return",
            "business_date": day,
            "lines": [{"line_id": line_id, "quantity": quantity}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("ret"),
        **headers,
    )
    assert returned.status_code == 200, returned.content


def _void(api: APIClient, headers: dict[str, str], sale_id: str, day: str) -> None:
    response = api.post(
        f"/api/v1/retail-sales/{sale_id}/void/",
        {"reason": "Wrong item", "business_date": day},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("void"),
        **headers,
    )
    assert response.status_code == 200, response.content


def _queries(gym: Gym, call: Any) -> tuple[Any, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as captured:
            payload = call(loaded)
    return payload, len(captured.captured_queries)


@override_settings(**jwt_settings())
def test_inventory_reports_reject_closed_callers_and_bound_cursors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("PRDGAT")
    staff = _staff(gym)
    reception = _headers(staff["reception"])
    trainer = _headers(staff["trainer"])
    accountant = _headers(staff["accountant"])
    assert api.get(SALES, **reception).status_code == 403
    assert api.get(SALES, RANGE, **reception).status_code == 403
    assert api.get("/api/v1/reports/daily/?date=2026-10-01", **reception).status_code == 403
    assert api.get(STOCK, LEAP, **reception).status_code == 200
    assert api.get(INVENTORY, **reception).status_code == 200
    assert api.get(STOCK, LEAP, **trainer).status_code == 404
    assert api.get(INVENTORY, **trainer).status_code == 404
    assert api.get(SALES, RANGE, **trainer).status_code == 403
    _grant_financial(gym, owner, staff["trainer"])
    assert api.get(SALES, RANGE, **trainer).status_code == 404
    for path in (SALES, STOCK):
        assert api.get(path, **accountant).status_code == 400
        assert api.get(f"{path}?date_from=2026-10-01", **accountant).status_code == 400
        assert (
            api.get(
                f"{path}?date_from=2026-10-01&date_from=2026-10-02&date_to=2026-10-02",
                **accountant,
            ).status_code
            == 400
        )
        assert api.get(f"{path}?date_from=&date_to=2026-10-01", **accountant).status_code == 400
        assert (
            api.get(f"{path}?date_from=2026-10-02&date_to=2026-10-01", **accountant).status_code
            == 400
        )
        assert (
            api.get(f"{path}?date_from=2024-01-01&date_to=2025-01-01", **accountant).status_code
            == 400
        )
        assert (
            api.get(f"{path}?date_from=1899-12-31&date_to=1899-12-31", **accountant).status_code
            == 400
        )
        assert api.get(f"{path}?{_query(RANGE)}&gym_id={gym.id}", **accountant).status_code == 400
    assert api.get(f"{SALES}?{_query(RANGE)}&limit=", **accountant).status_code == 400
    assert api.get(f"{SALES}?{_query(RANGE)}&limit=0", **accountant).status_code == 400
    assert api.get(f"{SALES}?{_query(RANGE)}&limit=101", **accountant).status_code == 400
    assert api.get(f"{SALES}?{_query(RANGE)}&limit=1&limit=2", **accountant).status_code == 400
    assert api.get(f"{SALES}?{_query(RANGE)}&after=", **accountant).status_code == 400
    assert api.get(f"{STOCK}?{_query(LEAP)}&limit=25", **accountant).status_code == 400
    dated_inventory = f"{INVENTORY}?date_from=2026-10-01&date_to=2026-10-01"
    assert api.get(dated_inventory, **accountant).status_code == 400
    assert api.get(f"{INVENTORY}?limit=25&limit=25", **accountant).status_code == 400

    empty_stock = _report(api, reception, STOCK, LEAP)
    assert empty_stock["source_status_policy"] == STOCK_MOVEMENT_SOURCE_STATUS_POLICY
    assert "currency" not in empty_stock
    assert "meta" not in empty_stock
    assert [row["date"] for row in empty_stock["rows"]] == [
        "2024-02-28",
        "2024-02-29",
        "2024-03-01",
    ]
    assert empty_stock["rows"][1]["net_quantity"] == "0.000"
    empty_inventory = _report(api, reception, INVENTORY)
    assert empty_inventory["source_status_policy"] == INVENTORY_SOURCE_STATUS_POLICY
    assert "currency" not in empty_inventory
    assert "range" not in empty_inventory
    assert empty_inventory["totals"]["product_count"] == 0
    assert empty_inventory["totals"]["total_stock_quantity"] == "0.000"
    assert empty_inventory["meta"] == {"next_after": None, "has_more": False}
    empty_sales = _report(api, owner_headers, SALES, RANGE)
    assert empty_sales["totals"]["product_count"] == 0
    assert empty_sales["totals"]["revenue"] == "0.00"
    assert empty_sales["currency"] == gym.currency

    first = _catalog(
        api, owner_headers, sku="AAA", price="1.00", cost="1.00", threshold="0.000", name="Alpha"
    )
    second = _catalog(
        api, owner_headers, sku="BBB", price="1.00", cost="1.00", threshold="0.000", name="Beta"
    )
    assert first != second
    page = _report(api, owner_headers, SALES, {**RANGE, "limit": "1"})
    assert [row["sku"] for row in page["rows"]] == ["AAA"]
    assert page["meta"]["has_more"] is True
    cursor = page["meta"]["next_after"]
    assert isinstance(cursor, str)
    loaded = signing.loads(cursor, salt="report-product-sales", max_age=24 * 60 * 60)
    assert set(loaded) == {
        "position_sku",
        "id",
        "route",
        "gym_id",
        "actor_membership_id",
        "date_from",
        "date_to",
        "limit",
        "ordering",
    }
    assert loaded["ordering"] == "sku ASC, id ASC"
    assert "example.test" not in str(loaded)
    nxt = _report(api, owner_headers, SALES, {**RANGE, "limit": "1", "after": cursor})
    assert [row["sku"] for row in nxt["rows"]] == ["BBB"]
    assert nxt["meta"]["has_more"] is False
    assert api.get(INVENTORY, {"limit": "1", "after": cursor}, **owner_headers).status_code == 400
    assert (
        api.get(SALES, {**RANGE, "limit": "2", "after": cursor}, **owner_headers).status_code == 400
    )
    changed = {**RANGE, "date_to": "2026-10-05", "limit": "1", "after": cursor}
    assert api.get(SALES, changed, **owner_headers).status_code == 400
    assert api.get(SALES, {**RANGE, "limit": "1", "after": cursor}, **accountant).status_code == 400
    _other_api, _other, _other_owner, other_headers = _gym("PRDOTH")
    replayed = api.get(SALES, {**RANGE, "limit": "1", "after": cursor}, **other_headers)
    assert replayed.status_code == 400
    tampered = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"
    assert (
        api.get(SALES, {**RANGE, "limit": "1", "after": tampered}, **owner_headers).status_code
        == 400
    )
    issued = time.time()
    monkeypatch.setattr("django.core.signing.time.time", lambda: issued + 24 * 60 * 60 + 5)
    expired = api.get(SALES, {**RANGE, "limit": "1", "after": cursor}, **owner_headers)
    assert expired.status_code == 400
    assert b"Invalid cursor." in expired.content

    _disable(gym, "finance")
    assert api.get(STOCK, LEAP, **reception).status_code == 200
    assert api.get(INVENTORY, **reception).status_code == 200
    assert api.get(SALES, RANGE, **owner_headers).status_code == 200
    _disable(gym, "reports")
    assert api.get(STOCK, LEAP, **reception).status_code == 403
    assert api.get(INVENTORY, **reception).status_code == 403
    assert api.get(SALES, RANGE, **owner_headers).status_code == 403


@override_settings(**jwt_settings())
def test_product_sales_reuse_correction_snapshots_and_stay_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("PRDSAL")
    staff = _staff(gym)
    desk = _headers(staff["reception"])
    manager = _headers(staff["manager"])
    protein = _catalog(
        api, owner_headers, sku="PRO", price="10.00", cost="4.00", threshold="1.000", name="Protein"
    )
    shaker = _catalog(
        api, owner_headers, sku="SHA", price="5.00", cost="1.00", threshold="1.000", name="Shaker"
    )
    bar = _catalog(
        api, owner_headers, sku="BAR", price="2.00", cost="1.00", threshold="1.000", name="Bar"
    )
    zero = _catalog(
        api, owner_headers, sku="ZER", price="3.00", cost="1.00", threshold="1.000", name="Zero"
    )
    for product_id in (protein, shaker, bar):
        _adjust(
            api,
            owner_headers,
            product_id,
            quantity="5.000",
            movement_type="OPENING",
            day="2026-09-30",
        )
    kept = _sell(
        api,
        desk,
        manager,
        product_id=protein,
        quantity="2.000",
        day="2026-10-01",
        reference="PRO-KEEP",
        price_body={},
    )
    voided = _sell(
        api,
        desk,
        manager,
        product_id=shaker,
        quantity="1.000",
        day="2026-10-01",
        reference="SHA-VOID",
        price_body={},
    )
    _sell(
        api,
        desk,
        manager,
        product_id=bar,
        quantity="1.000",
        day="2026-10-01",
        reference="BAR-KEEP",
        price_body={},
    )
    _return_one(api, owner_headers, kept, quantity="1.000", day="2026-10-02")
    _void(api, owner_headers, voided, "2026-10-04")
    detail = api.get(f"/api/v1/retail-sales/{kept}/", **owner_headers)
    assert detail.json()["data"]["lines"][0]["cost_of_goods_sold"] == "8.00"
    _patch(api, owner_headers, protein, {"current_unit_cost": "99.00"})
    _patch(api, owner_headers, bar, {"is_active": False})
    changed = api.get(f"/api/v1/products/{protein}/", **owner_headers)
    assert changed.json()["data"]["current_unit_cost"] == "99.00"

    other_api, other, _other_owner, other_headers = _gym("SALOTH")
    other_staff = _staff(other)
    other_product = _catalog(
        other_api,
        other_headers,
        sku="OTH",
        price="50.00",
        cost="10.00",
        threshold="0.000",
        name="Other",
    )
    _adjust(
        other_api,
        other_headers,
        other_product,
        quantity="5.000",
        movement_type="OPENING",
        day="2026-09-30",
    )
    _sell(
        other_api,
        _headers(other_staff["reception"]),
        _headers(other_staff["manager"]),
        product_id=other_product,
        quantity="1.000",
        day="2026-10-01",
        reference="OTH-SALE",
        price_body={},
    )

    _disable(gym, "finance")
    report = _report(api, owner_headers, SALES, RANGE)
    assert report["source_status_policy"] == PRODUCT_SALES_SOURCE_STATUS_POLICY
    assert report["currency"] == gym.currency
    assert report["range"] == RANGE
    assert "2026-10-19" in report["generated_at"]
    assert report["totals"] == {
        "quantity_sold": "2.000",
        "revenue": "12.00",
        "cogs": "5.00",
        "gross_profit": "7.00",
        "product_count": 4,
        "active_product_count": 3,
    }
    ordered = _pages(api, owner_headers, SALES, RANGE)
    assert [row["sku"] for row in ordered] == ["BAR", "PRO", "SHA", "ZER"]
    rows = {row["sku"]: row for row in ordered}
    assert rows["PRO"]["is_active"] is True
    assert rows["PRO"]["quantity_sold"] == "1.000"
    assert rows["PRO"]["revenue"] == "10.00"
    assert rows["PRO"]["cogs"] == "4.00"
    assert rows["PRO"]["gross_profit"] == "6.00"
    assert rows["SHA"]["quantity_sold"] == "0.000"
    assert rows["SHA"]["revenue"] == "0.00"
    assert rows["SHA"]["cogs"] == "0.00"
    assert rows["SHA"]["gross_profit"] == "0.00"
    assert rows["BAR"]["is_active"] is False
    assert rows["BAR"]["quantity_sold"] == "1.000"
    assert rows["BAR"]["revenue"] == "2.00"
    assert rows["BAR"]["cogs"] == "1.00"
    assert rows["BAR"]["gross_profit"] == "1.00"
    assert rows["ZER"]["product_id"] == zero
    assert rows["ZER"]["quantity_sold"] == "0.000"
    assert rows["ZER"]["revenue"] == "0.00"
    assert "99.00" not in str(report)
    assert owner.user.email not in str(report)
    summed = {
        "quantity_sold": Decimal("0.000"),
        "revenue": Decimal("0.00"),
        "cogs": Decimal("0.00"),
        "gross_profit": Decimal("0.00"),
    }
    for row in rows.values():
        summed["quantity_sold"] += Decimal(row["quantity_sold"])
        summed["revenue"] += Decimal(row["revenue"])
        summed["cogs"] += Decimal(row["cogs"])
        summed["gross_profit"] += Decimal(row["gross_profit"])
    assert summed["quantity_sold"] == Decimal(report["totals"]["quantity_sold"])
    assert summed["revenue"] == Decimal(report["totals"]["revenue"])
    assert summed["cogs"] == Decimal(report["totals"]["cogs"])
    assert summed["gross_profit"] == Decimal(report["totals"]["gross_profit"])

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        period = period_report(
            gym=loaded,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 4),
            generated_at=FROZEN,
        )
    assert period["totals"]["product_quantity"] == report["totals"]["quantity_sold"]
    assert period["totals"]["product_revenue"] == report["totals"]["revenue"]
    assert period["totals"]["product_cogs"] == report["totals"]["cogs"]
    assert period["totals"]["product_gross_profit"] == report["totals"]["gross_profit"]

    _payload, narrow = _queries(
        gym,
        lambda loaded_gym: product_sales_report(
            gym=loaded_gym,
            actor_membership_id=owner.id,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 4),
            limit=1,
            after="",
            generated_at=FROZEN,
        ),
    )
    _wide, wide = _queries(
        gym,
        lambda loaded_gym: product_sales_report(
            gym=loaded_gym,
            actor_membership_id=owner.id,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 4),
            limit=100,
            after="",
            generated_at=FROZEN,
        ),
    )
    _day, one_day = _queries(
        gym,
        lambda loaded_gym: product_sales_report(
            gym=loaded_gym,
            actor_membership_id=owner.id,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 1),
            limit=25,
            after="",
            generated_at=FROZEN,
        ),
    )
    _year, full_year = _queries(
        gym,
        lambda loaded_gym: product_sales_report(
            gym=loaded_gym,
            actor_membership_id=owner.id,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            limit=25,
            after="",
            generated_at=FROZEN,
        ),
    )
    assert narrow == wide == one_day == full_year == PRODUCT_SALES_QUERY_COUNT
    assert _day["totals"]["revenue"] == "27.00"
    assert _day["totals"]["product_count"] == 4
    assert _year["totals"]["revenue"] == "0.00"
    assert _year["totals"]["product_count"] == 4


@override_settings(**jwt_settings())
def test_stock_movements_keep_signed_quantities_and_one_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, _owner, owner_headers = _gym("STKMOV")
    staff = _staff(gym)
    product = _catalog(
        api, owner_headers, sku="STK", price="10.00", cost="4.00", threshold="0.000", name="Stock"
    )
    _adjust(
        api, owner_headers, product, quantity="10.000", movement_type="OPENING", day="2024-02-28"
    )
    _adjust(
        api, owner_headers, product, quantity="2.500", movement_type="PURCHASE", day="2024-03-01"
    )
    sale = _sell(
        api,
        _headers(staff["reception"]),
        _headers(staff["manager"]),
        product_id=product,
        quantity="1.000",
        day="2024-03-01",
        reference="STK-SALE",
        price_body={},
    )
    _adjust(
        api, owner_headers, product, quantity="-0.100", movement_type="DAMAGE", day="2024-03-01"
    )
    _adjust(api, owner_headers, product, quantity="-0.050", movement_type="LOSS", day="2024-03-01")
    _adjust(
        api, owner_headers, product, quantity="0.200", movement_type="ADJUSTMENT", day="2024-03-01"
    )
    _adjust(
        api, owner_headers, product, quantity="-0.025", movement_type="CORRECTION", day="2024-03-01"
    )
    _return_one(api, owner_headers, sale, quantity="0.250", day="2024-03-01")
    _adjust(
        api, owner_headers, product, quantity="-0.001", movement_type="DAMAGE", day="2024-03-02"
    )

    other_api, _other, _other_owner, other_headers = _gym("STKOTH")
    other_product = _catalog(
        other_api,
        other_headers,
        sku="OTH",
        price="1.00",
        cost="1.00",
        threshold="0.000",
        name="Other",
    )
    _adjust(
        other_api,
        other_headers,
        other_product,
        quantity="50.000",
        movement_type="OPENING",
        day="2024-02-28",
    )

    _disable(gym, "finance")
    report = _report(api, _headers(staff["reception"]), STOCK, LEAP)
    assert report["source_status_policy"] == STOCK_MOVEMENT_SOURCE_STATUS_POLICY
    assert "currency" not in report
    assert [row["date"] for row in report["rows"]] == ["2024-02-28", "2024-02-29", "2024-03-01"]
    opening, leap, moved = report["rows"]
    assert opening["opening_quantity"] == "10.000"
    assert opening["net_quantity"] == "10.000"
    assert leap["opening_quantity"] == "0.000"
    assert leap["net_quantity"] == "0.000"
    assert moved == {
        "date": "2024-03-01",
        "opening_quantity": "0.000",
        "purchase_quantity": "2.500",
        "sale_quantity": "-1.000",
        "sale_return_quantity": "0.250",
        "damage_quantity": "-0.100",
        "loss_quantity": "-0.050",
        "adjustment_quantity": "0.200",
        "correction_quantity": "-0.025",
        "net_quantity": "1.775",
    }
    assert report["totals"]["opening_quantity"] == "10.000"
    assert report["totals"]["purchase_quantity"] == "2.500"
    assert report["totals"]["sale_quantity"] == "-1.000"
    assert report["totals"]["sale_return_quantity"] == "0.250"
    assert report["totals"]["damage_quantity"] == "-0.100"
    assert report["totals"]["loss_quantity"] == "-0.050"
    assert report["totals"]["adjustment_quantity"] == "0.200"
    assert report["totals"]["correction_quantity"] == "-0.025"
    assert report["totals"]["net_quantity"] == "11.775"
    assert _headers(staff["trainer"])
    assert api.get(STOCK, LEAP, **_headers(staff["trainer"])).status_code == 404

    _one, one_day = _queries(
        gym,
        lambda loaded: stock_movement_report(
            gym=loaded,
            date_from=date(2024, 3, 1),
            date_to=date(2024, 3, 1),
            generated_at=FROZEN,
        ),
    )
    year, full_year = _queries(
        gym,
        lambda loaded: stock_movement_report(
            gym=loaded,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            generated_at=FROZEN,
        ),
    )
    assert one_day == full_year == STOCK_MOVEMENT_QUERY_COUNT
    assert len(year["rows"]) == 366
    february = next(row for row in year["rows"] if row["date"] == date(2024, 2, 28))
    assert february["opening_quantity"] == "10.000"


@override_settings(**jwt_settings())
def test_inventory_snapshot_pages_current_stock(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("INVSNP")
    staff = _staff(gym)
    alpha = _catalog(
        api, owner_headers, sku="AAA", price="1.00", cost="1.00", threshold="2.000", name="Alpha"
    )
    beta = _catalog(
        api, owner_headers, sku="BBB", price="1.00", cost="1.00", threshold="1.000", name="Beta"
    )
    gamma = _catalog(
        api, owner_headers, sku="CCC", price="1.00", cost="1.00", threshold="1.000", name="Gamma"
    )
    _adjust(api, owner_headers, alpha, quantity="5.000", movement_type="OPENING", day="2026-10-01")
    _adjust(api, owner_headers, gamma, quantity="1.000", movement_type="OPENING", day="2026-10-01")
    _patch(api, owner_headers, gamma, {"is_active": False})
    other_api, _other, _other_owner, other_headers = _gym("INVOTH")
    other_product = _catalog(
        other_api,
        other_headers,
        sku="OTH",
        price="1.00",
        cost="1.00",
        threshold="0.000",
        name="Other",
    )
    _adjust(
        other_api,
        other_headers,
        other_product,
        quantity="100.000",
        movement_type="OPENING",
        day="2026-10-01",
    )

    _disable(gym, "finance")
    report = _report(api, _headers(staff["reception"]), INVENTORY)
    assert report["source_status_policy"] == INVENTORY_SOURCE_STATUS_POLICY
    assert "currency" not in report
    assert "range" not in report
    assert report["totals"] == {
        "product_count": 3,
        "active_product_count": 2,
        "low_stock_product_count": 2,
        "total_stock_quantity": "6.000",
    }
    rows = {row["sku"]: row for row in _pages(api, owner_headers, INVENTORY, {})}
    assert list(rows) == ["AAA", "BBB", "CCC"]
    assert rows["AAA"]["stock_quantity"] == "5.000"
    assert rows["AAA"]["is_low_stock"] is False
    assert rows["AAA"]["category_code"] == "AAA"
    assert rows["BBB"]["product_id"] == beta
    assert rows["BBB"]["stock_quantity"] == "0.000"
    assert rows["BBB"]["low_stock_threshold"] == "1.000"
    assert rows["BBB"]["is_low_stock"] is True
    assert rows["CCC"]["is_active"] is False
    assert rows["CCC"]["stock_quantity"] == "1.000"
    assert rows["CCC"]["is_low_stock"] is True
    assert owner.user.email not in str(report)

    _small, small = _queries(
        gym,
        lambda loaded: inventory_report(
            gym=loaded,
            actor_membership_id=owner.id,
            limit=1,
            after="",
            generated_at=FROZEN,
        ),
    )
    _large, large = _queries(
        gym,
        lambda loaded: inventory_report(
            gym=loaded,
            actor_membership_id=owner.id,
            limit=100,
            after="",
            generated_at=FROZEN,
        ),
    )
    assert small == large == INVENTORY_QUERY_COUNT
    assert _large["totals"]["total_stock_quantity"] == "6.000"
    assert len(_small["rows"]) == 1


def _pages(
    api: APIClient,
    headers: dict[str, str],
    path: str,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = ""
    seen: set[str] = set()
    for _page in range(6):
        query = {**params, "limit": "1"}
        if after:
            query["after"] = after
        report = _report(api, headers, path, query)
        assert len(report["rows"]) <= 1
        for row in report["rows"]:
            sku = str(row["sku"])
            assert sku not in seen
            seen.add(sku)
            rows.append(row)
        if report["meta"]["has_more"] is False:
            return rows
        cursor = report["meta"]["next_after"]
        assert isinstance(cursor, str) and cursor
        assert cursor not in seen
        after = cursor
    raise AssertionError("paging did not finish")


def _query(params: dict[str, str]) -> str:
    return "&".join(f"{key}={value}" for key, value in params.items())
