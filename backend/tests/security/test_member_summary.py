from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.members.services import gym_today
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from apps.users.models import User
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
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

PAYMENT_FIELDS = {
    "available",
    "currency",
    "total_charged",
    "total_refunded",
    "net_paid",
    "outstanding_membership_balance",
}
HIGH_ID = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
LOW_ID = UUID("00000000-0000-4000-8000-000000000001")
MID_ID = UUID("10000000-0000-4000-8000-000000000001")
LATE = datetime(2024, 6, 2, tzinfo=UTC)
EARLY = datetime(2024, 6, 1, tzinfo=UTC)


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _summary(api: APIClient, member_id: object, headers: dict[str, str], query: str = ""):
    return api.get(f"/api/v1/members/{member_id}/summary/{query}", **headers)


def _staff_without_member_view(gym: Gym, owner: GymMembership, email: str) -> GymMembership:
    user = User.objects.create_user(email, PASSWORD)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
            activated_at=timezone.now(),
        )
        role = Role.objects.create(
            gym=gym,
            key="FLOOR",
            name="Floor",
            created_by_membership_id=owner.id,
        )
        RolePermission.objects.create(
            gym=gym,
            role=role,
            permission=Permission.objects.get(code="gym.view"),
            created_by_membership_id=owner.id,
        )
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=membership,
            role=role,
            created_by_membership_id=owner.id,
        )
    membership.user = user
    return membership


