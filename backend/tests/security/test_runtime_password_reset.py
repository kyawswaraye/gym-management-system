from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.users.models import PasswordResetToken, User
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


@contextmanager
def _as_runtime() -> Iterator[None]:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")


def _deny(statement: str, params: list[object] | None = None) -> None:
    with (
        pytest.raises(DatabaseError, match=r"permission denied|password reset transition denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(statement, params)


def test_runtime_cannot_update_password_directly_but_can_use_reset_function() -> None:
    user = User.objects.create_user("reset.runtime@example.test", PASSWORD)
    now = timezone.now()
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_password_reset_token("live-reset-token"),
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    encoded = make_password("replaced horse battery staple", hasher="pbkdf2_sha256")
    _deny(
        "UPDATE public.users_user SET password = %s WHERE id = %s",
        [encoded, user.id],
    )
    with _as_runtime(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT app_accept_password_reset(%s, %s, %s)",
            [token.token_hash, encoded, now],
        )
        cursor.execute("SELECT app_bump_password_reset_authz(%s)", [user.id])
    user.refresh_from_db()
    token.refresh_from_db()
    assert user.check_password("replaced horse battery staple")
    assert token.consumed_at is not None


def test_runtime_reset_function_rejects_md5_and_expired_tokens() -> None:
    user = User.objects.create_user("reset.denied@example.test", PASSWORD)
    now = timezone.now()
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_password_reset_token("expired-reset-token"),
        issued_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=1),
    )
    md5 = make_password("replaced horse battery staple", hasher="md5")
    live_hash = hash_password_reset_token("unused")
    PasswordResetToken.objects.create(
        user=User.objects.create_user("reset.denied.live@example.test", PASSWORD),
        token_hash=live_hash,
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    _deny(
        "SELECT app_accept_password_reset(%s, %s, %s)",
        [live_hash, md5, now],
    )
    _deny(
        "SELECT app_accept_password_reset(%s, %s, %s)",
        [
            token.token_hash,
            make_password("replaced horse battery staple", hasher="pbkdf2_sha256"),
            now,
        ],
    )
