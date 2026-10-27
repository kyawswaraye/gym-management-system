"""Member sign-in from a verified Google ID token.

Staff still create the member and store the contact email. The first successful
Google sign-in creates a passwordless user and links that member. A later
sign-in reuses the link. The token's email is taken only after Google signature
checks, never from a client-supplied email field.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from jwt.exceptions import PyJWKClientError
from rest_framework.exceptions import ValidationError

from apps.common.api.exceptions import (
    AuthenticationFailed,
    GoogleAccountAlreadyLinked,
    GoogleSignInNotConfigured,
    MemberAccountAmbiguous,
    MemberAccountInactive,
    MemberAccountLinkedElsewhere,
    MemberAccountMissing,
    MemberSignInUnavailable,
)
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.normalization import normalize_member_email
from apps.members.services import link_member_identity
from apps.users.identity import get_or_create_invited_identity
from apps.users.models import User
from apps.users.services import TokenPair

from .member_sessions import _active_gym, create_member_session

_GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
_jwk_client: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class MemberEmailCandidate:
    member_id: uuid.UUID
    gym_id: uuid.UUID
    status: str
    linked_user_id: uuid.UUID | None
    authz_version: int
    gym_eligible: bool


def _signing_key(token: str) -> Any:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(_GOOGLE_CERTS, cache_keys=True, lifespan=3600)
    return _jwk_client.get_signing_key_from_jwt(token).key


def verify_google_id_token(token: str) -> str:
    """Return the lowercase email from a Google ID token addressed to this app."""

    client_ids = tuple(settings.GOOGLE_OAUTH_CLIENT_IDS)
    if not client_ids:
        raise GoogleSignInNotConfigured()
    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            audience=list(client_ids),
            issuer=_GOOGLE_ISSUERS,
            leeway=30,
            options={"require": ["exp", "iat", "aud", "iss", "sub", "email"]},
        )
    except (jwt.PyJWTError, PyJWKClientError) as exc:
        raise AuthenticationFailed() from exc
    if claims.get("email_verified") is not True:
        raise AuthenticationFailed()
    email = normalize_member_email(str(claims.get("email", "")))
    if email is None or email.count("@") != 1:
        raise AuthenticationFailed()
    return email


def member_email_candidates(email: str) -> list[MemberEmailCandidate]:
    """Read matching members without opening a tenant context.

    The security-definer function returns ids and status only. It does not
    return the email, name, or phone.
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT member_id, gym_id, status, linked_user_id, authz_version, gym_eligible "
                "FROM app_member_email_candidates(%s)",
                [email],
            )
            fetched = cursor.fetchall()
    except DatabaseError as exc:
        raise AuthenticationFailed() from exc
    return [
        MemberEmailCandidate(
            member_id=row[0],
            gym_id=row[1],
            status=str(row[2]),
            linked_user_id=row[3],
            authz_version=int(row[4]),
            gym_eligible=bool(row[5]),
        )
        for row in fetched
    ]


def select_google_member(rows: list[MemberEmailCandidate]) -> MemberEmailCandidate:
    """Pick the one active member at a gym that can host a member session."""

    if not rows:
        raise MemberAccountMissing()
    eligible = [row for row in rows if row.status == Member.Status.ACTIVE and row.gym_eligible]
    if len(eligible) > 1:
        raise MemberAccountAmbiguous()
    if len(eligible) == 1:
        return eligible[0]
    raise MemberAccountInactive()


def _require_member_login_user(user: User) -> None:
    if not user.is_active:
        raise MemberAccountInactive()
    if user.is_platform_admin or user.is_superuser or user.mfa_enabled:
        raise MemberSignInUnavailable()


def _user_for_member(member: Member, email: str) -> User:
    if member.linked_user_id is not None:
        try:
            user = User.objects.get(pk=member.linked_user_id)
        except User.DoesNotExist as exc:
            raise MemberAccountInactive() from exc
        if user.normalized_email != email:
            raise MemberAccountLinkedElsewhere()
        _require_member_login_user(user)
        return user
    try:
        user = User.objects.get(normalized_email=email)
    except User.DoesNotExist:
        try:
            user = get_or_create_invited_identity(
                email=email,
                first_name=member.first_name.strip(),
                last_name=member.last_name.strip(),
                phone=(member.phone or "").strip(),
            )
        except DatabaseError as exc:
            raise MemberSignInUnavailable() from exc
    _require_member_login_user(user)
    return user


@transaction.atomic
def login_member_with_google_email(
    *,
    email: str,
    device_name: str,
    ip_address: str | None,
) -> tuple[TokenPair, Member]:
    """Issue a member session when the Google email matches one active member."""

    normalized = normalize_member_email(email)
    if normalized is None:
        raise AuthenticationFailed()
    chosen = select_google_member(member_email_candidates(normalized))
    try:
        gym = _active_gym(chosen.gym_id)
    except AuthenticationFailed as exc:
        raise MemberAccountInactive() from exc
    set_gym_context(gym_id=gym.id)
    try:
        member = Member.objects.select_for_update().get(id=chosen.member_id, gym_id=gym.id)
    except Member.DoesNotExist as exc:
        raise MemberAccountMissing() from exc
    if member.email != normalized:
        raise MemberAccountMissing()
    if member.status != Member.Status.ACTIVE:
        raise MemberAccountInactive()
    user = _user_for_member(member, normalized)
    if member.linked_user_id != user.id:
        try:
            member = link_member_identity(gym=gym, member_id=member.id, user=user)
        except ValidationError as exc:
            if "already linked" in str(exc.detail):
                member.refresh_from_db()
                if member.linked_user_id != user.id:
                    raise GoogleAccountAlreadyLinked() from exc
            else:
                raise MemberAccountInactive() from exc
    pair = create_member_session(
        user_id=user.id,
        member=member,
        device_name=device_name,
        ip_address=ip_address,
    )
    return pair, member
