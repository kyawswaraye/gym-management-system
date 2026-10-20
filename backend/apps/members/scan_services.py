"""Onsite QR redemption for a member session and for an assigned trainer.

Lock order, and only this order:

1. the challenge row by its global hash, ``FOR UPDATE``;
2. the named member, ``FOR UPDATE``;
3. the eligible membership, ``FOR UPDATE``.

Do not lock ``GymSettings``. Staff issuance locks settings and then active
challenges; taking those locks in the opposite order can deadlock. The
duplicate window and the trainer assignment check are plain reads.

The request body is not stored. A failed check raises before the use counter
moves, and the surrounding transaction rolls back any later insert failure.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.audit.services import write_tenant_audit
from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX, challenge_digest
from apps.members.checkin_services import (
    _audit,
    _duplicate_window,
    _eligible_membership,
    _locked_member,
    _reject_conflicts,
    _snapshot,
)
from apps.members.exceptions import CheckInCodeInvalid, CheckInDuplicate, CheckInIneligible
from apps.members.models import CheckIn, CheckInChallenge, Member
from apps.tenants.models import Gym, GymMembership, StaffProfile
from apps.trainers.selectors import member_is_assigned
from apps.users.models import User

_PAYLOAD_SUFFIX = re.compile(r"^[A-Za-z0-9_-]{20,90}$")
_PAYLOAD_MAX_LENGTH = 128
_MemberReady = Callable[[Member, date], None]
_RecordVisit = Callable[[CheckIn], None]


def payload_is_well_formed(payload: str) -> bool:
    """Accept only the issued prefix plus a bounded token alphabet."""

    if len(payload) > _PAYLOAD_MAX_LENGTH or not payload.startswith(CHALLENGE_PAYLOAD_PREFIX):
        return False
    suffix = payload[len(CHALLENGE_PAYLOAD_PREFIX) :]
    return _PAYLOAD_SUFFIX.fullmatch(suffix) is not None


@transaction.atomic
def redeem_member_check_in_challenge(
    *,
    gym: Gym,
    member_id: UUID,
    user_id: UUID,
    payload: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckIn:
    """Redeem one challenge use into one QR check-in for the token member."""

    linked_user: list[User] = []

    def prepare(member: Member, _as_of: date) -> None:
        linked = member.linked_user
        if member.status != Member.Status.ACTIVE or linked is None or linked.pk != user_id:
            raise CheckInIneligible()
        linked_user.append(linked)

    def record(check_in: CheckIn) -> None:
        write_tenant_audit(
            gym=gym,
            actor_user=linked_user[0],
            actor_membership_id=None,
            action="MEMBER_CHECK_IN_RECORDED",
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
                "check_in_at": check_in.check_in_at.isoformat(),
                "check_out_at": None,
                "backdated": False,
            },
        )

    return _redeem_onsite_challenge(
        gym=gym,
        member_id=member_id,
        payload=payload,
        prepare_member=prepare,
        record_visit=record,
    )


@transaction.atomic
def redeem_trainer_check_in_challenge(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    payload: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckIn:
    """Redeem one challenge use for a member assigned to this trainer today.

    The visit stays a ``QR`` row with null membership actors. The staff actor
    is the audit row, using the same check-in provenance values as a manual
    visit. Owner or manager on this membership does not widen the assignment.
    """

    def prepare(member: Member, as_of: date) -> None:
        _require_trainer_assignment(gym=gym, actor=actor, member=member, as_of=as_of)

    def record(check_in: CheckIn) -> None:
        _audit(
            gym=gym,
            actor=actor,
            action="CHECK_IN_RECORDED",
            check_in=check_in,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            backdated=False,
        )

    return _redeem_onsite_challenge(
        gym=gym,
        member_id=member_id,
        payload=payload,
        prepare_member=prepare,
        record_visit=record,
    )


def _require_trainer_assignment(
    *,
    gym: Gym,
    actor: GymMembership,
    member: Member,
    as_of: date,
) -> None:
    if member.status != Member.Status.ACTIVE:
        raise Http404
    profile_ids = tuple(
        StaffProfile.objects.filter(
            gym_id=gym.id,
            gym_membership_id=actor.id,
            status=StaffProfile.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    if not member_is_assigned(
        gym_id=gym.id,
        profile_ids=profile_ids,
        member_id=member.id,
        as_of=as_of,
    ):
        raise Http404


def _redeem_onsite_challenge(
    *,
    gym: Gym,
    member_id: UUID,
    payload: str,
    prepare_member: _MemberReady,
    record_visit: _RecordVisit,
) -> CheckIn:
    if not payload_is_well_formed(payload):
        raise CheckInCodeInvalid()
    now = timezone.now()
    challenge = (
        CheckInChallenge.objects.select_for_update()
        .filter(challenge_hash=challenge_digest(payload))
        .first()
    )
    if not _redeemable(challenge, gym_id=gym.id, now=now):
        raise CheckInCodeInvalid()
    assert challenge is not None
    member = _locked_member(gym, member_id)
    evaluated_on = now.astimezone(ZoneInfo(gym.timezone)).date()
    prepare_member(member, evaluated_on)
    membership = _eligible_membership(gym=gym, member=member, evaluated_on=evaluated_on)
    _reject_conflicts(
        gym=gym,
        member=member,
        check_in_at=now,
        window_seconds=_duplicate_window(gym),
    )
    if _member_already_redeemed(gym=gym, member=member, issued_at=challenge.issued_at):
        raise CheckInDuplicate()
    _consume_one_use(challenge, now=now)
    check_in = CheckIn.objects.create(
        gym=gym,
        member=member,
        membership=membership,
        check_in_at=now,
        method=CheckIn.Method.QR,
        recorded_by_membership_id=None,
        device_id=None,
        source_metadata=None,
        idempotency_key=None,
        note="",
        eligibility_snapshot=_snapshot(
            membership=membership,
            backdated=False,
            evaluated_on=evaluated_on,
        ),
        created_by_membership_id=None,
    )
    check_in = CheckIn.objects.select_related("member").get(gym=gym, id=check_in.id)
    record_visit(check_in)
    return check_in


def _redeemable(
    challenge: CheckInChallenge | None,
    *,
    gym_id: UUID,
    now: datetime,
) -> bool:
    if challenge is None or challenge.gym_id != gym_id:
        return False
    return (
        challenge.status == CheckInChallenge.Status.ACTIVE
        and challenge.consumed_at is None
        and challenge.expires_at > now
        and challenge.use_count < challenge.max_uses
    )


def _member_already_redeemed(*, gym: Gym, member: Member, issued_at: datetime) -> bool:
    """One member redeems one active challenge at most once.

    Staff issuance revokes the previous active staff challenge, so a QR visit
    at or after this challenge's issue time is a redemption of this challenge.
    A later challenge has a later issue time and is not blocked by the earlier
    visit. The check runs before the use counter moves.
    """

    return CheckIn.objects.filter(
        gym=gym,
        member=member,
        method=CheckIn.Method.QR,
        check_in_at__gte=issued_at,
    ).exists()


def _consume_one_use(challenge: CheckInChallenge, *, now: datetime) -> None:
    challenge.use_count += 1
    update_fields = ["use_count", "updated_at"]
    if challenge.use_count == challenge.max_uses:
        challenge.status = CheckInChallenge.Status.CONSUMED
        challenge.consumed_at = now
        update_fields.extend(["status", "consumed_at"])
    challenge.save(update_fields=update_fields)
