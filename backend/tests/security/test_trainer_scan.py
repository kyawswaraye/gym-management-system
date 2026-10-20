"""Assignment-scoped trainer QR scan. The generic staff scan route stays absent."""

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
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX, challenge_digest
from apps.members.checkin_services import check_out_check_in
from apps.members.exceptions import CheckInCodeInvalid
from apps.members.models import CheckIn, CheckInChallenge, Member
from apps.members.scan_services import redeem_trainer_check_in_challenge
from apps.members.services import gym_today, link_member_identity
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    GymSettings,
    Permission,
    Role,
    RolePermission,
    StaffProfile,
)
from apps.trainers.models import TrainerAssignment
from apps.users.models import User
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
from tests.security.test_trainer_assignments import _assign, _profile

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
SCAN = "/api/v1/trainer/check-ins/scan/"


def _staff_client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


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


def _scan(headers: dict[str, str], body: dict[str, object], **extra: str) -> object:
    return _staff_client().post(SCAN, body, format="json", **headers, **extra)


def _load(gym: Gym, challenge_id: str) -> CheckInChallenge:
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


def _feature(gym: Gym, code: str, *, enabled: bool) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        updated = GymFeatureEntitlement.objects.filter(gym=gym, feature__code=code).update(
            enabled=enabled
        )
    assert updated == 1


def _effects(gym: Gym, challenge_id: str) -> tuple[int, int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            CheckInChallenge.objects.get(id=challenge_id).use_count,
            CheckIn.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(
                gym=gym, action__in=["CHECK_IN_RECORDED", "MEMBER_CHECK_IN_RECORDED"]
            ).count(),
        )


def _assert_untouched(gym: Gym, challenge_id: str, before: tuple[int, int, int]) -> None:
    assert _effects(gym, challenge_id) == before


def _assert_invalid(response: object, *hidden: str) -> None:
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "CHECK_IN_CODE_INVALID"
    assert error["message"] == "The check-in code is not valid."
    rendered = response.content.decode()
    for value in hidden:
        assert value not in rendered


def _sell(
    api: APIClient, headers: dict[str, str], *, member_id: str, plan_id: str, today: str
) -> str:
    sale = _sale(api, headers, member_id=member_id, plan_id=plan_id, start_date=today)
    assert sale.status_code == 201, sale.content
    return str(sale.json()["data"]["id"])


