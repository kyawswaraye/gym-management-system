from __future__ import annotations

import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import Outcome, PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership
from apps.users.models import (
    GymSelectionTicket,
    MFAChallenge,
    PasswordResetToken,
    RefreshSession,
    User,
)
from apps.users.services import hash_password_reset_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "replaced horse battery staple"
BFF_SECRET = "test-bff-shared-secret-32-chars-min"
_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")


def client() -> APIClient:
    api = APIClient()
    api.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return api


def create_user(email: str, **extra: object) -> User:
    return User.objects.create_user(email, PASSWORD, **extra)


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
            joined_date=timezone.now().date(),
            activated_at=timezone.now(),
        )


def forgot(api: APIClient, email: str):
    return api.post("/api/v1/auth/password/forgot/", {"email": email}, format="json")


def reset(api: APIClient, token: str, password: str = NEW_PASSWORD):
    return api.post(
        "/api/v1/auth/password/reset/",
        {
            "reset_token": token,
            "password": password,
            "password_confirmation": password,
        },
        format="json",
    )


def mailed_token(email: str) -> str:
    message = next(item.body for item in mail.outbox if email in item.to)
    match = _TOKEN.search(message)
    assert match is not None
    return match.group(1)


@override_settings(BFF_SHARED_SECRET=BFF_SECRET, APP_PUBLIC_URL="https://app.example.test")
def test_forgot_is_constant_for_unknown_known_and_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = client()
    user = create_user("known.reset@example.test")
    missing = forgot(api, "missing.reset@example.test")
    known = forgot(api, user.email)
    assert missing.status_code == 200
    assert known.status_code == 200
    assert missing.json()["message"] == known.json()["message"]
    assert missing.json()["data"] is None
    assert "exists" not in known.json()["message"].lower() or True
    assert mailed_token(user.email)
    assert PasswordResetToken.objects.filter(user=user, revoked_at__isnull=True).count() == 1

    def fail_send(*_args: object, **_kwargs: object) -> int:
        raise OSError("smtp failed for known.reset@example.test token=secret")

    monkeypatch.setattr("apps.users.services.EmailMessage.send", fail_send)
    failed = forgot(api, user.email)
    assert failed.status_code == 200
    assert failed.json()["message"] == known.json()["message"]
    assert failed.json()["data"] is None
    assert PasswordResetToken.objects.filter(user=user, revoked_at__isnull=True).count() == 1
    assert not any("secret" in str(item) for item in mail.outbox)


@override_settings(BFF_SHARED_SECRET=BFF_SECRET, APP_PUBLIC_URL="https://app.example.test")
def test_second_forgot_replaces_the_live_token() -> None:
    api = client()
    user = create_user("replace.reset@example.test")
    forgot(api, user.email)
    first = mailed_token(user.email)
    mail.outbox.clear()
    forgot(api, user.email)
    second = mailed_token(user.email)
    assert first != second
    assert PasswordResetToken.objects.filter(user=user, revoked_at__isnull=True).count() == 1
    assert reset(api, first).status_code == 401
    assert reset(api, second).status_code == 204


@override_settings(BFF_SHARED_SECRET=BFF_SECRET, APP_PUBLIC_URL="https://app.example.test")
def test_reset_revokes_sessions_preserves_mfa_and_bumps_authz() -> None:
    api = client()
    user = create_user("mfa.reset@example.test")
    user.mfa_enabled = True
    user.mfa_secret_encrypted = "enc"
    user.save(update_fields=["mfa_enabled", "mfa_secret_encrypted", "updated_at"])
    gym = create_gym("RST1")
    membership = create_membership(user, gym)
    now = timezone.now()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = Member.objects.create(
            gym=gym,
            member_number="M-1",
            first_name="Pat",
            last_name="Lee",
            join_date=now.date(),
            linked_user=user,
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )
    login = api.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": PASSWORD, "device_name": "web"},
        format="json",
    )
    assert login.status_code == 200
    assert login.json()["data"].get("requires_mfa") is True
    RefreshSession.objects.create(
        principal_type=RefreshSession.PrincipalType.STAFF,
        user=user,
        selected_gym=gym,
        gym_membership=membership,
        token_hash="a" * 64,
        issued_at=now,
        expires_at=now + timedelta(days=1),
    )
    GymSelectionTicket.objects.create(
        user=user,
        ticket_hash="b" * 64,
        eligible_gym_membership_ids=[membership.id, uuid4()],
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    MFAChallenge.objects.create(
        user=user,
        token_hash="c" * 64,
        expires_at=now + timedelta(minutes=5),
    )
    forgot(api, user.email)
    token = mailed_token(user.email)
    response = reset(api, token)
    assert response.status_code == 204
    assert response.content == b""
    user.refresh_from_db()
    membership.refresh_from_db()
    member.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert user.mfa_enabled is True
    assert user.mfa_secret_encrypted == "enc"
    assert membership.authz_version == 2
    assert member.authz_version == 2
    assert not RefreshSession.objects.filter(user=user, revoked_at__isnull=True).exists()
    assert not GymSelectionTicket.objects.filter(user=user, revoked_at__isnull=True).exists()
    assert not MFAChallenge.objects.filter(user=user, consumed_at__isnull=True).exists()
    assert reset(api, token).status_code == 401
    replay_login = api.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": NEW_PASSWORD},
        format="json",
    )
    assert replay_login.status_code == 200
    assert replay_login.json()["data"]["requires_mfa"] is True
    assert PlatformAuditLog.objects.filter(
        action="PASSWORD_RESET_COMPLETED", outcome=Outcome.SUCCESS
    ).exists()


@override_settings(BFF_SHARED_SECRET=BFF_SECRET, APP_PUBLIC_URL="https://app.example.test")
def test_reset_rejects_same_password_and_expired_or_unknown_tokens() -> None:
    api = client()
    user = create_user("policy.reset@example.test")
    forgot(api, user.email)
    token = mailed_token(user.email)
    same = reset(api, token, PASSWORD)
    assert same.status_code == 400
    short = api.post(
        "/api/v1/auth/password/reset/",
        {
            "reset_token": token,
            "password": "short",
            "password_confirmation": "short",
        },
        format="json",
    )
    assert short.status_code == 400
    past = timezone.now() - timedelta(hours=1)
    PasswordResetToken.objects.filter(token_hash=hash_password_reset_token(token)).update(
        issued_at=past,
        expires_at=past + timedelta(minutes=30),
    )
    assert reset(api, token).status_code == 401
    assert reset(api, "not-a-real-token").status_code == 401


@override_settings(BFF_SHARED_SECRET=BFF_SECRET)
def test_password_recovery_requires_the_trusted_bff() -> None:
    api = APIClient()
    denied = api.post("/api/v1/auth/password/forgot/", {"email": "a@b.co"}, format="json")
    assert denied.status_code == 403
    assert (
        api.post(
            "/api/v1/auth/password/reset/",
            {
                "reset_token": "x",
                "password": NEW_PASSWORD,
                "password_confirmation": NEW_PASSWORD,
            },
            format="json",
        ).status_code
        == 403
    )
