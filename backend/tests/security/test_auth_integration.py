from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership
from apps.users.models import GymSelectionTicket, RefreshSession, User
from apps.users.services import issue_gym_selection_ticket
from apps.users.tokens import decode_access_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"
BFF_SECRET = "test-bff-shared-secret-32-chars-min"


def _keys() -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


PRIVATE_PEM, PUBLIC_PEM = _keys()
JWT_SETTINGS = {
    "REFRESH_TOKEN_PEPPER": "test-pepper",
    "JWT_PRIVATE_KEY": PRIVATE_PEM,
    "JWT_PUBLIC_KEYS": {"test-key": PUBLIC_PEM},
    "JWT_KEY_ID": "test-key",
    "BFF_SHARED_SECRET": BFF_SECRET,
}


def client() -> APIClient:
    api = APIClient()
    api.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return api


def create_gym(code: str) -> Gym:
    return Gym.objects.create(
        code=code,
        name=code,
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )


def create_membership(user: User, gym: Gym) -> GymMembership:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )


def login(api: APIClient, email: str) -> dict:
    response = api.post(
        "/api/v1/auth/login/", {"email": email, "password": PASSWORD}, format="json"
    )
    return {"status": response.status_code, "body": response.json()}


@override_settings(**JWT_SETTINGS)
def test_login_with_zero_memberships_is_rejected() -> None:
    User.objects.create_user("zero@example.test", PASSWORD)
    result = login(client(), "zero@example.test")
    assert result["status"] == 401
    assert result["body"]["error"]["code"] == "AUTHENTICATION_FAILED"


@override_settings(**JWT_SETTINGS)
def test_login_with_one_membership_issues_tokens_without_ticket() -> None:
    user = User.objects.create_user("one@example.test", PASSWORD)
    gym = create_gym("ONE")
    membership = create_membership(user, gym)
    result = login(client(), "one@example.test")
    data = result["body"]["data"]
    assert result["status"] == 200
    assert data["requires_gym_selection"] is False
    assert "gym_selection_ticket" not in data
    claims = decode_access_token(data["access_token"])
    assert claims["gym_id"] == str(gym.id)
    assert claims["gym_membership_id"] == str(membership.id)
    assert RefreshSession.objects.filter(id=claims["session_id"]).exists()


@override_settings(**JWT_SETTINGS)
def test_login_with_multiple_memberships_issues_ticket_only() -> None:
    user = User.objects.create_user("many@example.test", PASSWORD)
    create_membership(user, create_gym("MANY-A"))
    create_membership(user, create_gym("MANY-B"))
    result = login(client(), "many@example.test")
    data = result["body"]["data"]
    ticket = GymSelectionTicket.objects.get(user=user, consumed_at__isnull=True)
    assert result["status"] == 200
    assert data["requires_gym_selection"] is True
    assert data["gym_selection_ticket"]
    assert "access_token" not in data
    assert "refresh_token" not in data
    assert data["gym_selection_ticket"] not in ticket.ticket_hash
    assert (ticket.expires_at - ticket.issued_at).total_seconds() == 300
    assert RefreshSession.objects.filter(user=user).count() == 0
    assert PlatformAuditLog.objects.filter(action="TICKET_ISSUED", actor_user=user).exists()


@override_settings(**JWT_SETTINGS)
def test_valid_ticket_creates_selected_session_and_me() -> None:
    user = User.objects.create_user("pick@example.test", PASSWORD)
    gym_a = create_gym("PICK-A")
    gym_b = create_gym("PICK-B")
    membership_a = create_membership(user, gym_a)
    create_membership(user, gym_b)
    issued = login(client(), "pick@example.test")["body"]["data"]
    api = client()
    selected = api.post(
        "/api/v1/auth/select-gym/",
        {
            "gym_membership_id": str(membership_a.id),
            "gym_selection_ticket": issued["gym_selection_ticket"],
        },
        format="json",
    )
    data = selected.json()["data"]
    claims = decode_access_token(data["access_token"])
    assert selected.status_code == 200
    assert claims["gym_id"] == str(gym_a.id)
    assert claims["gym_membership_id"] == str(membership_a.id)
    assert claims["session_id"]
    me = api.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {data['access_token']}")
    assert me.status_code == 200
    assert me.json()["data"]["gym"]["id"] == str(gym_a.id)
    assert PlatformAuditLog.objects.filter(action="TICKET_CONSUMED", actor_user=user).exists()