@override_settings(**jwt_settings())
def test_trainer_scan_is_assignment_scoped_and_stores_no_secret() -> None:
    client, platform_access = platform_token("tscan-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="TSCN-A", owner_email="tscan-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="TSCN-B", owner_email="tscan-other@example.test"
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
    trainer_membership = add_staff(gym, email="tscan-ada@example.test", role_key="TRAINER")
    other_trainer_membership = add_staff(gym, email="tscan-bea@example.test", role_key="TRAINER")
    receptionist = add_staff(gym, email="tscan-desk@example.test", role_key="RECEPTIONIST")
    trainer = _profile(gym, trainer_membership, code="TR-TSCN", name="Ada")
    other_trainer = _profile(gym, other_trainer_membership, code="TR-TSC2", name="Bea")
    api = _staff_client()
    headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    today = gym_today(gym)
    today_text = today.isoformat()
    ada = _member(api, headers, first_name="Ada")
    second = _member(api, headers, first_name="Second")
    future_member = _member(api, headers, first_name="Future")
    expired_member = _member(api, headers, first_name="Expired")
    ended_member = _member(api, headers, first_name="Ended")
    cancelled_member = _member(api, headers, first_name="Cancelled")
    stranger = _member(api, headers, first_name="Stranger")
    other_member = _member(api, headers, first_name="Other")
    inactive = _member(api, headers, first_name="Inactive")
    away_member = _member(api, headers, first_name="Away")
    bare = _member(api, headers, first_name="Bare")
    foreign = _member(api, other_headers, first_name="Foreign")
    plan_id = _plan(api, headers, code="TSCAN", duration_value=30, duration_unit="DAY")
    ada_membership = _sell(api, headers, member_id=ada, plan_id=plan_id, today=today_text)
    _sell(api, headers, member_id=second, plan_id=plan_id, today=today_text)
    for member_id in (
        future_member,
        expired_member,
        ended_member,
        cancelled_member,
        inactive,
        away_member,
    ):
        _sell(api, headers, member_id=member_id, plan_id=plan_id, today=today_text)
    open_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=ada, start=today_text
    )
    second_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=second, start=today_text
    )
    future_assignment = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=future_member,
        start=(today + timedelta(days=1)).isoformat(),
    )
    expired_assignment = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=expired_member,
        start=(today - timedelta(days=10)).isoformat(),
        until=(today - timedelta(days=1)).isoformat(),
    )
    ended_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=ended_member, start=today_text
    )
    cancelled_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=cancelled_member, start=today_text
    )
    inactive_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=inactive, start=today_text
    )
    away_membership = add_staff(gym, email="tscan-away@example.test", role_key="TRAINER")
    away_profile = _profile(gym, away_membership, code="TR-AWAY", name="Away")
    away_assignment = _assign(
        api, headers, trainer_id=str(away_profile.id), member_id=away_member, start=today_text
    )
    other_assignment = _assign(
        api, headers, trainer_id=str(other_trainer.id), member_id=other_member, start=today_text
    )
    bare_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=bare, start=today_text
    )
    assert open_assignment.status_code == 201, open_assignment.content
    assert second_assignment.status_code == 201, second_assignment.content
    assert future_assignment.status_code == 201, future_assignment.content
    assert expired_assignment.status_code == 201, expired_assignment.content
    assert ended_assignment.status_code == 201, ended_assignment.content
    assert cancelled_assignment.status_code == 201, cancelled_assignment.content
    assert inactive_assignment.status_code == 201, inactive_assignment.content
    assert away_assignment.status_code == 201, away_assignment.content
    assert other_assignment.status_code == 201, other_assignment.content
    assert bare_assignment.status_code == 201, bare_assignment.content
    ended = api.post(
        f"/api/v1/trainer-assignments/{ended_assignment.json()['data']['id']}/end/",
        {"effective_until": today_text, "reason": "Program finished"},
        format="json",
        **headers,
    )
    cancelled = api.post(
        f"/api/v1/trainer-assignments/{cancelled_assignment.json()['data']['id']}/cancel/",
        {"reason": "Did not start"},
        format="json",
        **headers,
    )
    assert ended.status_code == 200, ended.content
    assert cancelled.status_code == 200, cancelled.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.filter(id=inactive).update(status=Member.Status.SUSPENDED)
        StaffProfile.objects.filter(id=away_profile.id).update(status=StaffProfile.Status.ON_LEAVE)
        assert (
            TrainerAssignment.objects.get(id=expired_assignment.json()["data"]["id"]).status
            == TrainerAssignment.Status.ACTIVE
        )
        coach = Role.objects.create(
            gym=gym,
            key="PERSONAL_TRAINER",
            name="Personal Trainer",
            created_by_membership_id=owner.id,
        )
        RolePermission.objects.create(
            gym=gym,
            role=coach,
            permission=Permission.objects.get(code="checkins.create"),
            created_by_membership_id=owner.id,
        )
    custom = add_staff(gym, email="tscan-coach@example.test", role_key="PERSONAL_TRAINER")
    _set_limits(gym, max_uses=2, window=0)

    anonymous = APIClient().post(SCAN, {"payload": "x", "member_id": ada}, format="json")
    assert anonymous.status_code == 403
    assert anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"
    linked = User.objects.create_user("tscan-linked@example.test", PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(ada), user=linked)
    member_login = APIClient().post(
        "/api/v1/member-auth/login/",
        {"email": "tscan-linked@example.test", "password": PASSWORD},
        format="json",
    )
    assert member_login.status_code == 200, member_login.content
    member_token = str(member_login.json()["data"]["access_token"])
    member_denied = APIClient().post(
        SCAN,
        {"payload": "gms-checkin:v1:abcdefghijklmnopqrstuvwxyz0123456789", "member_id": ada},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {member_token}",
    )
    assert member_denied.status_code == 401, member_denied.content
    platform_denied = api.post(
        SCAN,
        {"payload": "gms-checkin:v1:abcdefghijklmnopqrstuvwxyz0123456789", "member_id": ada},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
    )
    assert platform_denied.status_code == 401, platform_denied.content

    challenge_id, payload = _issue(headers)
    digest = challenge_digest(payload)
    denied_callers = (
        auth_headers(staff_token(owner)),
        auth_headers(staff_token(receptionist)),
        auth_headers(staff_token(custom)),
    )
    for caller in denied_callers:
        before = _effects(gym, challenge_id)
        denied = _scan(caller, {"payload": payload, "member_id": ada})
        assert denied.status_code == 403, denied.content
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
        _assert_untouched(gym, challenge_id, before)

    hidden = (
        future_member,
        expired_member,
        ended_member,
        cancelled_member,
        stranger,
        other_member,
        inactive,
        foreign,
    )
    away_headers = auth_headers(staff_token(away_membership))
    before = _effects(gym, challenge_id)
    away_denied = _scan(away_headers, {"payload": payload, "member_id": away_member})
    assert away_denied.status_code == 404, away_denied.content
    _assert_untouched(gym, challenge_id, before)
    for member_id in hidden:
        before = _effects(gym, challenge_id)
        missing = _scan(trainer_headers, {"payload": payload, "member_id": member_id})
        assert missing.status_code == 404, missing.content
        assert member_id not in missing.content.decode()
        _assert_untouched(gym, challenge_id, before)

    before = _effects(gym, challenge_id)
    bare_denied = _scan(trainer_headers, {"payload": payload, "member_id": bare})
    assert bare_denied.status_code == 409, bare_denied.content
    assert bare_denied.json()["error"]["code"] == "CHECK_IN_INELIGIBLE"
    _assert_untouched(gym, challenge_id, before)

    before = _effects(gym, challenge_id)
    keyed = _scan(
        trainer_headers,
        {"payload": payload, "member_id": ada},
        HTTP_IDEMPOTENCY_KEY="trainer-scan-key-123456",
    )
    assert keyed.status_code == 400, keyed.content
    assert "Idempotency-Key" in keyed.content.decode()
    _assert_untouched(gym, challenge_id, before)
    before = _effects(gym, challenge_id)
    extra = _scan(
        trainer_headers,
        {
            "payload": payload,
            "member_id": ada,
            "gym_id": str(gym.id),
            "method": "MANUAL",
            "status": "OPEN",
            "recorded_by_membership_id": str(trainer_membership.id),
            "created_by_membership_id": str(trainer_membership.id),
            "source_metadata": {"desk": "front"},
            "idempotency_key": "trainer-scan-key-123456",
            "device_id": str(trainer.id),
        },
    )
    assert extra.status_code == 400, extra.content
    _assert_untouched(gym, challenge_id, before)

    with CaptureQueriesContext(connection) as captured:
        created = _scan(trainer_headers, {"payload": payload, "member_id": ada})
    assert created.status_code == 201, created.content
    body = created.json()["data"]
    assert set(body) == READ_FIELDS
    assert body["method"] == "QR"
    assert body["status"] == "OPEN"
    assert body["membership_id"] == ada_membership
    assert body["eligibility"]["backdated"] is False
    rendered = created.content.decode()
    assert payload not in rendered
    assert digest not in rendered
    assignment_sql = [
        query["sql"]
        for query in captured.captured_queries
        if "trainers_trainer_assignment" in query["sql"]
    ]
    assert len(assignment_sql) == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        visit = CheckIn.objects.get(id=body["id"])
        audit = AuditLog.objects.get(action="CHECK_IN_RECORDED", target_id=visit.id)
        assert visit.method == CheckIn.Method.QR
        assert visit.created_by_membership_id is None
        assert visit.recorded_by_membership_id is None
        assert visit.device_id is None
        assert visit.source_metadata is None
        assert visit.idempotency_key is None
        assert audit.actor_membership_id == trainer_membership.id
        assert audit.actor_user_id == trainer_membership.user_id
        assert audit.module == "checkins"
        assert set(audit.new_values) == {
            "member_id",
            "membership_id",
            "method",
            "check_in_at",
            "check_out_at",
            "backdated",
        }
        assert audit.new_values["method"] == "QR"
        assert audit.new_values["backdated"] is False
        stored = f"{audit.old_values} {audit.new_values}"
        assert payload not in stored
        assert digest not in stored
        assert not AuditLog.objects.filter(action="MEMBER_CHECK_IN_RECORDED", gym=gym).exists()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT row_to_json(members_check_in)::text FROM members_check_in WHERE id = %s",
                [visit.id],
            )
            visit_row = cursor.fetchone()[0]
    assert payload not in visit_row
    assert digest not in visit_row
    assert _load(gym, challenge_id).use_count == 1

    opened = _scan(trainer_headers, {"payload": payload, "member_id": ada})
    assert opened.status_code == 409, opened.content
    assert opened.json()["error"]["code"] == "CHECK_IN_OPEN"
    assert _load(gym, challenge_id).use_count == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        check_out_check_in(
            gym=gym,
            actor=owner,
            check_in_id=visit.id,
            occurred_at=None,
            request_id="trainer-scan-checkout",
            ip_address=None,
            user_agent="",
        )
    repeated = _scan(trainer_headers, {"payload": payload, "member_id": ada})
    assert repeated.status_code == 409, repeated.content
    assert repeated.json()["error"]["code"] == "CHECK_IN_DUPLICATE"
    assert _load(gym, challenge_id).use_count == 1
    second_visit = _scan(trainer_headers, {"payload": payload, "member_id": second})
    assert second_visit.status_code == 201, second_visit.content
    finished = _load(gym, challenge_id)
    assert finished.use_count == 2
    assert finished.status == CheckInChallenge.Status.CONSUMED
    replayed = _scan(trainer_headers, {"payload": payload, "member_id": second})
    _assert_invalid(replayed, payload, digest)
    assert _load(gym, challenge_id).use_count == 2

    _other_id, other_payload = _issue(other_headers)
    foreign_code = _scan(trainer_headers, {"payload": other_payload, "member_id": ada})
    _assert_invalid(foreign_code, other_payload, str(other.id))
    assert _load(other, _other_id).use_count == 0
    fresh_id, fresh_payload = _issue(headers)
    modified = fresh_payload[:-1] + ("y" if fresh_payload[-1] != "y" else "z")
    for candidate in (modified, f"{CHALLENGE_PAYLOAD_PREFIX}{'a' * 43}", "not-a-code"):
        before = _effects(gym, fresh_id)
        _assert_invalid(
            _scan(trainer_headers, {"payload": candidate, "member_id": ada}),
            fresh_payload,
        )
        _assert_untouched(gym, fresh_id, before)
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
    before = _effects(gym, str(expired.id))
    _assert_invalid(
        _scan(trainer_headers, {"payload": expired_payload, "member_id": ada}),
        expired_payload,
    )
    _assert_untouched(gym, str(expired.id), before)
    revoked_id, revoked_payload = _issue(headers)
    _issue(headers)
    assert _load(gym, revoked_id).status == CheckInChallenge.Status.REVOKED
    before = _effects(gym, revoked_id)
    _assert_invalid(
        _scan(trainer_headers, {"payload": revoked_payload, "member_id": ada}),
        revoked_payload,
    )
    _assert_untouched(gym, revoked_id, before)

    live_id, live_payload = _issue(headers)
    _feature(gym, "memberships", enabled=False)
    before = _effects(gym, live_id)
    entitlement = _scan(trainer_headers, {"payload": live_payload, "member_id": ada})
    assert entitlement.status_code == 403, entitlement.content
    _assert_untouched(gym, live_id, before)
    _feature(gym, "memberships", enabled=True)
    _feature(gym, "members", enabled=False)
    before = _effects(gym, live_id)
    members_off = _scan(trainer_headers, {"payload": live_payload, "member_id": ada})
    assert members_off.status_code == 403, members_off.content
    _assert_untouched(gym, live_id, before)
    _feature(gym, "members", enabled=True)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        removed = RolePermission.objects.filter(
            gym=gym,
            role__key="TRAINER",
            role__source_template__key="TRAINER",
            permission__code="checkins.create",
        ).delete()
    assert removed[0] >= 1
    before = _effects(gym, live_id)
    unprivileged = _scan(trainer_headers, {"payload": live_payload, "member_id": ada})
    assert unprivileged.status_code == 403, unprivileged.content
    _assert_untouched(gym, live_id, before)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer_membership,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
    mixed_headers = auth_headers(staff_token(trainer_membership))
    mixed_challenge, mixed_payload = _issue(headers)
    before = _effects(gym, mixed_challenge)
    mixed_hidden = _scan(mixed_headers, {"payload": mixed_payload, "member_id": stranger})
    assert mixed_hidden.status_code == 404, mixed_hidden.content
    _assert_untouched(gym, mixed_challenge, before)
    mixed_created = _scan(mixed_headers, {"payload": mixed_payload, "member_id": ada})
    assert mixed_created.status_code == 201, mixed_created.content
    missing_route = api.post(
        "/api/v1/check-ins/scan/",
        {"payload": live_payload, "member_id": ada},
        format="json",
        **mixed_headers,
    )
    assert missing_route.status_code == 404
    member_route = APIClient().post(
        "/api/v1/check-ins/scan/",
        {"payload": live_payload},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {member_token}",
    )
    assert member_route.status_code == 404


