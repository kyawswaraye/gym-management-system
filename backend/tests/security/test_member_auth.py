from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.test import override_settings
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.test import APIClient, APIRequestFactory

from apps.audit.models import PlatformAuditLog
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import archive_member, link_member_identity
from apps.tenants.models import Gym, GymMembership
from apps.users.authentication import MemberJWTAuthentication, StaffJWTAuthentication
from apps.users.member_sessions import create_member_session, rotate_member_refresh_token
from apps.users.models import RefreshSession, User
from apps.users.services import create_staff_session, hash_refresh_token, rotate_refresh_token
from tests.security.test_memberships import _member
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


def _member_client() -> APIClient:
    return APIClient()


def _authenticate(token: str, authenticator: type) -> object:
    raw = APIRequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {token}")
    raw.request_id = "req_member_principal"
    with transaction.atomic():
        return authenticator().authenticate(Request(raw))


def _assert_generic(response: object, *hidden: str) -> None:
    assert response.status_code == 401, response.content
    body = response.json()
    assert body["error"]["code"] == "AUTHENTICATION_FAILED"
    assert body["error"]["message"] == "Authentication credentials are invalid."
    rendered = response.content.decode()
    for value in hidden:
        assert value not in rendered


def _linked_member(code: str, email: str) -> tuple[Gym, GymMembership, User, str]:
    client, token = platform_token(f"platform-{code}@example.test")
    payload = provision(client, token, code=code, owner_email=f"owner-{code}@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    member_id = _member(APIClient(), auth_headers(staff_token(owner)), first_name="Linked")
    user = User.objects.create_user(email, PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(member_id), user=user)
    return gym, owner, user, member_id


@override_settings(**jwt_settings())
def test_linked_member_can_login_refresh_and_logout_without_a_staff_session() -> None:
    gym, owner, user, member_id = _linked_member("MEM-AUTH", "member-auth@example.test")
    api = _member_client()
    logged_in = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD, "device_name": "pixel"},
        format="json",
    )
    assert logged_in.status_code == 200, logged_in.content
    data = logged_in.json()["data"]
    assert data["member_id"] == member_id
    assert data["gym_id"] == str(gym.id)
    assert PASSWORD not in logged_in.content.decode()
    assert "Linked" not in logged_in.content.decode()
    audit = PlatformAuditLog.objects.get(action="member_auth.login", target_id=member_id)
    recorded = str(audit.new_values)
    assert "access_token" not in recorded
    assert "refresh_token" not in recorded
    assert PASSWORD not in recorded
    assert audit.new_values == {"gym_id": str(gym.id)}

    principal, _claims = _authenticate(data["access_token"], MemberJWTAuthentication)
    assert principal.pk == user.pk
    with pytest.raises(AuthenticationFailed):
        _authenticate(data["access_token"], StaffJWTAuthentication)
    with pytest.raises(AuthenticationFailed):
        _authenticate(staff_token(owner), MemberJWTAuthentication)

    staff_pair = create_staff_session(user=owner.user, membership=owner)
    rejected_staff = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": staff_pair.refresh_token},
        format="json",
    )
    _assert_generic(rejected_staff, staff_pair.refresh_token)
    assert (
        RefreshSession.objects.get(
            token_hash=hash_refresh_token(staff_pair.refresh_token)
        ).revoked_at
        is None
    )
    assert rotate_refresh_token(staff_pair.refresh_token, request_id="req_staff_kept").access_token

    bff = APIClient()
    bff.defaults["HTTP_X_GYM_BFF_SECRET"] = settings.BFF_SHARED_SECRET
    rejected_member = bff.post(
        "/api/v1/auth/refresh/",
        {"refresh_token": data["refresh_token"]},
        format="json",
    )
    _assert_generic(rejected_member)
    assert (
        RefreshSession.objects.get(token_hash=hash_refresh_token(data["refresh_token"])).revoked_at
        is None
    )

    refreshed = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": data["refresh_token"]},
        format="json",
    )
    assert refreshed.status_code == 200, refreshed.content
    fresh = refreshed.json()["data"]
    logout = api.post(
        "/api/v1/member-auth/logout/",
        {"refresh_token": fresh["refresh_token"]},
        format="json",
    )
    assert logout.status_code == 204
    with pytest.raises(AuthenticationFailed):
        _authenticate(fresh["access_token"], MemberJWTAuthentication)
    denied = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": fresh["refresh_token"]},
        format="json",
    )
    _assert_generic(denied)


