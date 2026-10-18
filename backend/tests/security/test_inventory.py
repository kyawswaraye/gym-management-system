"""PostgreSQL inventory isolation, ledger, and command tests."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.inventory import services as inventory_services
from apps.inventory.models import Product, StockMovement
from apps.inventory.selectors import page_low_stock, page_products
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    RolePermission,
)
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code,
        defaults={"name": code, "description": code},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _gym(code: str) -> tuple[APIClient, Gym, GymMembership, dict[str, str]]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(
        client,
        token,
        code=code,
        owner_email=f"{code.lower()}-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user", "gym").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    return _client(), gym, owner, auth_headers(staff_token(owner))


def _category_body(code: str = "DRINKS", name: str = "Drinks") -> dict[str, object]:
    return {"code": code, "name": name}


def _product_body(category_id: str, sku: str = "WATER-1") -> dict[str, object]:
    return {
        "sku": sku,
        "barcode": "5901234" if sku == "WATER-1" else None,
        "name": "Spring Water",
        "category_id": category_id,
        "unit": "BTL",
        "sale_price": "25.00",
        "current_unit_cost": "10.00",
        "default_unit_cost": "9.00",
        "low_stock_threshold": "2.000",
    }


def _post_category(
    api: APIClient,
    headers: dict[str, str],
    body: dict[str, object] | None = None,
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        "/api/v1/product-categories/",
        body or _category_body(),
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"inv-cat-{uuid4().hex}",
        **headers,
    )


def _post_product(
    api: APIClient,
    headers: dict[str, str],
    category_id: str,
    *,
    sku: str = "WATER-1",
    key: str | None = None,
    extra: dict[str, object] | None = None,
) -> Any:
    body = _product_body(category_id, sku)
    if extra:
        body.update(extra)
    return api.post(
        "/api/v1/products/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"inv-prod-{uuid4().hex}",
        **headers,
    )


def _adjust(
    api: APIClient,
    headers: dict[str, str],
    product_id: str,
    *,
    quantity: str,
    movement_type: str = "ADJUSTMENT",
    key: str | None = None,
    gym: Gym | None = None,
    extra: dict[str, object] | None = None,
) -> Any:
    body: dict[str, object] = {
        "product_id": product_id,
        "quantity": quantity,
        "movement_type": movement_type,
        "business_date": gym_today(gym).isoformat() if gym is not None else "2026-10-18",
        "reason": "Counted on the shelf",
    }
    if extra:
        body.update(extra)
    return api.post(
        "/api/v1/stock-movements/adjust/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"inv-adj-{uuid4().hex}",
        **headers,
    )


@override_settings(**jwt_settings())
def test_catalog_is_tenant_scoped_and_permissioned() -> None:
    api, gym, _owner, headers = _gym("INVPERM")
    _api_b, gym_b, _owner_b, headers_b = _gym("INVPERB")
    receptionist = add_staff(gym, email="invperm-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="invperm-accountant@example.test", role_key="ACCOUNTANT")
    trainer = add_staff(gym, email="invperm-trainer@example.test", role_key="TRAINER")
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    platform, platform_access = platform_token("invperm-platform-read@example.test")

    denied_platform = platform.get(
        "/api/v1/products/",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
    )
    assert denied_platform.status_code in {401, 403}
    assert trainer_headers and api.get("/api/v1/products/", **trainer_headers).status_code == 403

    created = _post_category(api, headers)
    assert created.status_code == 201, created.content
    category_id = created.json()["data"]["id"]
    assert created.json()["data"]["code"] == "DRINKS"
    duplicate_name = _post_category(api, headers, {"code": "WATER", "name": " drinks "})
    assert duplicate_name.status_code == 400
    duplicate_code = _post_category(api, headers, _category_body())
    assert duplicate_code.status_code == 400

    product = _post_product(api, headers, category_id)
    assert product.status_code == 201, product.content
    body = product.json()["data"]
    product_id = body["id"]
    assert body["quantity_on_hand"] == "0.000"
    assert body["sold_quantity"] == "0.000"
    assert body["revenue"] == "0.00"
    assert body["cost_of_goods_sold"] == "0.00"
    assert body["gross_profit"] == "0.00"
    assert body["current_unit_cost"] == "10.00"

    same_sku = _post_product(api, headers, category_id, sku="WATER-1")
    assert same_sku.status_code == 400
    other_barcode = _post_product(
        api,
        headers,
        category_id,
        sku="SODA-1",
        extra={"barcode": "5901234"},
    )
    assert other_barcode.status_code == 400
    mass = _post_product(
        api,
        headers,
        category_id,
        sku="JUICE-1",
        extra={"gym_id": str(gym.id), "quantity_on_hand": "4.000"},
    )
    assert mass.status_code == 400

    foreign_category = _post_product(api, headers_b, category_id, sku="OTHER-1")
    assert foreign_category.status_code == 404
    assert api.get(f"/api/v1/products/{product_id}/", **headers_b).status_code == 404
    assert api.get(f"/api/v1/products/?category_id={category_id}", **headers_b).status_code == 404

    reception_product = _post_product(api, reception_headers, category_id, sku="TEA-1")
    assert reception_product.status_code == 201, reception_product.content
    accountant_category = _post_category(
        api, accountant_headers, {"code": "SNACKS", "name": "Snacks"}
    )
    assert accountant_category.status_code == 403
    assert (
        api.patch(
            f"/api/v1/products/{product_id}/",
            {"name": "Still Water"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"inv-acct-patch-{uuid4().hex}",
            **accountant_headers,
        ).status_code
        == 403
    )
    opened = _adjust(api, headers, product_id, quantity="5.000", movement_type="OPENING", gym=gym)
    assert opened.status_code == 201, opened.content
    assert opened.json()["data"]["product"]["quantity_on_hand"] == "5.000"
    assert opened.json()["data"]["product"]["current_unit_cost"] == "10.00"
    assert opened.json()["data"]["movement"]["unit_cost_snapshot"] == "10.00"
    reception_adjust = _adjust(api, reception_headers, product_id, quantity="-1.000", gym=gym)
    assert reception_adjust.status_code == 403
    accountant_adjust = _adjust(api, accountant_headers, product_id, quantity="-1.000", gym=gym)
    assert accountant_adjust.status_code == 201, accountant_adjust.content

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT count(*) FROM inventory_product WHERE id = %s", [product_id])
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT count(*) FROM inventory_product_category WHERE id = %s",
                [category_id],
            )
            assert cursor.fetchone()[0] == 0


@override_settings(**jwt_settings())
def test_adjustment_contract_audit_idempotency_and_low_stock() -> None:
    api, gym, owner, headers = _gym("INVADJ")
    _other_api, _other_gym, _other_owner, other_headers = _gym("INVADJB")
    category_id = _post_category(api, headers).json()["data"]["id"]
    created = _post_product(api, headers, category_id)
    product_id = created.json()["data"]["id"]
    inactive = _post_product(
        api,
        headers,
        category_id,
        sku="OLD-1",
        extra={"is_active": False, "low_stock_threshold": "5.000"},
    )
    assert inactive.status_code == 201, inactive.content
    above = _post_product(
        api,
        headers,
        category_id,
        sku="CASE-1",
        extra={"low_stock_threshold": "1.000"},
    )
    assert above.status_code == 201, above.content

    forged = _adjust(
        api,
        headers,
        product_id,
        quantity="-1.000",
        movement_type="SALE",
        gym=gym,
        extra={"reverses_movement_id": str(uuid4())},
    )
    assert forged.status_code == 400
    inexact = _adjust(
        api, headers, product_id, quantity="1.2345", movement_type="PURCHASE", gym=gym
    )
    assert inexact.status_code == 400
    negative_open = _adjust(
        api, headers, product_id, quantity="-1.000", movement_type="OPENING", gym=gym
    )
    assert negative_open.status_code == 400
    purchased = _adjust(
        api, headers, product_id, quantity="2.000", movement_type="PURCHASE", gym=gym
    )
    assert purchased.status_code == 201, purchased.content
    assert purchased.json()["data"]["product"]["current_unit_cost"] == "10.00"
    key = "inventory-adjust-replay01"
    first = _adjust(api, headers, product_id, quantity="-1.000", gym=gym, key=key)
    assert first.status_code == 201, first.content
    replay = _adjust(api, headers, product_id, quantity="-1.000", gym=gym, key=key)
    assert replay.status_code == 201
    assert replay.json()["data"]["movement"]["id"] == first.json()["data"]["movement"]["id"]
    mismatch = _adjust(api, headers, product_id, quantity="-2.000", gym=gym, key=key)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    empty = _adjust(api, headers, product_id, quantity="-1.000", gym=gym, extra={"reason": "   "})
    assert empty.status_code == 400
    overdrawn = _adjust(api, headers, product_id, quantity="-20.000", gym=gym)
    assert overdrawn.status_code == 400
    _adjust(
        api,
        headers,
        above.json()["data"]["id"],
        quantity="3.000",
        movement_type="OPENING",
        gym=gym,
    )

    low = api.get("/api/v1/products/low-stock/", **headers)
    assert low.status_code == 200, low.content
    low_skus = {row["sku"] for row in low.json()["data"]}
    assert low_skus == {"WATER-1"}
    assert "OLD-1" not in low_skus
    assert "CASE-1" not in low_skus
    foreign_low = api.get("/api/v1/products/low-stock/", **other_headers)
    assert foreign_low.status_code == 200
    assert foreign_low.json()["data"] == []

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = Product.objects.get(id=product_id)
        assert stored.current_unit_cost == Decimal("10.00")
        movements = list(StockMovement.objects.filter(product_id=product_id))
        assert len(movements) == 2
        assert sum((row.quantity for row in movements), Decimal("0")) == Decimal("1.000")
        audit = AuditLog.objects.get(
            gym=gym,
            action="STOCK_ADJUSTED",
            target_id=first.json()["data"]["movement"]["id"],
        )
        assert audit.actor_membership_id == owner.id
        assert audit.module == "inventory"
    listed = api.get(f"/api/v1/stock-movements/?product_id={product_id}", **headers)
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 2
    foreign_movements = api.get(
        f"/api/v1/stock-movements/?product_id={product_id}",
        **other_headers,
    )
    assert foreign_movements.status_code == 404


@override_settings(**jwt_settings())
def test_runtime_cannot_forge_sales_or_drive_stock_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, gym, owner, headers = _gym("INVDB")
    category_id = _post_category(api, headers).json()["data"]["id"]
    product_id = _post_product(api, headers, category_id).json()["data"]["id"]
    _adjust(api, headers, product_id, quantity="2.000", movement_type="OPENING", gym=gym)

    def _as_runtime(cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('gym_runtime', 'inventory_product_category', 'SELECT'),
                   has_table_privilege('gym_runtime', 'inventory_product_category', 'INSERT'),
                   has_table_privilege('gym_runtime', 'inventory_product_category', 'UPDATE'),
                   has_table_privilege('gym_runtime', 'inventory_product_category', 'DELETE'),
                   has_table_privilege('gym_runtime', 'inventory_product', 'UPDATE'),
                   has_table_privilege('gym_runtime', 'inventory_product', 'DELETE'),
                   has_table_privilege('gym_runtime', 'inventory_stock_movement', 'INSERT'),
                   has_table_privilege('gym_runtime', 'inventory_stock_movement', 'UPDATE'),
                   has_table_privilege('gym_runtime', 'inventory_stock_movement', 'DELETE')
            """
        )
        privileges = cursor.fetchone()
    assert privileges == (True, True, False, False, True, False, True, False, False)

    with pytest.raises(DatabaseError, match="permission denied"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    "DELETE FROM inventory_stock_movement WHERE product_id = %s",
                    [product_id],
                )
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute(
                    "UPDATE inventory_stock_movement SET quantity = 1 WHERE product_id = %s",
                    [product_id],
                )
    with pytest.raises(DatabaseError, match="sale and reversal evidence"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    INSERT INTO inventory_stock_movement (
                      id, created_at, updated_at, quantity, movement_type, unit_cost_snapshot,
                      reason, business_date, product_id, created_by_membership_id, gym_id
                    ) VALUES (
                      %s, NOW(), NOW(), -1, 'SALE', 10, '', %s, %s, %s, %s
                    )
                    """,
                    [uuid4(), gym_today(gym), product_id, owner.id, gym.id],
                )
    with pytest.raises(DatabaseError, match="quantity on hand cannot be negative"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    INSERT INTO inventory_stock_movement (
                      id, created_at, updated_at, quantity, movement_type, unit_cost_snapshot,
                      reason, business_date, product_id, created_by_membership_id, gym_id
                    ) VALUES (
                      %s, NOW(), NOW(), -5, 'ADJUSTMENT', 10, 'too many', %s, %s, %s, %s
                    )
                    """,
                    [uuid4(), gym_today(gym), product_id, owner.id, gym.id],
                )
    with pytest.raises(DatabaseError, match="identity columns are immutable"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute(
                    "UPDATE inventory_product SET created_by_membership_id = %s WHERE id = %s",
                    [uuid4(), product_id],
                )

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("forced rollback")

    monkeypatch.setattr(inventory_services, "write_tenant_audit", _boom)
    rolled = _adjust(api, headers, product_id, quantity="-1.000", gym=gym)
    assert rolled.status_code == 500
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert StockMovement.objects.filter(product_id=product_id).count() == 1


@override_settings(**jwt_settings())
def test_live_revocation_and_concurrent_adjustments_keep_one_nonnegative_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, gym, _owner, headers = _gym("INVLIVE")
    accountant = add_staff(gym, email="invlive-accountant@example.test", role_key="ACCOUNTANT")
    accountant_headers = auth_headers(staff_token(accountant))
    category_id = _post_category(api, headers).json()["data"]["id"]
    product_id = _post_product(api, headers, category_id).json()["data"]["id"]
    _adjust(api, headers, product_id, quantity="10.000", movement_type="OPENING", gym=gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        role_id = GymMembershipRole.objects.get(gym_membership=accountant).role_id
    original = inventory_services.revalidate_cash_actor

    def revoke_then_revalidate(**kwargs: object) -> tuple[Gym, GymMembership]:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            RolePermission.objects.filter(
                gym_id=gym.id,
                role_id=role_id,
                permission__code="inventory.adjust",
            ).delete()
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(inventory_services, "revalidate_cash_actor", revoke_then_revalidate)
    key = "inventory-live-revoke01"
    denied = _adjust(
        api,
        accountant_headers,
        product_id,
        quantity="-1.000",
        gym=gym,
        key=key,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    monkeypatch.undo()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        adjustments = StockMovement.objects.filter(
            product_id=product_id, movement_type="ADJUSTMENT"
        )
        assert adjustments.count() == 0
        claim = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
        assert claim.state == IdempotencyRecord.State.IN_PROGRESS
        assert claim.response_status is None
    retried = _adjust(api, accountant_headers, product_id, quantity="-1.000", gym=gym, key=key)
    assert retried.status_code == 201, retried.content

    barrier = Barrier(2, timeout=20)

    def deduct() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                inventory_services.adjust_stock(
                    gym=gym,
                    actor=accountant,
                    fields={
                        "product_id": product_id,
                        "quantity": Decimal("-6.000"),
                        "movement_type": "ADJUSTMENT",
                        "business_date": gym_today(gym),
                        "reason": "Race",
                    },
                    request_id=f"req_inv_race_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "ok"
        except (ValidationError, DatabaseError):
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(future.result() for future in (pool.submit(deduct), pool.submit(deduct)))
    assert outcomes == ["ok", "rejected"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        quantities = list(
            StockMovement.objects.filter(product_id=product_id).values_list("quantity", flat=True)
        )
        assert sum(quantities, Decimal("0")) == Decimal("3.000")
        assert all(total >= 0 for total in [sum(quantities, Decimal("0"))])

    same_key = Barrier(2, timeout=20)

    def adjust_same() -> str:
        connections.close_all()
        client = _client()
        try:
            same_key.wait()
            response = _adjust(
                client,
                headers,
                product_id,
                quantity="-1.000",
                gym=gym,
                key="inventory-same-key-0001",
            )
            for _attempt in range(20):
                if response.status_code != 409:
                    break
                if response.json()["error"]["code"] != "IDEMPOTENCY_IN_PROGRESS":
                    break
                response = _adjust(
                    client,
                    headers,
                    product_id,
                    quantity="-1.000",
                    gym=gym,
                    key="inventory-same-key-0001",
                )
            assert response.status_code == 201, response.content
            return str(response.json()["data"]["movement"]["id"])
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        movement_ids = {
            future.result() for future in (pool.submit(adjust_same), pool.submit(adjust_same))
        }
    assert len(movement_ids) == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert StockMovement.objects.filter(id=movement_ids.pop()).count() == 1


@override_settings(**jwt_settings())
def test_lists_are_cursor_bound_and_do_not_scan_per_product() -> None:
    api, gym, owner, headers = _gym("INVLIST")
    _api_b, _gym_b, _owner_b, headers_b = _gym("INVLSTB")
    category_id = _post_category(api, headers).json()["data"]["id"]
    for index, sku in enumerate(("AAA-1", "BBB-1", "CCC-1")):
        created = _post_product(
            api,
            headers,
            category_id,
            sku=sku,
            extra={"low_stock_threshold": "0.000"},
        )
        assert created.status_code == 201, created.content
        if index == 0:
            _adjust(
                api,
                headers,
                created.json()["data"]["id"],
                quantity="1.000",
                movement_type="OPENING",
                gym=gym,
            )
    first = api.get("/api/v1/products/?limit=1", **headers)
    assert first.status_code == 200, first.content
    assert [row["sku"] for row in first.json()["data"]] == ["AAA-1"]
    cursor = first.json()["meta"]["next_after"]
    second = api.get(f"/api/v1/products/?limit=1&after={cursor}", **headers)
    assert second.status_code == 200
    assert [row["sku"] for row in second.json()["data"]] == ["BBB-1"]
    stolen = api.get(f"/api/v1/products/?limit=1&after={cursor}", **headers_b)
    assert stolen.status_code == 400
    filtered = api.get(f"/api/v1/products/?limit=1&sku=CCC-1&after={cursor}", **headers)
    assert filtered.status_code == 400

    def _count(limit_rows: int) -> int:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with CaptureQueriesContext(connection) as captured:
                page_products(
                    gym_id=gym.id,
                    actor_membership_id=owner.id,
                    category_id=None,
                    is_active=None,
                    sku="",
                    barcode="",
                    limit=limit_rows,
                    after="",
                )
        return len(captured.captured_queries)

    assert _count(1) == _count(25) <= 2
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with CaptureQueriesContext(connection) as low_captured:
            page_low_stock(
                gym_id=gym.id,
                actor_membership_id=owner.id,
                category_id=None,
                limit=25,
                after="",
            )
        low_sql = low_captured.captured_queries[-1]["sql"]
        with CaptureQueriesContext(connection) as list_captured:
            listed, _meta = page_products(
                gym_id=gym.id,
                actor_membership_id=owner.id,
                category_id=None,
                is_active=None,
                sku="",
                barcode="",
                limit=25,
                after="",
            )
        list_sql = list_captured.captured_queries[-1]["sql"]
    assert len(listed) == 3
    assert "%s" not in list_sql
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("EXPLAIN (FORMAT JSON) " + list_sql)
        plan = cursor.fetchone()
    rendered = json.dumps(plan[0] if plan is not None else {})
    assert "Index" in rendered
    assert "Seq Scan on inventory_product" not in rendered
    assert "gym_id" in low_sql
