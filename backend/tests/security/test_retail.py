"""PostgreSQL retail sale posting, isolation, and concurrency tests."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.inventory.models import StockMovement
from apps.members.models import Payment, Receipt
from apps.members.payment_services import _issue_receipt
from apps.members.services import gym_today
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailSale, RetailSaleLine
from apps.sales.services import approve_sale, reject_sale
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
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
    owner.user.first_name = "Olivia"
    owner.user.last_name = "Owner"
    owner.user.save(update_fields=["first_name", "last_name"])
    _enable(gym, "finance")
    _enable(gym, "reports")
    return _client(), gym, owner, auth_headers(staff_token(owner))


def _staff(gym: Gym, role: str, email: str, first: str) -> tuple[GymMembership, dict[str, str]]:
    membership = add_staff(gym, email=email, role_key=role)
    membership.user.first_name = first
    membership.user.last_name = "Staff"
    membership.user.save(update_fields=["first_name", "last_name"])
    return membership, auth_headers(staff_token(membership))


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:20]}"


def _product(
    api: APIClient,
    headers: dict[str, str],
    *,
    sku: str = "WATER-1",
    price: str = "10.00",
    cost: str = "4.00",
) -> str:
    category = api.post(
        "/api/v1/product-categories/",
        {"code": sku.replace("-", "")[:12], "name": sku},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("cat"),
        **headers,
    )
    assert category.status_code == 201, category.content
    created = api.post(
        "/api/v1/products/",
        {
            "sku": sku,
            "name": sku.replace("-", " "),
            "category_id": category.json()["data"]["id"],
            "unit": "BTL",
            "sale_price": price,
            "current_unit_cost": cost,
            "default_unit_cost": cost,
            "low_stock_threshold": "0.000",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("prd"),
        **headers,
    )
    assert created.status_code == 201, created.content
    return str(created.json()["data"]["id"])


def _open_stock(
    api: APIClient, headers: dict[str, str], gym: Gym, product_id: str, qty: str
) -> None:
    response = api.post(
        "/api/v1/stock-movements/adjust/",
        {
            "product_id": product_id,
            "quantity": qty,
            "movement_type": "OPENING",
            "business_date": gym_today(gym).isoformat(),
            "reason": "Opening count",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("open"),
        **headers,
    )
    assert response.status_code == 201, response.content


def _sale_body(product_id: str, gym: Gym, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "business_date": gym_today(gym).isoformat(),
        "payment_method": "CARD",
        "reference_number": "POS-1",
        "lines": [{"product_id": product_id, "quantity": "1.250", "discount": "0.50"}],
    }
    body.update(overrides)
    return body


def _create(
    api: APIClient,
    headers: dict[str, str],
    body: dict[str, object],
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        "/api/v1/retail-sales/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or _key("sale"),
        **headers,
    )


def _command(
    api: APIClient,
    headers: dict[str, str],
    sale_id: str,
    action: str,
    body: dict[str, object] | None = None,
) -> Any:
    return api.post(
        f"/api/v1/retail-sales/{sale_id}/{action}/",
        {} if body is None else body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key(action),
        **headers,
    )


def _member(api: APIClient, headers: dict[str, str], gym: Gym) -> str:
    response = api.post(
        "/api/v1/members/",
        {"first_name": "Ada", "last_name": "Member", "join_date": gym_today(gym).isoformat()},
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.content
    return str(response.json()["data"]["id"])


def _open_till(api: APIClient, headers: dict[str, str], gym: Gym, register: str = "FRONT") -> str:
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": register,
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "20.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("till"),
        **headers,
    )
    assert opened.status_code == 201, opened.content
    return str(opened.json()["data"]["session"]["id"])


@override_settings(**jwt_settings())
def test_draft_totals_are_exact_and_clients_cannot_write_snapshots() -> None:
    api, gym, _owner, headers = _gym("RTLTOT")
    product_id = _product(api, headers, price="0.10", cost="0.01")
    forged = _create(
        api,
        headers,
        {
            **_sale_body(product_id, gym, payment_method="CARD"),
            "lines": [{"product_id": product_id, "quantity": "1.250", "discount": "0.00"}],
            "total_amount": "1.00",
            "gross_profit": "9.00",
            "status": "POSTED",
        },
    )
    assert forged.status_code == 400
    created = _create(
        api,
        headers,
        {
            "business_date": gym_today(gym).isoformat(),
            "payment_method": "CARD",
            "lines": [{"product_id": product_id, "quantity": "1.250", "discount": "0.00"}],
        },
    )
    assert created.status_code == 201, created.content
    data = created.json()["data"]
    assert data["currency"] == gym.currency
    assert data["status"] == "DRAFT"
    assert data["subtotal"] == "0.13"
    assert data["discount_total"] == "0.00"
    assert data["total_amount"] == "0.13"
    assert data["cogs_total"] == "0.01"
    assert data["gross_profit_total"] == "0.12"
    assert data["lines"][0]["unit_price"] == "0.10"
    assert data["lines"][0]["unit_cost"] == "0.01"
    assert data["lines"][0]["line_revenue"] == "0.13"
    assert data["member_id"] is None
    assert data["payment_id"] is None
    too_much = _create(
        api,
        headers,
        {
            "business_date": gym_today(gym).isoformat(),
            "payment_method": "CARD",
            "lines": [{"product_id": product_id, "quantity": "1.000", "discount": "0.11"}],
        },
    )
    assert too_much.status_code == 400


@override_settings(**jwt_settings())
def test_approval_posts_stock_payment_receipt_and_cash_or_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, gym, owner, headers = _gym("RTLPOST")
    receptionist, reception_headers = _staff(
        gym, "RECEPTIONIST", "rtlpost-reception@example.test", "Rita"
    )
    manager, manager_headers = _staff(gym, "MANAGER", "rtlpost-manager@example.test", "Mina")
    product_id = _product(api, headers)
    _open_stock(api, headers, gym, product_id, "5.000")
    member_id = _member(api, headers, gym)
    other_gym_api, other_gym, _other_owner, other_headers = _gym("RTLOTH")
    other_product = _product(other_gym_api, other_headers, sku="OTHER-1")
    created = _create(
        api,
        reception_headers,
        _sale_body(
            product_id,
            gym,
            payment_method="CASH",
            member_id=member_id,
        ),
    )
    assert created.status_code == 201, created.content
    sale_id = created.json()["data"]["id"]
    assert created.json()["data"]["total_amount"] == "12.00"
    assert created.json()["data"]["lines"][0]["line_revenue"] == "12.00"
    assert created.json()["data"]["lines"][0]["cost_of_goods_sold"] == "5.00"
    assert created.json()["data"]["lines"][0]["gross_profit"] == "7.00"

    cross_product = _create(api, headers, _sale_body(other_product, gym))
    assert cross_product.status_code == 400
    cross_member = _create(
        api,
        headers,
        _sale_body(product_id, gym, member_id=str(uuid4())),
    )
    assert cross_member.status_code == 400

    direct = api.post(
        "/api/v1/payments/",
        {
            "purpose": "PRODUCT",
            "description": "Direct",
            "amount": "12.00",
            "currency": gym.currency,
            "method": "CARD",
            "business_date": gym_today(gym).isoformat(),
            "retail_sale_id": sale_id,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("direct"),
        **headers,
    )
    assert direct.status_code == 400

    submitted = _command(api, reception_headers, sale_id, "submit")
    assert submitted.status_code == 200, submitted.content
    edited = api.patch(
        f"/api/v1/retail-sales/{sale_id}/",
        {"reference_number": "TOO-LATE"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("patch"),
        **reception_headers,
    )
    assert edited.status_code == 400

    self_approve = _command(api, reception_headers, sale_id, "approve", {})
    assert self_approve.status_code == 403
    owner_headers = auth_headers(staff_token(owner))
    owner_sale = _create(api, owner_headers, _sale_body(product_id, gym, payment_method="CARD"))
    owner_id = owner_sale.json()["data"]["id"]
    assert _command(api, owner_headers, owner_id, "submit").status_code == 200
    assert _command(api, owner_headers, owner_id, "approve", {}).status_code == 403

    missing_session = _command(api, manager_headers, sale_id, "approve", {})
    assert missing_session.status_code == 400
    card_sale = _create(api, reception_headers, _sale_body(product_id, gym, payment_method="CARD"))
    card_id = card_sale.json()["data"]["id"]
    assert _command(api, reception_headers, card_id, "submit").status_code == 200
    session_id = _open_till(api, reception_headers, gym)
    rejected_session = _command(
        api, manager_headers, card_id, "approve", {"cash_session_id": session_id}
    )
    assert rejected_session.status_code == 400

    other_session = _open_till(api, owner_headers, gym, "OWNER")
    wrong_till = _command(
        api, manager_headers, sale_id, "approve", {"cash_session_id": other_session}
    )
    assert wrong_till.status_code == 400
    cross_session = _open_till(other_gym_api, other_headers, other_gym, "OTHER")
    cross_till = _command(
        api, manager_headers, sale_id, "approve", {"cash_session_id": cross_session}
    )
    assert cross_till.status_code in {400, 404, 409}

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("forced rollback")

    monkeypatch.setattr("apps.members.payment_services._issue_receipt", _boom)
    rolled = _command(api, manager_headers, sale_id, "approve", {"cash_session_id": session_id})
    assert rolled.status_code == 500
    monkeypatch.setattr("apps.members.payment_services._issue_receipt", _issue_receipt)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            StockMovement.objects.filter(product_id=product_id, movement_type="SALE").count() == 0
        )
        assert Payment.objects.filter(purpose="PRODUCT").count() == 0
        assert RetailSale.objects.get(id=sale_id).status == "PENDING_APPROVAL"

    approved = _command(api, manager_headers, sale_id, "approve", {"cash_session_id": session_id})
    assert approved.status_code == 200, approved.content
    payload = approved.json()["data"]
    assert payload["retail_sale"]["status"] == "POSTED"
    assert payload["payment"]["purpose"] == "PRODUCT"
    assert payload["payment"]["status"] == "POSTED"
    assert payload["payment"]["amount"] == "12.00"
    assert payload["payment"]["retail_sale_id"] == sale_id
    assert payload["payment"]["member_id"] == member_id
    assert payload["receipt"]["amount"] == "12.00"
    assert payload["cash_transaction"]["direction"] == "IN"
    assert payload["cash_transaction"]["source_type"] == "PAYMENT"

    hidden = api.get(f"/api/v1/retail-sales/{sale_id}/", **reception_headers)
    assert hidden.status_code == 404
    listed = api.get("/api/v1/retail-sales/", **reception_headers)
    assert sale_id not in {row["id"] for row in listed.json()["data"]}
    other_reception, other_reception_headers = _staff(
        gym, "RECEPTIONIST", "rtlpost-other@example.test", "Owen"
    )
    assert api.get(f"/api/v1/retail-sales/{card_id}/", **other_reception_headers).status_code == 404
    visible = api.get(f"/api/v1/retail-sales/{sale_id}/", **manager_headers)
    assert visible.status_code == 200

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        movements = StockMovement.objects.filter(product_id=product_id, movement_type="SALE")
        assert movements.count() == 1
        movement = movements.get()
        assert movement.quantity == Decimal("-1.250")
        assert movement.unit_cost_snapshot == Decimal("4.00")
        assert movement.retail_sale_line_id is not None
        posted_payments = Payment.objects.filter(gym_id=gym.id, purpose="PRODUCT", status="POSTED")
        assert posted_payments.count() == 1
        assert Receipt.objects.filter(gym_id=gym.id, status="ISSUED").count() == 1
        cash_in = CashTransaction.objects.filter(
            gym_id=gym.id, source_type="PAYMENT", direction="IN"
        )
        assert cash_in.count() == 1
        product = api.get(f"/api/v1/products/{product_id}/", **headers)
    assert product.json()["data"]["sold_quantity"] == "1.250"
    assert product.json()["data"]["revenue"] == "12.00"
    assert product.json()["data"]["quantity_on_hand"] == "3.750"

    daily = api.get(f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}", **headers)
    assert daily.status_code == 200, daily.content
    report = daily.json()["data"]
    assert report["product_quantity"] == "1.250"
    assert report["product_revenue"] == "12.00"
    assert report["product_cogs"] == "5.00"
    assert report["product_gross_profit"] == "7.00"
    assert report["product_total"] == report["product_revenue"]
    assert report["pt_total"] == "0.00"
    assert report["class_total"] == "0.00"
    assert report["gross_sales"] == "12.00"

    assert api.get(f"/api/v1/retail-sales/{sale_id}/", **other_headers).status_code == 404
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other_gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM sales_retail_sale WHERE id = %s", [sale_id])
        assert cursor.fetchone() == (0,)
        cursor.execute(
            "SELECT count(*) FROM sales_retail_sale_line WHERE sale_id = %s",
            [sale_id],
        )
        assert cursor.fetchone() == (0,)

    walk_in = _command(api, manager_headers, card_id, "approve", {})
    assert walk_in.status_code == 200, walk_in.content
    walk_in_body = walk_in.json()["data"]
    assert walk_in_body["retail_sale"]["member_id"] is None
    assert walk_in_body["retail_sale"]["status"] == "POSTED"
    assert walk_in_body["payment"]["member_id"] is None
    assert walk_in_body["payment"]["purpose"] == "PRODUCT"
    assert walk_in_body["payment"]["amount"] == "12.00"
    assert walk_in_body["receipt"]["member_name"] == "Walk-in"
    assert walk_in_body["cash_transaction"] is None

    del receptionist
    del other_reception
    del manager


@override_settings(**jwt_settings())
def test_reject_and_cancel_create_no_effects_and_idempotency_is_stable() -> None:
    api, gym, _owner, headers = _gym("RTLRJT")
    _manager, manager_headers = _staff(gym, "MANAGER", "rtlrjt-manager@example.test", "Mina")
    product_id = _product(api, headers)
    _open_stock(api, headers, gym, product_id, "2.000")
    created = _create(api, headers, _sale_body(product_id, gym))
    sale_id = created.json()["data"]["id"]
    assert _command(api, headers, sale_id, "submit").status_code == 200
    key = _key("rejectkey")
    rejected = api.post(
        f"/api/v1/retail-sales/{sale_id}/reject/",
        {"rejection_reason": "Not now"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **manager_headers,
    )
    assert rejected.status_code == 200, rejected.content
    replay = api.post(
        f"/api/v1/retail-sales/{sale_id}/reject/",
        {"rejection_reason": "Not now"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **manager_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == sale_id
    mismatch = api.post(
        f"/api/v1/retail-sales/{sale_id}/reject/",
        {"rejection_reason": "A different reason"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **manager_headers,
    )
    assert mismatch.status_code == 409
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert StockMovement.objects.filter(movement_type="SALE").count() == 0
        assert Payment.objects.filter(purpose="PRODUCT").count() == 0
        assert Receipt.objects.filter(gym_id=gym.id).count() == 0

    draft = _create(api, headers, _sale_body(product_id, gym))
    draft_id = draft.json()["data"]["id"]
    cancelled = _command(api, headers, draft_id, "cancel", {"reason": "Changed mind"})
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert RetailSaleLine.objects.filter(sale_id=draft_id).exists()
        assert StockMovement.objects.filter(movement_type="SALE").count() == 0


@override_settings(**jwt_settings())
def test_stale_price_inactive_product_and_manual_sale_forgery_are_denied() -> None:
    api, gym, owner, headers = _gym("RTLSTALE")
    _manager, manager_headers = _staff(gym, "MANAGER", "rtlstale-manager@example.test", "Mina")
    product_id = _product(api, headers)
    _open_stock(api, headers, gym, product_id, "3.000")
    created = _create(api, headers, _sale_body(product_id, gym, payment_method="CARD"))
    sale_id = created.json()["data"]["id"]
    assert _command(api, headers, sale_id, "submit").status_code == 200
    changed = api.patch(
        f"/api/v1/products/{product_id}/",
        {"sale_price": "11.00"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("price"),
        **headers,
    )
    assert changed.status_code == 200, changed.content
    stale = _command(api, manager_headers, sale_id, "approve", {})
    assert stale.status_code == 400
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert RetailSale.objects.get(id=sale_id).status == "PENDING_APPROVAL"
        assert RetailSale.objects.get(id=sale_id).total_amount == Decimal("12.00")
        assert StockMovement.objects.filter(movement_type="SALE").count() == 0

    inactive = api.patch(
        f"/api/v1/products/{product_id}/",
        {"is_active": False},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("arch"),
        **headers,
    )
    assert inactive.status_code == 200, inactive.content
    fresh = _create(api, headers, _sale_body(product_id, gym))
    assert fresh.status_code == 400

    with pytest.raises(DatabaseError, match="sale and reversal evidence"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO inventory_stock_movement (
                      id, created_at, updated_at, quantity, movement_type, unit_cost_snapshot,
                      reason, business_date, product_id, created_by_membership_id, gym_id
                    ) VALUES (
                      %s, NOW(), NOW(), -1, 'SALE', 4, '', %s, %s, %s, %s
                    )
                    """,
                    [uuid4(), gym_today(gym), product_id, owner.id, gym.id],
                )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'sales_retail_sale', 'SELECT'),
              has_table_privilege('gym_runtime', 'sales_retail_sale', 'INSERT'),
              has_table_privilege('gym_runtime', 'sales_retail_sale', 'UPDATE'),
              has_table_privilege('gym_runtime', 'sales_retail_sale', 'DELETE'),
              has_table_privilege('gym_runtime', 'sales_retail_sale_line', 'SELECT'),
              has_table_privilege('gym_runtime', 'sales_retail_sale_line', 'DELETE'),
              has_table_privilege('gym_runtime', 'inventory_stock_movement', 'UPDATE'),
              has_table_privilege('gym_runtime', 'inventory_stock_movement', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False, True, True, False, False)

    forged = api.post(
        "/api/v1/stock-movements/adjust/",
        {
            "product_id": product_id,
            "quantity": "-1.000",
            "movement_type": "SALE",
            "business_date": gym_today(gym).isoformat(),
            "reason": "Forged",
            "retail_sale_line_id": str(uuid4()),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("forge"),
        **headers,
    )
    assert forged.status_code == 400


@override_settings(**jwt_settings())
def test_concurrent_approve_reject_and_last_item_sales() -> None:
    api, gym, _owner, headers = _gym("RTLRACE")
    manager, _manager_headers = _staff(gym, "MANAGER", "rtlrace-manager@example.test", "Mina")
    race_product = _product(api, headers, sku="RACE-1")
    last_product = _product(api, headers, sku="LAST-1")
    _open_stock(api, headers, gym, race_product, "5.000")
    _open_stock(api, headers, gym, last_product, "1.000")

    def pending(product_id: str) -> str:
        created = _create(
            api,
            headers,
            _sale_body(
                product_id,
                gym,
                payment_method="CARD",
                lines=[{"product_id": product_id, "quantity": "1.000", "discount": "0.00"}],
            ),
        )
        assert created.status_code == 201, created.content
        sale_id = str(created.json()["data"]["id"])
        assert _command(api, headers, sale_id, "submit").status_code == 200
        return sale_id

    contested = pending(race_product)
    first_last = pending(last_product)
    second_last = pending(last_product)
    barrier = Barrier(2, timeout=20)

    def settle(action: str) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                if action == "approve":
                    approve_sale(
                        gym=gym,
                        actor=manager,
                        sale_id=contested,
                        cash_session_id=None,
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                else:
                    reject_sale(
                        gym=gym,
                        actor=manager,
                        sale_id=contested,
                        reason="Race",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
            return "ok"
        except (RetailPostingConflict, DatabaseError, ValidationError):
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = tuple(pool.submit(settle, action) for action in ("approve", "reject"))
        outcomes = sorted(future.result() for future in futures)
    assert outcomes == ["ok", "rejected"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = RetailSale.objects.get(id=contested)
        if sale.status == "POSTED":
            assert Payment.objects.filter(retail_sale_id=sale.id, status="POSTED").count() == 1
        else:
            assert sale.status == "REJECTED"
            assert Payment.objects.filter(retail_sale_id=sale.id).count() == 0

    stock_barrier = Barrier(2, timeout=20)

    def sell(sale_id: str) -> str:
        connections.close_all()
        try:
            stock_barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                approve_sale(
                    gym=gym,
                    actor=manager,
                    sale_id=sale_id,
                    cash_session_id=None,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "ok"
        except (RetailPostingConflict, DatabaseError, ValidationError):
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        stock_futures = tuple(pool.submit(sell, sale_id) for sale_id in (first_last, second_last))
        stock_outcomes = sorted(future.result() for future in stock_futures)
    assert stock_outcomes == ["ok", "rejected"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        quantities = list(
            StockMovement.objects.filter(product_id=last_product).values_list("quantity", flat=True)
        )
        assert sum(quantities, Decimal("0")) == Decimal("0.000")