@override_settings(**JWT_SETTINGS)
def test_ticket_replay_is_rejected() -> None:
    user = User.objects.create_user("replay@example.test", PASSWORD)
    membership = create_membership(user, create_gym("REPLAY-A"))
    create_membership(user, create_gym("REPLAY-B"))
    ticket = login(client(), "replay@example.test")["body"]["data"]["gym_selection_ticket"]
    api = client()
    first = api.post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership.id), "gym_selection_ticket": ticket},
        format="json",
    )
    second = api.post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership.id), "gym_selection_ticket": ticket},
        format="json",
    )
    assert first.status_code == 200
    assert second.status_code == 401
    assert RefreshSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1
    assert PlatformAuditLog.objects.filter(action="TICKET_REJECTED", actor_user=user).exists()


@override_settings(**JWT_SETTINGS)
def test_concurrent_ticket_consume_creates_one_session() -> None:
    user = User.objects.create_user("race@example.test", PASSWORD)
    membership = create_membership(user, create_gym("RACE-A"))
    create_membership(user, create_gym("RACE-B"))
    ticket = login(client(), "race@example.test")["body"]["data"]["gym_selection_ticket"]

    def consume() -> int:
        connections.close_all()
        api = client()
        response = api.post(
            "/api/v1/auth/select-gym/",
            {"gym_membership_id": str(membership.id), "gym_selection_ticket": ticket},
            format="json",
        )
        connections.close_all()
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: consume(), range(2)))
    assert statuses.count(200) == 1
    assert statuses.count(401) == 1
    assert RefreshSession.objects.filter(user=user).count() == 1


@override_settings(**JWT_SETTINGS)
def test_expired_ticket_is_rejected() -> None:
    user = User.objects.create_user("expired@example.test", PASSWORD)
    membership = create_membership(user, create_gym("EXP-A"))
    create_membership(user, create_gym("EXP-B"))
    ticket = login(client(), "expired@example.test")["body"]["data"]["gym_selection_ticket"]
    record = GymSelectionTicket.objects.get(user=user, consumed_at__isnull=True)
    record.issued_at = timezone.now() - timedelta(minutes=6)
    record.expires_at = record.issued_at + timedelta(minutes=5)
    record.save(update_fields=["issued_at", "expires_at", "updated_at"])
    response = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership.id), "gym_selection_ticket": ticket},
        format="json",
    )
    assert response.status_code == 401
    assert RefreshSession.objects.filter(user=user).count() == 0
    record.refresh_from_db()
    assert record.revoked_at is not None


@override_settings(**JWT_SETTINGS)
def test_wrong_and_inactive_membership_are_rejected() -> None:
    user = User.objects.create_user("wrong@example.test", PASSWORD)
    gym_a = create_gym("WRONG-A")
    gym_b = create_gym("WRONG-B")
    membership_a = create_membership(user, gym_a)
    membership_b = create_membership(user, gym_b)
    ticket = login(client(), "wrong@example.test")["body"]["data"]["gym_selection_ticket"]
    foreign = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(uuid4()), "gym_selection_ticket": ticket},
        format="json",
    )
    assert foreign.status_code == 401
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        membership_a.status = GymMembership.Status.SUSPENDED
        membership_a.save(update_fields=["status", "updated_at"])
    inactive = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership_a.id), "gym_selection_ticket": ticket},
        format="json",
    )
    assert inactive.status_code == 401
    assert RefreshSession.objects.filter(user=user).count() == 0
    still_valid = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership_b.id), "gym_selection_ticket": ticket},
        format="json",
    )
    assert still_valid.status_code == 401


@override_settings(**JWT_SETTINGS)
def test_user_cannot_consume_another_users_ticket() -> None:
    owner = User.objects.create_user("owner-ticket@example.test", PASSWORD)
    stranger = User.objects.create_user("stranger@example.test", PASSWORD)
    membership = create_membership(owner, create_gym("OWN-A"))
    create_membership(owner, create_gym("OWN-B"))
    stranger_membership = create_membership(stranger, create_gym("STR-A"))
    create_membership(stranger, create_gym("STR-B"))
    owner_ticket = login(client(), "owner-ticket@example.test")["body"]["data"][
        "gym_selection_ticket"
    ]
    stolen = client().post(
        "/api/v1/auth/select-gym/",
        {
            "gym_membership_id": str(stranger_membership.id),
            "gym_selection_ticket": owner_ticket,
        },
        format="json",
    )
    assert stolen.status_code == 401
    assert RefreshSession.objects.filter(user=stranger).count() == 0
    owned = client().post(
        "/api/v1/auth/select-gym/",
        {
            "gym_membership_id": str(membership.id),
            "gym_selection_ticket": owner_ticket,
        },
        format="json",
    )
    assert owned.status_code == 200
    assert decode_access_token(owned.json()["data"]["access_token"])["sub"] == str(owner.id)


