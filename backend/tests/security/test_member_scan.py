from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX, challenge_digest
from apps.members.checkin_services import check_out_check_in
from apps.members.exceptions import CheckInCodeInvalid, CheckInDuplicate, CheckInOpen
from apps.members.models import CheckIn, CheckInChallenge, Membership, Payment
from apps.members.scan_services import redeem_member_check_in_challenge
from apps.members.services import gym_today, link_member_identity
from apps.tenants.models import Gym, GymMembership, GymSettings
from apps.users.models import User
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


def _staff_client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _scan(token: str, body: dict[str, object], **headers: str) -> object:
    return APIClient().post(
        "/api/v1/member/check-ins/scan/",
        body,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        **headers,
    )


def _access_token(email: str) -> str:
    response = APIClient().post(
        "/api/v1/member-auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.content
    return str(response.json()["data"]["access_token"])


def _link(gym: Gym, member_id: str, email: str) -> tuple[User, str]:
    user = User.objects.create_user(email, PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(member_id), user=user)
    return user, _access_token(email)


def _issue(headers: dict[str, str]) -> tuple[str, str]:
    response = _staff_client().post(
        "/api/v1/check-in-challenges/",
        {"display_location": "Front door"},
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.content
    data = response.json()["data"]
    return str(data["id"]), str(data["payload"])


def _assert_invalid(response: object, *hidden: str) -> None:
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "CHECK_IN_CODE_INVALID"
    assert error["message"] == "The check-in code is not valid."
    rendered = response.content.decode()
    for value in hidden:
        assert value not in rendered


def _load_challenge(gym: Gym, challenge_id: str) -> CheckInChallenge:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return CheckInChallenge.objects.get(id=challenge_id)


def _set_limits(gym: Gym, *, max_uses: int | None = None, window: int | None = None) -> None:
    values: dict[str, int] = {}
    if max_uses is not None:
        values["check_in_challenge_max_uses"] = max_uses
    if window is not None:
        values["check_in_duplicate_window_seconds"] = window
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(**values)


@override_settings(**jwt_settings())
def test_member_scan_redeems_one_visit_without_storing_or_leaking_the_secret() -> None:
    client, platform_access = platform_token("scan-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-A", owner_email="scan-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="SCAN-B", owner_email="scan-owner-b@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    _enable_memberships(other)
    staff = _staff_client()
    headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    today = gym_today(gym).isoformat()
    member_id = _member(staff, headers, first_name="Ada")
    other_member_id = _member(staff, headers, first_name="Bea")
    plan_id = _plan(staff, headers, code="SCAN", duration_value=30, duration_unit="DAY")
    sale = _sale(staff, headers, member_id=member_id, plan_id=plan_id, start_date=today)
    other_sale = _sale(staff, headers, member_id=other_member_id, plan_id=plan_id, start_date=today)
    assert sale.status_code == 201, sale.content
    assert other_sale.status_code == 201, other_sale.content
    membership_id = str(sale.json()["data"]["id"])
    user, token = _link(gym, member_id, "scan-ada@example.test")
    _other_user, other_token = _link(gym, other_member_id, "scan-bea@example.test")
    _set_limits(gym, max_uses=2)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment_status = Membership.objects.get(id=membership_id).payment_status
        payment_count = Payment.objects.filter(gym=gym).count()

    challenge_id, payload = _issue(headers)
    digest = challenge_digest(payload)
    _other_id, other_payload = _issue(other_headers)
    staff_denied = _scan(staff_token(owner), {"payload": payload})
    assert staff_denied.status_code == 401, staff_denied.content
    platform_denied = _scan(platform_access, {"payload": payload})
    assert platform_denied.status_code == 401, platform_denied.content
    missing_route = APIClient().post(
        "/api/v1/check-ins/scan/",
        {"payload": payload},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert missing_route.status_code == 404
    extra = _scan(
        token,
        {
            "payload": payload,
            "member_id": member_id,
            "gym_id": str(gym.id),
            "membership_id": membership_id,
            "method": "MANUAL",
            "occurred_at": timezone.now().isoformat(),
            "device_id": str(owner.id),
            "source_metadata": {"desk": "front"},
            "status": "OPEN",
        },
    )
    assert extra.status_code == 400, extra.content
    replay_key = _scan(
        token,
        {"payload": payload},
        HTTP_IDEMPOTENCY_KEY="member-scan-key-123456",
    )
    assert replay_key.status_code == 400, replay_key.content
    assert _load_challenge(gym, challenge_id).use_count == 0

    created = _scan(token, {"payload": payload})
    assert created.status_code == 201, created.content
    body = created.json()["data"]
    assert set(body) == READ_FIELDS
    assert body["method"] == "QR"
    assert body["status"] == "OPEN"
    assert body["membership_id"] == membership_id
    assert body["eligibility"]["backdated"] is False
    assert body["eligibility"]["eligible"] is True
    rendered = created.content.decode()
    assert payload not in rendered
    assert digest not in rendered
    assert "Idempotency-Key" not in rendered

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        challenge = CheckInChallenge.objects.get(id=challenge_id)
        visit = CheckIn.objects.get(id=body["id"])
        audit = AuditLog.objects.get(action="MEMBER_CHECK_IN_RECORDED", target_id=visit.id)
        assert challenge.use_count == 1
        assert challenge.status == CheckInChallenge.Status.ACTIVE
        assert challenge.consumed_at is None
        assert visit.method == CheckIn.Method.QR
        assert visit.created_by_membership_id is None
        assert visit.recorded_by_membership_id is None
        assert visit.device_id is None
        assert visit.source_metadata is None
        assert visit.idempotency_key is None
        assert visit.note == ""
        assert Membership.objects.get(id=membership_id).payment_status == payment_status
        assert Payment.objects.filter(gym=gym).count() == payment_count
        assert audit.actor_membership_id is None
        assert audit.actor_user_id == user.id
        assert audit.module == "checkins"
        stored = f"{audit.old_values} {audit.new_values}"
        assert payload not in stored
        assert digest not in stored
        assert "payload" not in audit.new_values
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT row_to_json(members_check_in)::text FROM members_check_in WHERE id = %s",
                [visit.id],
            )
            visit_row = cursor.fetchone()[0]
            cursor.execute(
                "SELECT row_to_json(members_check_in_challenge)::text "
                "FROM members_check_in_challenge WHERE id = %s",
                [challenge.id],
            )
            challenge_row = cursor.fetchone()[0]
    assert payload not in visit_row
    assert digest not in visit_row
    assert payload not in challenge_row
    assert digest in challenge_row

    opened = _scan(token, {"payload": payload})
    assert opened.status_code == 409, opened.content
    assert opened.json()["error"]["code"] == "CHECK_IN_OPEN"
    assert _load_challenge(gym, challenge_id).use_count == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        check_out_check_in(
            gym=gym,
            actor=owner,
            check_in_id=visit.id,
            occurred_at=None,
            request_id="scan-checkout",
            ip_address=None,
            user_agent="",
        )
    _set_limits(gym, window=0)
    repeated = _scan(token, {"payload": payload})
    assert repeated.status_code == 409, repeated.content
    assert repeated.json()["error"]["code"] == "CHECK_IN_DUPLICATE"
    assert _load_challenge(gym, challenge_id).use_count == 1

    second = _scan(other_token, {"payload": payload})
    assert second.status_code == 201, second.content
    finished = _load_challenge(gym, challenge_id)
    assert finished.use_count == 2
    assert finished.status == CheckInChallenge.Status.CONSUMED
    assert finished.consumed_at is not None
    consumed = _scan(other_token, {"payload": payload})
    _assert_invalid(consumed, payload, digest)
    assert _load_challenge(gym, challenge_id).use_count == 2

    foreign = _scan(token, {"payload": other_payload})
    _assert_invalid(foreign, other_payload, str(other.id), _other_id)
    assert _load_challenge(other, _other_id).use_count == 0
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM members_check_in_challenge WHERE id = %s",
                [_other_id],
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT count(*) FROM members_check_in_challenge WHERE id = %s",
                [challenge_id],
            )
            assert cursor.fetchone() == (1,)


@override_settings(**jwt_settings())
def test_bad_challenge_payloads_fail_without_existence_leakage_or_a_use() -> None:
    client, platform_access = platform_token("scan-bad-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-BAD", owner_email="scan-bad-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    staff = _staff_client()
    headers = auth_headers(staff_token(owner))
    member_id = _member(staff, headers, first_name="Cara")
    plan_id = _plan(staff, headers, code="BAD", duration_value=14, duration_unit="DAY")
    sale = _sale(
        staff,
        headers,
        member_id=member_id,
        plan_id=plan_id,
        start_date=gym_today(gym).isoformat(),
    )
    assert sale.status_code == 201, sale.content
    _user, token = _link(gym, member_id, "scan-cara@example.test")
    challenge_id, payload = _issue(headers)
    modified = payload[:-1] + ("y" if payload[-1] != "y" else "z")
    unknown = f"{CHALLENGE_PAYLOAD_PREFIX}{'a' * 43}"
    for candidate in (modified, unknown, "not-a-code"):
        _assert_invalid(_scan(token, {"payload": candidate}), payload, challenge_id)

    issued_at = timezone.now() - timedelta(minutes=3)
    expired_payload = f"{CHALLENGE_PAYLOAD_PREFIX}{secrets.token_urlsafe(32)}"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        expired = CheckInChallenge.objects.create(
            gym=gym,
            challenge_hash=challenge_digest(expired_payload),
            display_location="Side door",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=60),
            use_count=0,
            max_uses=1,
            issued_by_membership_id=owner.id,
            display_device_id=None,
            status=CheckInChallenge.Status.ACTIVE,
            created_by_membership_id=owner.id,
        )
    _assert_invalid(_scan(token, {"payload": expired_payload}), expired_payload)
    assert _load_challenge(gym, str(expired.id)).status == CheckInChallenge.Status.ACTIVE
    assert _load_challenge(gym, str(expired.id)).use_count == 0

    revoked_id, revoked_payload = _issue(headers)
    _replacement_id, _replacement_payload = _issue(headers)
    assert _load_challenge(gym, revoked_id).status == CheckInChallenge.Status.REVOKED
    _assert_invalid(_scan(token, {"payload": revoked_payload}), revoked_payload, revoked_id)
    assert _load_challenge(gym, revoked_id).use_count == 0
    assert _load_challenge(gym, challenge_id).use_count == 0
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, method=CheckIn.Method.QR).count() == 0


@override_settings(**jwt_settings())
def test_ineligible_scan_rolls_back_without_a_use() -> None:
    client, platform_access = platform_token("scan-roll-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-ROLL", owner_email="scan-roll-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    staff = _staff_client()
    headers = auth_headers(staff_token(owner))
    member_id = _member(staff, headers, first_name="NoPlan")
    _user, token = _link(gym, member_id, "scan-noplan@example.test")
    challenge_id, payload = _issue(headers)
    denied = _scan(token, {"payload": payload})
    assert denied.status_code == 409, denied.content
    assert denied.json()["error"]["code"] == "CHECK_IN_INELIGIBLE"
    challenge = _load_challenge(gym, challenge_id)
    assert challenge.use_count == 0
    assert challenge.status == CheckInChallenge.Status.ACTIVE
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_concurrent_members_cannot_exceed_max_uses() -> None:
    client, platform_access = platform_token("scan-race-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-RACE", owner_email="scan-race-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    staff = _staff_client()
    headers = auth_headers(staff_token(owner))
    today = gym_today(gym).isoformat()
    first_id = _member(staff, headers, first_name="One")
    second_id = _member(staff, headers, first_name="Two")
    plan_id = _plan(staff, headers, code="RACE", duration_value=7, duration_unit="DAY")
    assert (
        _sale(staff, headers, member_id=first_id, plan_id=plan_id, start_date=today).status_code
        == 201
    )
    assert (
        _sale(staff, headers, member_id=second_id, plan_id=plan_id, start_date=today).status_code
        == 201
    )
    first_user, _first_token = _link(gym, first_id, "scan-one@example.test")
    second_user, _second_token = _link(gym, second_id, "scan-two@example.test")
    _challenge_id, payload = _issue(headers)
    barrier = Barrier(2, timeout=20)

    def attempt(member_id: str, user_id: UUID) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                redeem_member_check_in_challenge(
                    gym=Gym.objects.get(id=gym.id),
                    member_id=UUID(member_id),
                    user_id=user_id,
                    payload=payload,
                    request_id="scan-race",
                    ip_address=None,
                    user_agent="",
                )
            return "created"
        except CheckInCodeInvalid:
            return "invalid"
        except Exception:
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in (
                pool.submit(attempt, first_id, first_user.id),
                pool.submit(attempt, second_id, second_user.id),
            )
        ]
    assert sorted(results) == ["created", "invalid"]
    challenge = _load_challenge(gym, _challenge_id)
    assert challenge.use_count == 1
    assert challenge.status == CheckInChallenge.Status.CONSUMED
    assert challenge.consumed_at is not None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, method=CheckIn.Method.QR).count() == 1


@override_settings(**jwt_settings())
def test_concurrent_scans_by_one_member_burn_one_use() -> None:
    client, platform_access = platform_token("scan-dup-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-DUP", owner_email="scan-dup-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    staff = _staff_client()
    headers = auth_headers(staff_token(owner))
    member_id = _member(staff, headers, first_name="Solo")
    plan_id = _plan(staff, headers, code="DUP", duration_value=7, duration_unit="DAY")
    assert (
        _sale(
            staff,
            headers,
            member_id=member_id,
            plan_id=plan_id,
            start_date=gym_today(gym).isoformat(),
        ).status_code
        == 201
    )
    user, _token = _link(gym, member_id, "scan-solo@example.test")
    _set_limits(gym, max_uses=2)
    challenge_id, payload = _issue(headers)
    barrier = Barrier(2, timeout=20)

    def attempt() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                redeem_member_check_in_challenge(
                    gym=Gym.objects.get(id=gym.id),
                    member_id=UUID(member_id),
                    user_id=user.id,
                    payload=payload,
                    request_id="scan-dup",
                    ip_address=None,
                    user_agent="",
                )
            return "created"
        except (CheckInOpen, CheckInDuplicate):
            return "rejected"
        except Exception:
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(attempt), pool.submit(attempt))]
    assert sorted(results) == ["created", "rejected"]
    challenge = _load_challenge(gym, challenge_id)
    assert challenge.use_count == 1
    assert challenge.status == CheckInChallenge.Status.ACTIVE
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, member_id=member_id).count() == 1


@override_settings(**jwt_settings())
def test_member_scan_rate_limit_ignores_the_payload() -> None:
    client, platform_access = platform_token("scan-rate-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="SCAN-RATE", owner_email="scan-rate-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    headers = auth_headers(staff_token(owner))
    member_id = _member(_staff_client(), headers, first_name="Rate")
    _user, token = _link(gym, member_id, "scan-rate@example.test")
    framework = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "member_check_in_scan": "2/min",
        },
    }
    cache.clear()
    payloads = [f"{CHALLENGE_PAYLOAD_PREFIX}{'b' * 40}{index}" for index in ("1", "2", "3")]
    with override_settings(REST_FRAMEWORK=framework):
        api_settings.reload()
        first = _scan(token, {"payload": payloads[0]})
        second = _scan(token, {"payload": payloads[1]})
        limited = _scan(token, {"payload": payloads[2]})
    api_settings.reload()
    _assert_invalid(first, *payloads)
    _assert_invalid(second, *payloads)
    assert limited.status_code == 429, limited.content
    rendered = limited.content.decode()
    for payload in payloads:
        assert payload not in rendered
