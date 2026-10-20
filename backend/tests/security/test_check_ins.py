from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.checkin_services import create_manual_check_in
from apps.members.exceptions import CheckInOpen
from apps.members.models import CheckIn, Membership, Payment
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, GymSettings
from tests.security.test_memberships import _enable_memberships, _member, _plan, _sale
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
    "member",
    "membership_id",
    "check_in_at",
    "check_out_at",
    "method",
    "status",
    "note",
    "eligibility",
}
MEMBER_FIELDS = {"id", "member_number", "first_name", "last_name"}
ELIGIBILITY_FIELDS = {
    "eligible",
    "backdated",
    "membership_id",
    "membership_status",
    "plan_code",
    "plan_name",
    "start_date",
    "expiry_date",
    "evaluated_on",
}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _window(gym: Gym, seconds: int) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(check_in_duplicate_window_seconds=seconds)


@override_settings(**jwt_settings())
def test_manual_check_in_checkout_history_and_summary() -> None:
    client, platform_access = platform_token("checkin-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="CHK-A", owner_email="checkin-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="CHK-B", owner_email="checkin-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym_a, email="checkin-trainer-a@example.test", role_key="TRAINER")
    receptionist = add_staff(
        gym_a, email="checkin-reception-a@example.test", role_key="RECEPTIONIST"
    )
    accountant = add_staff(gym_a, email="checkin-accountant-a@example.test", role_key="ACCOUNTANT")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    _enable_memberships(gym_a)
    _enable_memberships(gym_b)
    api = _client()
    backdated_at = timezone.now() - timedelta(hours=1)
    covered_on = min(
        gym_today(gym_a),
        backdated_at.astimezone(ZoneInfo(gym_a.timezone)).date(),
    )
    today = covered_on.isoformat()

    assert api.get("/api/v1/check-ins/", **accountant_headers).status_code == 403
    trainer_list = api.get("/api/v1/check-ins/", **trainer_headers)
    assert trainer_list.status_code == 200
    assert trainer_list.json()["data"] == []
    backdate_denied = api.post(
        "/api/v1/check-ins/",
        {"member_id": str(uuid4()), "occurred_at": timezone.now().isoformat()},
        format="json",
        **trainer_headers,
    )
    assert backdate_denied.status_code == 403
    assert backdate_denied.json()["error"]["code"] == "PERMISSION_DENIED"

    member_b = _member(api, other_headers, first_name="Other")
    hidden = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_b},
        format="json",
        **owner_headers,
    )
    assert hidden.status_code == 404

    member_id = _member(api, owner_headers, first_name="Ada")
    plan_id = _plan(api, owner_headers, code="DAY", duration_value=30, duration_unit="DAY")
    sale = _sale(api, owner_headers, member_id=member_id, plan_id=plan_id, start_date=today)
    assert sale.status_code == 201, sale.json()
    membership_id = sale.json()["data"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert Payment.objects.filter(member_id=member_id).count() == 0

    rejected = api.post(
        "/api/v1/check-ins/",
        {
            "member_id": member_id,
            "gym_id": str(gym_b.id),
            "membership_id": membership_id,
            "method": "QR",
            "status": "OPEN",
            "source_metadata": {"channel": "desk"},
            "device_id": str(uuid4()),
            "check_out_at": timezone.now().isoformat(),
        },
        format="json",
        **owner_headers,
    )
    assert rejected.status_code == 400
    assert set(rejected.json()["error"]["fields"]) >= {
        "gym_id",
        "membership_id",
        "method",
        "status",
        "source_metadata",
        "device_id",
        "check_out_at",
    }

    created = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "note": "  Front desk  "},
        format="json",
        HTTP_IDEMPOTENCY_KEY="check-in-retry-key-001",
        **reception_headers,
    )
    assert created.status_code == 201, created.json()
    body = created.json()["data"]
    assert set(body) == READ_FIELDS
    assert set(body["member"]) == MEMBER_FIELDS
    assert set(body["eligibility"]) == ELIGIBILITY_FIELDS
    assert body["member"]["id"] == member_id
    assert body["membership_id"] == membership_id
    assert body["method"] == "MANUAL"
    assert body["status"] == "OPEN"
    assert body["check_out_at"] is None
    assert body["note"] == "Front desk"
    assert body["eligibility"]["eligible"] is True
    assert body["eligibility"]["backdated"] is False
    assert body["eligibility"]["membership_status"] == "ACTIVE"
    assert body["eligibility"]["plan_code"] == "DAY"
    check_in_id = body["id"]
    replay = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "note": "Front desk"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="check-in-retry-key-001",
        **reception_headers,
    )
    assert replay.status_code == 200, replay.json()
    assert replay.json()["data"]["id"] == check_in_id
    reused = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "note": "Different"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="check-in-retry-key-001",
        **owner_headers,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    invalid_key = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="short",
        **owner_headers,
    )
    assert invalid_key.status_code == 400

    duplicate = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id},
        format="json",
        **owner_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CHECK_IN_OPEN"
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        stored = CheckIn.objects.get(id=check_in_id)
        assert stored.method == "MANUAL"
        assert stored.device_id is None
        assert stored.source_metadata is None
        assert stored.created_by_membership_id == receptionist.id
        assert Payment.objects.filter(member_id=member_id).count() == 0
        assert Membership.objects.get(id=membership_id).payment_status == "UNPAID"
        recorded = AuditLog.objects.filter(target_id=check_in_id, action="CHECK_IN_RECORDED")
        assert recorded.count() == 1
        audit = recorded.get()
        assert "note" not in audit.new_values
        assert "Front desk" not in str(audit.new_values)

    summary = api.get(f"/api/v1/members/{member_id}/summary/", **owner_headers)
    assert summary.status_code == 200, summary.json()
    check_in_summary = summary.json()["data"]["check_in_summary"]
    assert check_in_summary["available"] is True
    assert check_in_summary["currently_checked_in"] is True
    assert check_in_summary["open_check_in_id"] == check_in_id
    assert check_in_summary["last_check_in_at"] == body["check_in_at"]
    assert check_in_summary["last_check_out_at"] is None

    detail_hidden = api.get(f"/api/v1/check-ins/{check_in_id}/", **other_headers)
    assert detail_hidden.status_code == 404
    listed = api.get(f"/api/v1/check-ins/?open=true&member_id={member_id}", **owner_headers)
    assert listed.status_code == 200, listed.json()
    assert [row["id"] for row in listed.json()["data"]] == [check_in_id]
    assert api.get(f"/api/v1/check-ins/?member_id={member_b}", **owner_headers).status_code == 404
    assert (
        api.get(f"/api/v1/members/{member_id}/check-ins/", **accountant_headers).status_code == 403
    )
    history = api.get(f"/api/v1/members/{member_id}/check-ins/", **owner_headers)
    assert history.status_code == 200, history.json()
    assert history.json()["data"][0]["id"] == check_in_id
    assert api.get(f"/api/v1/members/{member_b}/check-ins/", **owner_headers).status_code == 404

    checked_out = api.post(
        f"/api/v1/check-ins/{check_in_id}/check-out/",
        {},
        format="json",
        **reception_headers,
    )
    assert checked_out.status_code == 200, checked_out.json()
    assert checked_out.json()["data"]["status"] == "CHECKED_OUT"
    checkout_at = checked_out.json()["data"]["check_out_at"]
    repeated = api.post(
        f"/api/v1/check-ins/{check_in_id}/check-out/",
        {},
        format="json",
        **owner_headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["check_out_at"] == checkout_at
    conflict = api.post(
        f"/api/v1/check-ins/{check_in_id}/check-out/",
        {"occurred_at": (timezone.now() + timedelta(seconds=10)).isoformat()},
        format="json",
        **owner_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CHECK_IN_STATE_CONFLICT"
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        assert (
            AuditLog.objects.filter(target_id=check_in_id, action="CHECK_IN_CHECKED_OUT").count()
            == 1
        )

    too_soon = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id},
        format="json",
        **owner_headers,
    )
    assert too_soon.status_code == 409
    assert too_soon.json()["error"]["code"] == "CHECK_IN_DUPLICATE"
    _window(gym_a, 0)
    again = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "note": "Return"},
        format="json",
        **owner_headers,
    )
    assert again.status_code == 201, again.json()
    second_id = again.json()["data"]["id"]
    late_checkout = api.post(
        f"/api/v1/check-ins/{second_id}/check-out/",
        {"occurred_at": (timezone.now() - timedelta(hours=25)).isoformat()},
        format="json",
        **owner_headers,
    )
    assert late_checkout.status_code == 400, late_checkout.json()
    assert "24 hours" in late_checkout.json()["error"]["fields"]["occurred_at"][0]
    skewed_checkout = api.post(
        f"/api/v1/check-ins/{second_id}/check-out/",
        {"occurred_at": (timezone.now() + timedelta(minutes=2)).isoformat()},
        format="json",
        **owner_headers,
    )
    assert skewed_checkout.status_code == 400, skewed_checkout.json()
    assert "future" in skewed_checkout.json()["error"]["fields"]["occurred_at"][0]
    still_open = api.get(f"/api/v1/check-ins/{second_id}/", **owner_headers)
    assert still_open.status_code == 200, still_open.json()
    assert still_open.json()["data"]["status"] == "OPEN"
    page = api.get("/api/v1/check-ins/?limit=1", **owner_headers)
    assert page.status_code == 200, page.json()
    assert page.json()["meta"]["has_more"] is True
    follow = api.get(
        f"/api/v1/check-ins/?limit=1&after={page.json()['meta']['next_after']}",
        **owner_headers,
    )
    assert follow.status_code == 200, follow.json()
    assert {page.json()["data"][0]["id"], follow.json()["data"][0]["id"]} == {
        check_in_id,
        second_id,
    }
    stolen_cursor = api.get(
        f"/api/v1/check-ins/?limit=1&after={page.json()['meta']['next_after']}",
        **other_headers,
    )
    assert stolen_cursor.status_code == 400

    future = api.post(
        "/api/v1/check-ins/",
        {
            "member_id": member_id,
            "occurred_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
        },
        format="json",
        **owner_headers,
    )
    assert future.status_code == 400
    old = api.post(
        "/api/v1/check-ins/",
        {
            "member_id": member_id,
            "occurred_at": (timezone.now() - timedelta(hours=25)).isoformat(),
        },
        format="json",
        **owner_headers,
    )
    assert old.status_code == 400
    naive = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "occurred_at": "2026-10-17T09:00:00"},
        format="json",
        **owner_headers,
    )
    assert naive.status_code == 400
    backdated = api.post(
        "/api/v1/check-ins/",
        {
            "member_id": member_id,
            "occurred_at": backdated_at.isoformat(),
        },
        format="json",
        **owner_headers,
    )
    assert backdated.status_code == 409
    assert backdated.json()["error"]["code"] == "CHECK_IN_OPEN"
    api.post(f"/api/v1/check-ins/{second_id}/check-out/", {}, format="json", **owner_headers)
    backdated = api.post(
        "/api/v1/check-ins/",
        {
            "member_id": member_id,
            "occurred_at": backdated_at.isoformat(),
            "note": "Corrected",
        },
        format="json",
        **owner_headers,
    )
    assert backdated.status_code == 201, backdated.json()
    assert backdated.json()["data"]["eligibility"]["backdated"] is True

    suspended = api.post(
        f"/api/v1/memberships/{membership_id}/suspend/",
        {},
        format="json",
        **owner_headers,
    )
    assert suspended.status_code == 200, suspended.json()
    api.post(
        f"/api/v1/check-ins/{backdated.json()['data']['id']}/check-out/",
        {},
        format="json",
        **owner_headers,
    )
    ineligible = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id},
        format="json",
        **owner_headers,
    )
    assert ineligible.status_code == 409
    assert ineligible.json()["error"]["code"] == "CHECK_IN_INELIGIBLE"

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="memberships").update(
            enabled=False
        )
    assert api.get("/api/v1/check-ins/", **owner_headers).status_code == 403
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="memberships").update(
            enabled=True
        )


