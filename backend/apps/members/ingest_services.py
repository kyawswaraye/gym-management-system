"""Device-authenticated NFC and biometric check-in ingestion.

Lock order, and only this order:

1. ``GymSettings``
2. the device row, and only that row
3. the member
4. the eligible membership

Challenge issuance uses the same settings-then-device prefix and then locks
that device's challenges. Member scan locks a challenge before the member.
This path does not lock a challenge and does not lock ``tenants_gym``. A
display credential cannot ingest, and an NFC or biometric credential cannot
issue a challenge.

The external event id is not stored. The idempotency key is its SHA-256 hex
digest mixed with the device id. Source metadata stays null. The visit does
not change payment state.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.members.checkin_services import (
    _eligible_membership,
    _locked_member,
    _reject_conflicts,
    _snapshot,
)
from apps.members.device_services import authenticate_device
from apps.members.exceptions import CheckInIneligible
from apps.members.models import CheckIn, CheckInDevice, Member
from apps.tenants.exceptions import IdempotencyKeyReused
from apps.tenants.models import Gym

_INGEST_KINDS = frozenset({CheckInDevice.Kind.NFC, CheckInDevice.Kind.BIOMETRIC})


def device_event_key(*, device_id: UUID, event_id: str) -> str:
    digest = hashlib.sha256(f"{device_id}:{event_id}".encode()).hexdigest()
    return digest


def ingest_device_check_in(
    *,
    authorization: str,
    member_id: UUID,
    event_id: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[CheckIn, bool]:
    """Record one server-timed visit for the authenticated ingest device."""

    with transaction.atomic():
        device, settings = authenticate_device(authorization=authorization)
        if device.kind not in _INGEST_KINDS:
            raise AuthenticationFailed()
        gym = device.gym
        now = timezone.now()
        member = _locked_member(gym, member_id)
        key = device_event_key(device_id=device.id, event_id=event_id)
        existing = CheckIn.objects.select_for_update().filter(gym=gym, idempotency_key=key).first()
        if existing is not None:
            _require_same_event(existing, member=member, device=device)
            return existing, False
        if member.status != Member.Status.ACTIVE:
            raise CheckInIneligible()
        evaluated_on = now.astimezone(ZoneInfo(gym.timezone)).date()
        membership = _eligible_membership(gym=gym, member=member, evaluated_on=evaluated_on)
        _reject_conflicts(
            gym=gym,
            member=member,
            check_in_at=now,
            window_seconds=settings.check_in_duplicate_window_seconds,
        )
        try:
            with transaction.atomic():
                check_in = CheckIn.objects.create(
                    gym=gym,
                    member=member,
                    membership=membership,
                    check_in_at=now,
                    method=device.kind,
                    recorded_by_membership_id=None,
                    device_id=device.id,
                    source_metadata=None,
                    idempotency_key=key,
                    note="",
                    eligibility_snapshot=_snapshot(
                        membership=membership, backdated=False, evaluated_on=evaluated_on
                    ),
                    created_by_membership_id=None,
                )
        except IntegrityError:
            raced = CheckIn.objects.select_for_update().get(gym=gym, idempotency_key=key)
            _require_same_event(raced, member=member, device=device)
            return raced, False
        check_in = CheckIn.objects.select_related("member").get(gym=gym, id=check_in.id)
        _audit(
            gym=gym,
            check_in=check_in,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            recorded_at=now,
        )
        return check_in, True


def _require_same_event(existing: CheckIn, *, member: Member, device: CheckInDevice) -> None:
    if (
        existing.member_id != member.id
        or existing.device_id != device.id
        or existing.method != device.kind
    ):
        raise IdempotencyKeyReused()


def _audit(
    *,
    gym: Gym,
    check_in: CheckIn,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    recorded_at: datetime,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=None,
        actor_membership_id=None,
        action="DEVICE_CHECK_IN_RECORDED",
        module="checkins",
        target_type="CheckIn",
        target_id=check_in.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "member_id": str(check_in.member_id),
            "membership_id": str(check_in.membership_id),
            "method": check_in.method,
            "check_in_at": recorded_at.isoformat(),
            "device_id": str(check_in.device_id),
            "backdated": False,
        },
    )
