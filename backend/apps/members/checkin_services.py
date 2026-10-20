"""Manual check-in commands and history reads.

Staff QR issuance lives in ``challenge_services``. Member-self and trainer QR
redemption live in ``scan_services``. This module does not store a device id
or source metadata.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.members.exceptions import (
    CheckInDuplicate,
    CheckInIneligible,
    CheckInOpen,
    CheckInStateConflict,
)
from apps.members.models import CheckIn, Member, Membership
from apps.tenants.exceptions import IdempotencyKeyReused
from apps.tenants.models import Gym, GymMembership, GymSettings

CHECK_IN_BACKDATE_LIMIT = timedelta(hours=24)
CHECK_IN_CLOCK_SKEW = timedelta(seconds=60)
CHECK_IN_NOTE_LIMIT = 500
CHECK_IN_LIST_ORDERING = "check_in_at DESC,id DESC"
CHECK_IN_LIST_CURSOR_SALT = "check-in-list"
MEMBER_CHECK_IN_HISTORY_CURSOR_SALT = "member-check-in-history"
_CHECK_IN_CURSOR_MAX_AGE = 24 * 60 * 60
_CHECK_IN_CURSOR_POSITION_KEYS = frozenset({"check_in_at", "id"})
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_SNAPSHOT_KEYS = (
    "eligible",
    "backdated",
    "membership_id",
    "membership_status",
    "plan_code",
    "plan_name",
    "start_date",
    "expiry_date",
    "evaluated_on",
)
_AWARE_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


class CheckInWrite(NamedTuple):
    check_in: CheckIn
    created: bool


def parse_idempotency_key(header: str | None) -> str | None:
    if header is None or header == "":
        return None
    if _IDEMPOTENCY_KEY_PATTERN.fullmatch(header) is None:
        raise ValidationError({"Idempotency-Key": ["Enter a valid idempotency key."]})
    return header


def validate_manual_occurred_at(
    occurred_at: datetime,
    *,
    now: datetime,
    action: str = "Check-in",
) -> datetime:
    """Accept a timezone-aware instant inside the manual backdate bound."""

    if timezone.is_naive(occurred_at):
        raise ValidationError({"occurred_at": ["Enter a timezone-aware time."]})
    if occurred_at > now + CHECK_IN_CLOCK_SKEW:
        raise ValidationError({"occurred_at": [f"{action} time cannot be in the future."]})
    if occurred_at < now - CHECK_IN_BACKDATE_LIMIT:
        raise ValidationError({"occurred_at": [f"{action} can only be backdated 24 hours."]})
    return occurred_at


def aware_instant_text(value: str) -> bool:
    return _AWARE_OFFSET.search(value.strip()) is not None


def member_check_in_summary(*, gym: Gym, member: Member) -> dict[str, object]:
    """Return the bounded attendance summary for one member.

    Two queries stay constant as history grows: the latest check-in, then the
    open check-in if one exists. An open visit is not assumed to be the latest
    row because a later backdated visit can have an earlier ``check_in_at``.
    """

    latest = CheckIn.objects.filter(gym=gym, member=member).order_by("-check_in_at", "-id").first()
    open_check_in = (
        CheckIn.objects.filter(gym=gym, member=member, check_out_at__isnull=True)
        .order_by("-check_in_at", "-id")
        .only("id")
        .first()
    )
    return {
        "available": True,
        "currently_checked_in": open_check_in is not None,
        "open_check_in_id": None if open_check_in is None else open_check_in.id,
        "last_check_in_at": None if latest is None else latest.check_in_at,
        "last_check_out_at": None if latest is None else latest.check_out_at,
    }


def page_check_ins(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID | None,
    open_state: str,
    limit: int,
    after: str,
) -> tuple[list[CheckIn], dict[str, Any]]:
    from apps.trainers.selectors import (
        gym_local_date,
        require_assigned_member,
        scope_to_assigned_members,
    )

    as_of = gym_local_date(gym)
    if member_id is not None:
        require_assigned_member(gym_id=gym.id, actor=actor, member_id=member_id, as_of=as_of)
        if not Member.objects.filter(gym=gym, id=member_id).exists():
            raise Http404
    scope = _list_scope(gym_id=gym.id, member_id=member_id, open_state=open_state, limit=limit)
    queryset = scope_to_assigned_members(
        _history_queryset(gym_id=gym.id, member_id=member_id, open_state=open_state),
        gym_id=gym.id,
        actor=actor,
        as_of=as_of,
        member_ref="member_id",
    )
    return _page(queryset, after=after, limit=limit, scope=scope, salt=CHECK_IN_LIST_CURSOR_SALT)


def page_member_check_ins(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    limit: int,
    after: str,
) -> tuple[list[CheckIn], dict[str, Any]]:
    from apps.trainers.selectors import gym_local_date, require_assigned_member

    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member_id,
        as_of=gym_local_date(gym),
    )
    if not Member.objects.filter(gym=gym, id=member_id).exists():
        raise Http404
    scope = _history_scope(gym_id=gym.id, member_id=member_id, limit=limit)
    queryset = _history_queryset(gym_id=gym.id, member_id=member_id, open_state="")
    return _page(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=MEMBER_CHECK_IN_HISTORY_CURSOR_SALT,
    )


def page_self_check_ins(
    *,
    gym: Gym,
    member_id: UUID,
    limit: int,
    after: str,
) -> tuple[list[CheckIn], dict[str, Any]]:
    """Page the token member's check-ins without a staff assignment gate."""

    scope = _history_scope(gym_id=gym.id, member_id=member_id, limit=limit)
    queryset = _history_queryset(gym_id=gym.id, member_id=member_id, open_state="")
    return _page(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=MEMBER_CHECK_IN_HISTORY_CURSOR_SALT,
    )


