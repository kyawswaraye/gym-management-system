"""Daily and monthly financial reports, isolation, and bounded reads."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.common.db.tenant_context import set_gym_context
from apps.expenses.services import post_pt_commission_expense
from apps.members.models import CheckIn, Payment
from apps.reports.selectors import PERIOD_REPORT_QUERY_COUNT, SOURCE_STATUS_POLICY, period_report
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from apps.tenants.sequence_services import EXPENSE_CATEGORY_CATALOG
from tests.security.test_dashboard import (
    FROZEN,
    _category,
    _dashboard,
    _expense,
    _headers,
    _legacy_class_payment,
    _member,
    _membership,
    _open_stock,
    _payment_body,
    _plan,
    _post_payment,
    _sell,
    _staff,
    _void_legacy,
    _zone,
)
from tests.security.test_retail import _enable, _gym, _product
from tests.security.test_salaries import _line, _profile
from tests.security.test_salary_retail_void import _post_salary
from tests.security.test_tenant_gym_settings import jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

BUCKETS = (
    "new_member_revenue",
    "renewal_revenue",
    "pt_revenue",
    "class_revenue",
    "product_revenue",
    "other_revenue",
)
MONEY = (
    *BUCKETS,
    "total_revenue",
    "product_cogs",
    "product_gross_profit",
    "total_expenses",
    "net_profit",
)


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _report(api: Any, headers: dict[str, str], path: str) -> dict[str, Any]:
    response = api.get(path, **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _row(report: dict[str, Any], day: str) -> dict[str, Any]:
    matches = [row for row in report["rows"] if row["date"] == day]
    assert len(matches) == 1
    return matches[0]


def _reconcile(section: dict[str, Any]) -> None:
    total = sum((Decimal(str(section[name])) for name in BUCKETS), Decimal("0.00"))
    assert total == Decimal(str(section["total_revenue"]))
    revenue = Decimal(str(section["total_revenue"]))
    expenses = Decimal(str(section["total_expenses"]))
    assert revenue - expenses == Decimal(str(section["net_profit"]))


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


@override_settings(**jwt_settings())
def test_period_reports_reject_closed_callers_and_unknown_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, _owner_headers = _gym("RPTGATE")
    _enable(gym, "reports")
    staff = _staff(gym)
    daily = "/api/v1/reports/daily/?date=2026-02-01"
    accountant = _headers(staff["accountant"])
    assert api.get("/api/v1/reports/daily/", **accountant).status_code == 400
    overridden = f"{daily}&gym_id={gym.id}&status=POSTED&formula=cash"
    assert api.get(overridden, **accountant).status_code == 400
    assert api.get("/api/v1/reports/daily/?date=1899-12-31", **accountant).status_code == 400
    assert api.get("/api/v1/reports/daily/?date=2026-02-31", **accountant).status_code == 400
    assert api.get("/api/v1/reports/monthly/?year=2026", **accountant).status_code == 400
    assert api.get("/api/v1/reports/monthly/?year=1899&month=1", **accountant).status_code == 400
    assert api.get("/api/v1/reports/monthly/?year=2026&month=13", **accountant).status_code == 400
    assert api.get(daily, **_headers(staff["reception"])).status_code == 403
    assert api.get(daily, **_headers(staff["trainer"])).status_code == 403
    _grant_financial(gym, owner, staff["trainer"])
    assert api.get(daily, **_headers(staff["trainer"])).status_code == 404

    february = _report(api, accountant, "/api/v1/reports/monthly/?year=2026&month=2")
    assert february["range"] == {"date_from": "2026-02-01", "date_to": "2026-02-28"}
    assert len(february["rows"]) == 28
    assert all(row["total_revenue"] == "0.00" and row["check_ins"] == 0 for row in february["rows"])
    assert february["totals"]["unique_attendees"] == 0
    GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(enabled=False)
    assert api.get(daily, **accountant).status_code == 403


@override_settings(**jwt_settings())
def test_period_reports_reconcile_corrections_and_stay_query_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("RPTFIN")
    for feature in ("reports", "finance", "members", "memberships", "pt"):
        _enable(gym, feature)
    _zone(gym, "Asia/Bangkok", 1)
    staff = _staff(gym)
    desk = _headers(staff["reception"])
    manager = _headers(staff["manager"])
    accountant = _headers(staff["accountant"])
    plan = _plan(gym, owner)
    member_a = _member(gym, owner, number="RPT-A", name="Ada", joined=date(2026, 10, 1))
    member_b = _member(gym, owner, number="RPT-B", name="Bea", joined=date(2026, 9, 1))
    member_c = _member(gym, owner, number="RPT-C", name="Cara", joined=date(2026, 9, 1))
    membership_a = _membership(
        gym,
        owner,
        member=member_a,
        plan=plan,
        start=date(2026, 10, 1),
        expiry=date(2026, 12, 31),
        amount="10.00",
        source="NEW_MEMBER",
    )
    membership_b = _membership(
        gym,
        owner,
        member=member_b,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 3),
        amount="10.00",
        source="RENEWAL",
    )
    membership_c = _membership(
        gym,
        owner,
        member=member_c,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 12, 31),
        amount="10.00",
        source="RENEWAL",
    )
    charge = _legacy_class_payment(
        gym, owner, member_a, amount="30.00", day=date(2026, 10, 1), sequence=1
    )
    _void_legacy(gym, owner, charge, day=date(2026, 10, 2), sequence=2)
    created_category = api.post(
        "/api/v1/expense-categories/",
        {"code": "SNACKS", "name": "Snacks"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="rpt-category-create",
        **owner_headers,
    )
    assert created_category.status_code == 201, created_category.content
    snacks = str(created_category.json()["data"]["id"])
    _expense(
        api,
        desk,
        manager,
        category_id=str(_category(gym, "RENT").id),
        amount="40.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    utilities = _expense(
        api,
        desk,
        manager,
        category_id=str(_category(gym, "UTILITIES").id),
        amount="5.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    voided_utilities = api.post(
        f"/api/v1/expenses/{utilities}/void/",
        {"reason": "Wrong meter", "business_date": "2026-10-03"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="rpt-expense-void-01",
        **owner_headers,
    )
    assert voided_utilities.status_code == 200, voided_utilities.content
    assert voided_utilities.json()["data"]["reversal_expense"]["business_date"] == "2026-10-03"
    _expense(
        api,
        desk,
        manager,
        category_id=snacks,
        amount="7.00",
        day="2026-10-02",
        gym=gym,
        post=True,
    )
    pending_expense = _expense(
        api,
        desk,
        manager,
        category_id=str(_category(gym, "RENT").id),
        amount="99.00",
        day="2026-10-02",
        gym=gym,
        post=False,
    )
    assert (
        api.post(
            f"/api/v1/expenses/{pending_expense}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="rpt-expense-pending",
            **desk,
        ).status_code
        == 200
    )
    draft = api.post(
        "/api/v1/payments/",
        _payment_body(
            gym,
            purpose="OTHER",
            amount="500.00",
            day="2026-10-01",
            member_id=str(member_a.id),
        ),
        format="json",
        HTTP_IDEMPOTENCY_KEY="rpt-draft-payment01",
        **desk,
    )
    assert draft.status_code == 201, draft.content
    trainer = _profile(gym, staff["trainer"], "RPTCOACH", "Coach")
    _post_salary(
        api,
        gym,
        owner_headers,
        manager,
        [_line(trainer, gym, "BANK_TRANSFER", "80.00")],
        [],
        business_date="2026-10-02",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        post_pt_commission_expense(
            gym=gym,
            approver=staff["manager"],
            created_by_membership_id=staff["reception"].id,
            submitted_by_membership_id=staff["reception"].id,
            business_date=date(2026, 10, 2),
            description="Commission payout",
            amount=Decimal("20.00"),
            currency=gym.currency,
            payment_method=Payment.Method.BANK_TRANSFER,
            cash_session_id=None,
            policy_note="Payout",
            request_id="rpt-commission",
            ip_address=None,
            user_agent="pytest",
        )
        for member, membership, stamp in (
            (member_a, membership_a, datetime(2026, 10, 1, 3, 0, tzinfo=UTC)),
            (member_a, membership_a, datetime(2026, 10, 2, 3, 0, tzinfo=UTC)),
            (member_b, membership_b, datetime(2026, 10, 2, 4, 0, tzinfo=UTC)),
            (member_c, membership_c, datetime(2026, 10, 1, 17, 30, tzinfo=UTC)),
        ):
            CheckIn.objects.create(
                gym=gym,
                member=member,
                membership=membership,
                check_in_at=stamp,
                method=CheckIn.Method.MANUAL,
                recorded_by_membership_id=owner.id,
                created_by_membership_id=owner.id,
                eligibility_snapshot={"eligible": True},
            )

    protein = _product(api, owner_headers, sku="RPTPRO", price="10.00", cost="4.00")
    shaker = _product(api, owner_headers, sku="RPTSHA", price="5.00", cost="1.00")
    _open_stock(api, owner_headers, protein, "2026-09-30")
    _open_stock(api, owner_headers, shaker, "2026-09-30")
    kept = _sell(
        api,
        desk,
        manager,
        product_id=protein,
        quantity="2.000",
        day="2026-10-01",
        reference="RPT-KEEP",
        price_body={},
    )
    void_sale = _sell(
        api,
        desk,
        manager,
        product_id=shaker,
        quantity="1.000",
        day="2026-10-01",
        reference="RPT-VOID",
        price_body={},
    )
    kept_detail = api.get(f"/api/v1/retail-sales/{kept}/", **owner_headers)
    assert kept_detail.status_code == 200, kept_detail.content
    line_id = kept_detail.json()["data"]["lines"][0]["id"]
    returned = api.post(
        f"/api/v1/retail-sales/{kept}/return/",
        {
            "reason": "One back",
            "business_date": "2026-10-02",
            "lines": [{"line_id": line_id, "quantity": "1.000"}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="rpt-retail-return01",
        **owner_headers,
    )
    assert returned.status_code == 200, returned.content
    voided_sale = api.post(
        f"/api/v1/retail-sales/{void_sale}/void/",
        {"reason": "Wrong item", "business_date": "2026-10-04"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="rpt-retail-void-001",
        **owner_headers,
    )
    assert voided_sale.status_code == 200, voided_sale.content

    other_api, other, other_owner, other_headers = _gym("RPTOTH")
    for feature in ("reports", "finance", "members"):
        _enable(other, feature)
    other_staff = _staff(other)
    other_member = _member(other, other_owner, number="RPT-Z", name="Zoe", joined=date(2026, 10, 1))
    _post_payment(
        other_api,
        other_headers,
        _headers(other_staff["manager"]),
        _payment_body(
            other,
            purpose="OTHER",
            amount="999.00",
            day="2026-10-01",
            member_id=str(other_member.id),
        ),
    )

    daily = _report(api, accountant, "/api/v1/reports/daily/?date=2026-10-01")
    monthly = _report(api, owner_headers, "/api/v1/reports/monthly/?year=2026&month=10")
    assert daily["gym"]["code"] == gym.code
    assert daily["gym"]["name"] == gym.name
    assert daily["timezone"] == "Asia/Bangkok"
    assert daily["currency"] == gym.currency
    assert daily["range"] == {"date_from": "2026-10-01", "date_to": "2026-10-01"}
    assert daily["source_status_policy"] == SOURCE_STATUS_POLICY
    assert "2026-10-19" in daily["generated_at"]
    assert len(daily["rows"]) == 1
    assert monthly["range"] == {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    assert [row["date"] for row in monthly["rows"]] == [
        date(2026, 10, day).isoformat() for day in range(1, 32)
    ]

    october_first = _row(monthly, "2026-10-01")
    assert october_first["class_revenue"] == "30.00"
    assert october_first["product_revenue"] == "25.00"
    assert october_first["total_revenue"] == "55.00"
    assert october_first["product_quantity"] == "3.000"
    assert october_first["product_cogs"] == "9.00"
    assert october_first["product_gross_profit"] == "16.00"
    assert october_first["total_expenses"] == "45.00"
    assert october_first["net_profit"] == "10.00"
    assert october_first["new_members"] == 1
    assert october_first["check_ins"] == 1
    assert october_first["unique_attendees"] == 1
    assert october_first["other_revenue"] == "0.00"
    assert daily["rows"][0] == october_first

    october_second = _row(monthly, "2026-10-02")
    assert october_second["class_revenue"] == "-30.00"
    assert october_second["product_revenue"] == "-10.00"
    assert october_second["total_revenue"] == "-40.00"
    assert october_second["product_quantity"] == "-1.000"
    assert october_second["product_cogs"] == "-4.00"
    assert october_second["product_gross_profit"] == "-6.00"
    assert october_second["total_expenses"] == "107.00"
    assert october_second["check_ins"] == 3
    assert october_second["unique_attendees"] == 3

    october_third = _row(monthly, "2026-10-03")
    assert october_third["total_revenue"] == "0.00"
    assert october_third["total_expenses"] == "-5.00"
    assert october_third["expired_memberships"] == 1
    october_fourth = _row(monthly, "2026-10-04")
    assert october_fourth["product_revenue"] == "-5.00"
    assert october_fourth["product_quantity"] == "-1.000"
    assert october_fourth["product_cogs"] == "-1.00"
    assert october_fourth["product_gross_profit"] == "-4.00"
    blank = _row(monthly, "2026-10-05")
    assert blank["total_revenue"] == "0.00"
    assert blank["total_expenses"] == "0.00"
    assert blank["product_quantity"] == "0.000"
    assert blank["check_ins"] == 0

    totals = monthly["totals"]
    assert totals["total_revenue"] == "10.00"
    assert totals["class_revenue"] == "0.00"
    assert totals["product_revenue"] == "10.00"
    assert totals["product_quantity"] == "1.000"
    assert totals["product_cogs"] == "4.00"
    assert totals["product_gross_profit"] == "6.00"
    assert totals["total_expenses"] == "147.00"
    assert totals["net_profit"] == "-137.00"
    assert totals["staff_salary_expense"] == "80.00"
    assert totals["pt_commission_expense"] == "20.00"
    assert totals["new_members"] == 1
    assert totals["expired_memberships"] == 1
    assert totals["check_ins"] == 4
    assert totals["unique_attendees"] == 3
    assert sum(row["unique_attendees"] for row in monthly["rows"]) == 4
    for section in (october_first, october_second, october_third, october_fourth, totals):
        _reconcile(section)
    counted = ("new_members", "expired_memberships", "check_ins")
    for field in (*MONEY, *counted, "product_quantity"):
        if field in counted:
            assert sum(row[field] for row in monthly["rows"]) == totals[field]
            continue
        row_total = sum((Decimal(str(row[field])) for row in monthly["rows"]), Decimal("0"))
        assert row_total == Decimal(str(totals[field]))
    categories = totals["expenses_by_category"]
    catalog = [code for code, _name in EXPENSE_CATEGORY_CATALOG]
    assert [item["system_key"] for item in categories] == catalog
    amounts = {item["system_key"]: item["amount"] for item in categories}
    assert amounts["RENT"] == "40.00"
    assert amounts["UTILITIES"] == "0.00"
    assert amounts["STAFF_SALARY"] == "80.00"
    assert amounts["PT_COMMISSION"] == "20.00"
    assert "SNACKS" not in amounts
    protected = sum((Decimal(item["amount"]) for item in categories), Decimal("0.00"))
    assert protected + Decimal("7.00") == Decimal(totals["total_expenses"])

    dashboard = _dashboard(api, accountant, "range=this_month")
    financial = dashboard["financial"]
    assert isinstance(financial, dict)
    for field in (
        "total_revenue",
        "class_revenue",
        "product_revenue",
        "other_revenue",
        "total_expenses",
        "net_profit",
        "staff_salary_expense",
        "pt_commission_expense",
        "expenses_by_category",
    ):
        assert financial[field] == totals[field]
    operational = dashboard["operational"]
    assert isinstance(operational, dict)
    assert operational["new_members"] == totals["new_members"]
    assert operational["expired_memberships"] == totals["expired_memberships"]
    assert operational["check_ins"] == totals["check_ins"]
    assert operational["unique_attendees"] == totals["unique_attendees"]

    isolated = _report(
        other_api,
        _headers(other_staff["accountant"]),
        "/api/v1/reports/daily/?date=2026-10-01",
    )
    assert isolated["gym"]["code"] == other.code
    assert isolated["rows"][0]["other_revenue"] == "999.00"
    assert isolated["rows"][0]["total_revenue"] == "999.00"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            period_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_month:
            period_report(
                gym=loaded,
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 31),
                generated_at=FROZEN,
            )
    one_sql = [query["sql"] for query in one_day.captured_queries]
    month_sql = [query["sql"] for query in full_month.captured_queries]
    assert len(one_sql) == len(month_sql) == PERIOD_REPORT_QUERY_COUNT, (one_sql, month_sql)
