from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import Outcome
from apps.audit.services import audit_ticket, write_platform_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context, set_identity_context
from apps.notifications.services import IDEMPOTENCY_HEADER
from apps.tenants.models import GymMembership

from .identity import InvitedIdentityError, bump_password_reset_authz, set_reset_password
from .models import GymSelectionTicket, MFAChallenge, PasswordResetToken, RefreshSession, User
from .tokens import issue_access_token, issue_platform_access_token


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


def hash_refresh_token(token: str) -> str:
    if not settings.REFRESH_TOKEN_PEPPER:
        raise RuntimeError("REFRESH_TOKEN_PEPPER is not configured")
    return hmac.new(
        settings.REFRESH_TOKEN_PEPPER.encode(), token.encode(), hashlib.sha256
    ).hexdigest()


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _staff_pair(session: RefreshSession, membership: GymMembership, plaintext: str) -> TokenPair:
    access, access_expiry = issue_access_token(
        user_id=session.user_id, membership=membership, session_id=session.id
    )
    return TokenPair(access, access_expiry, plaintext, session.expires_at)


def _platform_pair(session: RefreshSession, plaintext: str) -> TokenPair:
    access, access_expiry = issue_platform_access_token(
        user_id=session.user_id, session_id=session.id
    )
    return TokenPair(access, access_expiry, plaintext, session.expires_at)


@transaction.atomic
def create_platform_session(
    *,
    user: User,
    device_name: str = "",
    ip_address: str | None = None,
    mfa_verified: bool = False,
) -> TokenPair:
    if not user.is_active or not user.is_platform_admin:
        raise AuthenticationFailed()
    plaintext = _new_refresh_token()
    now = timezone.now()
    session = RefreshSession.objects.create(
        principal_type=RefreshSession.PrincipalType.PLATFORM,
        user=user,
        selected_gym=None,
        gym_membership=None,
        member_id=None,
        token_hash=hash_refresh_token(plaintext),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TTL_SECONDS),
        device_id=device_name[:255],
        ip_address=ip_address,
        mfa_verified_at=now if mfa_verified else None,
    )
    return _platform_pair(session, plaintext)


@transaction.atomic
def create_staff_session(
    *,
    user: User,
    membership: GymMembership,
    device_name: str = "",
    ip_address: str | None = None,
    mfa_verified: bool = False,
) -> TokenPair:
    plaintext = _new_refresh_token()
    now = timezone.now()
    session = RefreshSession.objects.create(
        principal_type=RefreshSession.PrincipalType.STAFF,
        user=user,
        selected_gym_id=membership.gym_id,
        gym_membership=membership,
        token_hash=hash_refresh_token(plaintext),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TTL_SECONDS),
        device_id=device_name[:255],
        ip_address=ip_address,
        mfa_verified_at=now if mfa_verified else None,
    )
    return _staff_pair(session, membership, plaintext)