@override_settings(**jwt_settings())
def test_member_login_failures_do_not_reveal_or_select_a_member() -> None:
    gym, _owner, user, member_id = _linked_member("MEM-DENY", "member-deny@example.test")
    api = _member_client()
    unknown = api.post(
        "/api/v1/member-auth/login/",
        {"email": "missing-member@example.test", "password": PASSWORD},
        format="json",
    )
    wrong = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": "not-the-password"},
        format="json",
    )
    unlinked = User.objects.create_user("unlinked-member@example.test", PASSWORD)
    no_link = api.post(
        "/api/v1/member-auth/login/",
        {"email": unlinked.email, "password": PASSWORD},
        format="json",
    )
    _assert_generic(unknown, "missing-member@example.test")
    _assert_generic(wrong, user.email, member_id)
    _assert_generic(no_link, unlinked.email)
    assert unknown.json()["error"] == wrong.json()["error"] == no_link.json()["error"]

    user.mfa_enabled = True
    user.mfa_secret_encrypted = "configured"
    user.save(update_fields=["mfa_enabled", "mfa_secret_encrypted", "updated_at"])
    mfa = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    _assert_generic(mfa, member_id)
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.save(update_fields=["mfa_enabled", "mfa_secret_encrypted", "updated_at"])

    second_gym, _second_owner, _same_user, second_member = _linked_member(
        "MEM-TWO", "member-two-owner@example.test"
    )
    link_member_identity(gym=second_gym, member_id=UUID(second_member), user=user)
    ambiguous = api.post(
        "/api/v1/member-auth/login/",
        {
            "email": user.email,
            "password": PASSWORD,
            "member_id": member_id,
            "gym_id": str(gym.id),
        },
        format="json",
    )
    assert ambiguous.status_code == 400
    assert RefreshSession.objects.filter(user=user, principal_type="MEMBER").count() == 0
    clean = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    _assert_generic(clean, member_id, second_member, gym.code, second_gym.code)
    assert RefreshSession.objects.filter(user=user, principal_type="MEMBER").count() == 0


@override_settings(**jwt_settings())
def test_member_access_follows_status_authz_link_and_subscription() -> None:
    gym, owner, user, member_id = _linked_member("MEM-LIFE", "member-life@example.test")
    api = _member_client()
    logged_in = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert logged_in.status_code == 200, logged_in.content
    access = logged_in.json()["data"]["access_token"]
    refresh = logged_in.json()["data"]["refresh_token"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        member.authz_version += 1
        member.save(update_fields=["authz_version", "updated_at"])
    with pytest.raises(AuthenticationFailed):
        _authenticate(access, MemberJWTAuthentication)
    refreshed = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": refresh},
        format="json",
    )
    assert refreshed.status_code == 200, refreshed.content
    current_access = refreshed.json()["data"]["access_token"]
    current_refresh = refreshed.json()["data"]["refresh_token"]
    assert _authenticate(current_access, MemberJWTAuthentication)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.filter(id=member_id).update(status=Member.Status.SUSPENDED)
    suspended = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": current_refresh},
        format="json",
    )
    _assert_generic(suspended)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.filter(id=member_id).update(status=Member.Status.ACTIVE)
    Gym.objects.filter(id=gym.id).update(subscription_status=Gym.SubscriptionStatus.PAST_DUE)
    inactive_gym = api.post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    _assert_generic(inactive_gym, gym.code)
    Gym.objects.filter(id=gym.id).update(subscription_status=Gym.SubscriptionStatus.ACTIVE)

    replacement = User.objects.create_user("member-relink@example.test", PASSWORD)
    link_member_identity(gym=gym, member_id=UUID(member_id), user=replacement)
    stale = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": refreshed.json()["data"]["refresh_token"]},
        format="json",
    )
    _assert_generic(stale)
    remaining = RefreshSession.objects.filter(
        user=user, principal_type="MEMBER", revoked_at__isnull=True
    )
    assert remaining.count() == 0
    relinked = api.post(
        "/api/v1/member-auth/login/",
        {"email": replacement.email, "password": PASSWORD},
        format="json",
    )
    assert relinked.status_code == 200, relinked.content

    staff_pair = create_staff_session(user=owner.user, membership=owner)
    archive_member(gym=gym, actor=owner, member_id=UUID(member_id))
    archived = api.post(
        "/api/v1/member-auth/refresh/",
        {"refresh_token": relinked.json()["data"]["refresh_token"]},
        format="json",
    )
    _assert_generic(archived)
    session = RefreshSession.objects.get(
        token_hash=hash_refresh_token(relinked.json()["data"]["refresh_token"])
    )
    assert session.revocation_reason == "MEMBER_ARCHIVED"
    kept = rotate_refresh_token(staff_pair.refresh_token, request_id="req_staff_after_archive")
    assert kept.access_token


