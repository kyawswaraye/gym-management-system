"""Staff-issued onsite QR challenges.

The plaintext payload is returned once and is never stored. This path locks
gym settings, then active staff challenges ordered by id. It does not lock a
display device. Device issuance lives in ``device_services`` and uses the same
settings-before-challenge order. Member-self redemption lives in
``scan_services``. Trainer scan is not implemented.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import NamedTuple

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.members.checkin_services import _deny_trainer_only
from apps.members.models import CheckInChallenge
from apps.tenants.models import Gym, GymMembership, GymSettings

CHALLENGE_PAYLOAD_PREFIX = "gms-checkin:v1:"
_CHALLENGE_LOCATION_LIMIT = 80


class IssuedCheckInChallenge(NamedTuple):
    challenge: CheckInChallenge
    payload: str


def challenge_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clean_display_location(value: str) -> str:
    cleaned = " ".join(value.split())
    if (
        not cleaned
        or len(cleaned) > _CHALLENGE_LOCATION_LIMIT
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        raise ValidationError(
            {"display_location": ["Enter a display location of 1 to 80 characters."]}
        )
    return cleaned


def issue_staff_check_in_challenge(
    *,
    gym: Gym,
    actor: GymMembership,
    display_location: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> IssuedCheckInChallenge:
    """Issue one onsite challenge and revoke older staff-issued active rows."""

    _deny_trainer_only(gym, actor)
    location = clean_display_location(display_location)
    with transaction.atomic():
        try:
            settings = GymSettings.objects.select_for_update().get(gym=gym)
        except GymSettings.DoesNotExist as exc:
            raise ValidationError(
                {"display_location": ["Check-in challenges are not available for this gym."]}
            ) from exc
        now = timezone.now()
        active = (
            CheckInChallenge.objects.select_for_update()
            .filter(
                gym=gym,
                status=CheckInChallenge.Status.ACTIVE,
                display_device_id__isnull=True,
            )
            .order_by("id")
        )
        for previous in active:
            previous.status = CheckInChallenge.Status.REVOKED
            previous.save(update_fields=["status", "updated_at"])
            _audit_challenge(
                gym=gym,
                actor=actor,
                challenge=previous,
                action="CHECK_IN_CHALLENGE_REVOKED",
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        issued = _insert_challenge(
            gym=gym,
            actor=actor,
            location=location,
            issued_at=now,
            ttl_seconds=settings.check_in_challenge_ttl_seconds,
            max_uses=settings.check_in_challenge_max_uses,
        )
        _audit_challenge(
            gym=gym,
            actor=actor,
            challenge=issued.challenge,
            action="CHECK_IN_CHALLENGE_ISSUED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return issued


def _insert_challenge(
    *,
    gym: Gym,
    actor: GymMembership,
    location: str,
    issued_at: datetime,
    ttl_seconds: int,
    max_uses: int,
) -> IssuedCheckInChallenge:
    for _attempt in range(2):
        payload = f"{CHALLENGE_PAYLOAD_PREFIX}{secrets.token_urlsafe(32)}"
        try:
            with transaction.atomic():
                challenge = CheckInChallenge.objects.create(
                    gym=gym,
                    challenge_hash=challenge_digest(payload),
                    display_location=location,
                    issued_at=issued_at,
                    expires_at=issued_at + timedelta(seconds=ttl_seconds),
                    use_count=0,
                    max_uses=max_uses,
                    issued_by_membership_id=actor.id,
                    display_device_id=None,
                    status=CheckInChallenge.Status.ACTIVE,
                    created_by_membership_id=actor.id,
                )
        except IntegrityError:
            continue
        return IssuedCheckInChallenge(challenge, payload)
    raise ValidationError({"display_location": ["The check-in challenge could not be issued."]})


def _audit_challenge(
    *,
    gym: Gym,
    actor: GymMembership,
    challenge: CheckInChallenge,
    action: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="checkins",
        target_type="CheckInChallenge",
        target_id=challenge.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "display_location": challenge.display_location,
            "issued_at": challenge.issued_at.isoformat(),
            "expires_at": challenge.expires_at.isoformat(),
            "max_uses": challenge.max_uses,
            "status": challenge.status,
            "display_device_id": None,
        },
    )
