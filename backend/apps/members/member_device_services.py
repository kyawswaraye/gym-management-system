"""Member device registration, rotation, consent, and revocation."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from apps.audit.services import write_tenant_audit
from apps.common.db.tenant_context import set_gym_context
from apps.members.exceptions import DeviceConflict
from apps.members.models import Member, MemberDevice
from apps.notifications.tokens import encrypt_push_token, fingerprint_push_token
from apps.tenants.models import Gym

_ACTIVE_DEVICE_LIMIT = 5


def device_payload(device: MemberDevice) -> dict[str, Any]:
    return {
        "id": device.id,
        "platform": device.platform,
        "installation_id": device.installation_id,
        "push_consent": device.push_consent,
        "push_token_present": device.token_ciphertext is not None,
        "app_version": device.app_version,
        "os_version": device.os_version,
        "device_name": device.device_name,
        "last_seen_at": device.last_seen_at,
        "revoked_at": device.revoked_at,
    }


@transaction.atomic
def register_member_device(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    actor_user: Any,
    platform: str,
    installation_id: uuid.UUID,
    push_consent: bool,
    push_token: str | None,
    app_version: str,
    os_version: str,
    device_name: str | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[MemberDevice, bool]:
    set_gym_context(gym_id=gym.id)
    member = Member.objects.select_for_update().filter(gym=gym, id=member_id).first()
    if member is None or member.status != Member.Status.ACTIVE:
        raise Http404
    existing = (
        MemberDevice.objects.select_for_update()
        .filter(gym=gym, member=member, installation_id=installation_id)
        .first()
    )
    if existing is not None and existing.platform != platform:
        raise DeviceConflict()
    created = existing is None
    now = timezone.now()
    _enforce_active_limit(
        gym=gym,
        member=member,
        keep_id=None if existing is None or existing.revoked_at is not None else existing.id,
    )
    ciphertext, fingerprint = _token_material(push_consent=push_consent, push_token=push_token)
    if existing is None:
        device = MemberDevice(
            gym=gym,
            member=member,
            installation_id=installation_id,
            platform=platform,
            push_consent=push_consent,
            token_ciphertext=ciphertext,
            token_fingerprint=fingerprint,
            app_version=app_version,
            os_version=os_version,
            device_name=device_name,
            last_seen_at=now,
        )
        _save_device(device)
        action = "MEMBER_DEVICE_REGISTERED"
    else:
        device = existing
        device.push_consent = push_consent
        device.token_ciphertext = ciphertext
        device.token_fingerprint = fingerprint
        device.app_version = app_version
        device.os_version = os_version
        device.device_name = device_name
        device.last_seen_at = now
        device.revoked_at = None
        device.revocation_reason = None
        _save_device(device)
        action = "MEMBER_DEVICE_REGISTERED" if created else "MEMBER_DEVICE_UPDATED"
    _audit_device(
        gym=gym,
        actor_user=actor_user,
        action=action,
        device=device,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return device, created


@transaction.atomic
def update_member_device(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    device_id: uuid.UUID,
    actor_user: Any,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> MemberDevice:
    set_gym_context(gym_id=gym.id)
    device = _own_device(gym=gym, member_id=member_id, device_id=device_id, lock=True)
    if device.revoked_at is not None:
        raise DeviceConflict()
    consent = changes["push_consent"] if "push_consent" in changes else device.push_consent
    if "push_token" in changes:
        token = changes["push_token"]
    elif consent and device.token_ciphertext is None:
        token = None
    else:
        token = None if not consent else _KEEP
    if consent and token is None:
        from apps.common.api.exceptions import RequestRejected

        raise RequestRejected({"push_token": ["A push token is required when consent is on."]})
    if not consent and changes.get("push_token"):
        from apps.common.api.exceptions import RequestRejected

        raise RequestRejected({"push_token": ["Omit the push token when consent is off."]})
    if token is _KEEP:
        ciphertext, fingerprint = device.token_ciphertext, device.token_fingerprint
    elif consent:
        ciphertext, fingerprint = _token_material(push_consent=True, push_token=str(token))
    else:
        ciphertext, fingerprint = None, None
    device.push_consent = bool(consent)
    device.token_ciphertext = ciphertext
    device.token_fingerprint = fingerprint
    if "app_version" in changes:
        device.app_version = str(changes["app_version"])
    if "os_version" in changes:
        device.os_version = str(changes["os_version"])
    if "device_name" in changes:
        device.device_name = changes["device_name"]
    device.last_seen_at = timezone.now()
    _save_device(device)
    _audit_device(
        gym=gym,
        actor_user=actor_user,
        action="MEMBER_DEVICE_UPDATED",
        device=device,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return device


@transaction.atomic
def delete_member_device(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    device_id: uuid.UUID,
    actor_user: Any,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    set_gym_context(gym_id=gym.id)
    device = _own_device(gym=gym, member_id=member_id, device_id=device_id, lock=True)
    if device.revoked_at is not None:
        return
    _revoke(device, reason=MemberDevice.RevocationReason.CLIENT_DELETED)
    _audit_device(
        gym=gym,
        actor_user=actor_user,
        action="MEMBER_DEVICE_REVOKED",
        device=device,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def revoke_member_devices(
    *,
    gym_id: uuid.UUID,
    member_id: uuid.UUID,
    reason: str,
) -> int:
    """Revoke every live device for one member. Ciphertext is cleared."""

    now = timezone.now()
    updated = MemberDevice.objects.filter(
        gym_id=gym_id,
        member_id=member_id,
        revoked_at__isnull=True,
    ).update(
        revoked_at=now,
        revocation_reason=reason,
        token_ciphertext=None,
        updated_at=now,
    )
    return int(updated)


def revoke_devices_for_user(*, user_id: uuid.UUID, reason: str) -> None:
    """Revoke devices in every gym linked to this user. Used by password reset."""

    for gym_id in Gym.objects.order_by("id").values_list("id", flat=True):
        set_gym_context(gym_id=gym_id)
        member_ids = Member.objects.filter(gym_id=gym_id, linked_user_id=user_id).values_list(
            "id", flat=True
        )
        for member_id in member_ids:
            revoke_member_devices(gym_id=gym_id, member_id=member_id, reason=reason)


def revoke_invalid_token(*, device: MemberDevice) -> None:
    if device.revoked_at is not None:
        return
    _revoke(device, reason=MemberDevice.RevocationReason.INVALID_TOKEN)


class _KeepToken:
    pass


_KEEP = _KeepToken()


def _token_material(*, push_consent: bool, push_token: str | None) -> tuple[str | None, str | None]:
    if not push_consent:
        return None, None
    if not push_token:
        from apps.common.api.exceptions import RequestRejected

        raise RequestRejected({"push_token": ["A push token is required when consent is on."]})
    return encrypt_push_token(push_token), fingerprint_push_token(push_token)


def _enforce_active_limit(*, gym: Gym, member: Member, keep_id: uuid.UUID | None) -> None:
    active = list(
        MemberDevice.objects.select_for_update()
        .filter(gym=gym, member=member, revoked_at__isnull=True)
        .order_by("id")
    )
    counted = [row for row in active if row.id != keep_id]
    if len(counted) < _ACTIVE_DEVICE_LIMIT:
        return
    victim = min(counted, key=lambda row: (row.last_seen_at, row.id))
    _revoke(victim, reason=MemberDevice.RevocationReason.SUPERSEDED)


def _revoke(device: MemberDevice, *, reason: str) -> None:
    device.revoked_at = timezone.now()
    device.revocation_reason = reason
    device.token_ciphertext = None
    device.save(update_fields=["revoked_at", "revocation_reason", "token_ciphertext", "updated_at"])


def _save_device(device: MemberDevice) -> None:
    try:
        device.save()
    except IntegrityError as exc:
        if "member_device_fingerprint_uniq" in str(exc):
            raise DeviceConflict() from exc
        raise


def _own_device(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    device_id: uuid.UUID,
    lock: bool,
) -> MemberDevice:
    queryset = MemberDevice.objects.filter(gym=gym, member_id=member_id, id=device_id)
    if lock:
        queryset = queryset.select_for_update()
    device = queryset.first()
    if device is None:
        raise Http404
    return device


def _audit_device(
    *,
    gym: Gym,
    actor_user: Any,
    action: str,
    device: MemberDevice,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=None,
        action=action,
        module="notifications",
        target_type="MemberDevice",
        target_id=device.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "platform": device.platform,
            "installation_id": str(device.installation_id),
            "push_consent": device.push_consent,
            "credential_present": device.token_ciphertext is not None,
            "revocation_reason": device.revocation_reason,
        },
    )