@transaction.atomic
def _rotate_refresh_token(plaintext: str, *, request_id: str) -> TokenPair | None:
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
    if current.revoked_at or current.expires_at <= now or not current.user.is_active:
        raise AuthenticationFailed()
    set_identity_context(user_id=current.user_id, request_id=request_id)
    if current.principal_type == RefreshSession.PrincipalType.PLATFORM:
        if (
            not current.user.is_platform_admin
            or current.selected_gym_id is not None
            or current.gym_membership_id is not None
            or current.member_id is not None
        ):
            raise AuthenticationFailed()
        replacement_plaintext = _new_refresh_token()
        replacement = RefreshSession.objects.create(
            principal_type=current.principal_type,
            user_id=current.user_id,
            selected_gym=None,
            gym_membership=None,
            member_id=None,
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
        return _platform_pair(replacement, replacement_plaintext)
    if (
        current.principal_type != RefreshSession.PrincipalType.STAFF
        or current.gym_membership_id is None
        or current.selected_gym is None
        or current.selected_gym.status != "ACTIVE"
        or current.selected_gym.subscription_status not in {"TRIAL", "ACTIVE"}
    ):
        raise AuthenticationFailed()
    selected_gym = current.selected_gym
    set_gym_context(gym_id=selected_gym.id)
    try:
        membership = GymMembership.objects.get(
            id=current.gym_membership_id,
            gym_id=selected_gym.id,
            user_id=current.user_id,
            status=GymMembership.Status.ACTIVE,
        )
    except GymMembership.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    replacement_plaintext = _new_refresh_token()
    replacement = RefreshSession.objects.create(
        principal_type=current.principal_type,
        user_id=current.user_id,
        selected_gym_id=selected_gym.id,
        gym_membership_id=current.gym_membership_id,
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
    return _staff_pair(replacement, membership, replacement_plaintext)


def rotate_refresh_token(plaintext: str, *, request_id: str) -> TokenPair:
    pair = _rotate_refresh_token(plaintext, request_id=request_id)
    if pair is None:
        raise AuthenticationFailed()
    return pair


@transaction.atomic
def revoke_refresh_token(plaintext: str, reason: str = "LOGOUT") -> None:
    token_hash = hash_refresh_token(plaintext)
    RefreshSession.objects.filter(token_hash=token_hash, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revocation_reason=reason
    )


@transaction.atomic
def revoke_all_user_sessions(user_id: uuid.UUID, reason: str = "LOGOUT_ALL") -> None:
    RefreshSession.objects.filter(user_id=user_id, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revocation_reason=reason
    )


def hash_selection_ticket(token: str) -> str:
    return hash_refresh_token(token)


@transaction.atomic
def purge_terminal_gym_selection_tickets(
    *,
    batch_size: int = 1000,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """Purge one bounded batch after the mandatory terminal retention window."""

    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    cutoff = (now or timezone.now()) - timedelta(days=settings.GYM_SELECTION_TICKET_RETENTION_DAYS)
    eligible = GymSelectionTicket.objects.filter(
        Q(consumed_at__lte=cutoff) | Q(revoked_at__lte=cutoff)
    )
    if dry_run:
        return eligible.count()
    ticket_ids = list(
        eligible.order_by("updated_at", "id").values_list("id", flat=True)[:batch_size]
    )
    if ticket_ids:
        GymSelectionTicket.objects.filter(id__in=ticket_ids).delete()
    return len(ticket_ids)


def _selection_membership_ids(memberships: list[dict[str, object]]) -> list[uuid.UUID]:
    return sorted(uuid.UUID(str(item["gym_membership_id"])) for item in memberships)


@transaction.atomic
def issue_gym_selection_ticket(
    *,
    user: User,
    memberships: list[dict[str, object]],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
    mfa_verified: bool = False,
) -> tuple[str, datetime]:
    ids = _selection_membership_ids(memberships)
    if len(ids) < 2:
        raise AuthenticationFailed()
    now = timezone.now()
    RefreshSession.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=now, revocation_reason="TICKET_REISSUE"
    )
    live_ids = list(
        GymSelectionTicket.objects.filter(
            user=user, consumed_at__isnull=True, revoked_at__isnull=True
        ).values_list("id", flat=True)
    )
    if live_ids:
        GymSelectionTicket.objects.filter(id__in=live_ids).update(
            revoked_at=now, revocation_reason="SUPERSEDED"
        )
        for ticket_id in live_ids:
            audit_ticket(
                user=user,
                ticket_id=ticket_id,
                action="TICKET_REVOKED",
                request_id=request_id,
                outcome=Outcome.DENIED,
                reason="SUPERSEDED",
            )
    plaintext = _new_refresh_token()
    ticket = GymSelectionTicket.objects.create(
        user=user,
        ticket_hash=hash_selection_ticket(plaintext),
        eligible_gym_membership_ids=ids,
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.GYM_SELECTION_TICKET_TTL_SECONDS),
        max_attempts=settings.GYM_SELECTION_TICKET_MAX_ATTEMPTS,
        ip_address=ip_address,
        user_agent=user_agent[:512],
        request_id=request_id[:128],
        mfa_verified_at=now if mfa_verified else None,
    )
    audit_ticket(
        user=user,
        ticket_id=ticket.id,
        action="TICKET_ISSUED",
        request_id=request_id,
    )
    return plaintext, now + timedelta(seconds=settings.GYM_SELECTION_TICKET_TTL_SECONDS)


def _revoke_ticket(ticket: GymSelectionTicket, reason: str) -> None:
    ticket.revoked_at = timezone.now()
    ticket.revocation_reason = reason
    ticket.save(update_fields=["revoked_at", "revocation_reason", "updated_at"])


class _TicketDenied(Exception):
    def __init__(self, ticket: GymSelectionTicket, action: str, reason: str) -> None:
        self.ticket = ticket
        self.action = action
        self.reason = reason


def consume_gym_selection_ticket(
    plaintext: str,
    *,
    gym_membership_id: uuid.UUID,
    request_id: str,
    membership_loader: Callable[[uuid.UUID], list[dict[str, Any]]],
) -> TokenPair:
    denied: _TicketDenied | None = None
    pair: TokenPair | None = None
    with transaction.atomic():
        try:
            ticket = (
                GymSelectionTicket.objects.select_for_update()
                .select_related("user")
                .get(ticket_hash=hash_selection_ticket(plaintext))
            )
        except GymSelectionTicket.DoesNotExist as exc:
            raise AuthenticationFailed() from exc
        now = timezone.now()
        if ticket.consumed_at or ticket.revoked_at:
            denied = _TicketDenied(ticket, "TICKET_REJECTED", "REPLAY")
        elif ticket.expires_at <= now:
            _revoke_ticket(ticket, "EXPIRED")
            denied = _TicketDenied(ticket, "TICKET_REVOKED", "EXPIRED")
        else:
            ticket.attempt_count += 1
            if ticket.attempt_count > ticket.max_attempts:
                _revoke_ticket(ticket, "ATTEMPTS_EXHAUSTED")
                denied = _TicketDenied(ticket, "TICKET_REVOKED", "ATTEMPTS_EXHAUSTED")
            else:
                ticket.save(update_fields=["attempt_count", "updated_at"])
                if gym_membership_id not in ticket.eligible_gym_membership_ids:
                    reason = "MEMBERSHIP_SUBSTITUTION"
                    if ticket.attempt_count >= ticket.max_attempts:
                        _revoke_ticket(ticket, reason)
                        denied = _TicketDenied(ticket, "TICKET_REVOKED", reason)
                    else:
                        denied = _TicketDenied(ticket, "TICKET_REJECTED", reason)
                elif not ticket.user.is_active:
                    _revoke_ticket(ticket, "USER_INACTIVE")
                    denied = _TicketDenied(ticket, "TICKET_REVOKED", "USER_INACTIVE")
                else:
                    set_identity_context(user_id=ticket.user_id, request_id=request_id)
                    rows = membership_loader(ticket.user_id)
                    selected = next(
                        (row for row in rows if row["gym_membership_id"] == str(gym_membership_id)),
                        None,
                    )
                    if selected is None:
                        _revoke_ticket(ticket, "MEMBERSHIP_INACTIVE")
                        denied = _TicketDenied(ticket, "TICKET_REVOKED", "MEMBERSHIP_INACTIVE")
                    else:
                        set_gym_context(gym_id=uuid.UUID(selected["gym_id"]))
                        membership = GymMembership.objects.get(
                            id=gym_membership_id,
                            gym_id=selected["gym_id"],
                            user_id=ticket.user_id,
                            status=GymMembership.Status.ACTIVE,
                        )
                        ticket.consumed_at = now
                        ticket.selected_gym_membership_id = gym_membership_id
                        ticket.save(
                            update_fields=[
                                "consumed_at",
                                "selected_gym_membership_id",
                                "updated_at",
                            ]
                        )
                        pair = create_staff_session(
                            user=ticket.user,
                            membership=membership,
                            mfa_verified=ticket.mfa_verified_at is not None,
                        )
                        audit_ticket(
                            user=ticket.user,
                            ticket_id=ticket.id,
                            action="TICKET_CONSUMED",
                            request_id=request_id,
                        )
    if denied is not None:
        audit_ticket(
            user=denied.ticket.user,
            ticket_id=denied.ticket.id,
            action=denied.action,
            request_id=request_id,
            outcome=Outcome.DENIED,
            reason=denied.reason,
        )
        raise AuthenticationFailed()
    if pair is None:
        raise AuthenticationFailed()
    return pair


def hash_password_reset_token(token: str) -> str:
    if not settings.PASSWORD_RESET_TOKEN_PEPPER:
        raise RuntimeError("PASSWORD_RESET_TOKEN_PEPPER is not configured")
    return hmac.new(
        settings.PASSWORD_RESET_TOKEN_PEPPER.encode(), token.encode(), hashlib.sha256
    ).hexdigest()


def _new_password_reset_token() -> str:
    return secrets.token_urlsafe(48)


@transaction.atomic
def request_password_reset(
    *,
    email: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> None:
    """Issue a live reset token when the address matches an active user.

    The HTTP layer always returns the same 200 envelope. Provider failure,
    a missing account, and a successful send stay indistinguishable.
    """

    normalized = email.strip().lower()
    try:
        user = User.objects.get(normalized_email=normalized, is_active=True)
    except User.DoesNotExist:
        return
    now = timezone.now()
    PasswordResetToken.objects.filter(
        user=user, consumed_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now)
    plaintext = _new_password_reset_token()
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_password_reset_token(plaintext),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.PASSWORD_RESET_TTL_SECONDS),
        request_id=request_id[:128],
        ip_address=ip_address,
        user_agent=user_agent[:512],
    )
    reset_url = f"{settings.APP_PUBLIC_URL.rstrip('/')}/reset-password?token={plaintext}"
    message = EmailMessage(
        subject="Reset your Gym Management password",
        body=(
            "Use this link to choose a new password. It expires in 30 minutes "
            f"and can be used once.\n\n{reset_url}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
        headers={IDEMPOTENCY_HEADER: f"password-reset:{token.id}"},
        connection=get_connection(fail_silently=False),
    )
    try:
        sent = message.send(fail_silently=False)
    except Exception:
        sent = 0
    write_platform_audit(
        actor=user,
        action="PASSWORD_RESET_REQUESTED",
        target_type="PasswordResetToken",
        target_id=token.id,
        request_id=request_id,
        outcome=Outcome.SUCCESS if sent == 1 else Outcome.DENIED,
        details={"reason": "REQUESTED" if sent == 1 else "PROVIDER_UNAVAILABLE"},
        ip_address=ip_address,
        user_agent=user_agent,
    )


@transaction.atomic
def reset_password(
    *,
    reset_token: str,
    password: str,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> None:
    token_hash = hash_password_reset_token(reset_token)
    now = timezone.now()
    token = (
        PasswordResetToken.objects.select_related("user")
        .select_for_update()
        .filter(token_hash=token_hash)
        .first()
    )
    if (
        token is None
        or token.consumed_at is not None
        or token.revoked_at is not None
        or token.expires_at <= now
        or not token.user.is_active
    ):
        raise AuthenticationFailed()
    user = token.user
    if user.check_password(password):
        raise ValidationError({"password": ["Enter a different password."]})
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise ValidationError({"password": list(exc.messages)}) from exc
    try:
        set_reset_password(
            token_hash=token_hash,
            user=user,
            password=password,
            changed_at=now,
        )
    except InvitedIdentityError as exc:
        raise AuthenticationFailed() from exc
    try:
        bump_password_reset_authz(user_id=user.id)
    except InvitedIdentityError as exc:
        raise AuthenticationFailed() from exc
    revoke_all_user_sessions(user.id, reason="PASSWORD_RESET")
    GymSelectionTicket.objects.filter(
        user=user, consumed_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now, revocation_reason="PASSWORD_RESET")
    MFAChallenge.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
    write_platform_audit(
        actor=user,
        action="PASSWORD_RESET_COMPLETED",
        target_type="PasswordResetToken",
        target_id=token.id,
        request_id=request_id,
        outcome=Outcome.SUCCESS,
        details={"reason": "RESET"},
        ip_address=ip_address,
        user_agent=user_agent,
    )
