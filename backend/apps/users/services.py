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
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import Outcome
from apps.audit.services import audit_ticket
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context, set_identity_context
from apps.tenants.models import GymMembership

from .models import GymSelectionTicket, RefreshSession, User
from .tokens import issue_access_token


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


def _pair(session: RefreshSession, membership: GymMembership, plaintext: str) -> TokenPair:
    access, access_expiry = issue_access_token(
        user_id=session.user_id, membership=membership, session_id=session.id
    )
    return TokenPair(access, access_expiry, plaintext, session.expires_at)


@transaction.atomic
def create_staff_session(
    *, user: User, membership: GymMembership, device_name: str = "", ip_address: str | None = None
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
    )
    return _pair(session, membership, plaintext)


@transaction.atomic
def _rotate_refresh_token(plaintext: str, *, request_id: str) -> TokenPair | None:
    token_hash = hash_refresh_token(plaintext)
    try:
        current = (
            RefreshSession.objects.select_for_update()
            .select_related("user", "selected_gym")
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
    if (
        current.revoked_at
        or current.expires_at <= now
        or not current.user.is_active
        or current.principal_type != RefreshSession.PrincipalType.STAFF
        or current.gym_membership_id is None
        or current.selected_gym.status != "ACTIVE"
        or current.selected_gym.subscription_status not in {"TRIAL", "ACTIVE"}
    ):
        raise AuthenticationFailed()
    set_identity_context(user_id=current.user_id, request_id=request_id)
    set_gym_context(gym_id=current.selected_gym_id)
    try:
        membership = GymMembership.objects.get(
            id=current.gym_membership_id,
            gym_id=current.selected_gym_id,
            user_id=current.user_id,
            status=GymMembership.Status.ACTIVE,
        )
    except GymMembership.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    replacement_plaintext = _new_refresh_token()
    replacement = RefreshSession.objects.create(
        principal_type=current.principal_type,
        user_id=current.user_id,
        selected_gym_id=current.selected_gym_id,
        gym_membership_id=current.gym_membership_id,
        token_hash=hash_refresh_token(replacement_plaintext),
        family_id=current.family_id,
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TTL_SECONDS),
        ip_address=current.ip_address,
        user_agent=current.user_agent,
        device_id=current.device_id,
    )
    current.rotated_at = now
    current.replaced_by_session = replacement
    current.save(update_fields=["rotated_at", "replaced_by_session", "updated_at"])
    return _pair(replacement, membership, replacement_plaintext)


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
def revoke_all_user_sessions(user_id: uuid.UUID) -> None:
    RefreshSession.objects.filter(user_id=user_id, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revocation_reason="LOGOUT_ALL"
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
                        pair = create_staff_session(user=ticket.user, membership=membership)
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