@override_settings(**jwt_settings())
def test_simultaneous_manual_check_ins_create_one_open_visit() -> None:
    client, platform_access = platform_token("checkin-race-platform@example.test")
    payload = provision(
        client, platform_access, code="CHK-RACE", owner_email="checkin-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    _enable_memberships(gym)
    api = _client()
    member_id = _member(api, headers, first_name="Race")
    plan_id = _plan(api, headers, code="RACE", duration_value=7, duration_unit="DAY")
    sale = _sale(
        api,
        headers,
        member_id=member_id,
        plan_id=plan_id,
        start_date=gym_today(gym).isoformat(),
    )
    assert sale.status_code == 201, sale.json()
    barrier = Barrier(2, timeout=20)

    def attempt() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                create_manual_check_in(
                    gym=gym,
                    actor=actor,
                    member_id=UUID(member_id),
                    occurred_at=None,
                    note="",
                    idempotency_key=None,
                    request_id="check-in-race",
                    ip_address=None,
                    user_agent="",
                )
            return "created"
        except CheckInOpen:
            return "open"
        except Exception:
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(attempt), pool.submit(attempt))]
    assert sorted(results) == ["created", "open"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(member_id=member_id).count() == 1


@override_settings(**jwt_settings())
def test_check_in_constraints_reject_biometric_metadata_and_mutation() -> None:
    client, platform_access = platform_token("checkin-db-platform@example.test")
    payload = provision(
        client, platform_access, code="CHK-DB", owner_email="checkin-db-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    _enable_memberships(gym)
    api = _client()
    member_id = _member(api, headers, first_name="Guard")
    plan_id = _plan(api, headers, code="GUARD", duration_value=7, duration_unit="DAY")
    sale = _sale(
        api,
        headers,
        member_id=member_id,
        plan_id=plan_id,
        start_date=gym_today(gym).isoformat(),
    )
    created = api.post("/api/v1/check-ins/", {"member_id": member_id}, format="json", **headers)
    assert created.status_code == 201, created.json()
    check_in_id = created.json()["data"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = CheckIn.objects.get(id=check_in_id)
        snapshot = row.eligibility_snapshot
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            CheckIn.objects.filter(id=check_in_id).update(note="changed")
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            CheckIn.objects.create(
                gym=gym,
                member_id=member_id,
                membership_id=sale.json()["data"]["id"],
                check_in_at=timezone.now(),
                method=CheckIn.Method.MANUAL,
                device_id=uuid4(),
                source_metadata=None,
                note="",
                eligibility_snapshot=snapshot,
                created_by_membership_id=owner.id,
                recorded_by_membership_id=owner.id,
            )
    with pytest.raises(DatabaseError, match="checkin_actor_method_ck"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            CheckIn.objects.create(
                gym=gym,
                member_id=member_id,
                membership_id=sale.json()["data"]["id"],
                check_in_at=timezone.now() - timedelta(days=2),
                method=CheckIn.Method.QR,
                device_id=None,
                source_metadata={"fingerprint": "template-secret"},
                note="",
                eligibility_snapshot=snapshot,
                created_by_membership_id=owner.id,
            )
    with pytest.raises(DatabaseError, match="device capability"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            CheckIn.objects.create(
                gym=gym,
                member_id=member_id,
                membership_id=sale.json()["data"]["id"],
                check_in_at=timezone.now() - timedelta(days=3),
                method=CheckIn.Method.NFC,
                source_metadata={"vendor": "x" * 3000},
                note="",
                eligibility_snapshot=snapshot,
                created_by_membership_id=owner.id,
            )
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'members_check_in', 'SELECT'),
              has_table_privilege('gym_runtime', 'members_check_in', 'INSERT'),
              has_table_privilege('gym_runtime', 'members_check_in', 'UPDATE'),
              has_table_privilege('gym_runtime', 'members_check_in', 'DELETE'),
              c.relrowsecurity,
              c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'members_check_in'
            """
        )
        privileges = cursor.fetchone()
    assert privileges == (True, True, True, False, True, True)
