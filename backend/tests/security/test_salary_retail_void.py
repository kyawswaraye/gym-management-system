"""PostgreSQL salary-period void and full retail-sale void."""

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
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook import services as cash_services
from apps.cashbook.models import CashSession, CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.expenses.selectors import expense_net_totals
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.salaries.exceptions import SalaryPostingConflict
from apps.salaries.models import SalaryPeriod
from apps.salaries.services import void_period
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailSale
from apps.sales.services import void_sale
from apps.tenants.models import Gym, GymMembership
from tests.security.test_retail import (
    _command,
    _open_stock,
    _product,
    _sale_body,
    _staff,
)
from tests.security.test_retail import (
    _create as _create_sale,
)
from tests.security.test_retail import (
    _gym as _retail_gym,
)
from tests.security.test_retail import (
    _open_till as _retail_till,
)
from tests.security.test_salaries import (
    _create,
    _gym,
    _line,
    _open_till,
    _profile,
    _put_lines,
)
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
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


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:20]}"


def _void_salary(
    api: APIClient,
    headers: dict[str, str],
    period_id: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        f"/api/v1/salary-periods/{period_id}/void/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or _key("salvoid"),
        **headers,
    )


def _void_sale(
    api: APIClient,
    headers: dict[str, str],
    sale_id: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        f"/api/v1/retail-sales/{sale_id}/void/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or _key("rtlvoid"),
        **headers,
    )


def _post_salary(
    api: APIClient,
    gym: Gym,
    creator_headers: dict[str, str],
    approver_headers: dict[str, str],
    lines: list[dict[str, str]],
    mappings: list[dict[str, str]],
    *,
    business_date: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    body = {
        "period_start": gym_today(gym).replace(day=1).isoformat(),
        "period_end": gym_today(gym).isoformat(),
        "business_date": business_date or gym_today(gym).isoformat(),
        "description": "Void coverage",
    }
    created = api.post(
        "/api/v1/salary-periods/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("salcreate"),
        **creator_headers,
    )
    assert created.status_code == 201, created.content
    period_id = str(created.json()["data"]["id"])
    replaced = _put_lines(api, creator_headers, period_id, lines)
    assert replaced.status_code == 200, replaced.content
    stored = replaced.json()["data"]["lines"]
    submitted = api.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("salsubmit"),
        **creator_headers,
    )
    assert submitted.status_code == 200, submitted.content
    approved = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": mappings},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("salapprove"),
        **approver_headers,
    )
    assert approved.status_code == 200, approved.content
    return period_id, stored


def _close(api: APIClient, headers: dict[str, str], session_id: str, actual: str) -> None:
    closed = api.post(
        f"/api/v1/cash-sessions/{session_id}/reconcile/",
        {"actual_closing": actual, "difference_reason": ""},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("close"),
        **headers,
    )
    assert closed.status_code == 200, closed.content
    assert closed.json()["data"]["session"]["status"] == "CLOSED"


def _posted_card_sale(
    api: APIClient,
    headers: dict[str, str],
    manager_headers: dict[str, str],
    gym: Gym,
    product_id: str,
    *,
    business_date: str | None = None,
) -> str:
    body = _sale_body(
        product_id,
        gym,
        payment_method="CARD",
        lines=[{"product_id": product_id, "quantity": "1.000", "discount": "0.00"}],
    )
    if business_date is not None:
        body["business_date"] = business_date
    created = _create_sale(api, headers, body)
    assert created.status_code == 201, created.content
    sale_id = str(created.json()["data"]["id"])
    assert _command(api, headers, sale_id, "submit").status_code == 200
    approved = _command(api, manager_headers, sale_id, "approve", {})
    assert approved.status_code == 200, approved.content
    return sale_id