def _member(gym: Gym, actor_id: object, *, number: str, first_name: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.create(
            gym=gym,
            member_number=number,
            first_name=first_name,
            join_date=date(2020, 1, 1),
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _plan(gym: Gym, actor_id: object) -> MembershipPlan:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return MembershipPlan.objects.create(
            gym=gym,
            code="SUM-DAY",
            name="Summary Day",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.DAY,
            price=Decimal("100.00"),
            currency=gym.currency,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _membership(
    gym: Gym,
    actor_id: object,
    *,
    member: Member,
    plan: MembershipPlan,
    start: date,
    expiry: date,
    status: str,
    final_amount: str = "100.00",
    membership_id: UUID | None = None,
    cancelled: bool = False,
    created_at: datetime | None = None,
) -> Membership:
    values: dict[str, object] = {
        "gym": gym,
        "member": member,
        "plan": plan,
        "plan_code": plan.code,
        "plan_name": plan.name,
        "duration_value": 1,
        "duration_unit": plan.duration_unit,
        "start_date": start,
        "expiry_date": expiry,
        "list_price": Decimal(final_amount),
        "discount_amount": Decimal("0.00"),
        "final_amount": Decimal(final_amount),
        "currency": gym.currency,
        "status": status,
        "revenue_source": Membership.RevenueSource.NEW_MEMBER,
        "created_by_membership_id": actor_id,
    }
    if membership_id is not None:
        values["id"] = membership_id
    if cancelled:
        values.update(
            cancelled_at=timezone.now(),
            cancelled_by_membership_id=actor_id,
            cancellation_reason="Closed",
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        if created_at is None:
            return Membership.objects.create(**values)
        created = Membership._meta.get_field("created_at")
        updated = Membership._meta.get_field("updated_at")
        previous = (created.auto_now_add, updated.auto_now)
        created.auto_now_add = False
        updated.auto_now = False
        values["created_at"] = created_at
        values["updated_at"] = created_at
        try:
            return Membership.objects.create(**values)
        finally:
            created.auto_now_add = previous[0]
            updated.auto_now = previous[1]


def _payment(
    gym: Gym,
    actor_id: object,
    *,
    member: Member,
    membership: Membership | None,
    entry_type: str,
    status: str,
    amount: str,
) -> Payment:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Payment.objects.create(
            gym=gym,
            member=member,
            membership=membership,
            entry_type=entry_type,
            purpose=Payment.Purpose.MEMBERSHIP,
            description="do-not-leak-description",
            amount=Decimal(amount),
            currency=gym.currency,
            method=Payment.Method.CASH,
            business_date=date(2026, 1, 15),
            received_at=timezone.now(),
            reference_number="REF-SECRET",
            status=status,
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _zone_ahead_of_utc() -> str:
    hour = timezone.now().astimezone(UTC).hour
    if hour >= 10:
        return "Pacific/Kiritimati"
    return "Pacific/Pago_Pago"


@override_settings(**jwt_settings())
def test_member_summary_is_scoped_permissioned_and_query_strict() -> None:
    client, platform_access = platform_token("summary-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="SUM-A", owner_email="summary-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="SUM-B", owner_email="summary-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym_a, email="summary-trainer@example.test", role_key="TRAINER")
    floor = _staff_without_member_view(gym_a, owner_a, "summary-floor@example.test")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer))
    floor_headers = auth_headers(staff_token(floor))
    api = _client()
    member = _member(gym_a, owner_a.id, number="SUM-000001", first_name="Summary Secret")

    denied = _summary(api, member.id, floor_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

    allowed = _summary(api, member.id, trainer_headers)
    assert allowed.status_code == 200, allowed.json()
    data = allowed.json()["data"]
    assert data["member"]["id"] == str(member.id)
    assert data["member"]["is_pt_client"] is False
    assert data["is_pt_client"] is False
    assert data["current_membership"] is None
    assert data["days_remaining"] is None
    assert data["membership_history"] == []
    assert data["membership_history_has_more"] is False
    assert data["check_in_summary"] == {"available": False}
    assert set(data["payment_summary"]) == PAYMENT_FIELDS
    assert data["payment_summary"]["available"] is True
    assert data["payment_summary"]["currency"] == gym_a.currency
    assert data["payment_summary"]["total_charged"] == "0.00"
    assert data["payment_summary"]["total_refunded"] == "0.00"
    assert data["payment_summary"]["net_paid"] == "0.00"
    assert data["payment_summary"]["outstanding_membership_balance"] == "0.00"
    assert "payment_history" not in data
    assert "gym_id" not in data["member"]

    hidden = _summary(api, member.id, other_headers)
    assert hidden.status_code == 404
    assert "Summary Secret" not in hidden.content.decode()

    for query in ("?gym_id=" + str(gym_b_payload["gym"]["id"]), "?limit=1", "?foo=1"):
        rejected = _summary(api, member.id, owner_headers, query)
        assert rejected.status_code == 400, rejected.json()
        assert rejected.json()["success"] is False
        assert rejected.json()["error"]["fields"]

    gym_rejected = _summary(api, member.id, owner_headers, "?gym_id=" + str(gym_a.id))
    assert gym_rejected.json()["error"]["fields"]["gym_id"] == ["This field is not allowed."]

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="members").update(
            enabled=False
        )
    blocked = _summary(api, member.id, owner_headers)
    assert blocked.status_code == 403
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="members").update(
            enabled=True
        )
    restored = _summary(api, member.id, owner_headers)
    assert restored.status_code == 200, restored.json()


@override_settings(**jwt_settings())
def test_member_summary_dates_history_balances_and_query_budget() -> None:
    client, platform_access = platform_token("summary-calc-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="SUM-C",
        owner_email="summary-calc-owner@example.test",
    )
    other_payload = provision(
        client,
        platform_access,
        code="SUM-D",
        owner_email="summary-calc-other@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    api = _client()
    gym.timezone = _zone_ahead_of_utc()
    gym.save(update_fields=["timezone", "updated_at"])
    local = gym_today(gym)
    utc_day = timezone.now().astimezone(UTC).date()
    assert local != utc_day
    plan = _plan(gym, owner.id)
    other_plan = _plan(other_gym, other_owner.id)

    dated = _member(gym, owner.id, number="SUM-DATE", first_name="Dated")
    current = _membership(
        gym,
        owner.id,
        member=dated,
        plan=plan,
        start=local,
        expiry=local,
        status=Membership.Status.SUSPENDED,
    )
    _membership(
        gym,
        owner.id,
        member=dated,
        plan=plan,
        start=local,
        expiry=local,
        status=Membership.Status.CANCELLED,
        cancelled=True,
    )
    past = _membership(
        gym,
        owner.id,
        member=dated,
        plan=plan,
        start=local - timedelta(days=1),
        expiry=local - timedelta(days=1),
        status=Membership.Status.ACTIVE,
    )
    future = _membership(
        gym,
        owner.id,
        member=dated,
        plan=plan,
        start=local + timedelta(days=1),
        expiry=local + timedelta(days=1),
        status=Membership.Status.PENDING,
    )
    spanned = _member(gym, owner.id, number="SUM-SPAN", first_name="Spanned")
    span = _membership(
        gym,
        owner.id,
        member=spanned,
        plan=plan,
        start=local,
        expiry=local + timedelta(days=2),
        status=Membership.Status.ACTIVE,
        final_amount="30.00",
    )

    dated_summary = _summary(api, dated.id, headers)
    assert dated_summary.status_code == 200, dated_summary.json()
    dated_data = dated_summary.json()["data"]
    assert dated_data["current_membership"]["id"] == str(current.id)
    assert dated_data["current_membership"]["status"] == "SUSPENDED"
    assert dated_data["days_remaining"] == 1
    utc_membership = past if past.start_date == utc_day else future
    assert utc_membership.start_date == utc_day
    assert dated_data["current_membership"]["id"] != str(utc_membership.id)

    span_summary = _summary(api, spanned.id, headers)
    assert span_summary.status_code == 200, span_summary.json()
    assert span_summary.json()["data"]["current_membership"]["id"] == str(span.id)
    assert span_summary.json()["data"]["days_remaining"] == 3

    history_member = _member(gym, owner.id, number="SUM-HIST", first_name="History")
    stored: list[Membership] = []
    for index in range(18):
        start = date(2010, 1, 1) + timedelta(days=index)
        stored.append(
            _membership(
                gym,
                owner.id,
                member=history_member,
                plan=plan,
                start=start,
                expiry=start,
                status=Membership.Status.EXPIRED,
            )
        )
    shared = date(2024, 6, 1)
    tie_rows = (
        (HIGH_ID, LATE),
        (LOW_ID, LATE),
        (MID_ID, EARLY),
    )
    for membership_id, created_at in tie_rows:
        stored.append(
            _membership(
                gym,
                owner.id,
                member=history_member,
                plan=plan,
                start=shared,
                expiry=shared,
                status=Membership.Status.EXPIRED,
                membership_id=membership_id,
                created_at=created_at,
            )
        )
    expected = sorted(
        stored, key=lambda row: (row.start_date, row.created_at, row.id), reverse=True
    )
    assert [row.id for row in expected[:3]] == [HIGH_ID, LOW_ID, MID_ID]

    finance = _member(gym, owner.id, number="SUM-PAY", first_name="Finance")
    active = _membership(
        gym,
        owner.id,
        member=finance,
        plan=plan,
        start=date(2020, 1, 1),
        expiry=date(2020, 1, 31),
        status=Membership.Status.EXPIRED,
        final_amount="100.00",
    )
    overpaid = _membership(
        gym,
        owner.id,
        member=finance,
        plan=plan,
        start=date(2020, 3, 1),
        expiry=date(2020, 3, 31),
        status=Membership.Status.EXPIRED,
        final_amount="50.00",
    )
    cancelled = _membership(
        gym,
        owner.id,
        member=finance,
        plan=plan,
        start=date(2020, 4, 1),
        expiry=date(2020, 4, 30),
        status=Membership.Status.CANCELLED,
        final_amount="200.00",
        cancelled=True,
    )
    _membership(
        gym,
        owner.id,
        member=finance,
        plan=plan,
        start=date(2021, 1, 1),
        expiry=date(2021, 1, 31),
        status=Membership.Status.EXPIRED,
        final_amount="10.00",
    )
    refund_heavy = _membership(
        gym,
        owner.id,
        member=finance,
        plan=plan,
        start=date(2022, 1, 1),
        expiry=date(2022, 1, 31),
        status=Membership.Status.EXPIRED,
        final_amount="15.00",
    )
    ledger = (
        (active, Payment.EntryType.CHARGE, Payment.Status.POSTED, "40.00"),
        (active, Payment.EntryType.REFUND, Payment.Status.POSTED, "10.00"),
        (active, Payment.EntryType.CHARGE, Payment.Status.VOID, "5.00"),
        (active, Payment.EntryType.REVERSAL, Payment.Status.POSTED, "5.00"),
        (active, Payment.EntryType.CHARGE, Payment.Status.DRAFT, "999.00"),
        (active, Payment.EntryType.CHARGE, Payment.Status.PENDING_APPROVAL, "888.00"),
        (active, Payment.EntryType.CHARGE, Payment.Status.REJECTED, "777.00"),
        (active, Payment.EntryType.CHARGE, Payment.Status.CANCELLED, "666.00"),
        (active, Payment.EntryType.REFUND, Payment.Status.REJECTED, "50.00"),
        (overpaid, Payment.EntryType.CHARGE, Payment.Status.POSTED, "80.00"),
        (cancelled, Payment.EntryType.CHARGE, Payment.Status.POSTED, "200.00"),
        (refund_heavy, Payment.EntryType.CHARGE, Payment.Status.POSTED, "10.00"),
        (refund_heavy, Payment.EntryType.REFUND, Payment.Status.POSTED, "12.00"),
        (None, Payment.EntryType.CHARGE, Payment.Status.POSTED, "25.00"),
        (None, Payment.EntryType.CHARGE, Payment.Status.VOID, "7.00"),
        (None, Payment.EntryType.REFUND, Payment.Status.POSTED, "3.00"),
        (None, Payment.EntryType.REFUND, Payment.Status.VOID, "2.00"),
        (None, Payment.EntryType.REFUND, Payment.Status.POSTED, "400.00"),
    )
    posted_charge = None
    for membership, entry_type, status, amount in ledger:
        payment = _payment(
            gym,
            owner.id,
            member=finance,
            membership=membership,
            entry_type=entry_type,
            status=status,
            amount=amount,
        )
        if (
            posted_charge is None
            and status == Payment.Status.POSTED
            and entry_type == Payment.EntryType.CHARGE
        ):
            posted_charge = payment
    assert posted_charge is not None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Receipt.objects.create(
            gym=gym,
            payment=posted_charge,
            receipt_number="RCPT-SECRET",
            issued_at=timezone.now(),
            member_number=finance.member_number,
            member_name="Finance Secret",
            description="do-not-leak-description",
            amount=posted_charge.amount,
            currency=gym.currency,
            method=posted_charge.method,
            business_date=posted_charge.business_date,
            issuer_name="Actor Secret",
            created_by_membership_id=owner.id,
        )
    bystander = _member(gym, owner.id, number="SUM-OTHER", first_name="Bystander")
    bystander_membership = _membership(
        gym,
        owner.id,
        member=bystander,
        plan=plan,
        start=date(2019, 1, 1),
        expiry=date(2019, 1, 31),
        status=Membership.Status.EXPIRED,
        final_amount="1000.00",
    )
    _payment(
        gym,
        owner.id,
        member=bystander,
        membership=bystander_membership,
        entry_type=Payment.EntryType.CHARGE,
        status=Payment.Status.POSTED,
        amount="1000.00",
    )
    foreign = _member(other_gym, other_owner.id, number="SUM-FOREIGN", first_name="Foreign")
    foreign_membership = _membership(
        other_gym,
        other_owner.id,
        member=foreign,
        plan=other_plan,
        start=date(2019, 2, 1),
        expiry=date(2019, 2, 28),
        status=Membership.Status.EXPIRED,
        final_amount="5000.00",
    )
    _payment(
        other_gym,
        other_owner.id,
        member=foreign,
        membership=foreign_membership,
        entry_type=Payment.EntryType.CHARGE,
        status=Payment.Status.POSTED,
        amount="5000.00",
    )

    finance_summary = _summary(api, finance.id, headers)
    assert finance_summary.status_code == 200, finance_summary.json()
    rendered = finance_summary.content.decode()
    payment_summary = finance_summary.json()["data"]["payment_summary"]
    assert payment_summary == {
        "available": True,
        "currency": gym.currency,
        "total_charged": "367.00",
        "total_refunded": "432.00",
        "net_paid": "0.00",
        "outstanding_membership_balance": "97.00",
    }
    for secret in ("do-not-leak-description", "REF-SECRET", "RCPT-SECRET", "Actor Secret"):
        assert secret not in rendered
    assert "payment_history" not in finance_summary.json()["data"]

    for _index in range(30):
        _payment(
            gym,
            owner.id,
            member=history_member,
            membership=stored[0],
            entry_type=Payment.EntryType.CHARGE,
            status=Payment.Status.POSTED,
            amount="1.00",
        )
    small = _member(gym, owner.id, number="SUM-SMALL", first_name="Small")
    small_membership = _membership(
        gym,
        owner.id,
        member=small,
        plan=plan,
        start=date(2018, 1, 1),
        expiry=date(2018, 1, 2),
        status=Membership.Status.EXPIRED,
    )
    _payment(
        gym,
        owner.id,
        member=small,
        membership=small_membership,
        entry_type=Payment.EntryType.CHARGE,
        status=Payment.Status.POSTED,
        amount="1.00",
    )

    def measured(member_id: object) -> list[dict[str, object]]:
        with CaptureQueriesContext(connection) as captured:
            response = _summary(api, member_id, headers)
        assert response.status_code == 200, response.json()
        return list(captured)

    measured(small.id)
    small_queries = measured(small.id)
    large_queries = measured(history_member.id)
    assert len(small_queries) == len(large_queries)
    history_body = _summary(api, history_member.id, headers).json()["data"]
    assert [row["id"] for row in history_body["membership_history"]] == [
        str(row.id) for row in expected[:20]
    ]
    assert history_body["membership_history_has_more"] is True
    assert str(expected[20].id) not in {row["id"] for row in history_body["membership_history"]}
    assert history_body["current_membership"] is None
    assert history_body["days_remaining"] is None
    payment_sql = [
        str(query["sql"]).lower()
        for query in large_queries
        if "members_payment" in str(query["sql"]).lower()
    ]
    assert len(payment_sql) == 2
    assert all("sum(" in sql for sql in payment_sql)
    combined = " ".join(
        f"{query['sql']} {query.get('params', '')}".lower() for query in large_queries
    )
    assert "limit 21" in combined or ("limit %s" in combined and "21" in combined)
