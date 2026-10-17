from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.exceptions import MembershipOverlap
from apps.members.models import Member, Membership, MembershipPlan
from apps.members.services import create_membership, gym_today, inclusive_expiry_date
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership
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

READ_FIELDS = {
    "id",
    "member_id",
    "plan_id",
    "plan_code",
    "plan_name",
    "duration_value",
    "duration_unit",
    "start_date",
    "expiry_date",
    "list_price",
    "discount_amount",
    "final_amount",
    "currency",
    "payment_status",
    "status",
    "note",
    "cancelled_at",
    "cancelled_by_membership_id",
    "cancellation_reason",
    "created_by_membership_id",
    "created_at",
    "updated_at",
}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable_memberships(gym: Gym) -> None:
    feature, _created = Feature.objects.get_or_create(
        code="memberships",
        defaults={"name": "Memberships", "description": "Memberships module"},
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


def _member(api: APIClient, headers: dict[str, str], *, first_name: str) -> str:
    response = api.post(
        "/api/v1/members/",
        {"first_name": first_name, "last_name": "Member", "join_date": "2020-01-01"},
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["data"]["id"])


def _plan(
    api: APIClient,
    headers: dict[str, str],
    *,
    code: str,
    duration_value: int,
    duration_unit: str,
    price: str = "1500.00",
) -> str:
    response = api.post(
        "/api/v1/membership-plans/",
        {
            "code": code,
            "name": code,
            "duration_value": duration_value,
            "duration_unit": duration_unit,
            "price": price,
        },
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["data"]["id"])


def _sale(
    api: APIClient,
    headers: dict[str, str],
    *,
    member_id: str,
    plan_id: str,
    start_date: str,
    discount_amount: str = "0.00",
    note: str = "",
) -> object:
    body: dict[str, object] = {
        "member_id": member_id,
        "plan_id": plan_id,
        "start_date": start_date,
        "discount_amount": discount_amount,
    }
    if note:
        body["note"] = note
    return api.post("/api/v1/memberships/", body, format="json", **headers)


@override_settings(**jwt_settings())
def test_membership_sale_is_scoped_immutable_and_paged() -> None:
    client, platform_access = platform_token("membership-platform@example.test")
    gym_a_payload = provision(
        client,
        platform_access,
        code="SALE-A",
        owner_email="membership-owner-a@example.test",
    )
    gym_b_payload = provision(
        client,
        platform_access,
        code="SALE-B",
        owner_email="membership-owner-b@example.test",
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym_a, email="membership-trainer-a@example.test", role_key="TRAINER")
    accountant = add_staff(
        gym_a, email="membership-accountant-a@example.test", role_key="ACCOUNTANT"
    )
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer))
    accountant_headers = auth_headers(staff_token(accountant))
    _enable_memberships(gym_a)
    _enable_memberships(gym_b)
    api = _client()
    today = gym_today(gym_a)

    denied = api.post(
        "/api/v1/memberships/",
        {
            "member_id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
            "plan_id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
            "start_date": today.isoformat(),
        },
        format="json",
        **trainer_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    accountant_denied = api.post(
        "/api/v1/memberships/",
        {
            "member_id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
            "plan_id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
            "start_date": today.isoformat(),
        },
        format="json",
        **accountant_headers,
    )
    assert accountant_denied.status_code == 403
    assert api.get("/api/v1/memberships/", **accountant_headers).status_code == 200

    member_b = _member(api, other_headers, first_name="Other")
    plan_b = _plan(api, other_headers, code="OTHER", duration_value=30, duration_unit="DAY")
    hidden = _sale(
        api,
        owner_headers,
        member_id=member_b,
        plan_id=plan_b,
        start_date=today.isoformat(),
    )
    assert hidden.status_code == 404
    assert member_b not in hidden.content.decode()

    member_id = _member(api, owner_headers, first_name="Ada")
    month_plan = _plan(api, owner_headers, code="MONTH", duration_value=1, duration_unit="MONTH")
    rejected = api.post(
        "/api/v1/memberships/",
        {
            "member_id": member_id,
            "plan_id": month_plan,
            "start_date": "2026-03-01",
            "gym_id": str(gym_b.id),
            "currency": "USD",
            "final_amount": "1.00",
            "status": "ACTIVE",
            "payment_status": "PAID",
            "plan_code": "HACK",
            "expiry_date": "2026-03-31",
            "created_by_membership_id": str(owner_b.id),
        },
        format="json",
        **owner_headers,
    )
    assert rejected.status_code == 400, rejected.json()
    assert set(rejected.json()["error"]["fields"]) >= {
        "gym_id",
        "currency",
        "final_amount",
        "status",
        "payment_status",
        "plan_code",
        "expiry_date",
        "created_by_membership_id",
    }

    created = _sale(
        api,
        owner_headers,
        member_id=member_id,
        plan_id=month_plan,
        start_date="2026-01-31",
        discount_amount="100.50",
        note="  Desk note  ",
    )
    assert created.status_code == 201, created.json()
    payload = created.json()["data"]
    assert set(payload) == READ_FIELDS
    assert payload["plan_code"] == "MONTH"
    assert payload["plan_name"] == "MONTH"
    assert payload["duration_value"] == 1
    assert payload["duration_unit"] == "MONTH"
    assert payload["start_date"] == "2026-01-31"
    assert payload["expiry_date"] == "2026-02-27"
    assert payload["list_price"] == "1500.00"
    assert payload["discount_amount"] == "100.50"
    assert payload["final_amount"] == "1399.50"
    assert payload["currency"] == "THB"
    assert payload["payment_status"] == "UNPAID"
    assert payload["status"] == "EXPIRED"
    assert payload["note"] == "Desk note"
    assert payload["created_by_membership_id"] == str(owner_a.id)
    assert payload["cancelled_at"] is None
    membership_id = payload["id"]

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        plan = MembershipPlan.objects.get(id=month_plan)
        plan.price = "10.00"
        plan.name = "Renamed"
        plan.save(update_fields=["price", "name", "updated_at"])
    detail = api.get(f"/api/v1/memberships/{membership_id}/", **owner_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["list_price"] == "1500.00"
    assert detail.json()["data"]["plan_name"] == "MONTH"
    assert api.get(f"/api/v1/memberships/{membership_id}/", **other_headers).status_code == 404

    too_large = _sale(
        api,
        owner_headers,
        member_id=_member(api, owner_headers, first_name="Bea"),
        plan_id=month_plan,
        start_date=today.isoformat(),
        discount_amount="1500.01",
    )
    assert too_large.status_code == 400
    assert "discount_amount" in too_large.json()["error"]["fields"]
    full_discount = _sale(
        api,
        owner_headers,
        member_id=_member(api, owner_headers, first_name="Bea"),
        plan_id=month_plan,
        start_date=today.isoformat(),
        discount_amount="10.00",
    )
    assert full_discount.status_code == 201, full_discount.json()
    assert full_discount.json()["data"]["list_price"] == "10.00"
    assert full_discount.json()["data"]["final_amount"] == "0.00"
    assert full_discount.json()["data"]["status"] == "ACTIVE"

    inactive_member = _member(api, owner_headers, first_name="Cara")
    archived = api.post(
        f"/api/v1/members/{inactive_member}/archive/", {}, format="json", **owner_headers
    )
    assert archived.status_code == 200, archived.json()
    inactive_sale = _sale(
        api,
        owner_headers,
        member_id=inactive_member,
        plan_id=month_plan,
        start_date=today.isoformat(),
    )
    assert inactive_sale.status_code == 409
    assert inactive_sale.json()["error"]["code"] == "MEMBERSHIP_STATE_CONFLICT"
    archived_plan = api.post(
        f"/api/v1/membership-plans/{month_plan}/archive/",
        {},
        format="json",
        **owner_headers,
    )
    assert archived_plan.status_code == 200, archived_plan.json()
    day_plan = _plan(api, owner_headers, code="DAY", duration_value=1, duration_unit="DAY")
    inactive_plan_sale = _sale(
        api,
        owner_headers,
        member_id=_member(api, owner_headers, first_name="Dora"),
        plan_id=month_plan,
        start_date=today.isoformat(),
    )
    assert inactive_plan_sale.status_code == 409
    assert inactive_plan_sale.json()["error"]["code"] == "MEMBERSHIP_STATE_CONFLICT"

    first = _sale(
        api,
        owner_headers,
        member_id=_member(api, owner_headers, first_name="Eve"),
        plan_id=day_plan,
        start_date=today.isoformat(),
    )
    assert first.status_code == 201, first.json()
    overlap = _sale(
        api,
        owner_headers,
        member_id=first.json()["data"]["member_id"],
        plan_id=day_plan,
        start_date=today.isoformat(),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "MEMBERSHIP_OVERLAP"
    assert first.json()["data"]["id"] not in overlap.content.decode()

    listed = api.get("/api/v1/memberships/?limit=1", **owner_headers)
    assert listed.status_code == 200, listed.json()
    assert listed.json()["meta"]["has_more"] is True
    cursor = listed.json()["meta"]["next_after"]
    page = api.get(f"/api/v1/memberships/?limit=1&after={cursor}", **owner_headers)
    assert page.status_code == 200, page.json()
    assert page.json()["data"][0]["id"] != listed.json()["data"][0]["id"]
    foreign_cursor = api.get(
        f"/api/v1/memberships/?limit=1&after={cursor}",
        **other_headers,
    )
    assert foreign_cursor.status_code == 400
    filtered_cursor = api.get(
        f"/api/v1/memberships/?limit=1&status=CANCELLED&after={cursor}",
        **owner_headers,
    )
    assert filtered_cursor.status_code == 400
    unknown_query = api.get("/api/v1/memberships/?bucket=today", **owner_headers)
    assert unknown_query.status_code == 400
    assert "bucket" in unknown_query.json()["error"]["fields"]
    missing_member = api.get(
        "/api/v1/memberships/?member_id=7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
        **owner_headers,
    )
    assert missing_member.status_code == 404
    assert "7df4f2d6-79d3-4fd1-b850-968142b1b6dd" not in missing_member.content.decode()
    foreign_member = api.get(f"/api/v1/memberships/?member_id={member_b}", **owner_headers)
    assert foreign_member.status_code == 404
    assert member_b not in foreign_member.content.decode()
    member_filter = api.get(f"/api/v1/memberships/?member_id={member_id}", **owner_headers)
    assert member_filter.status_code == 200, member_filter.json()
    assert {row["member_id"] for row in member_filter.json()["data"]} == {member_id}
    unbound_member_cursor = api.get(
        f"/api/v1/memberships/?limit=1&member_id={member_id}&after={cursor}",
        **owner_headers,
    )
    assert unbound_member_cursor.status_code == 400
    empty_member = _member(api, owner_headers, first_name="Finn")
    empty_page = api.get(f"/api/v1/memberships/?member_id={empty_member}", **owner_headers)
    assert empty_page.status_code == 200, empty_page.json()
    assert empty_page.json()["data"] == []

    patched = api.patch(
        f"/api/v1/memberships/{membership_id}/",
        {"note": "  Updated note  ", "final_amount": "1.00"},
        format="json",
        **owner_headers,
    )
    assert patched.status_code == 400
    assert "final_amount" in patched.json()["error"]["fields"]
    patched = api.patch(
        f"/api/v1/memberships/{membership_id}/",
        {"note": "  Updated note  "},
        format="json",
        **owner_headers,
    )
    assert patched.status_code == 200, patched.json()
    assert patched.json()["data"]["note"] == "Updated note"
    assert patched.json()["data"]["list_price"] == "1500.00"
    assert (
        api.patch(
            f"/api/v1/memberships/{membership_id}/",
            {"note": "Stolen"},
            format="json",
            **other_headers,
        ).status_code
        == 404
    )
    empty_patch = api.patch(
        f"/api/v1/memberships/{membership_id}/", {}, format="json", **owner_headers
    )
    assert empty_patch.status_code == 400

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="memberships").update(
            enabled=False
        )
    assert api.get("/api/v1/memberships/", **owner_headers).status_code == 403
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="memberships").update(
            enabled=True
        )


@override_settings(**jwt_settings())
def test_membership_lifecycle_and_expiry_buckets() -> None:
    client, platform_access = platform_token("lifecycle-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="LIFE-SALE",
        owner_email="lifecycle-sale-owner@example.test",
    )
    other_payload = provision(
        client,
        platform_access,
        code="LIFE-OTHER",
        owner_email="lifecycle-other-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    _enable_memberships(gym)
    _enable_memberships(Gym.objects.get(id=other_payload["gym"]["id"]))
    api = _client()
    today = gym_today(gym)
    plans = {
        days: _plan(
            api,
            headers,
            code=f"D{days}",
            duration_value=days,
            duration_unit="DAY",
            price="100.00",
        )
        for days in (1, 2, 4, 5, 8, 9, 31, 32)
    }
    year_plan = _plan(
        api, headers, code="LEAP", duration_value=1, duration_unit="YEAR", price="100.00"
    )

    def sell(days: int, *, start_offset: int = 0, first_name: str) -> dict[str, object]:
        member_id = _member(api, headers, first_name=first_name)
        response = _sale(
            api,
            headers,
            member_id=member_id,
            plan_id=plans[days],
            start_date=(today + timedelta(days=start_offset)).isoformat(),
        )
        assert response.status_code == 201, response.json()
        body = response.json()["data"]
        assert (
            body["expiry_date"]
            == inclusive_expiry_date(today + timedelta(days=start_offset), days, "DAY").isoformat()
        )
        return body

    expired = sell(1, start_offset=-1, first_name="Expired")
    due_today = sell(1, first_name="Today")
    within_three_start = sell(2, first_name="ThreeStart")
    within_three_end = sell(4, first_name="ThreeEnd")
    within_seven_start = sell(5, first_name="SevenStart")
    within_seven_end = sell(8, first_name="SevenEnd")
    within_thirty_start = sell(9, first_name="ThirtyStart")
    within_thirty_end = sell(31, first_name="ThirtyEnd")
    outside = sell(32, first_name="Outside")
    cancelled = sell(2, first_name="Cancelled")
    pending = sell(1, start_offset=40, first_name="Pending")
    leap = _sale(
        api,
        headers,
        member_id=_member(api, headers, first_name="Leap"),
        plan_id=year_plan,
        start_date="2024-02-29",
    )
    assert leap.status_code == 201, leap.json()
    assert leap.json()["data"]["expiry_date"] == "2025-02-27"
    assert pending["status"] == "PENDING"
    assert expired["status"] == "EXPIRED"

    suspended = api.post(
        f"/api/v1/memberships/{due_today['id']}/suspend/", {}, format="json", **headers
    )
    assert suspended.status_code == 200, suspended.json()
    assert suspended.json()["data"]["status"] == "SUSPENDED"
    repeated_suspend = api.post(
        f"/api/v1/memberships/{due_today['id']}/suspend/", {}, format="json", **headers
    )
    assert repeated_suspend.status_code == 200
    assert repeated_suspend.json()["data"]["updated_at"] == suspended.json()["data"]["updated_at"]
    assert (
        api.post(
            f"/api/v1/memberships/{pending['id']}/suspend/", {}, format="json", **headers
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/api/v1/memberships/{due_today['id']}/suspend/",
            {"status": "SUSPENDED"},
            format="json",
            **headers,
        ).status_code
        == 400
    )

    resumed = api.post(
        f"/api/v1/memberships/{due_today['id']}/resume/", {}, format="json", **headers
    )
    assert resumed.status_code == 200, resumed.json()
    assert resumed.json()["data"]["status"] == "ACTIVE"
    repeated_resume = api.post(
        f"/api/v1/memberships/{due_today['id']}/resume/", {}, format="json", **headers
    )
    assert repeated_resume.status_code == 200
    assert repeated_resume.json()["data"]["updated_at"] == resumed.json()["data"]["updated_at"]

    cancel = api.post(
        f"/api/v1/memberships/{cancelled['id']}/cancel/",
        {"reason": "  Moved away  "},
        format="json",
        **headers,
    )
    assert cancel.status_code == 200, cancel.json()
    assert cancel.json()["data"]["status"] == "CANCELLED"
    assert cancel.json()["data"]["cancellation_reason"] == "Moved away"
    assert cancel.json()["data"]["cancelled_by_membership_id"] == str(owner.id)
    assert cancel.json()["data"]["cancelled_at"]
    repeat = api.post(
        f"/api/v1/memberships/{cancelled['id']}/cancel/",
        {"reason": "Moved away"},
        format="json",
        **headers,
    )
    assert repeat.status_code == 200
    assert repeat.json()["data"]["cancelled_at"] == cancel.json()["data"]["cancelled_at"]
    conflict = api.post(
        f"/api/v1/memberships/{cancelled['id']}/cancel/",
        {"reason": "Different reason"},
        format="json",
        **headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "MEMBERSHIP_STATE_CONFLICT"
    assert (
        api.get(f"/api/v1/memberships/{cancelled['id']}/", **headers).json()["data"][
            "cancellation_reason"
        ]
        == "Moved away"
    )
    blank = api.post(
        f"/api/v1/memberships/{within_three_start['id']}/cancel/",
        {"reason": "   "},
        format="json",
        **headers,
    )
    assert blank.status_code == 400
    expired_cancel = api.post(
        f"/api/v1/memberships/{expired['id']}/cancel/",
        {"reason": "Too late"},
        format="json",
        **headers,
    )
    assert expired_cancel.status_code == 409
    assert expired_cancel.json()["error"]["code"] == "MEMBERSHIP_STATE_CONFLICT"
    for path in ("cancel/", "suspend/", "resume/"):
        assert (
            api.post(
                f"/api/v1/memberships/{cancelled['id']}/{path}",
                {"reason": "Moved away"} if path == "cancel/" else {},
                format="json",
                **other_headers,
            ).status_code
            == 404
        )
    assert api.get(f"/api/v1/memberships/{cancelled['id']}/", **other_headers).status_code == 404
    assert (
        api.patch(
            f"/api/v1/memberships/{cancelled['id']}/",
            {"note": "Stolen"},
            format="json",
            **other_headers,
        ).status_code
        == 404
    )

    suspended_again = api.post(
        f"/api/v1/memberships/{due_today['id']}/suspend/", {}, format="json", **headers
    )
    assert suspended_again.status_code == 200
    overlap = _sale(
        api,
        headers,
        member_id=str(due_today["member_id"]),
        plan_id=plans[1],
        start_date=today.isoformat(),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "MEMBERSHIP_OVERLAP"

    expected = {
        "expired": {expired["id"], leap.json()["data"]["id"]},
        "today": {due_today["id"]},
        "within_3_days": {within_three_start["id"], within_three_end["id"]},
        "within_7_days": {within_seven_start["id"], within_seven_end["id"]},
        "within_30_days": {within_thirty_start["id"], within_thirty_end["id"]},
    }
    seen: set[str] = set()
    for bucket, ids in expected.items():
        response = api.get(f"/api/v1/memberships/expiry/?bucket={bucket}&limit=100", **headers)
        assert response.status_code == 200, response.json()
        found = {row["id"] for row in response.json()["data"]}
        assert found == ids
        assert seen.isdisjoint(found)
        seen.update(found)
    assert cancelled["id"] not in seen
    assert pending["id"] not in seen
    assert outside["id"] not in seen
    missing = api.get("/api/v1/memberships/expiry/", **headers)
    assert missing.status_code == 400
    assert "bucket" in missing.json()["error"]["fields"]
    assert api.get("/api/v1/memberships/expiry/?bucket=soon", **headers).status_code == 400
    other_bucket = api.get("/api/v1/memberships/expiry/?bucket=today", **other_headers)
    assert other_bucket.status_code == 200
    assert other_bucket.json()["data"] == []

    first_page = api.get("/api/v1/memberships/expiry/?bucket=within_3_days&limit=1", **headers)
    assert first_page.status_code == 200, first_page.json()
    assert first_page.json()["meta"]["has_more"] is True
    second_page = api.get(
        "/api/v1/memberships/expiry/?bucket=within_3_days&limit=1"
        f"&after={first_page.json()['meta']['next_after']}",
        **headers,
    )
    assert second_page.status_code == 200, second_page.json()
    paged_ids = {
        first_page.json()["data"][0]["id"],
        second_page.json()["data"][0]["id"],
    }
    assert paged_ids == expected["within_3_days"]
    assert (
        api.get(
            "/api/v1/memberships/expiry/?bucket=today"
            f"&after={first_page.json()['meta']['next_after']}",
            **headers,
        ).status_code
        == 400
    )


@override_settings(**jwt_settings())
def test_concurrent_membership_sales_cannot_overlap() -> None:
    client, platform_access = platform_token("race-platform@example.test")
    payload = provision(
        client, platform_access, code="RACE-SALE", owner_email="race-sale-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    _enable_memberships(gym)
    api = _client()
    today = gym_today(gym)
    member_id = _member(api, headers, first_name="Race")
    plan_id = _plan(api, headers, code="RACE", duration_value=30, duration_unit="DAY")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        plan = MembershipPlan.objects.get(id=plan_id)
    barrier = Barrier(2)
    failures: list[str] = []

    def sell() -> str:
        try:
            barrier.wait(timeout=10)
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                create_membership(
                    gym=gym,
                    actor=owner,
                    member=member,
                    plan=plan,
                    start_date=today,
                    discount_amount=plan.price,
                )
            return "created"
        except MembershipOverlap:
            return "overlap"
        except Exception:
            failures.append(traceback.format_exc())
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(sell), pool.submit(sell))]

    assert not failures, failures
    assert sorted(results) == ["created", "overlap"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            Membership.objects.filter(
                gym=gym,
                member_id=member_id,
                status__in=["PENDING", "ACTIVE", "SUSPENDED"],
            ).count()
            == 1
        )


@override_settings(**jwt_settings())
def test_membership_database_guards_reject_invalid_rows() -> None:
    client, platform_access = platform_token("guard-platform@example.test")
    payload = provision(
        client, platform_access, code="GUARD-A", owner_email="guard-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="GUARD-B", owner_email="guard-owner-b@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    _enable_memberships(gym)
    _enable_memberships(Gym.objects.get(id=other_payload["gym"]["id"]))
    api = _client()
    today = gym_today(gym).isoformat()
    member_id = _member(api, headers, first_name="Guard")
    other_member = _member(api, other_headers, first_name="Foreign")
    plan_id = _plan(api, headers, code="GUARD", duration_value=30, duration_unit="DAY")
    created = _sale(
        api,
        headers,
        member_id=member_id,
        plan_id=plan_id,
        start_date=today,
        discount_amount="0.00",
    )
    assert created.status_code == 201, created.json()
    membership_id = created.json()["data"]["id"]

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'members_membership'::regclass
            """
        )
        names = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = 'members_membership'
            """
        )
        indexes = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'members_membership'::regclass
              AND conname = 'membership_no_active_overlap_excl'
            """
        )
        exclusion = cursor.fetchone()[0]

    assert {
        "membership_list_nonnegative_ck",
        "membership_discount_nonnegative_ck",
        "membership_final_nonnegative_ck",
        "membership_discount_within_list_ck",
        "membership_total_exact_ck",
        "membership_dates_valid_ck",
        "membership_duration_positive_ck",
        "membership_cancellation_state_ck",
        "membership_note_length_ck",
        "membership_reason_length_ck",
        "membership_creator_same_gym_fk",
        "membership_canceller_same_gym_fk",
        "membership_member_same_gym_fk",
        "membership_plan_same_gym_fk",
    } <= names
    assert "SUSPENDED" in exclusion
    for name in (
        "membership_gym_expiry_idx",
        "membership_member_status_idx",
        "membership_status_created_idx",
        "membership_gym_plan_idx",
        "membership_gym_creator_idx",
        "membership_gym_canceller_idx",
    ):
        assert indexes[name].startswith("CREATE INDEX")
        assert "gym_id" in indexes[name]

    def insert(**overrides: object) -> None:
        values: dict[str, object] = {
            "gym_id": gym.id,
            "member_id": member_id,
            "plan_id": plan_id,
            "plan_code": "GUARD",
            "plan_name": "GUARD",
            "duration_value": 30,
            "duration_unit": "DAY",
            "start_date": "2030-01-01",
            "expiry_date": "2030-01-30",
            "list_price": "100.00",
            "discount_amount": "0.00",
            "final_amount": "100.00",
            "currency": "THB",
            "payment_status": "UNPAID",
            "status": "EXPIRED",
            "revenue_source": "NEW_MEMBER",
            "note": "",
            "cancellation_reason": "",
            "created_by_membership_id": owner.id,
            "cancelled_by_membership_id": None,
            "cancelled_at": None,
        }
        values.update(overrides)
        columns = list(values)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO members_membership (
                    id, created_at, updated_at, {", ".join(columns)}
                )
                VALUES (
                    gen_random_uuid(), now(), now(),
                    {", ".join(["%s"] * len(columns))}
                )
                """,
                [values[column] for column in columns],
            )

    def rejected_insert(**overrides: object) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            set_gym_context(gym_id=gym.id)
            insert(**overrides)

    rejected_insert(discount_amount="-1.00", final_amount="101.00")
    rejected_insert(final_amount="90.00")
    rejected_insert(status="ACTIVE", cancelled_at="2026-10-17T00:00:00Z")
    rejected_insert(member_id=other_member)
    rejected_insert(created_by_membership_id=other_owner.id)
    rejected_insert(
        status="CANCELLED",
        cancelled_at="2026-10-17T00:00:00Z",
        cancelled_by_membership_id=other_owner.id,
        cancellation_reason="Foreign actor",
    )
    rejected_insert(status="SUSPENDED", start_date=today, expiry_date=today)

    with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE members_membership SET plan_code = %s WHERE id = %s",
                ["CHANGED", membership_id],
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE members_membership SET note = %s WHERE id = %s",
                ["Database note", membership_id],
            )
            cursor.execute(
                "SELECT note, plan_code FROM members_membership WHERE id = %s",
                [membership_id],
            )
            assert cursor.fetchone() == ("Database note", "GUARD")