@override_settings(**jwt_settings())
def test_noncash_salary_void_dates_mappings_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, gym, _owner, headers = _gym("VOID8B1")
    trainer = add_staff(gym, email="void8b1-trainer@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="void8b1-manager@example.test", role_key="MANAGER")
    accountant = add_staff(gym, email="void8b1-accountant@example.test", role_key="ACCOUNTANT")
    trainer_headers = auth_headers(staff_token(trainer))
    manager_headers = auth_headers(staff_token(manager))
    accountant_headers = auth_headers(staff_token(accountant))
    coach = _profile(gym, trainer, "COACH", "Coach")
    yesterday = (gym_today(gym) - timedelta(days=1)).isoformat()
    period_id, lines = _post_salary(
        api,
        gym,
        headers,
        manager_headers,
        [_line(coach, gym, "BANK_TRANSFER", "80.00")],
        [],
        business_date=yesterday,
    )
    line_id = lines[0]["id"]
    original_expense_id = lines[0]["expense_id"]
    assert original_expense_id is None

    blank = _void_salary(api, accountant_headers, period_id, {"reason": "  "})
    assert blank.status_code == 400
    denied = _void_salary(api, trainer_headers, period_id, {"reason": "Trainer"})
    assert denied.status_code == 403
    mapped = _void_salary(
        api,
        accountant_headers,
        period_id,
        {
            "reason": "Non-cash line",
            "cash_sessions": [{"line_id": line_id, "cash_session_id": str(uuid4())}],
        },
    )
    assert mapped.status_code == 400
    early = _void_salary(
        api,
        accountant_headers,
        period_id,
        {"reason": "Too early", "business_date": "2020-01-01"},
    )
    assert early.status_code == 400
    future = _void_salary(
        api,
        accountant_headers,
        period_id,
        {"reason": "Future", "business_date": "2999-01-01"},
    )
    assert future.status_code == 400

    real = void_period.__globals__["void_linked_salary_expense"]
    calls = {"count": 0}

    def fail_second(**kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("forced rollback")
        return real(**kwargs)

    monkeypatch.setattr(
        "apps.salaries.services.void_linked_salary_expense",
        fail_second,
    )
    second_profile = _profile(gym, manager, "LEAD", "Lead")
    rollback_id, _rollback_lines = _post_salary(
        api,
        gym,
        headers,
        manager_headers,
        [
            _line(coach, gym, "BANK_TRANSFER", "10.00"),
            _line(second_profile, gym, "CARD", "12.00"),
        ],
        [],
        business_date=yesterday,
    )
    rolled = _void_salary(api, accountant_headers, rollback_id, {"reason": "Rollback"})
    assert rolled.status_code == 500
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert SalaryPeriod.objects.get(id=rollback_id).status == "POSTED"
        assert Expense.objects.filter(gym_id=gym.id, original_expense_id__isnull=False).count() == 0
    monkeypatch.undo()

    voided = _void_salary(
        api,
        accountant_headers,
        period_id,
        {"reason": "Payroll correction"},
        key="salary-void-bank-0001",
    )
    assert voided.status_code == 200, voided.content
    payload = voided.json()["data"]
    assert payload["salary_period"]["status"] == "VOID"
    assert payload["salary_period"]["void_business_date"] == gym_today(gym).isoformat()
    assert payload["salary_period"]["voided_by_membership_id"] == str(accountant.id)
    assert (
        payload["salary_period"]["lines"][0]["expense_id"]
        != payload["reversals"][0]["reversal_expense_id"]
    )
    assert payload["reversals"][0]["cash_transaction"] is None
    assert payload["reversals"][0]["reversal_expense"]["amount"] == "80.00"
    assert (
        payload["reversals"][0]["reversal_expense"]["business_date"] == gym_today(gym).isoformat()
    )
    replay = _void_salary(
        api,
        accountant_headers,
        period_id,
        {"reason": "Payroll correction"},
        key="salary-void-bank-0001",
    )
    assert replay.status_code == 200
    assert replay.content == voided.content
    again = _void_salary(api, accountant_headers, period_id, {"reason": "Second void"})
    assert again.status_code == 409
    _other, other_gym, _other_owner, other_headers = _gym("VOID8B2")
    assert _void_salary(api, other_headers, period_id, {"reason": "Cross gym"}).status_code == 404
    denied_platform = api.post(
        f"/api/v1/salary-periods/{period_id}/void/",
        {"reason": "Platform"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("platform"),
        HTTP_AUTHORIZATION=f"Bearer {platform_token('void8b1-platform-denied@example.test')[1]}",
    )
    assert denied_platform.status_code == 401

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = SalaryPeriod.objects.get(id=period_id)
        line = period.lines.get()
        original = Expense.objects.get(id=line.expense_id)
        reversal = Expense.objects.get(id=original.reversal_expense_id)
        assert original.status == "VOID"
        assert original.business_date.isoformat() == yesterday
        assert reversal.status == "POSTED"
        assert reversal.business_date == gym_today(gym)
        assert reversal.amount == original.amount
        assert reversal.payment_method == "BANK_TRANSFER"
        assert reversal.cash_transaction_id is None
        yesterday_totals = expense_net_totals(gym_id=gym.id, business_date=original.business_date)
        today_totals = expense_net_totals(gym_id=gym.id, business_date=gym_today(gym))
        assert yesterday_totals["gross_amount"] == Decimal("102.00")
        assert yesterday_totals["net_amount"] == Decimal("102.00")
        assert today_totals["reversal_amount"] == Decimal("80.00")
        assert today_totals["net_amount"] == Decimal("-80.00")
        assert AuditLog.objects.filter(gym_id=gym.id, action="SALARY_PERIOD_VOIDED").count() == 1
        expense_id = original.id

    protected = api.post(
        f"/api/v1/expenses/{expense_id}/void/",
        {"reason": "Direct salary void"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("protected"),
        **headers,
    )
    assert protected.status_code == 422
    assert protected.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"

    real_permission = cash_services.membership_has_permission

    def revoke(*, membership: GymMembership, code: str) -> bool:
        if code == "salaries.void":
            return False
        return real_permission(membership=membership, code=code)

    monkeypatch.setattr(cash_services, "membership_has_permission", revoke)
    cash_period, cash_lines = _post_salary(
        api,
        gym,
        headers,
        manager_headers,
        [_line(coach, gym, "BANK_TRANSFER", "15.00")],
        [],
    )
    del cash_lines
    live = _void_salary(api, headers, cash_period, {"reason": "Revoked"})
    assert live.status_code == 403
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert SalaryPeriod.objects.get(id=cash_period).status == "POSTED"
    del other_gym


@override_settings(**jwt_settings())
def test_cash_salary_void_requires_one_open_destination_session() -> None:
    api, gym, owner, headers = _gym("VOID8B3")
    receptionist = add_staff(gym, email="void8b3-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="void8b3-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    desk = _profile(gym, receptionist, "DESK", "Front Desk")
    created = _create(api, reception_headers, gym)
    assert created.status_code == 201, created.content
    period_id = created.json()["data"]["id"]
    replaced = _put_lines(
        api,
        reception_headers,
        period_id,
        [
            _line(desk, gym, "CASH", "100.00"),
            _line(_profile(gym, manager, "MGR", "Manager"), gym, "OTHER", "20.00"),
        ],
    )
    assert replaced.status_code == 200, replaced.content
    draft_lines = replaced.json()["data"]["lines"]
    cash_line = next(line for line in draft_lines if line["payment_method"] == "CASH")
    other_line = next(line for line in draft_lines if line["payment_method"] == "OTHER")
    original_till = _open_till(api, reception_headers, gym, "FRONT")
    submitted = api.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("submit"),
        **reception_headers,
    )
    assert submitted.status_code == 200, submitted.content
    approved = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": original_till}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("approve"),
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    posted_lines = approved.json()["data"]["salary_period"]["lines"]
    cash_line = next(line for line in posted_lines if line["payment_method"] == "CASH")
    other_line = next(line for line in posted_lines if line["payment_method"] == "OTHER")
    _close(api, reception_headers, original_till, "499900.00")
    destination = _open_till(api, headers, gym, "SAFE")
    closed_destination = _open_till(api, headers, gym, "OLD")
    _close(api, headers, closed_destination, "500000.00")
    _other_api, other_gym, _other_owner, _other_headers = _gym("VOID8B4")
    foreign = _open_till(_other_api, _other_headers, other_gym, "AWAY")

    missing = _void_salary(api, headers, period_id, {"reason": "Missing till"})
    assert missing.status_code == 400
    duplicate = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Duplicate",
            "cash_sessions": [
                {"line_id": cash_line["id"], "cash_session_id": destination},
                {"line_id": cash_line["id"], "cash_session_id": destination},
            ],
        },
    )
    assert duplicate.status_code == 400
    noncash = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Extra",
            "cash_sessions": [
                {"line_id": cash_line["id"], "cash_session_id": destination},
                {"line_id": other_line["id"], "cash_session_id": destination},
            ],
        },
    )
    assert noncash.status_code == 400
    closed = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Closed till",
            "cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": original_till}],
        },
    )
    assert closed.status_code == 400
    cross = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Other gym",
            "cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": foreign}],
        },
    )
    assert cross.status_code == 400
    yesterday = gym_today(gym) - timedelta(days=1)

    def _shift_session(session_id: str, business_date: object) -> None:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
                cursor.execute(
                    "UPDATE cashbook_cash_session SET business_date = %s WHERE id = %s",
                    [business_date, session_id],
                )

    _shift_session(destination, yesterday)
    wrong_date = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Wrong date",
            "cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": destination}],
        },
    )
    assert wrong_date.status_code == 400
    _shift_session(destination, gym_today(gym))

    voided = _void_salary(
        api,
        headers,
        period_id,
        {
            "reason": "Return cash to the open till",
            "cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": destination}],
        },
        key="salary-void-cash-0001",
    )
    assert voided.status_code == 200, voided.content
    body = voided.json()["data"]
    cash_reversal = next(row for row in body["reversals"] if row["line_id"] == cash_line["id"])
    other_reversal = next(row for row in body["reversals"] if row["line_id"] == other_line["id"])
    assert cash_reversal["cash_transaction"]["direction"] == "IN"
    assert cash_reversal["cash_transaction"]["amount"] == "100.00"
    assert cash_reversal["cash_transaction"]["cash_session_id"] == destination
    assert other_reversal["cash_transaction"] is None
    assert body["salary_period"]["lines"][0]["expense_id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = SalaryPeriod.objects.get(id=period_id)
        assert period.status == "VOID"
        assert CashSession.objects.get(id=original_till).status == "CLOSED"
        movement = CashTransaction.objects.get(id=cash_reversal["cash_transaction"]["id"])
        original_expense = Expense.objects.get(id=cash_line["expense_id"])
        assert movement.reversal_of_id == original_expense.cash_transaction_id
        assert movement.direction == "IN"
        assert Expense.objects.filter(gym_id=gym.id, original_expense_id__isnull=False).count() == 2

    barrier = Barrier(2, timeout=20)
    other_period, _other_lines = _post_salary(
        api,
        gym,
        headers,
        manager_headers,
        [_line(desk, gym, "BANK_TRANSFER", "30.00")],
        [],
    )

    def race(actor: GymMembership) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                void_period(
                    gym=gym,
                    actor=actor,
                    period_id=other_period,
                    reason="Race",
                    business_date=None,
                    cash_sessions=[],
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "ok"
        except (SalaryPostingConflict, DatabaseError, ValidationError, PermissionDenied):
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(race, (owner, manager)))
    assert outcomes == ["ok", "rejected"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        raced = SalaryPeriod.objects.get(id=other_period)
        assert raced.status == "VOID"
        assert (
            Expense.objects.filter(
                gym_id=gym.id,
                original_expense_id=raced.lines.get().expense_id,
            ).count()
            == 1
        )

    posted_id, _posted_lines = _post_salary(
        api,
        gym,
        headers,
        manager_headers,
        [_line(desk, gym, "OTHER", "5.00")],
        [],
    )
    with pytest.raises(DatabaseError, match="salary_posting"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    UPDATE salaries_salary_period
                    SET status = 'VOID',
                        voided_at = now(),
                        voided_by_membership_id = %s,
                        void_business_date = business_date,
                        reason = 'incomplete'
                    WHERE id = %s
                    """,
                    [str(owner.id), posted_id],
                )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert SalaryPeriod.objects.get(id=posted_id).status == "POSTED"


@override_settings(**jwt_settings())
def test_retail_card_void_reconciles_same_day_and_later_day() -> None:
    api, gym, _owner, headers = _retail_gym("VOID8B5")
    manager, manager_headers = _staff(gym, "MANAGER", "void8b5-manager@example.test", "Mina")
    accountant, accountant_headers = _staff(
        gym, "ACCOUNTANT", "void8b5-accountant@example.test", "Ada"
    )
    receptionist, reception_headers = _staff(
        gym, "RECEPTIONIST", "void8b5-reception@example.test", "Ren"
    )
    product_id = _product(api, headers, sku="CARD-1")
    _open_stock(api, headers, gym, product_id, "5.000")
    yesterday = (gym_today(gym) - timedelta(days=1)).isoformat()
    later = _posted_card_sale(
        api, headers, manager_headers, gym, product_id, business_date=yesterday
    )
    same = _posted_card_sale(api, headers, manager_headers, gym, product_id)
    assert _void_sale(api, reception_headers, later, {"reason": "Desk"}).status_code == 403
    assert (
        _void_sale(
            api,
            accountant_headers,
            later,
            {"reason": "Card till", "cash_session_id": str(uuid4())},
        ).status_code
        == 400
    )
    later_void = _void_sale(
        api,
        accountant_headers,
        later,
        {"reason": "Next-day correction"},
        key="retail-void-card-0001",
    )
    assert later_void.status_code == 200, later_void.content
    later_body = later_void.json()["data"]
    assert later_body["retail_sale"]["status"] == "VOID"
    assert later_body["retail_sale"]["void_business_date"] == gym_today(gym).isoformat()
    assert later_body["retail_sale"]["void_reason"] == "Next-day correction"
    assert later_body["original_payment"]["status"] == "VOID"
    assert later_body["reversal_payment"]["entry_type"] == "REVERSAL"
    assert later_body["reversal_payment"]["status"] == "POSTED"
    assert later_body["reversal_payment"]["amount"] == "10.00"
    assert later_body["original_receipt"]["status"] == "VOID"
    assert later_body["reversal_receipt"]["status"] == "ISSUED"
    assert later_body["cash_transaction"] is None
    assert len(later_body["stock_movements"]) == 1
    assert later_body["stock_movements"][0]["movement_type"] == "SALE_RETURN"
    assert later_body["stock_movements"][0]["quantity"] == "1.000"
    replay = _void_sale(
        api,
        accountant_headers,
        later,
        {"reason": "Next-day correction"},
        key="retail-void-card-0001",
    )
    assert replay.content == later_void.content
    assert _void_sale(api, accountant_headers, later, {"reason": "Again"}).status_code == 409
    same_void = _void_sale(api, headers, same, {"reason": "Same day"})
    assert same_void.status_code == 200, same_void.content

    original_day = api.get(f"/api/v1/sales/daily/?date={yesterday}", **headers)
    correction_day = api.get(f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}", **headers)
    assert original_day.status_code == 200, original_day.content
    assert correction_day.status_code == 200, correction_day.content
    original_report = original_day.json()["data"]
    correction_report = correction_day.json()["data"]
    assert original_report["product_quantity"] == "1.000"
    assert original_report["product_revenue"] == "10.00"
    assert original_report["product_cogs"] == "4.00"
    assert original_report["product_gross_profit"] == "6.00"
    assert original_report["product_total"] == "10.00"
    assert correction_report["product_quantity"] == "-1.000"
    assert correction_report["product_revenue"] == "-10.00"
    assert correction_report["product_cogs"] == "-4.00"
    assert correction_report["product_gross_profit"] == "-6.00"
    assert correction_report["product_total"] == "-10.00"
    product = api.get(f"/api/v1/products/{product_id}/", **headers)
    assert product.json()["data"]["sold_quantity"] == "0.000"
    assert product.json()["data"]["revenue"] == "0.00"
    assert product.json()["data"]["cost_of_goods_sold"] == "0.00"
    assert product.json()["data"]["gross_profit"] == "0.00"
    assert product.json()["data"]["quantity_on_hand"] == "5.000"
    payment_id = later_body["original_payment"]["id"]
    blocked = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Public product void"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("payvoid"),
        **headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "RETAIL_CORRECTION_UNAVAILABLE"
    refund = api.post(
        f"/api/v1/payments/{payment_id}/refund/",
        {
            "amount": "10.00",
            "reason": "Public product refund",
            "method": "CARD",
            "business_date": gym_today(gym).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("payrefund"),
        **headers,
    )
    assert refund.status_code == 422
    assert refund.json()["error"]["code"] == "RETAIL_CORRECTION_UNAVAILABLE"
    missing_return = api.post(
        f"/api/v1/retail-sales/{same}/return/",
        {"reason": "Partial"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("return"),
        **headers,
    )
    assert missing_return.status_code == 400
    forged = api.post(
        "/api/v1/stock-movements/adjust/",
        {
            "product_id": product_id,
            "quantity": "1.000",
            "movement_type": "SALE_RETURN",
            "business_date": gym_today(gym).isoformat(),
            "reason": "Forged return",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("forge"),
        **headers,
    )
    assert forged.status_code == 400
    del manager
    del accountant
    del receptionist

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert StockMovement.objects.filter(gym_id=gym.id, movement_type="SALE_RETURN").count() == 2
        assert (
            Payment.objects.filter(gym_id=gym.id, entry_type="REVERSAL", purpose="PRODUCT").count()
            == 2
        )
        assert Receipt.objects.filter(gym_id=gym.id, status="VOID").count() == 2
        assert AuditLog.objects.filter(gym_id=gym.id, action="RETAIL_SALE_VOIDED").count() == 2


@override_settings(**jwt_settings())
def test_retail_cash_void_restores_stock_and_rejects_incomplete_sql() -> None:
    api, gym, owner, headers = _retail_gym("VOID8B6")
    _manager, manager_headers = _staff(gym, "MANAGER", "void8b6-manager@example.test", "Mina")
    product_id = _product(api, headers, sku="CASH-1", price="10.00", cost="4.00")
    _open_stock(api, headers, gym, product_id, "1.000")
    session_id = _retail_till(api, headers, gym, "FRONT")
    created = _create_sale(
        api,
        headers,
        _sale_body(
            product_id,
            gym,
            payment_method="CASH",
            lines=[{"product_id": product_id, "quantity": "1.000", "discount": "0.00"}],
        ),
    )
    assert created.status_code == 201, created.content
    sale_id = str(created.json()["data"]["id"])
    assert _command(api, headers, sale_id, "submit").status_code == 200
    approved = _command(
        api,
        manager_headers,
        sale_id,
        "approve",
        {"cash_session_id": session_id},
    )
    assert approved.status_code == 200, approved.content
    _close(api, headers, session_id, "30.00")
    destination = _retail_till(api, headers, gym, "SAFE")
    missing = _void_sale(api, headers, sale_id, {"reason": "No till"})
    assert missing.status_code == 400
    closed = _void_sale(
        api,
        headers,
        sale_id,
        {"reason": "Closed original", "cash_session_id": session_id},
    )
    assert closed.status_code == 400
    voided = _void_sale(
        api,
        headers,
        sale_id,
        {"reason": "Cash correction", "cash_session_id": destination},
    )
    assert voided.status_code == 200, voided.content
    movement = voided.json()["data"]["cash_transaction"]
    assert movement["direction"] == "OUT"
    assert movement["amount"] == "10.00"
    assert movement["cash_session_id"] == destination
    product = api.get(f"/api/v1/products/{product_id}/", **headers)
    assert product.json()["data"]["quantity_on_hand"] == "1.000"
    assert product.json()["data"]["sold_quantity"] == "0.000"
    same_day = api.get(f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}", **headers)
    assert same_day.json()["data"]["product_revenue"] == "0.00"
    assert same_day.json()["data"]["product_total"] == "0.00"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = CashTransaction.objects.get(gym_id=gym.id, source_type="PAYMENT", direction="IN")
        correction = CashTransaction.objects.get(id=movement["id"])
        assert correction.reversal_of_id == original.id
        assert CashSession.objects.get(id=session_id).status == "CLOSED"
        assert RetailSale.objects.get(id=sale_id).status == "VOID"

    _other_api, other_gym, _other_owner, other_headers = _retail_gym("VOID8B7")
    assert _void_sale(_other_api, other_headers, sale_id, {"reason": "Other"}).status_code == 404
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other_gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM sales_retail_sale WHERE id = %s", [sale_id])
        assert cursor.fetchone() == (0,)
        cursor.execute(
            """
            SELECT has_table_privilege('gym_runtime', 'sales_retail_sale', 'DELETE'),
                   has_table_privilege('gym_runtime', 'inventory_stock_movement', 'UPDATE'),
                   has_table_privilege('gym_runtime', 'inventory_stock_movement', 'DELETE'),
                   has_table_privilege('gym_runtime', 'members_payment', 'DELETE')
            """
        )
        assert cursor.fetchone() == (False, False, False, False)

    race_product = _product(api, headers, sku="RACE-1")
    _open_stock(api, headers, gym, race_product, "1.000")
    race_sale = _posted_card_sale(api, headers, manager_headers, gym, race_product)
    barrier = Barrier(2, timeout=20)

    def restore() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                void_sale(
                    gym=gym,
                    actor=owner,
                    sale_id=race_sale,
                    reason="Race",
                    business_date=None,
                    cash_session_id=None,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "void"
        except (RetailPostingConflict, DatabaseError, ValidationError):
            return "rejected"
        finally:
            connections.close_all()

    def damage() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                adjust_stock(
                    gym=gym,
                    actor=owner,
                    fields={
                        "product_id": race_product,
                        "quantity": "-1.000",
                        "movement_type": "DAMAGE",
                        "business_date": gym_today(gym),
                        "reason": "Broken during the race",
                    },
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            return "adjust"
        except (ValidationError, DatabaseError):
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(future.result() for future in (pool.submit(restore), pool.submit(damage)))
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        total = StockMovement.objects.filter(gym_id=gym.id, product_id=race_product)
        quantity = sum((row.quantity for row in total), Decimal("0"))
        assert quantity >= 0
        assert (
            StockMovement.objects.filter(
                gym_id=gym.id, product_id=race_product, movement_type="SALE_RETURN"
            ).count()
            == 1
        )
        assert RetailSale.objects.get(id=race_sale).status == "VOID"
    assert "void" in outcomes

    _open_stock(api, headers, gym, race_product, "1.000")
    fresh = _posted_card_sale(api, headers, manager_headers, gym, race_product)
    with pytest.raises(DatabaseError, match=r"retail_posting|stock_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    UPDATE sales_retail_sale
                    SET status = 'VOID',
                        voided_at = now(),
                        voided_by_membership_id = %s,
                        void_business_date = business_date,
                        void_reason = 'incomplete'
                    WHERE id = %s
                    """,
                    [str(owner.id), fresh],
                )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert RetailSale.objects.get(id=fresh).status == "POSTED"