@override_settings(**jwt_settings())
def test_concurrent_trainer_scans_cannot_exceed_the_last_use() -> None:
    client, platform_access = platform_token("tscan-race-platform@example.test")
    gym_payload = provision(
        client, platform_access, code="TSCN-R", owner_email="tscan-race-owner@example.test"
    )
    gym = Gym.objects.get(id=gym_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    trainer_membership = add_staff(gym, email="tscan-race-ada@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-RACE", name="Ada")
    api = _staff_client()
    headers = auth_headers(staff_token(owner))
    today = gym_today(gym).isoformat()
    first = _member(api, headers, first_name="One")
    second = _member(api, headers, first_name="Two")
    plan_id = _plan(api, headers, code="RACE", duration_value=14, duration_unit="DAY")
    _sell(api, headers, member_id=first, plan_id=plan_id, today=today)
    _sell(api, headers, member_id=second, plan_id=plan_id, today=today)
    assert (
        _assign(api, headers, trainer_id=str(trainer.id), member_id=first, start=today).status_code
        == 201
    )
    second_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=second, start=today
    )
    assert second_assignment.status_code == 201
    _set_limits(gym, max_uses=1)
    _challenge_id, payload = _issue(headers)
    barrier = Barrier(2, timeout=20)

    def attempt(member_id: str) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                redeem_trainer_check_in_challenge(
                    gym=Gym.objects.get(id=gym.id),
                    actor=GymMembership.objects.select_related("user").get(
                        id=trainer_membership.id
                    ),
                    member_id=UUID(member_id),
                    payload=payload,
                    request_id="trainer-scan-race",
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
        results = sorted(pool.map(attempt, (first, second)))
    assert results == ["created", "invalid"]
    challenge = _load(gym, _challenge_id)
    assert challenge.use_count == 1
    assert challenge.status == CheckInChallenge.Status.CONSUMED
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CheckIn.objects.filter(gym=gym, method=CheckIn.Method.QR).count() == 1


@override_settings(**jwt_settings())
def test_trainer_scan_rate_limit_does_not_touch_the_challenge() -> None:
    client, platform_access = platform_token("tscan-rate-platform@example.test")
    payload = provision(
        client, platform_access, code="TSR-A", owner_email="tscan-rate-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable_memberships(gym)
    trainer_membership = add_staff(gym, email="tscan-rate-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="TSR-ADA", name="Rate Coach")
    api = _staff_client()
    owner_headers = auth_headers(staff_token(owner))
    member_id = _member(api, owner_headers, first_name="Nila")
    today = gym_today(gym).isoformat()
    assert (
        _assign(
            api, owner_headers, trainer_id=str(ada.id), member_id=member_id, start=today
        ).status_code
        == 201
    )
    challenge_id, issued = _issue(owner_headers)
    headers = auth_headers(staff_token(trainer_membership))
    before = _effects(gym, challenge_id)
    framework = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "trainer_check_in_scan": "2/min",
            "trainer_check_in_scan_ip": "2/min",
        },
    }
    cache.clear()
    payloads = [f"{CHALLENGE_PAYLOAD_PREFIX}{'c' * 40}{index}" for index in ("1", "2", "3")]
    with override_settings(REST_FRAMEWORK=framework):
        api_settings.reload()
        first = _scan(headers, {"payload": payloads[0], "member_id": member_id})
        second = _scan(headers, {"payload": payloads[1], "member_id": member_id})
        limited = _scan(headers, {"payload": payloads[2], "member_id": member_id})
    api_settings.reload()
    _assert_invalid(first, *payloads, issued)
    _assert_invalid(second, *payloads, issued)
    assert limited.status_code == 429, limited.content
    rendered = limited.content.decode()
    for value in (*payloads, issued, member_id):
        assert value not in rendered
    _assert_untouched(gym, challenge_id, before)
    cache.clear()
