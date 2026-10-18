"""Least-privilege identity writes for the runtime database role.

Direct ``INSERT`` and password ``UPDATE`` are revoked from ``gym_runtime``.
Invitation provisioning and acceptance call the security-definer functions
created in users migration 0006. Password installation always uses Django's
PBKDF2 hasher, which is the only encoded form that function accepts.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.users.models import User

_PASSWORD_DENIED = "invitation password transition denied"
_RESET_DENIED = "password reset transition denied"
_AUTHZ_DENIED = "password reset authz bump denied"
_PROFILE_DENIED = "invited profile confirmation denied"


class InvitedIdentityError(Exception):
    """The database rejected an invitation identity or password transition."""


def get_or_create_invited_identity(
    *,
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
) -> User:
    normalized = email.strip().lower()
    try:
        return User.objects.get(normalized_email=normalized)
    except User.DoesNotExist:
        try:
            with transaction.atomic():
                return _insert_invited_identity(
                    email=normalized,
                    first_name=first_name.strip(),
                    last_name=last_name.strip(),
                    phone=phone.strip(),
                )
        except IntegrityError:
            return User.objects.get(normalized_email=normalized)


def set_invitation_password(
    *,
    token_hash: str,
    user: User,
    password: str,
    changed_at: datetime,
) -> None:
    encoded = make_password(password, hasher="pbkdf2_sha256")
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                [token_hash, encoded, changed_at],
            )
        except DatabaseError as exc:
            if _PASSWORD_DENIED in str(exc):
                raise InvitedIdentityError() from exc
            raise
    user.password = encoded
    user.password_changed_at = changed_at


def set_reset_password(
    *,
    token_hash: str,
    user: User,
    password: str,
    changed_at: datetime,
) -> None:
    encoded = make_password(password, hasher="pbkdf2_sha256")
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                "SELECT app_accept_password_reset(%s, %s, %s)",
                [token_hash, encoded, changed_at],
            )
        except DatabaseError as exc:
            if _RESET_DENIED in str(exc):
                raise InvitedIdentityError() from exc
            raise
    user.password = encoded
    user.password_changed_at = changed_at


def bump_password_reset_authz(*, user_id: UUID) -> None:
    with connection.cursor() as cursor:
        try:
            cursor.execute("SELECT app_bump_password_reset_authz(%s)", [user_id])
        except DatabaseError as exc:
            if _AUTHZ_DENIED in str(exc) or _RESET_DENIED in str(exc):
                raise InvitedIdentityError() from exc
            raise


def confirm_invited_profile(
    *,
    token_hash: str,
    first_name: str,
    last_name: str,
    phone: str,
    changed_at: datetime,
) -> None:
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                "SELECT app_confirm_invited_profile(%s, %s, %s, %s, %s)",
                [token_hash, first_name, last_name, phone, changed_at],
            )
        except DatabaseError as exc:
            if _PROFILE_DENIED in str(exc):
                raise InvitedIdentityError() from exc
            raise


def _insert_invited_identity(
    *,
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
) -> User:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT app_create_invited_identity(%s, %s, %s, %s)",
            [email, first_name, last_name, phone],
        )
        row = cursor.fetchone()
    if row is None:
        raise InvitedIdentityError()
    return User.objects.get(pk=row[0])