@override_settings(**JWT_SETTINGS)
def test_old_access_token_cannot_bypass_ticket_selection() -> None:
    user = User.objects.create_user("stale@example.test", PASSWORD)
    first_gym = create_gym("STALE-1")
    first_membership = create_membership(user, first_gym)
    first_login = login(client(), "stale@example.test")
    old_access = first_login["body"]["data"]["access_token"]
    create_membership(user, create_gym("STALE-2"))
    multi = login(client(), "stale@example.test")["body"]["data"]
    assert multi["requires_gym_selection"] is True
    assert "access_token" not in multi
    bypass = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(first_membership.id)},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {old_access}",
    )
    assert bypass.status_code == 401
    me = client().get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {old_access}")
    assert me.status_code == 401
    selected = client().post(
        "/api/v1/auth/select-gym/",
        {
            "gym_membership_id": str(first_membership.id),
            "gym_selection_ticket": multi["gym_selection_ticket"],
        },
        format="json",
    )
    assert selected.status_code == 200


@override_settings(**JWT_SETTINGS)
def test_browser_body_ticket_is_not_required_when_missing_and_untrusted_auth_is_denied() -> None:
    response = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(uuid4())},
        format="json",
    )
    assert response.status_code == 401
    denied = APIClient().post(
        "/api/v1/auth/login/",
        {"email": "anyone@example.test", "password": PASSWORD},
        format="json",
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "BFF_REQUIRED"


@override_settings(**JWT_SETTINGS)
def test_selected_session_claims_match_membership() -> None:
    user = User.objects.create_user("claims@example.test", PASSWORD)
    gym_a = create_gym("CLM-A")
    gym_b = create_gym("CLM-B")
    membership = create_membership(user, gym_a)
    create_membership(user, gym_b)
    memberships = [
        {"gym_membership_id": str(membership.id), "gym_id": str(gym_a.id)},
        {"gym_membership_id": str(uuid4()), "gym_id": str(gym_b.id)},
    ]
    plaintext, _expires = issue_gym_selection_ticket(
        user=user, memberships=memberships, request_id="req_claims"
    )
    selected = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership.id), "gym_selection_ticket": plaintext},
        format="json",
    )
    token = selected.json()["data"]["access_token"]
    claims = jwt.decode(
        token,
        PUBLIC_PEM,
        algorithms=["RS256"],
        audience="gym-platform",
        issuer="gym-platform-api",
    )
    assert claims["gym_id"] == str(gym_a.id)
    assert claims["gym_membership_id"] == str(membership.id)
    assert RefreshSession.objects.filter(id=claims["session_id"], user=user).exists()


@override_settings(**JWT_SETTINGS)
def test_select_gym_jwt_path_switches_membership_inside_atomic() -> None:
    user = User.objects.create_user("switch@example.test", PASSWORD)
    gym_a = create_gym("SW-A")
    gym_b = create_gym("SW-B")
    membership_a = create_membership(user, gym_a)
    membership_b = create_membership(user, gym_b)
    ticket = login(client(), "switch@example.test")["body"]["data"]["gym_selection_ticket"]
    first = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership_a.id), "gym_selection_ticket": ticket},
        format="json",
    )
    access = first.json()["data"]["access_token"]
    first_session = decode_access_token(access)["session_id"]
    switched = client().post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(membership_b.id)},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    data = switched.json()["data"]
    claims = decode_access_token(data["access_token"])
    assert first.status_code == 200
    assert switched.status_code == 200
    assert claims["gym_id"] == str(gym_b.id)
    assert claims["gym_membership_id"] == str(membership_b.id)
    assert claims["session_id"] != first_session
    assert RefreshSession.objects.get(id=first_session).revoked_at is not None
    me = client().get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {data['access_token']}")
    assert me.status_code == 200
    assert me.json()["data"]["gym"]["id"] == str(gym_b.id)
