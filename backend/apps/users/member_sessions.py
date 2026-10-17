from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context, set_identity_context
from apps.members.models import Member
from apps.tenants.models import Gym

from .models import RefreshSession
from .services import TokenPair, hash_refresh_token
from .tokens import issue_member_access_token


@dataclass(frozen=True)
class LinkedMember:
    member_id: uuid.UUID
    gym_id: uuid.UUID
    authz_version: int


def linked_member_rows() -> list[LinkedMember]:
    """Read the bootstrap function. Identity context must already be set."""

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT member_id, gym_id, authz_version FROM app_linked_members()")
            fetched = cursor.fetchall()
    except DatabaseError as exc:
        raise AuthenticationFailed() from exc
    return [LinkedMember(member_id=row[0], gym_id=row[1], authz_version=row[2]) for row in fetched]


def _active_gym(gym_id: uuid.UUID) -> Gym:
    try:
        return Gym.objects.get(
            id=gym_id,
            status=Gym.Status.ACTIVE,
            subscription_status__in=["TRIAL", "ACTIVE"],
        )
    except Gym.DoesNotExist as exc:
        raise AuthenticationFailed() from exc


def establish_member_principal(
    *,
    user_id: uuid.UUID,
    member_id: uuid.UUID,
    gym_id: uuid.UUID,
    request_id: str,
    expected_authz_version: int | None,
) -> tuple[Member, Gym]:
    """Prove the selected member from identity context, then reload it under RLS.

    ``app.gym_id`` is installed only after the bootstrap row matches the
    claimed member. A missing, cross-user, inactive, or mismatched row fails
    closed before any ordinary tenant read.
    """

    set_identity_context(user_id=user_id, request_id=request_id)
    matches = [
        row for row in linked_member_rows() if row.member_id == member_id and row.gym_id == gym_id
    ]
    if len(matches) != 1:
        raise AuthenticationFailed()
    gym = _active_gym(matches[0].gym_id)
    set_gym_context(gym_id=gym.id)
    try:
        if expected_authz_version is None:
            member = Member.objects.get(
                id=member_id,
                gym_id=gym.id,
                linked_user_id=user_id,
                status=Member.Status.ACTIVE,
            )
        else:
            member = Member.objects.get(
                id=member_id,
                gym_id=gym.id,
                linked_user_id=user_id,
                status=Member.Status.ACTIVE,
                authz_version=expected_authz_version,
            )
    except Member.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    return member, gym


def _member_pair(session: RefreshSession, member: Member, plaintext: str) -> TokenPair:
    access, access_expiry = issue_member_access_token(
        user_id=session.user_id,
        gym_id=member.gym_id,
        member_id=member.id,
        session_id=session.id,
        authz_version=member.authz_version,
    )
    return TokenPair(
        access_token=access,
        access_expires_at=access_expiry,
        refresh_token=plaintext,
        refresh_expires_at=session.expires_at,
    )


@transaction.atomic
def create_member_session(
    *,
    user_id: uuid.UUID,
    member: Member,
    device_name: str,
    ip_address: str | None,
) -> TokenPair:
    now = timezone.now()
    plaintext = secrets.token_urlsafe(48)
    session = RefreshSession.objects.create(
        principal_type=RefreshSession.PrincipalType.MEMBER,
        user_id=user_id,
        selected_gym_id=member.gym_id,
        gym_membership=None,
        member_id=member.id,
        token_hash=hash_refresh_token(plaintext),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TTL_SECONDS),
        device_id=device_name[:255],
        ip_address=ip_address,
    )
    return _member_pair(session, member, plaintext)


@transaction.atomic
def login_member(
    *,
    email: str,
    password: str,
    device_name: str,
    ip_address: str | None,
    request_id: str,
) -> tuple[TokenPair, Member]:
    """Issue a session only when the user has exactly one active linked member.

    Zero and several active links return the same generic failure. The request
    cannot choose a gym or member. MFA-enabled identities also fail closed
    until a member challenge contract exists; this path never skips MFA.
    """

    from .models import User

    normalized = email.strip().lower()
    try:
        user = User.objects.get(normalized_email=normalized, is_active=True)
    except User.DoesNotExist as exc:
        User().set_password(password)
        raise AuthenticationFailed() from exc
    if not user.check_password(password) or user.mfa_enabled:
        raise AuthenticationFailed()
    set_identity_context(user_id=user.id, request_id=request_id)
    rows = linked_member_rows()
    if len(rows) != 1:
        raise AuthenticationFailed()
    selected = rows[0]
    gym = _active_gym(selected.gym_id)
    set_gym_context(gym_id=gym.id)
    try:
        member = Member.objects.get(
            id=selected.member_id,
            gym_id=selected.gym_id,
            linked_user_id=user.id,
            status=Member.Status.ACTIVE,
            authz_version=selected.authz_version,
        )
    except Member.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    pair = create_member_session(
        user_id=user.id,
        member=member,
        device_name=device_name,
        ip_address=ip_address,
    )
    return pair, member


@transaction.atomic
def _rotate_member_refresh_token(
    plaintext: str, *, request_id: str
) -> tuple[TokenPair, Member] | None:
    token_hash = hash_refresh_token(plaintext)
    try:
        current = (
            RefreshSession.objects.select_for_update()
            .select_related("user")
            .get(token_hash=token_hash)
        )
    except RefreshSession.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    now = timezone.now()
    if current.rotated_at or current.replaced_by_session_id:
        RefreshSession.objects.filter(family_id=current.family_id, revoked_at__isnull=True).update(
            revoked_at=now, revocation_reason="REFRESH_REPLAY"
        )
        return None
    if current.principal_type != RefreshSession.PrincipalType.MEMBER or current.member_id is None:
        raise AuthenticationFailed()
    if (
        current.revoked_at
        or current.expires_at <= now
        or not current.user.is_active
        or current.selected_gym_id is None
    ):
        raise AuthenticationFailed()
    member, _gym = establish_member_principal(
        user_id=current.user_id,
        member_id=current.member_id,
        gym_id=current.selected_gym_id,
        request_id=request_id,
        expected_authz_version=None,
    )
    replacement_plaintext = secrets.token_urlsafe(48)
    replacement = RefreshSession.objects.create(
        principal_type=RefreshSession.PrincipalType.MEMBER,
        user_id=current.user_id,
        selected_gym_id=current.selected_gym_id,
        gym_membership=None,
        member_id=current.member_id,
        token_hash=hash_refresh_token(replacement_plaintext),
        family_id=current.family_id,
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TTL_SECONDS),
        ip_address=current.ip_address,
        user_agent=current.user_agent,
        device_id=current.device_id,
        mfa_verified_at=current.mfa_verified_at,
    )
    current.rotated_at = now
    current.replaced_by_session = replacement
    current.save(update_fields=["rotated_at", "replaced_by_session", "updated_at"])
    return _member_pair(replacement, member, replacement_plaintext), member


def rotate_member_refresh_token(plaintext: str, *, request_id: str) -> tuple[TokenPair, Member]:
    rotated = _rotate_member_refresh_token(plaintext, request_id=request_id)
    if rotated is None:
        raise AuthenticationFailed()
    return rotated


def revoke_member_sessions(*, gym_id: uuid.UUID, member_id: uuid.UUID, reason: str) -> None:
    RefreshSession.objects.filter(
        principal_type=RefreshSession.PrincipalType.MEMBER,
        selected_gym_id=gym_id,
        member_id=member_id,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now(), revocation_reason=reason[:64])
