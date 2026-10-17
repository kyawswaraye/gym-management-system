from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.ingest_services import ingest_device_check_in
from apps.members.models import CheckIn, CheckInDevice, Membership, Payment
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership, GymSettings
from tests.security.test_gym_provisioning import enable_recent_mfa
from tests.security.test_memberships import _enable_memberships, _member, _plan, _sale
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
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

_EVENT = "door-event-00000001"


def _staff() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _ingest(credential: str, body: dict[str, object]) -> object:
    return APIClient().post(
        "/api/v1/device/check-ins/",
        body,
        format="json",
        HTTP_AUTHORIZATION=f"Device {credential}",
    )


@override_settings(**jwt_settings())
def test_nfc_and_biometric_credentials_ingest_without_storing_secrets() -> None:
    client, platform_access = platform_token("ingest-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="ING-A", owner_email="ingest-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="ING-B", owner_email="ingest-owner-b@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    owner.user.set_password(PASSWORD)
    owner.user.save(update_fields=["password"])
    other_owner.user.set_password(PASSWORD)
    other_owner.user.save(update_fields=["password"])
    _enable_memberships(gym)
    _enable_memberships(other)
    api = _staff()
    token = staff_token(owner)
    enable_recent_mfa(api, token)
    headers = auth_headers(token)
    other_token = staff_token(other_owner)
    enable_recent_mfa(api, other_token)
    member_id = _member(api, headers, first_name="Ada")
    member_two = _member(api, headers, first_name="Cam")
    other_member = _member(_staff(), auth_headers(other_token), first_name="Bea")
    plan_id = _plan(api, headers, code="DOOR", duration_value=14, duration_unit="DAY")
    sale = _sale(
        api, headers, member_id=member_id, plan_id=plan_id, start_date=gym_today(gym).isoformat()
    )
    assert sale.status_code == 201, sale.json()
    display = api.post(
        "/api/v1/check-in-display-devices/",
        {"label": "Screen", "location": "Lobby"},
        format="json",
        **headers,
    )
    assert display.status_code == 201, display.content
    assert display.json()["data"]["kind"] == "DISPLAY"
    nfc = api.post(
        "/api/v1/check-in-integration-devices/",
        {"label": "Door", "location": "Lobby", "kind": "NFC"},
        format="json",
        **headers,
    )
    assert nfc.status_code == 201, nfc.content
    nfc_body = nfc.json()["data"]
    nfc_credential = nfc_body["credential"]
    secret = nfc_credential.rsplit(".", 1)[1]
    bio = api.post(
        "/api/v1/check-in-integration-devices/",
        {"label": "Pad", "location": "Desk", "kind": "BIOMETRIC"},
        format="json",
        **headers,
    )
    assert bio.status_code == 201, bio.content
    listed = api.get("/api/v1/check-in-integration-devices/", **headers)
    assert listed.status_code == 200, listed.content
    assert {row["kind"] for row in listed.json()["data"]} == {"NFC", "BIOMETRIC"}
    assert all("credential" not in row for row in listed.json()["data"])
    assert secret not in listed.content.decode()
    assert "pbkdf2_sha256" not in listed.content.decode()
    display_list = api.get("/api/v1/check-in-display-devices/", **headers)
    assert {row["kind"] for row in display_list.json()["data"]} == {"DISPLAY"}

    spoofed = _ingest(
        nfc_credential,
        {
            "member_id": member_id,
            "event_id": _EVENT,
            "method": "BIOMETRIC",
            "gym_id": str(gym.id),
            "membership_id": sale.json()["data"]["id"],
            "device_id": nfc_body["id"],
            "occurred_at": "2026-10-18T00:00:00Z",
            "status": "OPEN",
            "template": "finger",
            "biometric_template": "template",
            "raw_capture": "capture",
            "nfc_secret": "card-secret",
            "vendor_credential": "vendor",
            "source_metadata": {"card_uid": "secret"},
        },
    )
    assert spoofed.status_code == 400, spoofed.content
    assert "method" in spoofed.json()["error"]["fields"]
    assert "nfc_secret" in spoofed.json()["error"]["fields"]
    assert "biometric_template" in spoofed.json()["error"]["fields"]

    created = _ingest(nfc_credential, {"member_id": member_id, "event_id": _EVENT})
    assert created.status_code == 201, created.content
    assert created.json()["data"]["method"] == "NFC"
    check_in_id = created.json()["data"]["id"]
    replay = _ingest(nfc_credential, {"member_id": member_id, "event_id": _EVENT})
    assert replay.status_code == 200, replay.content
    assert replay.json()["data"]["id"] == check_in_id
    conflict = _ingest(nfc_credential, {"member_id": member_two, "event_id": _EVENT})
    assert conflict.status_code == 409, conflict.content
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    foreign = _ingest(
        nfc_credential, {"member_id": other_member, "event_id": "door-event-00000002"}
    )
    assert foreign.status_code == 404, foreign.content
    display_denied = _ingest(
        display.json()["data"]["credential"],
        {"member_id": member_id, "event_id": "door-event-00000003"},
    )
    assert display_denied.status_code == 401, display_denied.content
    challenge_denied = APIClient().post(
        "/api/v1/device/check-in-challenges/",
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Device {nfc_credential}",
    )
    assert challenge_denied.status_code == 401, challenge_denied.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = CheckIn.objects.get(id=check_in_id)
        assert stored.method == "NFC"
        assert stored.device_id == UUID(nfc_body["id"])
        assert stored.created_by_membership_id is None
        assert stored.recorded_by_membership_id is None
        assert stored.source_metadata is None
        assert stored.idempotency_key is not None
        assert _EVENT not in stored.idempotency_key
        assert secret not in stored.idempotency_key
        assert Payment.objects.filter(member_id=member_id).count() == 0
        assert Membership.objects.get(id=sale.json()["data"]["id"]).payment_status == "UNPAID"
        audit = AuditLog.objects.get(action="DEVICE_CHECK_IN_RECORDED", target_id=stored.id)
        assert audit.actor_membership_id is None
        assert _EVENT not in str(audit.new_values)
        assert secret not in str(audit.new_values)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT row_to_json(members_check_in)::text FROM members_check_in WHERE id = %s",
                [stored.id],
            )
            raw = cursor.fetchone()[0]
    assert secret not in raw
    assert _EVENT not in raw
    assert "template" not in raw

    open_again = _ingest(
        nfc_credential, {"member_id": member_id, "event_id": "door-event-00000004"}
    )
    assert open_again.status_code == 409
    assert open_again.json()["error"]["code"] == "CHECK_IN_OPEN"
    checked_out = api.post(
        f"/api/v1/check-ins/{check_in_id}/check-out/", {}, format="json", **headers
    )
    assert checked_out.status_code == 200, checked_out.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(check_in_duplicate_window_seconds=3600)
    duplicate = _ingest(nfc_credential, {"member_id": member_id, "event_id": "door-event-00000005"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CHECK_IN_DUPLICATE"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(check_in_duplicate_window_seconds=0)
    biometric = _ingest(
        bio.json()["data"]["credential"],
        {"member_id": member_id, "event_id": "pad-event-000000001"},
    )
    assert biometric.status_code == 201, biometric.content
    assert biometric.json()["data"]["method"] == "BIOMETRIC"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with pytest.raises(DatabaseError, match="device capability"):
            with transaction.atomic():
                CheckIn.objects.create(
                    gym=gym,
                    member_id=member_id,
                    membership_id=sale.json()["data"]["id"],
                    check_in_at=timezone.now(),
                    method=CheckIn.Method.NFC,
                    device_id=display.json()["data"]["id"],
                    source_metadata=None,
                    note="",
                    eligibility_snapshot=CheckIn.objects.get(id=check_in_id).eligibility_snapshot,
                    created_by_membership_id=None,
                    recorded_by_membership_id=None,
                )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM members_check_in_device WHERE id = %s",
                [nfc_body["id"]],
            )
            own = cursor.fetchone()
            cursor.execute(
                """
                SELECT count(*) FROM members_check_in_device
                WHERE credential_selector = %s
                """,
                ["not-this-gym"],
            )
    assert own == (1,)

    rotated = api.post(
        f"/api/v1/check-in-integration-devices/{nfc_body['id']}/rotate-credential/",
        {},
        format="json",
        **headers,
    )
    assert rotated.status_code == 200, rotated.content
    old = _ingest(nfc_credential, {"member_id": member_id, "event_id": "door-event-00000006"})
    assert old.status_code == 401, old.content
    revoked = api.post(
        f"/api/v1/check-in-integration-devices/{bio.json()['data']['id']}/revoke/",
        {},
        format="json",
        **headers,
    )
    assert revoked.status_code == 200, revoked.content
    assert "credential" not in revoked.json()["data"]
    dead = _ingest(
        bio.json()["data"]["credential"],
        {"member_id": member_id, "event_id": "pad-event-000000002"},
    )
    assert dead.status_code == 401, dead.content
    hidden = api.post(
        f"/api/v1/check-in-integration-devices/{nfc_body['id']}/revoke/",
        {},
        format="json",
        **auth_headers(other_token),
    )
    assert hidden.status_code == 404
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM members_check_in_device WHERE id = %s",
                [nfc_body["id"]],
            )
            assert cursor.fetchone() == (0,)


