"""Tenant dashboard ranges, isolation, and exact source reconciliation.

Pending payment totals use expired memberships only. Member package prices and
PT sale fees are catalog amounts, not ``UNPAID`` or ``PARTIAL`` balances, and
class grants have no priced debt field. Those sources are not invented here.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.expenses.services import post_pt_commission_expense
from apps.members.models import CheckIn, Member, Membership, MembershipPlan, Payment, Receipt
from apps.tenants.models import (
    ExpenseCategory,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymSettings,
    StaffProfile,
)
from apps.tenants.sequence_services import EXPENSE_CATEGORY_CATALOG
from tests.security.test_pt_sales import _sale
from tests.security.test_retail import _command, _create, _enable, _gym, _product
from tests.security.test_salaries import _line, _profile, _put_lines
from tests.security.test_salary_retail_void import _post_salary
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

FROZEN = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
TODAY = date(2026, 10, 19)
SOURCES = ("NEW_MEMBER", "RENEWAL", "PT", "CLASS", "PRODUCT", "OTHER")


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _headers(membership: GymMembership) -> dict[str, str]:
    return auth_headers(staff_token(membership))


def _staff(gym: Gym) -> dict[str, GymMembership]:
    return {
        "reception": add_staff(
            gym, email=f"{gym.code.lower()}-desk@example.test", role_key="RECEPTIONIST"
        ),
        "manager": add_staff(gym, email=f"{gym.code.lower()}-mgr@example.test", role_key="MANAGER"),
        "accountant": add_staff(
            gym, email=f"{gym.code.lower()}-books@example.test", role_key="ACCOUNTANT"
        ),
        "trainer": add_staff(
            gym, email=f"{gym.code.lower()}-coach@example.test", role_key="TRAINER"
        ),
    }


def _zone(gym: Gym, zone: str, week_starts_on: int) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Gym.objects.filter(id=gym.id).update(timezone=zone)
        GymSettings.objects.filter(gym_id=gym.id).update(week_starts_on=week_starts_on)
    gym.refresh_from_db()


def _dashboard(api: APIClient, headers: dict[str, str], query: str) -> dict[str, object]:
    response = api.get(f"/api/v1/dashboard/?{query}", **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _member(
    gym: Gym,
    actor: GymMembership,
    *,
    number: str,
    name: str,
    joined: date,
    status: str = Member.Status.ACTIVE,
) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.create(
            gym=gym,
            member_number=number,
            first_name=name,
            join_date=joined,
            status=status,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )


def _plan(gym: Gym, actor: GymMembership) -> MembershipPlan:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return MembershipPlan.objects.create(
            gym=gym,
            code="DASH",
            name="Dashboard",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("100.00"),
            currency=gym.currency,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )


def _membership(
    gym: Gym,
    actor: GymMembership,
    *,
    member: Member,
    plan: MembershipPlan,
    start: date,
    expiry: date,
    amount: str,
    source: str,
    status: str = Membership.Status.ACTIVE,
    payment_status: str = Membership.PaymentStatus.PAID,
) -> Membership:
    cancelled = status == Membership.Status.CANCELLED
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Membership.objects.create(
            gym=gym,
            member=member,
            plan=plan,
            plan_code=plan.code,
            plan_name=plan.name,
            duration_value=1,
            duration_unit=plan.duration_unit,
            start_date=start,
            expiry_date=expiry,
            list_price=Decimal(amount),
            discount_amount=Decimal("0.00"),
            final_amount=Decimal(amount),
            currency=gym.currency,
            payment_status=payment_status,
            status=status,
            revenue_source=source,
            cancelled_at=FROZEN if cancelled else None,
            cancelled_by_membership_id=actor.id if cancelled else None,
            cancellation_reason="Ended" if cancelled else "",
            created_by_membership_id=actor.id,
        )


def _payment_body(
    gym: Gym,
    *,
    purpose: str,
    amount: str,
    day: str,
    member_id: str | None = None,
    membership_id: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "purpose": purpose,
        "description": f"{purpose} {amount}",
        "amount": amount,
        "currency": gym.currency,
        "method": "BANK_TRANSFER",
        "business_date": day,
    }
    if day != TODAY.isoformat():
        body["received_at"] = f"{day}T12:00:00Z"
    if member_id is not None:
        body["member_id"] = member_id
    if membership_id is not None:
        body["membership_id"] = membership_id
    return body


def _command_payment(
    api: APIClient,
    headers: dict[str, str],
    payment_id: str,
    action: str,
    body: dict[str, object] | None = None,
) -> object:
    return api.post(
        f"/api/v1/payments/{payment_id}/{action}/",
        {} if body is None else body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key(action),
        **headers,
    )


def _post_payment(
    api: APIClient,
    creator: dict[str, str],
    approver: dict[str, str],
    body: dict[str, object],
) -> str:
    created = api.post(
        "/api/v1/payments/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("pay"),
        **creator,
    )
    assert created.status_code == 201, created.content
    payment_id = str(created.json()["data"]["id"])
    assert _command_payment(api, creator, payment_id, "submit").status_code == 200
    approved = _command_payment(api, approver, payment_id, "approve")
    assert approved.status_code == 200, approved.content
    return payment_id


def _expense(
    api: APIClient,
    creator: dict[str, str],
    approver: dict[str, str],
    *,
    category_id: str,
    amount: str,
    day: str,
    gym: Gym,
    post: bool,
) -> str:
    created = api.post(
        "/api/v1/expenses/",
        {
            "category_id": category_id,
            "business_date": day,
            "description": f"Expense {amount} {day}",
            "amount": amount,
            "currency": gym.currency,
            "payment_method": "BANK_TRANSFER",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("exp"),
        **creator,
    )
    assert created.status_code == 201, created.content
    expense_id = str(created.json()["data"]["id"])
    if not post:
        return expense_id
    submitted = api.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("expsub"),
        **creator,
    )
    assert submitted.status_code == 200, submitted.content
    approved = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("expapr"),
        **approver,
    )
    assert approved.status_code == 200, approved.content
    return expense_id


def _category(gym: Gym, system_key: str) -> ExpenseCategory:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return ExpenseCategory.objects.get(gym_id=gym.id, system_key=system_key)


def _legacy_class_payment(
    gym: Gym,
    actor: GymMembership,
    member: Member,
    *,
    amount: str,
    day: date,
    sequence: int,
) -> Payment:
    posted_at = FROZEN
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.create(
            gym=gym,
            member=member,
            purpose=Payment.Purpose.CLASS,
            description=f"Class {amount}",
            amount=Decimal(amount),
            currency=gym.currency,
            method=Payment.Method.CASH,
            business_date=day,
            received_at=posted_at,
            status=Payment.Status.POSTED,
            submitted_at=posted_at,
            submitted_by_membership_id=actor.id,
            approved_at=posted_at,
            approved_by_membership_id=actor.id,
            posted_at=posted_at,
            posted_by_membership_id=actor.id,
            legacy_unlinked_cash=True,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
        Receipt.objects.create(
            gym=gym,
            payment=payment,
            receipt_number=f"DASH-CLASS-{sequence}",
            sequence_key=f"DASHCLASS:{day.year}",
            sequence_value=sequence,
            sequence_year=day.year,
            issued_at=posted_at,
            gym_code=gym.code,
            gym_name=gym.name,
            gym_address="",
            member_number=member.member_number,
            member_name=member.display_name,
            description=payment.description,
            amount=payment.amount,
            currency=payment.currency,
            method=payment.method,
            business_date=day,
            issuer_name="Owner",
            status=Receipt.Status.ISSUED,
            created_by_membership_id=actor.id,
        )
        return payment


def _void_legacy(
    gym: Gym, actor: GymMembership, original: Payment, *, day: date, sequence: int
) -> None:
    posted_at = FROZEN
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        reversal = Payment.objects.create(
            gym=gym,
            member_id=original.member_id,
            original_payment=original,
            entry_type=Payment.EntryType.REVERSAL,
            purpose=original.purpose,
            description="Class reversal",
            amount=original.amount,
            currency=original.currency,
            method=original.method,
            business_date=day,
            received_at=posted_at,
            status=Payment.Status.POSTED,
            submitted_at=posted_at,
            submitted_by_membership_id=actor.id,
            approved_at=posted_at,
            approved_by_membership_id=actor.id,
            posted_at=posted_at,
            posted_by_membership_id=actor.id,
            legacy_unlinked_cash=True,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
        Receipt.objects.create(
            gym=gym,
            payment=reversal,
            receipt_number=f"DASH-CLASS-{sequence}",
            sequence_key=f"DASHCLASS:{day.year}",
            sequence_value=sequence,
            sequence_year=day.year,
            issued_at=posted_at,
            gym_code=gym.code,
            gym_name=gym.name,
            gym_address="",
            member_number=original.member.member_number,
            member_name=original.member.display_name,
            description=reversal.description,
            amount=reversal.amount,
            currency=reversal.currency,
            method=reversal.method,
            business_date=day,
            issuer_name="Owner",
            status=Receipt.Status.ISSUED,
            created_by_membership_id=actor.id,
        )
        issued = original.receipts.get(status=Receipt.Status.ISSUED)
        issued.status = Receipt.Status.VOID
        issued.voided_at = posted_at
        issued.voided_by_membership_id = actor.id
        issued.void_reason = "Same day correction"
        issued.save()
        original.status = Payment.Status.VOID
        original.voided_at = posted_at
        original.voided_by_membership_id = actor.id
        original.void_reason = "Same day correction"
        original.reversal_payment = reversal
        original.updated_by_membership_id = actor.id
        original.save()


def _open_stock(api: APIClient, headers: dict[str, str], product_id: str, day: str) -> None:
    response = api.post(
        "/api/v1/stock-movements/adjust/",
        {
            "product_id": product_id,
            "quantity": "20.000",
            "movement_type": "OPENING",
            "business_date": day,
            "reason": "Opening count",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("stock"),
        **headers,
    )
    assert response.status_code == 201, response.content


def _sell(
    api: APIClient,
    creator: dict[str, str],
    approver: dict[str, str],
    *,
    product_id: str,
    quantity: str,
    day: str,
    reference: str,
    price_body: dict[str, object],
) -> str:
    created = _create(
        api,
        creator,
        {
            **price_body,
            "business_date": day,
            "payment_method": "CARD",
            "reference_number": reference,
            "lines": [{"product_id": product_id, "quantity": quantity, "discount": "0.00"}],
        },
    )
    assert created.status_code == 201, created.content
    sale_id = str(created.json()["data"]["id"])
    assert _command(api, creator, sale_id, "submit").status_code == 200
    approved = _command(api, approver, sale_id, "approve", {})
    assert approved.status_code == 200, approved.content
    return sale_id


@override_settings(**jwt_settings())
def test_dashboard_query_access_and_zero_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    api, gym, _owner, _owner_headers = _gym("DASHEMP")
    _enable(gym, "reports")
    _zone(gym, "Asia/Bangkok", 0)
    staff = _staff(gym)
    reception = _headers(staff["reception"])
    accountant = _headers(staff["accountant"])
    trainer = _headers(staff["trainer"])

    denied = api.get("/api/v1/dashboard/", **accountant)
    assert denied.status_code == 400
    unknown = api.get("/api/v1/dashboard/?range=month", **accountant)
    assert unknown.status_code == 400
    mixed = api.get("/api/v1/dashboard/?range=today&date_from=2026-10-01", **accountant)
    assert mixed.status_code == 400
    partial = api.get("/api/v1/dashboard/?date_from=2026-10-01", **accountant)
    assert partial.status_code == 400
    too_long = api.get(
        "/api/v1/dashboard/?date_from=2024-01-01&date_to=2025-01-01",
        **accountant,
    )
    assert too_long.status_code == 400
    overridden = api.get("/api/v1/dashboard/?range=today&gym_id=1&status=POSTED", **accountant)
    assert overridden.status_code == 400

    week = _dashboard(api, accountant, "range=this_week")
    assert week["range"] == {"date_from": "2026-10-18", "date_to": "2026-10-24"}
    assert week["timezone"] == "Asia/Bangkok"
    assert week["currency"] == "THB"
    financial = week["financial"]
    assert isinstance(financial, dict)
    assert [item["source"] for item in financial["revenue_sources_ranked"]] == list(SOURCES)
    assert {item["amount"] for item in financial["revenue_sources_ranked"]} == {"0.00"}
    assert [item["system_key"] for item in financial["expenses_by_category"]] == [
        code for code, _name in EXPENSE_CATEGORY_CATALOG
    ]
    assert {item["amount"] for item in financial["expenses_by_category"]} == {"0.00"}
    assert (
        financial["total_revenue"]
        == financial["total_expenses"]
        == financial["net_profit"]
        == "0.00"
    )

    hidden = _dashboard(api, reception, "range=today")
    assert hidden["financial"] is None
    assert hidden["operational"]["active_members"] == 0
    assert api.get("/api/v1/dashboard/?range=today", **trainer).status_code == 404

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="finance").update(enabled=False)
    still_financial = _dashboard(api, accountant, "range=today")
    assert still_financial["financial"] is not None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        updated = GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(
            enabled=False
        )
    assert updated == 1
    assert api.get("/api/v1/dashboard/?range=today", **accountant).status_code == 403


@override_settings(**jwt_settings())
def test_dashboard_timezone_boundaries_and_gym_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    api, bangkok, owner, owner_headers = _gym("DASHBKK")
    _other, honolulu, other_owner, _other_headers = _gym("DASHHNL")
    _enable(bangkok, "reports")
    _enable(honolulu, "reports")
    _zone(bangkok, "Asia/Bangkok", 0)
    _zone(honolulu, "Pacific/Honolulu", 1)
    bangkok_staff = _staff(bangkok)
    honolulu_staff = _staff(honolulu)
    bangkok_accountant = _headers(bangkok_staff["accountant"])
    honolulu_accountant = _headers(honolulu_staff["accountant"])
    plan = _plan(bangkok, owner)
    other_plan = _plan(honolulu, other_owner)
    bangkok_member = _member(
        bangkok, owner, number="BKK-1", name="Bangkok", joined=date(2026, 10, 19)
    )
    honolulu_member = _member(
        honolulu, other_owner, number="HNL-1", name="Honolulu", joined=date(2026, 10, 18)
    )
    bangkok_membership = _membership(
        bangkok,
        owner,
        member=bangkok_member,
        plan=plan,
        start=date(2026, 10, 1),
        expiry=date(2026, 12, 31),
        amount="77.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    _membership(
        honolulu,
        other_owner,
        member=honolulu_member,
        plan=other_plan,
        start=date(2026, 10, 1),
        expiry=date(2026, 12, 31),
        amount="77.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    _post_payment(
        api,
        owner_headers,
        _headers(bangkok_staff["manager"]),
        _payment_body(
            bangkok,
            purpose="MEMBERSHIP",
            amount="77.00",
            day="2026-10-18",
            member_id=str(bangkok_member.id),
            membership_id=str(bangkok_membership.id),
        ),
    )
    with transaction.atomic():
        set_gym_context(gym_id=bangkok.id)
        CheckIn.objects.create(
            gym=bangkok,
            member=bangkok_member,
            membership=bangkok_membership,
            check_in_at=datetime(2026, 10, 18, 10, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 10, 18, 11, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=bangkok,
            member=bangkok_member,
            membership=bangkok_membership,
            check_in_at=datetime(2026, 10, 18, 20, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 10, 18, 21, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=bangkok,
            member=bangkok_member,
            membership=bangkok_membership,
            check_in_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        set_gym_context(gym_id=honolulu.id)
        CheckIn.objects.create(
            gym=honolulu,
            member=honolulu_member,
            check_in_at=datetime(2026, 10, 18, 10, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 10, 18, 11, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=other_owner.id,
            created_by_membership_id=other_owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=honolulu,
            member=honolulu_member,
            check_in_at=datetime(2026, 10, 18, 20, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 10, 18, 21, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=other_owner.id,
            created_by_membership_id=other_owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=honolulu,
            member=honolulu_member,
            check_in_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=other_owner.id,
            created_by_membership_id=other_owner.id,
            eligibility_snapshot={"eligible": True},
        )

    bangkok_today = _dashboard(api, bangkok_accountant, "range=today")
    honolulu_today = _dashboard(api, honolulu_accountant, "range=today")
    assert bangkok_today["range"] == {"date_from": "2026-10-19", "date_to": "2026-10-19"}
    assert honolulu_today["range"] == {"date_from": "2026-10-18", "date_to": "2026-10-18"}
    assert bangkok_today["operational"]["check_ins"] == 1
    assert bangkok_today["operational"]["new_members"] == 1
    assert bangkok_today["operational"]["currently_checked_in"] == 1
    assert honolulu_today["operational"]["check_ins"] == 2
    assert honolulu_today["operational"]["new_members"] == 1
    assert honolulu_today["operational"]["currently_checked_in"] == 1
    bangkok_financial = bangkok_today["financial"]
    honolulu_financial = honolulu_today["financial"]
    assert isinstance(bangkok_financial, dict)
    assert isinstance(honolulu_financial, dict)
    assert bangkok_financial["total_revenue"] == "0.00"
    assert honolulu_financial["total_revenue"] == "0.00"
    earlier = _dashboard(api, bangkok_accountant, "date_from=2026-10-18&date_to=2026-10-18")
    earlier_financial = earlier["financial"]
    assert isinstance(earlier_financial, dict)
    assert earlier_financial["new_member_revenue"] == "77.00"
    assert earlier["operational"]["check_ins"] == 1


@override_settings(**jwt_settings())
def test_dashboard_reconciles_posted_corrections_and_operational_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    api, gym, owner, owner_headers = _gym("DASHFIN")
    for feature in ("reports", "finance", "members", "memberships", "pt"):
        _enable(gym, feature)
    _zone(gym, "Asia/Bangkok", 1)
    staff = _staff(gym)
    desk = _headers(staff["reception"])
    manager = _headers(staff["manager"])
    accountant = _headers(staff["accountant"])
    plan = _plan(gym, owner)
    long_member = _member(gym, owner, number="M-LONG", name="Long", joined=date(2026, 10, 2))
    window_member = _member(gym, owner, number="M-WIN", name="Window", joined=date(2026, 10, 3))
    week_member = _member(gym, owner, number="M-WEEK", name="Week", joined=date(2026, 9, 1))
    month_member = _member(gym, owner, number="M-MONTH", name="Month", joined=date(2026, 9, 1))
    later_member = _member(gym, owner, number="M-LATER", name="Later", joined=date(2026, 9, 1))
    suspended_member = _member(
        gym, owner, number="M-SUS", name="Suspended", joined=date(2026, 9, 1)
    )
    pending_member = _member(gym, owner, number="M-PEND", name="Pending", joined=date(2026, 9, 1))
    partial_member = _member(gym, owner, number="M-PART", name="Partial", joined=date(2026, 9, 1))
    paid_member = _member(gym, owner, number="M-PAID", name="Paid", joined=date(2026, 9, 1))
    cancelled_member = _member(
        gym, owner, number="M-CAN", name="Cancelled", joined=date(2026, 8, 1)
    )
    overdue_member = _member(gym, owner, number="M-DUE", name="Due", joined=date(2026, 9, 1))
    _member(
        gym,
        owner,
        number="M-LEFT",
        name="Left",
        joined=date(2026, 10, 4),
        status=Member.Status.CANCELLED,
    )
    pt_member = _member(gym, owner, number="M-PT", name="Client", joined=date(2026, 9, 15))
    long_membership = _membership(
        gym,
        owner,
        member=long_member,
        plan=plan,
        start=date(2026, 1, 1),
        expiry=date(2027, 1, 1),
        amount="100.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    _membership(
        gym,
        owner,
        member=window_member,
        plan=plan,
        start=date(2026, 10, 1),
        expiry=date(2026, 10, 22),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
    )
    _membership(
        gym,
        owner,
        member=week_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 26),
        amount="10.00",
        source=Membership.RevenueSource.RENEWAL,
    )
    _membership(
        gym,
        owner,
        member=month_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 11, 18),
        amount="10.00",
        source=Membership.RevenueSource.RENEWAL,
    )
    renewal = _membership(
        gym,
        owner,
        member=later_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 11, 19),
        amount="50.00",
        source=Membership.RevenueSource.RENEWAL,
    )
    _membership(
        gym,
        owner,
        member=suspended_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 20),
        amount="10.00",
        source=Membership.RevenueSource.RENEWAL,
        status=Membership.Status.SUSPENDED,
    )
    _membership(
        gym,
        owner,
        member=pending_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 22),
        amount="10.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        status=Membership.Status.PENDING,
    )
    partial = _membership(
        gym,
        owner,
        member=partial_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 10),
        amount="40.00",
        source=Membership.RevenueSource.NEW_MEMBER,
        payment_status=Membership.PaymentStatus.PARTIAL,
    )
    paid = _membership(
        gym,
        owner,
        member=paid_member,
        plan=plan,
        start=date(2026, 8, 1),
        expiry=date(2026, 10, 5),
        amount="20.00",
        source=Membership.RevenueSource.RENEWAL,
    )
    _membership(
        gym,
        owner,
        member=cancelled_member,
        plan=plan,
        start=date(2026, 8, 1),
        expiry=date(2026, 10, 18),
        amount="15.00",
        source=Membership.RevenueSource.RENEWAL,
        status=Membership.Status.CANCELLED,
        payment_status=Membership.PaymentStatus.UNPAID,
    )
    _membership(
        gym,
        owner,
        member=overdue_member,
        plan=plan,
        start=date(2026, 9, 1),
        expiry=date(2026, 10, 1),
        amount="40.00",
        source=Membership.RevenueSource.RENEWAL,
        payment_status=Membership.PaymentStatus.UNPAID,
    )
    _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="100.00",
            day="2026-10-19",
            member_id=str(long_member.id),
            membership_id=str(long_membership.id),
        ),
    )
    _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="50.00",
            day="2026-10-19",
            member_id=str(later_member.id),
            membership_id=str(renewal.id),
        ),
    )
    _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="10.00",
            day="2026-10-10",
            member_id=str(partial_member.id),
            membership_id=str(partial.id),
        ),
    )
    _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="20.00",
            day="2026-10-05",
            member_id=str(paid_member.id),
            membership_id=str(paid.id),
        ),
    )
    other_id = _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="OTHER",
            amount="40.00",
            day="2026-10-01",
            member_id=str(long_member.id),
        ),
    )
    voided = _command_payment(
        api,
        owner_headers,
        other_id,
        "void",
        {"reason": "Wrong day", "business_date": "2026-10-19"},
    )
    assert voided.status_code == 200, voided.content
    _post_payment(
        api,
        desk,
        manager,
        _payment_body(
            gym,
            purpose="OTHER",
            amount="12.00",
            day="2026-10-19",
            member_id=str(long_member.id),
        ),
    )
    draft = api.post(
        "/api/v1/payments/",
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="999.00",
            day="2026-10-19",
            member_id=str(long_member.id),
            membership_id=str(long_membership.id),
        ),
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("draft"),
        **desk,
    )
    assert draft.status_code == 201, draft.content
    pending_payment = api.post(
        "/api/v1/payments/",
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="77.00",
            day="2026-10-19",
            member_id=str(long_member.id),
            membership_id=str(long_membership.id),
        ),
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("pendpay"),
        **desk,
    )
    assert pending_payment.status_code == 201, pending_payment.content
    pending_payment_id = str(pending_payment.json()["data"]["id"])
    assert _command_payment(api, desk, pending_payment_id, "submit").status_code == 200
    rejected_payment = api.post(
        "/api/v1/payments/",
        _payment_body(
            gym,
            purpose="MEMBERSHIP",
            amount="66.00",
            day="2026-10-19",
            member_id=str(long_member.id),
            membership_id=str(long_membership.id),
        ),
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("rejpay"),
        **desk,
    )
    assert rejected_payment.status_code == 201, rejected_payment.content
    rejected_payment_id = str(rejected_payment.json()["data"]["id"])
    assert _command_payment(api, desk, rejected_payment_id, "submit").status_code == 200
    rejected = _command_payment(
        api,
        manager,
        rejected_payment_id,
        "reject",
        {"rejection_reason": "Duplicate"},
    )
    assert rejected.status_code == 200, rejected.content
    kept = _legacy_class_payment(gym, owner, long_member, amount="12.00", day=TODAY, sequence=1)
    void_class = _legacy_class_payment(
        gym, owner, long_member, amount="15.00", day=TODAY, sequence=2
    )
    _void_legacy(gym, owner, void_class, day=TODAY, sequence=3)
    assert kept.amount == Decimal("12.00")

    trainer = _profile(gym, staff["trainer"], "COACH", "Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=trainer.id).update(
            default_commission_percent=Decimal("10.00")
        )
    assigned = _assign(
        api,
        owner_headers,
        trainer_id=str(trainer.id),
        member_id=str(pt_member.id),
        start="2026-09-01",
    )
    assert assigned.status_code == 201, assigned.content
    posted_sale = api.post(
        "/api/v1/pt-sales/",
        {
            **_sale(str(pt_member.id), str(trainer.id), "2026-10-19", gross_fee="30.00"),
            "payment": {
                "description": "Session fee",
                "amount": "30.00",
                "method": "BANK_TRANSFER",
                "business_date": "2026-10-19",
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("ptsale"),
        **desk,
    )
    assert posted_sale.status_code == 201, posted_sale.content
    posted_sale_id = str(posted_sale.json()["data"]["id"])
    assert (
        api.post(
            f"/api/v1/pt-sales/{posted_sale_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("ptsub"),
            **desk,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/pt-sales/{posted_sale_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("ptapr"),
            **manager,
        ).status_code
        == 200
    )
    pending_sale = api.post(
        "/api/v1/pt-sales/",
        _sale(str(pt_member.id), str(trainer.id), "2026-10-19", gross_fee="8.00"),
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("ptpend"),
        **desk,
    )
    assert pending_sale.status_code == 201, pending_sale.content
    pending_sale_id = str(pending_sale.json()["data"]["id"])
    assert (
        api.post(
            f"/api/v1/pt-sales/{pending_sale_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("ptpendsub"),
            **desk,
        ).status_code
        == 200
    )

    protein = _product(api, owner_headers, sku="PROTEIN", price="10.00", cost="4.00")
    shaker = _product(api, owner_headers, sku="SHAKER", price="5.00", cost="1.00")
    _open_stock(api, owner_headers, protein, "2026-10-16")
    _open_stock(api, owner_headers, shaker, "2026-10-16")
    kept_sale = _sell(
        api,
        desk,
        manager,
        product_id=protein,
        quantity="2.000",
        day="2026-10-16",
        reference="POS-KEEP",
        price_body={},
    )
    void_sale = _sell(
        api,
        desk,
        manager,
        product_id=shaker,
        quantity="1.000",
        day="2026-10-16",
        reference="POS-VOID",
        price_body={},
    )
    kept_detail = api.get(f"/api/v1/retail-sales/{kept_sale}/", **owner_headers)
    assert kept_detail.status_code == 200, kept_detail.content
    assert kept_detail.json()["data"]["total_amount"] == "20.00"
    line_id = kept_detail.json()["data"]["lines"][0]["id"]
    returned = api.post(
        f"/api/v1/retail-sales/{kept_sale}/return/",
        {
            "reason": "One back",
            "business_date": "2026-10-19",
            "lines": [{"line_id": line_id, "quantity": "1.000"}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("return"),
        **owner_headers,
    )
    assert returned.status_code == 200, returned.content
    voided_sale = api.post(
        f"/api/v1/retail-sales/{void_sale}/void/",
        {"reason": "Wrong item", "business_date": "2026-10-18"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("rtlvoid"),
        **owner_headers,
    )
    assert voided_sale.status_code == 200, voided_sale.content
    pending_retail = _create(
        api,
        desk,
        {
            "business_date": "2026-10-19",
            "payment_method": "CARD",
            "reference_number": "POS-PEND",
            "lines": [{"product_id": protein, "quantity": "1.000", "discount": "0.00"}],
        },
    )
    assert pending_retail.status_code == 201, pending_retail.content
    pending_retail_id = str(pending_retail.json()["data"]["id"])
    assert _command(api, desk, pending_retail_id, "submit").status_code == 200

    rent = _category(gym, "RENT")
    _expense(
        api,
        desk,
        manager,
        category_id=str(rent.id),
        amount="100.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    reversed_rent = _expense(
        api,
        desk,
        manager,
        category_id=str(rent.id),
        amount="40.00",
        day="2026-10-01",
        gym=gym,
        post=True,
    )
    rent_void = api.post(
        f"/api/v1/expenses/{reversed_rent}/void/",
        {"reason": "Duplicate bill", "business_date": "2026-10-19"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("expvoid"),
        **owner_headers,
    )
    assert rent_void.status_code == 200, rent_void.content
    snacks = api.post(
        "/api/v1/expense-categories/",
        {"code": "SNACKS", "name": "Snacks"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("snack"),
        **manager,
    )
    assert snacks.status_code == 201, snacks.content
    _expense(
        api,
        desk,
        manager,
        category_id=str(snacks.json()["data"]["id"]),
        amount="7.00",
        day="2026-10-19",
        gym=gym,
        post=True,
    )
    _expense(
        api,
        desk,
        manager,
        category_id=str(rent.id),
        amount="500.00",
        day="2026-10-19",
        gym=gym,
        post=False,
    )
    pending_expense = _expense(
        api,
        desk,
        manager,
        category_id=str(rent.id),
        amount="11.00",
        day="2026-10-19",
        gym=gym,
        post=False,
    )
    assert (
        api.post(
            f"/api/v1/expenses/{pending_expense}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("exppend"),
            **desk,
        ).status_code
        == 200
    )
    rejected_expense = _expense(
        api,
        desk,
        manager,
        category_id=str(rent.id),
        amount="13.00",
        day="2026-10-19",
        gym=gym,
        post=False,
    )
    assert (
        api.post(
            f"/api/v1/expenses/{rejected_expense}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("exprejsub"),
            **desk,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/expenses/{rejected_expense}/reject/",
            {"rejection_reason": "Not ours"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("exprej"),
            **manager,
        ).status_code
        == 200
    )
    _post_salary(
        api,
        gym,
        owner_headers,
        manager,
        [_line(trainer, gym, "BANK_TRANSFER", "80.00")],
        [],
        business_date="2026-10-19",
    )
    pending_period = api.post(
        "/api/v1/salary-periods/",
        {
            "period_start": "2026-10-01",
            "period_end": "2026-10-19",
            "business_date": "2026-10-19",
            "description": "Waiting",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=_key("salpend"),
        **owner_headers,
    )
    assert pending_period.status_code == 201, pending_period.content
    period_id = str(pending_period.json()["data"]["id"])
    assert (
        _put_lines(
            api, owner_headers, period_id, [_line(trainer, gym, "BANK_TRANSFER", "9.00")]
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/salary-periods/{period_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=_key("salpendsub"),
            **owner_headers,
        ).status_code
        == 200
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        post_pt_commission_expense(
            gym=gym,
            approver=staff["manager"],
            created_by_membership_id=staff["reception"].id,
            submitted_by_membership_id=staff["reception"].id,
            business_date=TODAY,
            description="Commission payout",
            amount=Decimal("20.00"),
            currency=gym.currency,
            payment_method=Payment.Method.BANK_TRANSFER,
            cash_session_id=None,
            policy_note="Payout",
            request_id="dash-commission",
            ip_address=None,
            user_agent="pytest",
        )
        CheckIn.objects.create(
            gym=gym,
            member=long_member,
            membership=long_membership,
            check_in_at=datetime(2026, 10, 19, 2, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=gym,
            member=long_member,
            membership=long_membership,
            check_in_at=datetime(2026, 10, 19, 4, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )
        CheckIn.objects.create(
            gym=gym,
            member=long_member,
            membership=long_membership,
            check_in_at=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 9, 30, 13, 0, tzinfo=UTC),
            method=CheckIn.Method.MANUAL,
            recorded_by_membership_id=owner.id,
            created_by_membership_id=owner.id,
            eligibility_snapshot={"eligible": True},
        )

    month = _dashboard(api, accountant, "range=this_month")
    assert month["range"] == {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    operational = month["operational"]
    assert operational["active_members"] == 3
    assert operational["new_members"] == 3
    assert operational["expired_memberships"] == 8
    assert operational["check_ins"] == 2
    assert operational["unique_attendees"] == 1
    assert operational["currently_checked_in"] == 1
    assert operational["expiring_within_3_days"] == 2
    assert operational["expiring_within_7_days"] == 3
    assert operational["expiring_within_30_days"] == 4
    assert operational["pending_approvals"] == 5
    financial = month["financial"]
    assert isinstance(financial, dict)
    assert financial["new_member_revenue"] == "110.00"
    assert financial["renewal_revenue"] == "70.00"
    assert financial["pt_revenue"] == "30.00"
    assert financial["class_revenue"] == "12.00"
    assert financial["product_revenue"] == "10.00"
    assert financial["other_revenue"] == "12.00"
    assert financial["total_revenue"] == "244.00"
    assert financial["pending_payment_member_count"] == 3
    assert financial["pending_payment_amount"] == "85.00"
    assert [item["source"] for item in financial["revenue_sources_ranked"]] == [
        "NEW_MEMBER",
        "RENEWAL",
        "PT",
        "CLASS",
        "OTHER",
        "PRODUCT",
    ]
    assert [item["system_key"] for item in financial["expenses_by_category"]] == [
        code for code, _name in EXPENSE_CATEGORY_CATALOG
    ]
    amounts = {item["system_key"]: item["amount"] for item in financial["expenses_by_category"]}
    assert amounts["RENT"] == "100.00"
    assert amounts["STAFF_SALARY"] == "80.00"
    assert amounts["PT_COMMISSION"] == "20.00"
    assert amounts["UTILITIES"] == amounts["INTERNET"] == amounts["CLEANING_SUPPLIES"] == "0.00"
    assert amounts["MAINTENANCE"] == amounts["MARKETING"] == amounts["WATER_DRINKS"] == "0.00"
    assert amounts["OTHER"] == "0.00"
    assert financial["total_expenses"] == "207.00"
    assert financial["staff_salary_expense"] == "80.00"
    assert financial["pt_commission_expense"] == "20.00"
    assert financial["net_profit"] == "37.00"
    assert financial["top_product_items"] == [
        {
            "product_id": protein,
            "name": "PROTEIN",
            "quantity_sold": "1.000",
            "revenue": "10.00",
            "gross_profit": "6.00",
        }
    ]

    october_first = _dashboard(api, accountant, "date_from=2026-10-01&date_to=2026-10-01")
    first_financial = october_first["financial"]
    assert isinstance(first_financial, dict)
    assert first_financial["other_revenue"] == "40.00"
    assert first_financial["class_revenue"] == "0.00"
    assert {item["system_key"]: item["amount"] for item in first_financial["expenses_by_category"]}[
        "RENT"
    ] == "140.00"
    sale_day = _dashboard(api, accountant, "date_from=2026-10-16&date_to=2026-10-16")
    sale_financial = sale_day["financial"]
    assert isinstance(sale_financial, dict)
    assert sale_financial["product_revenue"] == "25.00"
    assert [item["name"] for item in sale_financial["top_product_items"]] == ["PROTEIN", "SHAKER"]
    assert sale_financial["top_product_items"][0]["quantity_sold"] == "2.000"
    assert sale_financial["top_product_items"][1]["revenue"] == "5.00"
    void_day = _dashboard(api, accountant, "date_from=2026-10-18&date_to=2026-10-18")
    void_financial = void_day["financial"]
    assert isinstance(void_financial, dict)
    assert void_financial["product_revenue"] == "-5.00"
    assert void_financial["top_product_items"] == [
        {
            "product_id": shaker,
            "name": "SHAKER",
            "quantity_sold": "-1.000",
            "revenue": "-5.00",
            "gross_profit": "-4.00",
        }
    ]
    return_day = _dashboard(api, accountant, "range=today")
    return_financial = return_day["financial"]
    assert isinstance(return_financial, dict)
    assert return_financial["product_revenue"] == "-10.00"
    assert return_financial["class_revenue"] == "12.00"
    assert return_financial["other_revenue"] == "-28.00"
    september = _dashboard(api, accountant, "date_from=2026-09-30&date_to=2026-09-30")
    assert september["operational"]["check_ins"] == 1
    assert september["operational"]["expired_memberships"] == 0
