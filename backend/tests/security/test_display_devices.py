from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connection, connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context
from apps.members.device_services import (
    issue_device_check_in_challenge,
    rotate_display_device_credential,
)
from apps.members.exceptions import CheckInCodeInvalid
from apps.members.models import CheckIn, CheckInChallenge, CheckInDevice
from apps.members.scan_services import redeem_member_check_in_challenge
from apps.members.services import gym_today, link_member_identity
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User
from tests.security.test_gym_provisioning import enable_recent_mfa
from tests.security.test_memberships import _enable_memberships, _member, _plan, _sale
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


def _staff() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enroll(
    api: APIClient, headers: dict[str, str], *, label: str = "Front door"
) -> dict[str, str]:
    response = api.post(
        "/api/v1/check-in-display-devices/",
        {"label": label, "location": "Lobby"},
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


def _issue(credential: str) -> object:
    return APIClient().post(
        "/api/v1/device/check-in-challenges/",
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Device {credential}",
    )


def _assert_denied(response: object, *hidden: str) -> None:
    assert response.status_code == 401, response.content
    error = response.json()["error"]
    assert error["code"] == "AUTHENTICATION_FAILED"
    assert error["message"] == "Authentication credentials are invalid."
    rendered = response.content.decode()
    for value in hidden:
        assert value not in rendered


@override_settings(**jwt_settings())
def test_owner_enrolls_rotates_and_revokes_a_display_without_storing_plaintext() -> None:
    client, platform_access = platform_token("device-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="DEV-A", owner_email="device-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="DEV-B", owner_email="device-owner-b@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="device-manager@example.test", role_key="MANAGER")
    _enable_memberships(gym)
    _enable_memberships(other)
    api = _staff()
    owner.user.set_password(PASSWORD)
    owner.user.save(update_fields=["password"])
    other_owner.user.set_password(PASSWORD)
    other_owner.user.save(update_fields=["password"])
    token = staff_token(owner)
    headers = auth_headers(token)
    manager_headers = auth_headers(staff_token(manager))
    other_token = staff_token(other_owner)
    enable_recent_mfa(api, other_token)
    other_headers = auth_headers(other_token)

    assert api.get("/api/v1/check-in-display-devices/", **headers).status_code == 200
    denied = api.post(
        "/api/v1/check-in-display-devices/",
        {"label": "Front door", "location": "Lobby"},
        format="json",
        **headers,
    )
    assert denied.status_code == 403, denied.content
    assert denied.json()["error"]["code"] == "MFA_STEP_UP_REQUIRED"
    assert (
        api.post(
            "/api/v1/check-in-display-devices/",
            {"label": "Front door", "location": "Lobby"},
            format="json",
            **manager_headers,
        ).status_code
        == 403
    )
    enable_recent_mfa(api, token)
    extra = api.post(
        "/api/v1/check-in-display-devices/",
        {"label": "Front door", "location": "Lobby", "gym_id": str(gym.id), "credential": "nope"},
        format="json",
        **headers,
    )
    assert extra.status_code == 400, extra.content
    idempotent = api.post(
        "/api/v1/check-in-display-devices/",
        {"label": "Front door", "location": "Lobby"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="display-device-key-123456",
        **headers,
    )
    assert idempotent.status_code == 400, idempotent.content

    enrolled = _enroll(api, headers)
    credential = enrolled["credential"]
    secret = credential.rsplit(".", 1)[1]
    listed = api.get("/api/v1/check-in-display-devices/", **headers)
    assert listed.status_code == 200, listed.content
    listed_row = listed.json()["data"][0]
    assert "credential" not in listed_row
    assert secret not in listed.content.decode()
    assert "pbkdf2_sha256" not in listed.content.decode()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        device = CheckInDevice.objects.get(id=enrolled["id"])
        audit = AuditLog.objects.get(action="CHECK_IN_DISPLAY_DEVICE_ENROLLED", target_id=device.id)
        assert device.credential_hash.startswith("pbkdf2_sha256$")
        assert secret not in device.credential_hash
        assert secret not in str(audit.new_values)
        assert device.created_by_membership_id == owner.id
        assert device.credential_version == 1
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT row_to_json(members_check_in_device)::text "
                "FROM members_check_in_device WHERE id = %s",
                [device.id],
            )
            stored = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT
                  has_table_privilege(
                    'gym_runtime', 'members_check_in_device', 'SELECT'
                  ),
                  has_table_privilege(
                    'gym_runtime', 'members_check_in_device', 'INSERT'
                  ),
                  has_table_privilege(
                    'gym_runtime', 'members_check_in_device', 'UPDATE'
                  ),
                  has_table_privilege(
                    'gym_runtime', 'members_check_in_device', 'DELETE'
                  ),
                  has_table_privilege(
                    'gym_auth_bootstrap', 'members_check_in_device', 'SELECT'
                  ),
                  has_column_privilege(
                    'gym_auth_bootstrap', 'members_check_in_device',
                    'credential_hash', 'SELECT'
                  ),
                  c.relrowsecurity,
                  c.relforcerowsecurity
                FROM pg_class c
                WHERE c.relname = 'members_check_in_device'
                """
            )
            privileges = cursor.fetchone()
    assert secret not in stored
    assert privileges == (True, True, True, False, False, False, True, True)

    staff_issue = api.post(
        "/api/v1/check-in-challenges/",
        {"display_location": "Desk"},
        format="json",
        **headers,
    )
    assert staff_issue.status_code == 201, staff_issue.content
    device_issue = _issue(credential)
    assert device_issue.status_code == 201, device_issue.content
    payload = device_issue.json()["data"]["payload"]
    assert str(gym.id) not in payload
    assert enrolled["credential_selector"] not in payload
    assert secret not in payload
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        challenge = CheckInChallenge.objects.get(id=device_issue.json()["data"]["id"])
        staff_challenge = CheckInChallenge.objects.get(
            status=CheckInChallenge.Status.ACTIVE, display_device_id=None
        )
        assert challenge.display_device_id == device.id
        assert challenge.issued_by_membership_id is None
        assert challenge.created_by_membership_id is None
        assert challenge.display_location == "Lobby"
        assert staff_challenge.status == CheckInChallenge.Status.ACTIVE
        issued_audit = AuditLog.objects.get(
            action="CHECK_IN_CHALLENGE_ISSUED", target_id=challenge.id
        )
        assert issued_audit.actor_membership_id is None
        assert payload not in str(issued_audit.new_values)
        assert challenge.challenge_hash not in str(issued_audit.new_values)

    second = _issue(credential)
    assert second.status_code == 201, second.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            CheckInChallenge.objects.get(id=challenge.id).status == CheckInChallenge.Status.REVOKED
        )
        assert (
            CheckInChallenge.objects.filter(
                display_device_id=device.id, status=CheckInChallenge.Status.ACTIVE
            ).count()
            == 1
        )
        assert (
            CheckInChallenge.objects.get(id=staff_challenge.id).status
            == CheckInChallenge.Status.ACTIVE
        )

    shaped = APIClient().post(
        "/api/v1/device/check-in-challenges/",
        {
            "gym_id": str(gym.id),
            "device_id": enrolled["id"],
            "max_uses": 9,
            "ttl_seconds": 30,
            "display_location": "Side door",
        },
        format="json",
        HTTP_AUTHORIZATION=f"Device {credential}",
        HTTP_IDEMPOTENCY_KEY="device-issue-key-12345678",
    )
    assert shaped.status_code == 400, shaped.content
    other_enrolled = _enroll(_staff(), other_headers, label="Other door")
    foreign = _issue(other_enrolled["credential"])
    assert foreign.status_code == 201, foreign.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with pytest.raises(CheckInCodeInvalid):
            redeem_member_check_in_challenge(
                gym=gym,
                member_id=uuid4(),
                user_id=uuid4(),
                payload=foreign.json()["data"]["payload"],
                request_id="cross-gym-device",
                ip_address=None,
                user_agent="",
            )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        assert (
            CheckInChallenge.objects.get(id=foreign.json()["data"]["id"]).status
            == CheckInChallenge.Status.ACTIVE
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM members_check_in_device WHERE id = %s",
                [other_enrolled["id"]],
            )
            assert cursor.fetchone() == (0,)

    rotated = api.post(
        f"/api/v1/check-in-display-devices/{enrolled['id']}/rotate-credential/",
        {},
        format="json",
        **headers,
    )
    assert rotated.status_code == 200, rotated.content
    new_credential = rotated.json()["data"]["credential"]
    assert new_credential != credential
    assert secret not in new_credential
    _assert_denied(_issue(credential), credential, secret)
    assert _issue(new_credential).status_code == 201
    cross = api.post(
        f"/api/v1/check-in-display-devices/{enrolled['id']}/revoke/",
        {},
        format="json",
        **other_headers,
    )
    assert cross.status_code == 404
    revoked = api.post(
        f"/api/v1/check-in-display-devices/{enrolled['id']}/revoke/",
        {},
        format="json",
        **headers,
    )
    assert revoked.status_code == 200, revoked.content
    assert "credential" not in revoked.json()["data"]
    _assert_denied(_issue(new_credential), new_credential)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckInDevice.objects.get(id=enrolled["id"]).status == CheckInDevice.Status.REVOKED
        assert (
            CheckInChallenge.objects.filter(
                display_device_id=enrolled["id"], status=CheckInChallenge.Status.ACTIVE
            ).count()
            == 0
        )
    unknown = "gms-device:v1:" + ("a" * 24) + "." + ("b" * 43)
    _assert_denied(_issue(unknown), unknown, secret)
    _assert_denied(
        APIClient().post(
            "/api/v1/device/check-in-challenges/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ),
        token,
    )


@override_settings(**jwt_settings())
def test_device_rotation_and_scan_do_not_cross_locks_or_reuse_an_old_secret() -> None:
    client, platform_access = platform_token("device-race-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="DEV-RACE", owner_email="device-race-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    api = _staff()
    headers = auth_headers(staff_token(owner))
    member_id = _member(api, headers, first_name="Door")
    plan_id = _plan(api, headers, code="DOOR", duration_value=14, duration_unit="DAY")
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
    user = User.objects.create_user("device-member@example.test", PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(member_id), user=user)
    owner.user.set_password(PASSWORD)
    owner.user.save(update_fields=["password"])
    token = staff_token(owner)
    enable_recent_mfa(api, token)
    enrolled = _enroll(api, auth_headers(token))
    credential = enrolled["credential"]
    first = _issue(credential)
    assert first.status_code == 201, first.content
    barrier = Barrier(2, timeout=30)
    rotated_secret: list[str] = []

    def rotate() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                rotated = rotate_display_device_credential(
                    gym=gym,
                    actor=actor,
                    device_id=UUID(enrolled["id"]),
                    request_id="device-rotate",
                    ip_address=None,
                    user_agent="",
                )
            rotated_secret.append(rotated.credential)
            return "rotated"
        except Exception:
            barrier.abort()
            raise
        finally:
            connections.close_all()

    def issue_old() -> str:
        connections.close_all()
        try:
            barrier.wait()
            issue_device_check_in_challenge(
                authorization=f"Device {credential}",
                request_id="device-old",
                ip_address=None,
                user_agent="",
            )
            return "issued"
        except AuthenticationFailed:
            return "denied"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(rotate), pool.submit(issue_old))]
    assert results.count("rotated") == 1
    assert results.count("issued") + results.count("denied") == 1
    assert len(rotated_secret) == 1
    current = rotated_secret[0]
    _assert_denied(_issue(credential), credential)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckInDevice.objects.get(id=enrolled["id"]).credential_version == 2
    fresh = _issue(current)
    assert fresh.status_code == 201, fresh.content
    payload = fresh.json()["data"]["payload"]
    scan_barrier = Barrier(2, timeout=30)

    def scan() -> str:
        connections.close_all()
        try:
            scan_barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                redeem_member_check_in_challenge(
                    gym=Gym.objects.get(id=gym.id),
                    member_id=UUID(member_id),
                    user_id=user.id,
                    payload=payload,
                    request_id="device-scan",
                    ip_address=None,
                    user_agent="",
                )
            return "scanned"
        except CheckInCodeInvalid:
            return "rejected"
        finally:
            connections.close_all()

    def reissue() -> str:
        connections.close_all()
        try:
            scan_barrier.wait()
            issue_device_check_in_challenge(
                authorization=f"Device {current}",
                request_id="device-reissue",
                ip_address=None,
                user_agent="",
            )
            return "reissued"
        except Exception:
            scan_barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = [future.result() for future in (pool.submit(scan), pool.submit(reissue))]
    assert "reissued" in raced
    assert raced.count("scanned") + raced.count("rejected") == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        raced_challenge = CheckInChallenge.objects.get(id=fresh.json()["data"]["id"])
        assert raced_challenge.status != CheckInChallenge.Status.ACTIVE
        assert (
            CheckInChallenge.objects.filter(
                display_device_id=enrolled["id"], status=CheckInChallenge.Status.ACTIVE
            ).count()
            == 1
        )
        recorded = CheckIn.objects.filter(gym=gym, method=CheckIn.Method.QR).count()
    assert recorded == (1 if "scanned" in raced else 0)
