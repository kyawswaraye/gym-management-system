"""Display-device enrollment and device-authenticated challenge issuance.

Lock order for every transaction that takes more than one of these rows:

1. ``GymSettings``
2. the display device
3. that device's challenge rows, ordered by id
4. the member, then the eligible membership, for a member scan

Staff issuance locks settings and then unbound challenges ordered by id. It
does not lock a display device. Member scan locks the challenge, then the
member, then the membership. It does not lock settings or a device. Device
issuance locks settings, then the device, then that device's challenges.
Credential rotation locks only the device. Revocation locks the device and
then its active challenges. Those orders do not invert settings and
challenges, or a device and its challenges. Device locks use
``select_for_update(of=("self",))`` so the gym row stays unlocked. A
``FOR UPDATE`` on ``tenants_gym`` deadlocks with the ``KEY SHARE`` lock an
insert takes through the gym foreign key.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID

from django.core import signing
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, QuerySet
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context
from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX, challenge_digest
from apps.members.checkin_services import _deny_trainer_only
from apps.members.device_credentials import (
    ParsedDeviceCredential,
    device_secret_matches,
    format_device_credential,
    hash_device_secret,
    new_device_secret,
    new_device_selector,
    parse_device_credential,
    reject_unknown_device_secret,
)
from apps.members.exceptions import DeviceStateConflict, DisplayDeviceStateConflict
from apps.members.models import CheckInChallenge, CheckInDevice
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, GymSettings

_TEXT_LIMIT = 80
_CURSOR_SALT = "check-in-display-device-list"
_CURSOR_MAX_AGE = 60 * 60 * 24
_REQUIRED_FEATURES = ("members", "memberships")


class EnrolledDisplayDevice(NamedTuple):
    device: CheckInDevice
    credential: str


class IssuedDeviceChallenge(NamedTuple):
    challenge: CheckInChallenge
    payload: str


def clean_device_text(value: str, *, field: str) -> str:
    cleaned = " ".join(value.split())
    if (
        not cleaned
        or len(cleaned) > _TEXT_LIMIT
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        raise ValidationError({field: ["Enter 1 to 80 characters without control characters."]})
    return cleaned


def enroll_display_device(
    *,
    gym: Gym,
    actor: GymMembership,
    label: str,
    location: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> EnrolledDisplayDevice:
    _deny_trainer_only(gym, actor)
    cleaned_label = clean_device_text(label, field="label")
    cleaned_location = clean_device_text(location, field="location")
    for _attempt in range(2):
        selector = new_device_selector()
        secret = new_device_secret()
        try:
            with transaction.atomic():
                device = CheckInDevice.objects.create(
                    gym=gym,
                    label=cleaned_label,
                    location=cleaned_location,
                    kind=CheckInDevice.Kind.DISPLAY,
                    status=CheckInDevice.Status.ACTIVE,
                    credential_selector=selector,
                    credential_hash=hash_device_secret(secret),
                    credential_version=1,
                    created_by_membership_id=actor.id,
                )
        except IntegrityError:
            continue
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_DISPLAY_DEVICE_ENROLLED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return EnrolledDisplayDevice(
            device,
            format_device_credential(selector=selector, secret=secret),
        )
    raise ValidationError({"label": ["The display device could not be enrolled."]})


def rotate_display_device_credential(
    *,
    gym: Gym,
    actor: GymMembership,
    device_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> EnrolledDisplayDevice:
    """Replace the secret immediately. Existing challenges stay until reissued."""

    _deny_trainer_only(gym, actor)
    secret = new_device_secret()
    with transaction.atomic():
        device = _locked_device(gym, device_id)
        if device.status != CheckInDevice.Status.ACTIVE:
            raise DisplayDeviceStateConflict()
        device.credential_hash = hash_device_secret(secret)
        device.credential_version += 1
        device.rotated_at = timezone.now()
        device.rotated_by_membership_id = actor.id
        device.save(
            update_fields=[
                "credential_hash",
                "credential_version",
                "rotated_at",
                "rotated_by_membership_id",
                "updated_at",
            ]
        )
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_DISPLAY_DEVICE_ROTATED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return EnrolledDisplayDevice(
        device,
        format_device_credential(selector=device.credential_selector, secret=secret),
    )


def revoke_display_device(
    *,
    gym: Gym,
    actor: GymMembership,
    device_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckInDevice:
    """Revoke the device and its active challenges in one transaction."""

    _deny_trainer_only(gym, actor)
    with transaction.atomic():
        device = _locked_device(gym, device_id)
        if device.status != CheckInDevice.Status.ACTIVE:
            raise DisplayDeviceStateConflict()
        now = timezone.now()
        active = list(_locked_device_challenges(gym, device.id))
        device.status = CheckInDevice.Status.REVOKED
        device.revoked_at = now
        device.revoked_by_membership_id = actor.id
        device.save(
            update_fields=["status", "revoked_at", "revoked_by_membership_id", "updated_at"]
        )
        for challenge in active:
            _revoke_challenge(challenge)
            _audit_device_challenge(
                gym=gym,
                actor=actor,
                challenge=challenge,
                action="CHECK_IN_CHALLENGE_REVOKED",
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_DISPLAY_DEVICE_REVOKED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return device


_INTEGRATION_KINDS = frozenset({CheckInDevice.Kind.NFC, CheckInDevice.Kind.BIOMETRIC})


def enroll_integration_device(
    *,
    gym: Gym,
    actor: GymMembership,
    label: str,
    location: str,
    kind: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> EnrolledDisplayDevice:
    if kind not in _INTEGRATION_KINDS:
        raise ValidationError({"kind": ["Select NFC or BIOMETRIC."]})
    _deny_trainer_only(gym, actor)
    cleaned_label = clean_device_text(label, field="label")
    cleaned_location = clean_device_text(location, field="location")
    for _attempt in range(2):
        selector = new_device_selector()
        secret = new_device_secret()
        try:
            with transaction.atomic():
                device = CheckInDevice.objects.create(
                    gym=gym,
                    label=cleaned_label,
                    location=cleaned_location,
                    kind=kind,
                    status=CheckInDevice.Status.ACTIVE,
                    credential_selector=selector,
                    credential_hash=hash_device_secret(secret),
                    credential_version=1,
                    created_by_membership_id=actor.id,
                )
        except IntegrityError:
            continue
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_INTEGRATION_DEVICE_ENROLLED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return EnrolledDisplayDevice(
            device, format_device_credential(selector=selector, secret=secret)
        )
    raise ValidationError({"label": ["The integration device could not be enrolled."]})


def rotate_integration_device_credential(
    *,
    gym: Gym,
    actor: GymMembership,
    device_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> EnrolledDisplayDevice:
    """Replace an NFC or biometric secret. Display credentials are not in scope."""

    _deny_trainer_only(gym, actor)
    secret = new_device_secret()
    with transaction.atomic():
        device = _locked_device(gym, device_id, kinds=_INTEGRATION_KINDS)
        if device.status != CheckInDevice.Status.ACTIVE:
            raise DeviceStateConflict()
        device.credential_hash = hash_device_secret(secret)
        device.credential_version += 1
        device.rotated_at = timezone.now()
        device.rotated_by_membership_id = actor.id
        device.save(
            update_fields=[
                "credential_hash",
                "credential_version",
                "rotated_at",
                "rotated_by_membership_id",
                "updated_at",
            ]
        )
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_INTEGRATION_DEVICE_ROTATED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return EnrolledDisplayDevice(
        device, format_device_credential(selector=device.credential_selector, secret=secret)
    )


def revoke_integration_device(
    *,
    gym: Gym,
    actor: GymMembership,
    device_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckInDevice:
    _deny_trainer_only(gym, actor)
    with transaction.atomic():
        device = _locked_device(gym, device_id, kinds=_INTEGRATION_KINDS)
        if device.status != CheckInDevice.Status.ACTIVE:
            raise DeviceStateConflict()
        now = timezone.now()
        active = list(_locked_device_challenges(gym, device.id))
        device.status = CheckInDevice.Status.REVOKED
        device.revoked_at = now
        device.revoked_by_membership_id = actor.id
        device.save(
            update_fields=["status", "revoked_at", "revoked_by_membership_id", "updated_at"]
        )
        for challenge in active:
            _revoke_challenge(challenge)
            _audit_device_challenge(
                gym=gym,
                actor=actor,
                challenge=challenge,
                action="CHECK_IN_CHALLENGE_REVOKED",
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        _audit_device(
            gym=gym,
            actor=actor,
            device=device,
            action="CHECK_IN_INTEGRATION_DEVICE_REVOKED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return device


def page_integration_devices(
    *,
    gym: Gym,
    actor: GymMembership,
    limit: int,
    after: str,
) -> tuple[list[CheckInDevice], dict[str, Any]]:
    _deny_trainer_only(gym, actor)
    scope = {"gym_id": str(gym.id), "limit": str(limit), "scope": "integration"}
    queryset = CheckInDevice.objects.filter(gym=gym, kind__in=_INTEGRATION_KINDS).order_by(
        "-created_at", "-id"
    )
    if after:
        created_at, device_id = _decode_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=device_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=_CURSOR_SALT,
            compress=True,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def page_display_devices(
    *,
    gym: Gym,
    actor: GymMembership,
    limit: int,
    after: str,
) -> tuple[list[CheckInDevice], dict[str, Any]]:
    _deny_trainer_only(gym, actor)
    scope = {"gym_id": str(gym.id), "limit": str(limit)}
    queryset = CheckInDevice.objects.filter(gym=gym, kind=CheckInDevice.Kind.DISPLAY).order_by(
        "-created_at", "-id"
    )
    if after:
        created_at, device_id = _decode_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=device_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=_CURSOR_SALT,
            compress=True,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def issue_device_check_in_challenge(
    *,
    authorization: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> IssuedDeviceChallenge:
    """Mint one challenge for the authenticated display. The body is ignored."""

    with transaction.atomic():
        device, settings = authenticate_device(authorization=authorization)
        if device.kind != CheckInDevice.Kind.DISPLAY:
            raise AuthenticationFailed()
        gym = device.gym
        now = timezone.now()
        for previous in list(_locked_device_challenges(gym, device.id)):
            _revoke_challenge(previous)
            _audit_device_challenge(
                gym=gym,
                actor=None,
                challenge=previous,
                action="CHECK_IN_CHALLENGE_REVOKED",
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        issued = _insert_device_challenge(
            gym=gym,
            device=device,
            issued_at=now,
            ttl_seconds=settings.check_in_challenge_ttl_seconds,
            max_uses=settings.check_in_challenge_max_uses,
        )
        _audit_device_challenge(
            gym=gym,
            actor=None,
            challenge=issued.challenge,
            action="CHECK_IN_CHALLENGE_ISSUED",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return issued


def authenticate_device(*, authorization: str) -> tuple[CheckInDevice, GymSettings]:
    """Lock gym settings, then the device row, and verify the secret.

    The caller is already inside ``transaction.atomic``. The gym comes from
    the credential. ``kind`` is not a capability gate here: each route rejects
    the kinds it cannot use after this check, with the same authentication
    failure. The device lock does not lock ``tenants_gym``.
    """

    parsed = _authorization_credential(authorization)
    if parsed is None:
        reject_unknown_device_secret(secrets.token_urlsafe(32))
        raise AuthenticationFailed()
    located = _locate_device(parsed.selector)
    if located is None:
        reject_unknown_device_secret(parsed.secret)
        raise AuthenticationFailed()
    set_gym_context(gym_id=located[1])
    try:
        settings = GymSettings.objects.select_for_update().get(gym_id=located[1])
    except GymSettings.DoesNotExist as exc:
        reject_unknown_device_secret(parsed.secret)
        raise AuthenticationFailed() from exc
    device = (
        CheckInDevice.objects.select_for_update(of=("self",))
        .select_related("gym")
        .filter(gym_id=located[1], id=located[0])
        .first()
    )
    if device is None or not device_secret_matches(parsed.secret, device.credential_hash):
        if device is None:
            reject_unknown_device_secret(parsed.secret)
        raise AuthenticationFailed()
    gym = device.gym
    if (
        device.status != CheckInDevice.Status.ACTIVE
        or gym.status != Gym.Status.ACTIVE
        or gym.subscription_status
        not in {Gym.SubscriptionStatus.TRIAL, Gym.SubscriptionStatus.ACTIVE}
        or not _features_enabled(gym)
    ):
        raise AuthenticationFailed()
    return device, settings


def _authorization_credential(authorization: str) -> ParsedDeviceCredential | None:
    scheme, _, remainder = authorization.partition(" ")
    if scheme != "Device" or not remainder or " " in remainder:
        return None
    return parse_device_credential(remainder)


def _locate_device(selector: str) -> tuple[UUID, UUID, str, int, str] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT device_id, gym_id, status, credential_version, kind "
            "FROM app_check_in_device(%s)",
            [selector],
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return (row[0], row[1], str(row[2]), int(row[3]), str(row[4]))


def _features_enabled(gym: Gym) -> bool:
    now = timezone.now()
    entitled = set(
        GymFeatureEntitlement.objects.filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=now),
            Q(effective_until__isnull=True) | Q(effective_until__gt=now),
            gym=gym,
            feature__code__in=_REQUIRED_FEATURES,
            enabled=True,
        ).values_list("feature__code", flat=True)
    )
    return all(code in entitled for code in _REQUIRED_FEATURES)


def _locked_device(
    gym: Gym, device_id: UUID, *, kinds: frozenset[str] = frozenset({CheckInDevice.Kind.DISPLAY})
) -> CheckInDevice:
    device = (
        CheckInDevice.objects.select_for_update(of=("self",))
        .filter(gym=gym, id=device_id, kind__in=kinds)
        .first()
    )
    if device is None:
        raise Http404
    return device


def _locked_device_challenges(gym: Gym, device_id: UUID) -> QuerySet[CheckInChallenge]:
    return (
        CheckInChallenge.objects.select_for_update()
        .filter(
            gym=gym,
            display_device_id=device_id,
            status=CheckInChallenge.Status.ACTIVE,
        )
        .order_by("id")
    )


def _revoke_challenge(challenge: CheckInChallenge) -> None:
    challenge.status = CheckInChallenge.Status.REVOKED
    challenge.save(update_fields=["status", "updated_at"])


def _insert_device_challenge(
    *,
    gym: Gym,
    device: CheckInDevice,
    issued_at: datetime,
    ttl_seconds: int,
    max_uses: int,
) -> IssuedDeviceChallenge:
    for _attempt in range(2):
        payload = f"{CHALLENGE_PAYLOAD_PREFIX}{secrets.token_urlsafe(32)}"
        try:
            with transaction.atomic():
                challenge = CheckInChallenge.objects.create(
                    gym=gym,
                    challenge_hash=challenge_digest(payload),
                    display_location=device.location,
                    issued_at=issued_at,
                    expires_at=issued_at + timedelta(seconds=ttl_seconds),
                    use_count=0,
                    max_uses=max_uses,
                    issued_by_membership_id=None,
                    display_device_id=device.id,
                    status=CheckInChallenge.Status.ACTIVE,
                    created_by_membership_id=None,
                )
        except IntegrityError:
            continue
        return IssuedDeviceChallenge(challenge, payload)
    raise AuthenticationFailed()


def _audit_device(
    *,
    gym: Gym,
    actor: GymMembership,
    device: CheckInDevice,
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
        target_type="CheckInDevice",
        target_id=device.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "label": device.label,
            "location": device.location,
            "kind": device.kind,
            "status": device.status,
            "credential_version": device.credential_version,
        },
    )


def _audit_device_challenge(
    *,
    gym: Gym,
    actor: GymMembership | None,
    challenge: CheckInChallenge,
    action: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=None if actor is None else actor.user,
        actor_membership_id=None if actor is None else actor.id,
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
            "display_device_id": (
                None if challenge.display_device_id is None else str(challenge.display_device_id)
            ),
        },
    )


def _decode_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(token, salt=_CURSOR_SALT, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected = set(scope) | {"created_at", "id"}
        if set(loaded) != expected or any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        raw_time = loaded["created_at"]
        if not isinstance(raw_time, str):
            raise TypeError
        created_at = datetime.fromisoformat(raw_time)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