@override_settings(**jwt_settings())
def test_member_bootstrap_returns_only_the_linked_identity_for_the_current_transaction() -> None:
    gym, _owner, user, member_id = _linked_member("MEM-BOOT", "member-boot@example.test")
    other = User.objects.create_user("member-boot-other@example.test", PASSWORD)

    def rows(user_setting: str | None) -> list[tuple[object, ...]]:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                if user_setting is not None:
                    cursor.execute(
                        "SELECT set_config('app.user_id', %s, true)",
                        [user_setting],
                    )
                cursor.execute("SELECT member_id, gym_id, authz_version FROM app_linked_members()")
                return list(cursor.fetchall())

    assert rows(None) == []
    assert rows("not-a-uuid") == []
    assert rows(str(other.id)) == []
    found = rows(str(user.id))
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        version = Member.objects.get(id=member_id).authz_version
    assert found == [(UUID(member_id), gym.id, version)]
    assert rows(None) == []

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_function_result('public.app_linked_members()'::regprocedure), "
            "pg_get_functiondef('public.app_linked_members()'::regprocedure)"
        )
        result, definition = cursor.fetchone()
        cursor.execute(
            """
            SELECT
              has_column_privilege(
                'gym_auth_bootstrap', 'members_member', 'first_name', 'SELECT'
              ),
              has_column_privilege(
                'gym_auth_bootstrap', 'members_member', 'email', 'SELECT'
              ),
              has_column_privilege(
                'gym_auth_bootstrap', 'members_member', 'phone', 'SELECT'
              ),
              has_column_privilege(
                'gym_auth_bootstrap', 'members_member', 'id', 'SELECT'
              ),
              has_column_privilege(
                'gym_auth_bootstrap', 'members_member', 'linked_user_id', 'SELECT'
              )
            """
        )
        privileges = cursor.fetchone()
    assert result == "TABLE(member_id uuid, gym_id uuid, authz_version integer)"
    assert "first_name" not in definition
    assert "email" not in definition
    assert "phone" not in definition
    assert privileges == (False, True, False, True, True)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT first_name FROM members_member WHERE id = %s", [member_id])
            assert cursor.fetchall() == []


@override_settings(**jwt_settings())
def test_member_refresh_replacement_stays_in_family_and_replay_revokes_it() -> None:
    gym, _owner, user, member_id = _linked_member("MEM-ROT", "member-rot@example.test")
    _other_gym, _other_owner, _other_user, other_member = _linked_member(
        "MEM-ROT-B", "member-rot-b@example.test"
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        first = create_member_session(
            user_id=user.id, member=member, device_name="one", ip_address=None
        )
        second = create_member_session(
            user_id=user.id, member=member, device_name="two", ip_address=None
        )
    original = RefreshSession.objects.get(token_hash=hash_refresh_token(first.refresh_token))
    sibling = RefreshSession.objects.get(token_hash=hash_refresh_token(second.refresh_token))
    with pytest.raises(DatabaseError, match="refresh replacement context mismatch"):
        with transaction.atomic():
            original.replaced_by_session = sibling
            original.save(update_fields=["replaced_by_session", "updated_at"])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RefreshSession.objects.filter(pk=original.pk).update(member_id=other_member)
    rotated = rotate_member_refresh_token(first.refresh_token, request_id="req_member_rotate")
    with pytest.raises(AuthenticationFailed):
        rotate_member_refresh_token(first.refresh_token, request_id="req_member_replay")
    family = RefreshSession.objects.filter(family_id=original.family_id)
    assert family.filter(revoked_at__isnull=True).count() == 0
    assert family.filter(revocation_reason="REFRESH_REPLAY").count() == 2
    assert rotated[0].access_token
    assert (
        RefreshSession.objects.get(token_hash=hash_refresh_token(second.refresh_token)).revoked_at
        is None
    )


@override_settings(**jwt_settings())
def test_concurrent_member_refresh_reuse_revokes_the_family() -> None:
    gym, _owner, user, member_id = _linked_member("MEM-RACE", "member-race@example.test")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.get(id=member_id)
        issued = create_member_session(
            user_id=user.id, member=member, device_name="race", ip_address=None
        )
    family_id = RefreshSession.objects.get(
        token_hash=hash_refresh_token(issued.refresh_token)
    ).family_id
    barrier = Barrier(2)

    def attempt() -> str:
        try:
            barrier.wait(timeout=5)
            rotate_member_refresh_token(issued.refresh_token, request_id="req_member_race")
            return "rotated"
        except AuthenticationFailed:
            return "replay"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(attempt), pool.submit(attempt))]
    assert sorted(results) == ["replay", "rotated"]
    assert RefreshSession.objects.filter(family_id=family_id, revoked_at__isnull=True).count() == 0


