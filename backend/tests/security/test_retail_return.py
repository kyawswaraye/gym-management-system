"""PostgreSQL partial retail returns."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.db.models import Sum
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook import services as cash_services
from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.inventory.models import StockMovement
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailReturn, RetailSale
from apps.sales.services import return_sale
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from tests.security.test_retail import (
    _command,
    _create,
    _gym,
    _key,
    _open_stock,
    _open_till,
    _product,
    _sale_body,
    _staff,
)
from tests.security.test_tenant_gym_settings import jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _return(
    api: APIClient,
    headers: dict[str, str],
    sale_id: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        f"/api/v1/retail-sales/{sale_id}/return/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or _key("return"),
        **headers,
    )


def _close(api: APIClient, headers: dict[str, str], session_id: str, actual: str) -> None:
    closed = api.post(
        f"/api/v1/cash-sessions/{session_id}/reconcile/",
        {"actual_closing": actual, "difference_reason": ""},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("close"),
        **headers,
    )
    assert closed.status_code == 200, closed.content


def _post_sale(
    api: APIClient,
    headers: dict[str, str],
    manager_headers: dict[str, str],
    gym: Gym,
    product_id: str,
    lines: list[dict[str, str]],
    *,
    method: str = "CARD",
    cash_session_id: str | None = None,
    business_date: str | None = None,
    reference: str = "POS-RET",
) -> tuple[str, str]:
    body = _sale_body(
        product_id,
        gym,
        payment_method=method,
        reference_number=reference,
        lines=lines,
    )
    if business_date is not None:
        body["business_date"] = business_date
    created = _create(api, headers, body)
    assert created.status_code == 201, created.content
    sale_id = str(created.json()["data"]["id"])
    line_id = str(created.json()["data"]["lines"][0]["id"])
    assert _command(api, headers, sale_id, "submit").status_code == 200
    approve_body = {} if cash_session_id is None else {"cash_session_id": cash_session_id}
    approved = _command(api, manager_headers, sale_id, "approve", approve_body)
    assert approved.status_code == 200, approved.content
    return sale_id, line_id


@override_settings(**jwt_settings())
def test_repeated_returns_allocate_remainders_and_block_void(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, gym, owner, headers = _gym("RET8C1")
    _manager, manager_headers = _staff(gym, "MANAGER", "ret8c1-manager@example.test", "Mia")
    _accountant, accountant_headers = _staff(
        gym, "ACCOUNTANT", "ret8c1-accountant@example.test", "Ada"
    )
    _desk, desk_headers = _staff(gym, "RECEPTIONIST", "ret8c1-desk@example.test", "Rex")
    product_id = _product(api, headers, sku="RET-ALLOC", price="10.00", cost="0.05")
    _open_stock(api, headers, gym, product_id, "5.000")
    sale_id, line_id = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        product_id,
        [{"product_id": product_id, "quantity": "3.000", "discount": "20.00"}],
    )
    detail = api.get(f"/api/v1/retail-sales/{sale_id}/", **headers)
    assert detail.status_code == 200, detail.content
    assert detail.json()["data"]["returned_amount"] == "0.00"
    assert detail.json()["data"]["return_count"] == 0
    assert detail.json()["data"]["lines"][0]["remaining_quantity"] == "3.000"

    denied = _return(
        api,
        desk_headers,
        sale_id,
        {"reason": "Desk", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
    )
    assert denied.status_code == 403
    missing_key = api.post(
        f"/api/v1/retail-sales/{sale_id}/return/",
        {"reason": "No key", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
        format="json",
        **headers,
    )
    assert missing_key.status_code == 400
    duplicate = _return(
        api,
        headers,
        sale_id,
        {
            "reason": "Twice",
            "lines": [
                {"line_id": line_id, "quantity": "1.000"},
                {"line_id": line_id, "quantity": "1.000"},
            ],
        },
    )
    assert duplicate.status_code == 400
    other_line = _return(
        api,
        headers,
        sale_id,
        {"reason": "Wrong line", "lines": [{"line_id": str(uuid4()), "quantity": "1.000"}]},
    )
    assert other_line.status_code == 400
    with_session = _return(
        api,
        headers,
        sale_id,
        {
            "reason": "Card session",
            "cash_session_id": str(uuid4()),
            "lines": [{"line_id": line_id, "quantity": "1.000"}],
        },
    )
    assert with_session.status_code == 400

    slices = []
    responses = []
    key = "retail-return-alloc-0001"
    for step in range(3):
        response = _return(
            api,
            accountant_headers,
            sale_id,
            {"reason": f"Slice {step}", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
            key=key if step == 0 else None,
        )
        assert response.status_code == 200, response.content
        responses.append(response)
        slices.append(response.json()["data"])
    replay = _return(
        api,
        accountant_headers,
        sale_id,
        {"reason": "Slice 0", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
        key=key,
    )
    assert replay.content == responses[0].content
    conflict = _return(
        api,
        accountant_headers,
        sale_id,
        {"reason": "Different", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
        key=key,
    )
    assert conflict.status_code == 409
    refunds = [row["retail_return"]["refund_amount"] for row in slices]
    discounts = [row["retail_return"]["discount_total"] for row in slices]
    costs = [row["retail_return"]["cogs_total"] for row in slices]
    profits = [row["retail_return"]["gross_profit_total"] for row in slices]
    assert refunds == ["3.33", "3.34", "3.33"]
    assert discounts == ["6.67", "6.66", "6.67"]
    assert costs == ["0.05", "0.05", "0.05"]
    assert profits == ["3.28", "3.29", "3.28"]
    assert slices[-1]["retail_sale"]["status"] == "POSTED"
    assert slices[-1]["retail_sale"]["lines"][0]["remaining_quantity"] == "0.000"
    assert slices[-1]["retail_sale"]["returned_amount"] == "10.00"
    assert slices[-1]["retail_sale"]["return_count"] == 3
    assert slices[0]["refund_payment"]["entry_type"] == "REFUND"
    assert slices[0]["refund_payment"]["status"] == "POSTED"
    assert slices[0]["refund_payment"]["purpose"] == "PRODUCT"
    assert slices[0]["refund_payment"]["method"] == "CARD"
    assert slices[0]["refund_payment"]["reference_number"] == "POS-RET"
    assert slices[0]["refund_payment"]["reversal_payment_id"] is None
    assert slices[0]["correction_receipt"]["status"] == "ISSUED"
    assert slices[0]["cash_transaction"] is None
    assert slices[0]["stock_movements"][0]["quantity"] == "1.000"
    assert slices[0]["stock_movements"][0]["movement_type"] == "SALE_RETURN"

    listed = api.get("/api/v1/retail-sales/?status=POSTED", **headers)
    assert listed.status_code == 200, listed.content
    listed_sale = next(row for row in listed.json()["data"] if row["id"] == sale_id)
    assert listed_sale["returned_amount"] == "10.00"
    assert listed_sale["lines"][0]["returned_quantity"] == "3.000"

    again = _return(
        api,
        headers,
        sale_id,
        {"reason": "Too much", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
    )
    assert again.status_code == 409
    voided = api.post(
        f"/api/v1/retail-sales/{sale_id}/void/",
        {"reason": "After a return"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("void"),
        **headers,
    )
    assert voided.status_code == 409
    payment_id = slices[0]["retail_sale"]["payment_id"]
    blocked = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Public product void"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("payvoid"),
        **headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "RETAIL_CORRECTION_UNAVAILABLE"
    public_refund = api.post(
        f"/api/v1/payments/{payment_id}/refund/",
        {
            "amount": "1.00",
            "reason": "Public product refund",
            "method": "CARD",
            "business_date": gym_today(gym).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("payrefund"),
        **headers,
    )
    assert public_refund.status_code == 422
    assert public_refund.json()["error"]["code"] == "RETAIL_CORRECTION_UNAVAILABLE"

    report = api.get(f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}", **headers)
    assert report.status_code == 200, report.content
    body = report.json()["data"]
    assert body["product_quantity"] == "0.000"
    assert body["product_revenue"] == "0.00"
    assert body["product_cogs"] == "0.00"
    assert body["product_gross_profit"] == "0.00"
    assert body["product_total"] == "0.00"
    product = api.get(f"/api/v1/products/{product_id}/", **headers)
    assert product.json()["data"]["sold_quantity"] == "0.000"
    assert product.json()["data"]["revenue"] == "0.00"
    assert product.json()["data"]["cost_of_goods_sold"] == "0.00"
    assert product.json()["data"]["gross_profit"] == "0.00"
    assert product.json()["data"]["quantity_on_hand"] == "5.000"

    other_api, other_gym, _other_owner, other_headers = _gym("RET8C1B")
    hidden = _return(
        other_api,
        other_headers,
        sale_id,
        {"reason": "Other gym", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
    )
    assert hidden.status_code == 404
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other_gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM sales_retail_return WHERE sale_id = %s", [sale_id])
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM members_payment WHERE retail_sale_id = %s", [sale_id])
        assert cursor.fetchone() == (0,)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert RetailSale.objects.get(id=sale_id).status == "POSTED"
        assert (
            Payment.objects.filter(
                gym_id=gym.id, retail_sale_id=sale_id, entry_type="CHARGE", status="POSTED"
            ).count()
            == 1
        )
        assert (
            Receipt.objects.filter(
                gym_id=gym.id, payment__retail_sale_id=sale_id, status="ISSUED"
            ).count()
            == 4
        )
        assert (
            StockMovement.objects.filter(
                gym_id=gym.id, movement_type="SALE_RETURN", retail_sale_line_id=line_id
            ).count()
            == 3
        )
        assert AuditLog.objects.filter(gym_id=gym.id, action="RETAIL_SALE_RETURNED").count() == 3
        stored = RetailReturn.objects.get(id=slices[0]["retail_return"]["id"])
        stored.reason = "Changed"
        with pytest.raises(DatabaseError):
            stored.save()

    real_permission = cash_services.membership_has_permission

    def revoke(*, membership: GymMembership, code: str) -> bool:
        if code == "retail.void":
            return False
        return real_permission(membership=membership, code=code)

    monkeypatch.setattr(cash_services, "membership_has_permission", revoke)
    live = _return(
        api,
        headers,
        sale_id,
        {"reason": "Revoked", "lines": [{"line_id": line_id, "quantity": "1.000"}]},
    )
    assert live.status_code == 403
    del owner


@override_settings(**jwt_settings())
def test_cash_and_zero_refunds_correct_stock_reports_and_roll_back() -> None:
    api, gym, owner, headers = _gym("RET8C2")
    _manager, manager_headers = _staff(gym, "MANAGER", "ret8c2-manager@example.test", "Mia")
    product_id = _product(api, headers, sku="RET-CASH", price="4.00", cost="1.00")
    _open_stock(api, headers, gym, product_id, "4.000")
    original = _open_till(api, headers, gym, "RET-A")
    cash_sale, cash_line = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        product_id,
        [{"product_id": product_id, "quantity": "1.000", "discount": "0.00"}],
        method="CASH",
        cash_session_id=original,
        reference="POS-CASH",
    )
    _close(api, headers, original, "24.00")
    destination = _open_till(api, headers, gym, "RET-B")
    missing_session = _return(
        api,
        headers,
        cash_sale,
        {"reason": "No till", "lines": [{"line_id": cash_line, "quantity": "1.000"}]},
    )
    assert missing_session.status_code == 400
    returned = _return(
        api,
        headers,
        cash_sale,
        {
            "reason": "Cash back",
            "cash_session_id": destination,
            "lines": [{"line_id": cash_line, "quantity": "1.000"}],
        },
    )
    assert returned.status_code == 200, returned.content
    payload = returned.json()["data"]
    assert payload["retail_sale"]["status"] == "POSTED"
    assert payload["refund_payment"]["method"] == "CASH"
    assert payload["refund_payment"]["amount"] == "4.00"
    assert payload["refund_payment"]["reference_number"] == "POS-CASH"
    assert payload["cash_transaction"]["direction"] == "OUT"
    assert payload["cash_transaction"]["source_type"] == "PAYMENT"
    assert payload["cash_transaction"]["source_id"] == payload["refund_payment"]["id"]
    assert payload["cash_transaction"]["reversal_of_id"] is None
    assert payload["cash_transaction"]["cash_session_id"] == destination
    assert payload["correction_receipt"]["status"] == "ISSUED"

    yesterday = (gym_today(gym) - timedelta(days=1)).isoformat()
    dated_product = _product(api, headers, sku="RET-DATE", price="10.00", cost="4.00")
    _open_stock(api, headers, gym, dated_product, "2.000")
    dated_sale, dated_line = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        dated_product,
        [{"product_id": dated_product, "quantity": "1.000", "discount": "0.00"}],
        business_date=yesterday,
        reference="POS-DATE",
    )
    dated = _return(
        api,
        headers,
        dated_sale,
        {"reason": "Next day", "lines": [{"line_id": dated_line, "quantity": "1.000"}]},
    )
    assert dated.status_code == 200, dated.content
    original_day = api.get(f"/api/v1/sales/daily/?date={yesterday}", **headers)
    correction_day = api.get(f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}", **headers)
    assert original_day.json()["data"]["product_revenue"] == "10.00"
    assert original_day.json()["data"]["product_total"] == "10.00"
    assert correction_day.json()["data"]["product_revenue"] == "-10.00"
    assert correction_day.json()["data"]["product_total"] == "-10.00"
    assert correction_day.json()["data"]["product_quantity"] == "-1.000"
    assert correction_day.json()["data"]["product_cogs"] == "-4.00"
    assert correction_day.json()["data"]["product_gross_profit"] == "-6.00"

    zero_product = _product(api, headers, sku="RET-ZERO", price="0.01", cost="0.01")
    _open_stock(api, headers, gym, zero_product, "3.000")
    zero_sale, zero_line = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        zero_product,
        [{"product_id": zero_product, "quantity": "3.000", "discount": "0.02"}],
        reference="POS-ZERO",
    )
    rejected_session = _return(
        api,
        headers,
        zero_sale,
        {
            "reason": "No money",
            "cash_session_id": destination,
            "lines": [{"line_id": zero_line, "quantity": "1.000"}],
        },
    )
    assert rejected_session.status_code == 400
    zero = _return(
        api,
        headers,
        zero_sale,
        {"reason": "No money", "lines": [{"line_id": zero_line, "quantity": "1.000"}]},
    )
    assert zero.status_code == 200, zero.content
    assert zero.json()["data"]["retail_return"]["refund_amount"] == "0.00"
    assert zero.json()["data"]["retail_return"]["cogs_total"] == "0.01"
    assert zero.json()["data"]["refund_payment"] is None
    assert zero.json()["data"]["correction_receipt"] is None
    assert zero.json()["data"]["cash_transaction"] is None
    assert zero.json()["data"]["stock_movements"][0]["quantity"] == "1.000"

    rollback_product = _product(api, headers, sku="RET-ROLL", price="4.00", cost="1.00")
    _open_stock(api, headers, gym, rollback_product, "1.000")
    rollback_sale, rollback_line = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        rollback_product,
        [{"product_id": rollback_product, "quantity": "1.000", "discount": "0.00"}],
        reference="POS-ROLL",
    )
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            return_sale(
                gym=gym,
                actor=owner,
                sale_id=rollback_sale,
                reason="Rolled back",
                business_date=None,
                cash_session_id=None,
                lines=[{"line_id": rollback_line, "quantity": Decimal("1.000")}],
                request_id="req_rollback",
                ip_address=None,
                user_agent="",
            )
            raise RuntimeError("rollback")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not RetailReturn.objects.filter(sale_id=rollback_sale).exists()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        entitlement = GymFeatureEntitlement.objects.select_related("feature").get(
            gym_id=gym.id, feature__code="finance"
        )
        entitlement.enabled = False
        entitlement.save(update_fields=["enabled"])
        with pytest.raises(PermissionDenied):
            return_sale(
                gym=gym,
                actor=owner,
                sale_id=uuid4(),
                reason="No finance",
                business_date=None,
                cash_session_id=None,
                lines=[{"line_id": uuid4(), "quantity": Decimal("1.000")}],
                request_id="req_entitlement",
                ip_address=None,
                user_agent="",
            )
        transaction.set_rollback(True)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            CashTransaction.objects.filter(
                gym_id=gym.id,
                source_id=payload["refund_payment"]["id"],
                reversal_of_id__isnull=False,
            ).count()
            == 0
        )
        assert Payment.objects.get(id=payload["retail_sale"]["payment_id"]).status == "POSTED"
        assert Receipt.objects.get(id=payload["retail_sale"]["receipt_id"]).status == "ISSUED"


@override_settings(**jwt_settings())
def test_concurrent_returns_serialize_on_the_sale() -> None:
    api, gym, owner, headers = _gym("RET8C3")
    manager, manager_headers = _staff(gym, "MANAGER", "ret8c3-manager@example.test", "Mia")
    product_id = _product(api, headers, sku="RET-RACE", price="10.00", cost="1.00")
    _open_stock(api, headers, gym, product_id, "6.000")
    sale_id, line_id = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        product_id,
        [{"product_id": product_id, "quantity": "3.000", "discount": "20.00"}],
    )
    over_sale, over_line = _post_sale(
        api,
        headers,
        manager_headers,
        gym,
        product_id,
        [{"product_id": product_id, "quantity": "3.000", "discount": "20.00"}],
        reference="POS-OVER",
    )
    barrier = Barrier(2, timeout=20)

    def once(quantity: str) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                result = return_sale(
                    gym=gym,
                    actor=owner,
                    sale_id=sale_id,
                    reason="Race",
                    business_date=None,
                    cash_session_id=None,
                    lines=[{"line_id": line_id, "quantity": Decimal(quantity)}],
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return str(result["retail_return"].refund_amount)
        except RetailPostingConflict:
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result() for future in (pool.submit(once, "1.000"), pool.submit(once, "1.000"))
        )
    assert outcomes == ["3.33", "3.34"]

    over_barrier = Barrier(2, timeout=20)

    def over() -> str:
        connections.close_all()
        try:
            over_barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                return_sale(
                    gym=gym,
                    actor=manager,
                    sale_id=over_sale,
                    reason="Too much",
                    business_date=None,
                    cash_session_id=None,
                    lines=[{"line_id": over_line, "quantity": Decimal("2.000")}],
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "ok"
        except RetailPostingConflict:
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        over_outcomes = sorted(future.result() for future in (pool.submit(over), pool.submit(over)))
    assert over_outcomes == ["ok", "rejected"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        taken = StockMovement.objects.filter(
            gym_id=gym.id, retail_sale_line_id=over_line, movement_type="SALE_RETURN"
        ).aggregate(total=Sum("quantity"))
        assert taken["total"] == Decimal("2.000")
        assert RetailSale.objects.get(id=sale_id).status == "POSTED"