def get_check_in(*, gym: Gym, actor: GymMembership, check_in_id: UUID) -> CheckIn:
    from apps.trainers.selectors import gym_local_date, require_assigned_member

    check_in = CheckIn.objects.select_related("member").filter(gym=gym, id=check_in_id).first()
    if check_in is None:
        raise Http404
    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=check_in.member_id,
        as_of=gym_local_date(gym),
    )
    return check_in


@transaction.atomic
def create_manual_check_in(
    *,
    gym: Gym,
    actor: GymMembership,
    member_id: UUID,
    occurred_at: datetime | None,
    note: str,
    idempotency_key: str | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckInWrite:
    from apps.trainers.selectors import gym_local_date, require_assigned_member

    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member_id,
        as_of=gym_local_date(gym),
    )
    cleaned_note = _clean_note(note)
    now = timezone.now()
    if occurred_at is not None:
        occurred_at = validate_manual_occurred_at(occurred_at, now=now)
    member = _locked_member(gym, member_id)
    if idempotency_key is not None:
        existing = (
            CheckIn.objects.select_related("member")
            .filter(gym=gym, idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            _require_same_request(
                existing,
                member_id=member.id,
                occurred_at=occurred_at,
                note=cleaned_note,
            )
            return CheckInWrite(existing, created=False)
    if member.status != Member.Status.ACTIVE:
        raise CheckInIneligible()
    check_in_at = occurred_at if occurred_at is not None else now
    evaluated_on = check_in_at.astimezone(ZoneInfo(gym.timezone)).date()
    membership = _eligible_membership(gym=gym, member=member, evaluated_on=evaluated_on)
    _reject_conflicts(
        gym=gym,
        member=member,
        check_in_at=check_in_at,
        window_seconds=_duplicate_window(gym),
    )
    snapshot = _snapshot(
        membership=membership,
        backdated=occurred_at is not None,
        evaluated_on=evaluated_on,
    )
    try:
        with transaction.atomic():
            check_in = CheckIn.objects.create(
                gym=gym,
                member=member,
                membership=membership,
                check_in_at=check_in_at,
                method=CheckIn.Method.MANUAL,
                recorded_by_membership_id=actor.id,
                device_id=None,
                source_metadata=None,
                idempotency_key=idempotency_key,
                note=cleaned_note,
                eligibility_snapshot=snapshot,
                created_by_membership_id=actor.id,
            )
    except IntegrityError as exc:
        if idempotency_key is None or "checkin_gym_idempotency_uniq" not in str(exc):
            raise
        existing = CheckIn.objects.select_related("member").get(
            gym=gym, idempotency_key=idempotency_key
        )
        _require_same_request(
            existing,
            member_id=member.id,
            occurred_at=occurred_at,
            note=cleaned_note,
        )
        return CheckInWrite(existing, created=False)
    check_in = CheckIn.objects.select_related("member").get(gym=gym, id=check_in.id)
    _audit(
        gym=gym,
        actor=actor,
        action="CHECK_IN_RECORDED",
        check_in=check_in,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        backdated=occurred_at is not None,
    )
    return CheckInWrite(check_in, created=True)


@transaction.atomic
def check_out_check_in(
    *,
    gym: Gym,
    actor: GymMembership,
    check_in_id: UUID,
    occurred_at: datetime | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> CheckIn:
    _deny_trainer_only(gym, actor)
    now = timezone.now()
    if occurred_at is not None and timezone.is_naive(occurred_at):
        raise ValidationError({"occurred_at": ["Enter a timezone-aware time."]})
    located = (
        CheckIn.objects.filter(gym=gym, id=check_in_id).values_list("member_id", flat=True).first()
    )
    if located is None:
        raise Http404
    _locked_member(gym, located)
    check_in = (
        CheckIn.objects.select_for_update()
        .select_related("member")
        .filter(gym=gym, id=check_in_id)
        .first()
    )
    if check_in is None:
        raise Http404
    if check_in.check_out_at is not None:
        if occurred_at is None or occurred_at == check_in.check_out_at:
            return check_in
        raise CheckInStateConflict()
    if occurred_at is not None:
        occurred_at = validate_manual_occurred_at(occurred_at, now=now, action="Check-out")
    checkout_at = occurred_at if occurred_at is not None else now
    if checkout_at <= check_in.check_in_at:
        raise ValidationError({"occurred_at": ["Check-out must be after check-in."]})
    check_in.check_out_at = checkout_at
    check_in.save(update_fields=["check_out_at", "updated_at"])
    _audit(
        gym=gym,
        actor=actor,
        action="CHECK_IN_CHECKED_OUT",
        check_in=check_in,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        backdated=occurred_at is not None,
    )
    return check_in


def _clean_note(note: str) -> str:
    cleaned = note.strip()
    if len(cleaned) > CHECK_IN_NOTE_LIMIT:
        raise ValidationError({"note": ["Note is too long."]})
    return cleaned


def _deny_trainer_only(gym: Gym, actor: GymMembership) -> None:
    """Deny display, challenge, and check-out access for trainer-only staff.

    Assignment scope does not open those routes. Member, membership, check-in
    history, and profile-photo routes use the effective assignment instead.
    """

    from apps.trainers.selectors import deny_trainer_only

    deny_trainer_only(gym_id=gym.id, actor=actor)


def _locked_member(gym: Gym, member_id: UUID) -> Member:
    try:
        return Member.objects.select_for_update().get(gym=gym, id=member_id)
    except Member.DoesNotExist as exc:
        raise Http404 from exc


def _eligible_membership(*, gym: Gym, member: Member, evaluated_on: date) -> Membership:
    candidate = (
        Membership.objects.filter(
            gym=gym,
            member=member,
            status=Membership.Status.ACTIVE,
            start_date__lte=evaluated_on,
            expiry_date__gte=evaluated_on,
        )
        .order_by("-start_date", "-created_at", "-id")
        .first()
    )
    if candidate is None:
        raise CheckInIneligible()
    membership = Membership.objects.select_for_update().get(gym=gym, id=candidate.id)
    if (
        membership.status != Membership.Status.ACTIVE
        or membership.start_date > evaluated_on
        or membership.expiry_date < evaluated_on
    ):
        raise CheckInIneligible()
    return membership


def _duplicate_window(gym: Gym) -> int:
    return GymSettings.objects.values_list("check_in_duplicate_window_seconds", flat=True).get(
        gym=gym
    )


def _reject_conflicts(
    *,
    gym: Gym,
    member: Member,
    check_in_at: datetime,
    window_seconds: int,
) -> None:
    if CheckIn.objects.filter(gym=gym, member=member, check_out_at__isnull=True).exists():
        raise CheckInOpen()
    if window_seconds <= 0:
        return
    window = timedelta(seconds=window_seconds)
    if CheckIn.objects.filter(
        gym=gym,
        member=member,
        check_in_at__gte=check_in_at - window,
        check_in_at__lte=check_in_at + window,
    ).exists():
        raise CheckInDuplicate()


def _snapshot(*, membership: Membership, backdated: bool, evaluated_on: date) -> dict[str, object]:
    evaluated = evaluated_on.isoformat()
    snapshot = {
        "eligible": True,
        "backdated": backdated,
        "membership_id": str(membership.id),
        "membership_status": membership.status,
        "plan_code": membership.plan_code,
        "plan_name": membership.plan_name,
        "start_date": membership.start_date.isoformat(),
        "expiry_date": membership.expiry_date.isoformat(),
        "evaluated_on": evaluated,
    }
    if set(snapshot) != set(_SNAPSHOT_KEYS):
        raise ValidationError({"eligibility": ["Eligibility snapshot is invalid."]})
    return snapshot


def _require_same_request(
    existing: CheckIn,
    *,
    member_id: UUID,
    occurred_at: datetime | None,
    note: str,
) -> None:
    raw_snapshot = existing.eligibility_snapshot
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    same_time = True if occurred_at is None else existing.check_in_at == occurred_at
    if (
        existing.member_id != member_id
        or existing.note != note
        or existing.method != CheckIn.Method.MANUAL
        or bool(snapshot.get("backdated")) != (occurred_at is not None)
        or not same_time
    ):
        raise IdempotencyKeyReused()


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    check_in: CheckIn,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    backdated: bool,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="checkins",
        target_type="CheckIn",
        target_id=check_in.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "member_id": str(check_in.member_id),
            "membership_id": (
                None if check_in.membership_id is None else str(check_in.membership_id)
            ),
            "method": check_in.method,
            "check_in_at": check_in.check_in_at.isoformat(),
            "check_out_at": None
            if check_in.check_out_at is None
            else check_in.check_out_at.isoformat(),
            "backdated": backdated,
        },
    )


def _list_scope(
    *,
    gym_id: UUID,
    member_id: UUID | None,
    open_state: str,
    limit: int,
) -> dict[str, str]:
    return {
        "gym_id": str(gym_id),
        "member_id": "" if member_id is None else str(member_id),
        "open": open_state,
        "limit": str(limit),
        "ordering": CHECK_IN_LIST_ORDERING,
    }


def _history_scope(*, gym_id: UUID, member_id: UUID, limit: int) -> dict[str, str]:
    return {
        "gym_id": str(gym_id),
        "member_id": str(member_id),
        "limit": str(limit),
        "ordering": CHECK_IN_LIST_ORDERING,
    }


def _history_queryset(
    *,
    gym_id: UUID,
    member_id: UUID | None,
    open_state: str,
) -> QuerySet[CheckIn]:
    queryset = CheckIn.objects.select_related("member").filter(gym_id=gym_id)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if open_state == "true":
        queryset = queryset.filter(check_out_at__isnull=True)
    elif open_state == "false":
        queryset = queryset.filter(check_out_at__isnull=False)
    return queryset.order_by("-check_in_at", "-id")


def _page(
    queryset: QuerySet[CheckIn],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
) -> tuple[list[CheckIn], dict[str, Any]]:
    if after:
        check_in_at, check_in_id = _decode_cursor(after, scope=scope, salt=salt)
        queryset = queryset.filter(
            Q(check_in_at__lt=check_in_at) | Q(check_in_at=check_in_at, id__lt=check_in_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = signing.dumps(
            {"check_in_at": last.check_in_at.isoformat(), "id": str(last.id), **scope},
            salt=salt,
            compress=True,
        )
    return rows, {"has_more": has_more, "next_after": next_after}


def _decode_cursor(token: str, *, scope: dict[str, str], salt: str) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CHECK_IN_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected = set(scope) | _CHECK_IN_CURSOR_POSITION_KEYS
        if set(loaded) != expected or any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        raw_time = loaded["check_in_at"]
        if not isinstance(raw_time, str):
            raise TypeError
        check_in_at = datetime.fromisoformat(raw_time)
        if check_in_at.tzinfo is None:
            raise ValueError
        check_in_id = UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return check_in_at, check_in_id