@override_settings(**jwt_settings())
def test_concurrent_device_events_create_one_check_in() -> None:
    client, platform_access = platform_token("ingest-race-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="ING-R", owner_email="ingest-race-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    owner.user.set_password(PASSWORD)
    owner.user.save(update_fields=["password"])
    _enable_memberships(gym)
    api = _staff()
    token = staff_token(owner)
    enable_recent_mfa(api, token)
    headers = auth_headers(token)
    member_id = _member(api, headers, first_name="Race")
    plan_id = _plan(api, headers, code="RACE", duration_value=7, duration_unit="DAY")
    assert (
        _sale(
            api,
            headers,
            member_id=member_id,
            plan_id=plan_id,
            start_date=gym_today(gym).isoformat(),
        ).status_code
        == 201
    )
    enrolled = api.post(
        "/api/v1/check-in-integration-devices/",
        {"label": "Race", "location": "Gate", "kind": "NFC"},
        format="json",
        **headers,
    )
    assert enrolled.status_code == 201, enrolled.content
    credential = enrolled.json()["data"]["credential"]
    barrier = Barrier(2, timeout=30)

    def attempt() -> str:
        connections.close_all()
        try:
            barrier.wait()
            _check_in, created = ingest_device_check_in(
                authorization=f"Device {credential}",
                member_id=UUID(member_id),
                event_id="race-event-00000001",
                request_id="ingest-race",
                ip_address=None,
                user_agent="",
            )
            return "created" if created else "replayed"
        except Exception:
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(attempt), pool.submit(attempt))]
    assert sorted(results) == ["created", "replayed"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, method=CheckIn.Method.NFC).count() == 1
        assert CheckInDevice.objects.get(id=enrolled.json()["data"]["id"]).kind == "NFC"