@override_settings(**jwt_settings())
def test_member_login_is_rate_limited_without_confirming_the_account() -> None:
    _gym, _owner, user, member_id = _linked_member("MEM-RATE", "member-rate@example.test")
    framework = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "member_login": "2/min",
        },
    }
    cache.clear()
    api = _member_client()
    with override_settings(REST_FRAMEWORK=framework):
        api_settings.reload()
        for _attempt in range(2):
            denied = api.post(
                "/api/v1/member-auth/login/",
                {"email": user.email, "password": "not-the-password"},
                format="json",
            )
            _assert_generic(denied, member_id)
        limited = api.post(
            "/api/v1/member-auth/login/",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
    api_settings.reload()
    assert limited.status_code == 429
    assert member_id not in limited.content.decode()
    assert RefreshSession.objects.filter(user=user, principal_type="MEMBER").count() == 0
    cache.clear()


@override_settings(**jwt_settings())
def test_member_and_staff_auth_throttles_do_not_exhaust_each_other() -> None:
    member_limit = int(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["member_login_ip"].split("/")[0]
    )
    anon_limit = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"].split("/")[0])
    member = _member_client()
    staff = APIClient()
    staff.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login_body = {"email": "boundary@example.test", "password": "not-the-password"}
    cache.clear()
    try:
        for _attempt in range(member_limit):
            response = member.post("/api/v1/member-auth/login/", login_body, format="json")
            assert response.status_code in {401, 429}, response.content
        exhausted = member.post("/api/v1/member-auth/login/", login_body, format="json")
        assert exhausted.status_code == 429, exhausted.content
        staff_login = staff.post("/api/v1/auth/login/", login_body, format="json")
        assert staff_login.status_code != 429, staff_login.content
        selected = staff.post(
            "/api/v1/auth/select-gym/",
            {
                "gym_membership_id": "00000000-0000-4000-8000-000000000001",
                "gym_selection_ticket": "not-a-selection-ticket",
            },
            format="json",
        )
        assert selected.status_code != 429, selected.content

        cache.clear()
        for attempt in range(anon_limit):
            allowed = staff.post(
                "/api/v1/auth/login/",
                {"email": f"staff-{attempt}@example.test", "password": "not-the-password"},
                format="json",
            )
            assert allowed.status_code != 429, allowed.content
        blocked = staff.post(
            "/api/v1/auth/login/",
            {"email": "staff-blocked@example.test", "password": "not-the-password"},
            format="json",
        )
        assert blocked.status_code == 429, blocked.content
        still_open = member.post("/api/v1/member-auth/login/", login_body, format="json")
        _assert_generic(still_open)
        refreshed = member.post(
            "/api/v1/member-auth/refresh/",
            {"refresh_token": "not-a-member-refresh-token"},
            format="json",
        )
        assert refreshed.status_code == 401, refreshed.content
        logged_out = member.post(
            "/api/v1/member-auth/logout/",
            {"refresh_token": "not-a-member-refresh-token"},
            format="json",
        )
        assert logged_out.status_code != 429, logged_out.content
    finally:
        cache.clear()
