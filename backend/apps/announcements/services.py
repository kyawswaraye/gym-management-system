"""Announcement lifecycle and server-side recipient resolution."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.announcements.exceptions import AnnouncementStateConflict
from apps.announcements.models import Announcement
from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership
from apps.members.services import gym_today
from apps.notifications.models import Notification
from apps.notifications.services import (
    ANNOUNCEMENT_TEMPLATE_KEY,
    ANNOUNCEMENT_TEMPLATE_VERSION,
    header_safe,
    in_app_announcement_deduplication_key,
    push_announcement_deduplication_key,
)
from apps.tenants.models import Gym, GymMembership
from apps.trainers.selectors import member_pt_client_badge

_CURSOR_SALT = "announcement-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_ORDERING = "created_at DESC, id DESC"
_PAST_START_GRACE = timedelta(seconds=60)


def announcement_payload(row: Announcement) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "body": row.body,
        "audience": row.audience,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "status": row.status,
        "published_at": row.published_at,
        "cancelled_at": row.cancelled_at,
        "expired_at": row.expired_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@transaction.atomic
def create_announcement(
    *,
    gym: Gym,
    actor: GymMembership,
    actor_user: Any,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Announcement:
    set_gym_context(gym_id=gym.id)
    _require_schedule(fields.get("starts_at"), fields.get("ends_at"))
    row = Announcement.objects.create(
        gym=gym,
        type=fields["type"],
        title=fields["title"],
        body=fields["body"],
        audience=fields["audience"],
        starts_at=fields.get("starts_at"),
        ends_at=fields.get("ends_at"),
        status=Announcement.Status.DRAFT,
        created_by_membership=actor,
    )
    _audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor.id,
        action="ANNOUNCEMENT_CREATED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def update_announcement(
    *,
    gym: Gym,
    actor: GymMembership,
    actor_user: Any,
    announcement_id: uuid.UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Announcement:
    set_gym_context(gym_id=gym.id)
    row = _locked(gym, announcement_id)
    if row.status != Announcement.Status.DRAFT:
        raise AnnouncementStateConflict()
    starts_at = fields["starts_at"] if "starts_at" in fields else row.starts_at
    ends_at = fields["ends_at"] if "ends_at" in fields else row.ends_at
    _require_schedule(starts_at, ends_at)
    for name in ("type", "title", "body", "audience", "starts_at", "ends_at"):
        if name in fields:
            setattr(row, name, fields[name])
    row.save(
        update_fields=["type", "title", "body", "audience", "starts_at", "ends_at", "updated_at"]
    )
    _audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor.id,
        action="ANNOUNCEMENT_UPDATED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def publish_announcement(
    *,
    gym: Gym,
    actor: GymMembership,
    actor_user: Any,
    announcement_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Announcement:
    set_gym_context(gym_id=gym.id)
    row = _locked(gym, announcement_id)
    if row.status != Announcement.Status.DRAFT:
        raise AnnouncementStateConflict()
    now = timezone.now()
    if row.starts_at is not None and row.starts_at > now:
        row.status = Announcement.Status.SCHEDULED
        row.save(update_fields=["status", "updated_at"])
        action = "ANNOUNCEMENT_SCHEDULED"
    else:
        _mark_published(row, actor_id=actor.id, now=now)
        _insert_recipients(gym=gym, row=row, now=now, actor_id=actor.id)
        action = "ANNOUNCEMENT_PUBLISHED"
    _audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor.id,
        action=action,
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


@transaction.atomic
def cancel_announcement(
    *,
    gym: Gym,
    actor: GymMembership,
    actor_user: Any,
    announcement_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Announcement:
    set_gym_context(gym_id=gym.id)
    row = _locked(gym, announcement_id)
    if row.status == Announcement.Status.CANCELLED:
        return row
    if row.status == Announcement.Status.EXPIRED:
        raise AnnouncementStateConflict()
    if row.status not in {
        Announcement.Status.DRAFT,
        Announcement.Status.SCHEDULED,
        Announcement.Status.PUBLISHED,
    }:
        raise AnnouncementStateConflict()
    now = timezone.now()
    was_published = row.status == Announcement.Status.PUBLISHED
    row.status = Announcement.Status.CANCELLED
    row.cancelled_at = now
    row.cancelled_by_membership = actor
    row.save(update_fields=["status", "cancelled_at", "cancelled_by_membership", "updated_at"])
    if was_published:
        _cancel_pending_pushes(gym=gym, announcement_id=row.id)
    _audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor.id,
        action="ANNOUNCEMENT_CANCELLED",
        row=row,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return row


def get_announcement(*, gym: Gym, announcement_id: uuid.UUID) -> Announcement:
    set_gym_context(gym_id=gym.id)
    row = Announcement.objects.filter(gym=gym, id=announcement_id).first()
    if row is None:
        raise NotFound()
    return row


def page_announcements(
    *,
    gym: Gym,
    status: str,
    announcement_type: str,
    audience: str,
    limit: int,
    after: str,
) -> tuple[list[Announcement], dict[str, Any]]:
    set_gym_context(gym_id=gym.id)
    scope = {
        "gym_id": str(gym.id),
        "status": status,
        "type": announcement_type,
        "audience": audience,
        "ordering": _ORDERING,
    }
    queryset = Announcement.objects.filter(gym=gym)
    if status:
        queryset = queryset.filter(status=status)
    if announcement_type:
        queryset = queryset.filter(type=announcement_type)
    if audience:
        queryset = queryset.filter(audience=audience)
    queryset = queryset.order_by("-created_at", "-id")
    if after:
        created_at, row_id = _decode_cursor(after, scope=scope)
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=_CURSOR_SALT,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


@transaction.atomic
def publish_due_announcement(*, gym: Gym, now: datetime) -> bool:
    """Promote one due scheduled announcement and insert recipient rows."""

    set_gym_context(gym_id=gym.id)
    row = (
        Announcement.objects.select_for_update(skip_locked=True)
        .filter(gym=gym, status=Announcement.Status.SCHEDULED, starts_at__lte=now)
        .order_by("starts_at", "id")
        .first()
    )
    if row is None:
        return False
    _mark_published(row, actor_id=row.created_by_membership_id, now=now)
    _insert_recipients(gym=gym, row=row, now=now, actor_id=row.created_by_membership_id)
    _audit(
        gym=gym,
        actor_user=None,
        actor_membership_id=row.created_by_membership_id,
        action="ANNOUNCEMENT_PUBLISHED",
        row=row,
        request_id="system",
        ip_address=None,
        user_agent="",
    )
    return True


@transaction.atomic
def expire_due_announcement(*, gym: Gym, now: datetime) -> bool:
    set_gym_context(gym_id=gym.id)
    row = (
        Announcement.objects.select_for_update(skip_locked=True)
        .filter(
            gym=gym,
            status=Announcement.Status.PUBLISHED,
            ends_at__isnull=False,
            ends_at__lt=now,
        )
        .order_by("ends_at", "id")
        .first()
    )
    if row is None:
        return False
    row.status = Announcement.Status.EXPIRED
    row.expired_at = now
    row.save(update_fields=["status", "expired_at", "updated_at"])
    _audit(
        gym=gym,
        actor_user=None,
        actor_membership_id=row.published_by_membership_id,
        action="ANNOUNCEMENT_EXPIRED",
        row=row,
        request_id="system",
        ip_address=None,
        user_agent="",
    )
    return True


def resolve_recipient_ids(*, gym: Gym, audience: str, today: object) -> list[uuid.UUID]:
    """Return active same-gym member ids for one fixed audience."""

    from datetime import date

    if not isinstance(today, date):
        raise TypeError("today must be a date")
    active = Member.objects.filter(gym=gym, status=Member.Status.ACTIVE)
    if audience == Announcement.Audience.ALL_ACTIVE_MEMBERS:
        return list(active.order_by("id").values_list("id", flat=True))
    if audience == Announcement.Audience.PT_CLIENTS:
        return list(
            active.filter(member_pt_client_badge(gym_id=gym.id, as_of=today))
            .order_by("id")
            .values_list("id", flat=True)
        )
    if audience == Announcement.Audience.EXPIRING_WITHIN_7_DAYS:
        return _expiring_member_ids(gym=gym, today=today, days=7)
    if audience == Announcement.Audience.EXPIRING_WITHIN_30_DAYS:
        return _expiring_member_ids(gym=gym, today=today, days=30)
    raise ValueError("audience is not allowed")


def _expiring_member_ids(*, gym: Gym, today: object, days: int) -> list[uuid.UUID]:
    from datetime import date

    if not isinstance(today, date):
        raise TypeError("today must be a date")
    until = today + timedelta(days=days)
    current: QuerySet[Membership] = (
        Membership.objects.filter(
            gym=gym,
            member__status=Member.Status.ACTIVE,
            status__in=(
                Membership.Status.PENDING,
                Membership.Status.ACTIVE,
                Membership.Status.SUSPENDED,
            ),
            start_date__lte=today,
            expiry_date__gte=today,
        )
        .order_by("member_id", "-start_date", "-created_at", "-id")
        .distinct("member_id")
    )
    return [row.member_id for row in current if today <= row.expiry_date <= until]


def _insert_recipients(
    *,
    gym: Gym,
    row: Announcement,
    now: datetime,
    actor_id: uuid.UUID,
) -> None:
    member_ids = resolve_recipient_ids(gym=gym, audience=row.audience, today=gym_today(gym))
    payload = {
        "title": header_safe(row.title, max_length=120),
        "body": header_safe(row.body, max_length=4000),
        "announcement_type": row.type,
    }
    for member_id in member_ids:
        _create_notification(
            gym=gym,
            member_id=member_id,
            announcement=row,
            channel=Notification.Channel.IN_APP,
            deduplication_key=in_app_announcement_deduplication_key(
                announcement_id=row.id,
                member_id=member_id,
            ),
            payload=payload,
            now=now,
            actor_id=actor_id,
            sent=True,
        )
        _create_notification(
            gym=gym,
            member_id=member_id,
            announcement=row,
            channel=Notification.Channel.PUSH,
            deduplication_key=push_announcement_deduplication_key(
                announcement_id=row.id,
                member_id=member_id,
            ),
            payload=payload,
            now=now,
            actor_id=actor_id,
            sent=False,
        )


def _create_notification(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    announcement: Announcement,
    channel: str,
    deduplication_key: str,
    payload: dict[str, str],
    now: datetime,
    actor_id: uuid.UUID,
    sent: bool,
) -> None:
    defaults: dict[str, Any] = {
        "recipient_member_id": member_id,
        "membership": None,
        "announcement": announcement,
        "type": Notification.Type.ANNOUNCEMENT,
        "template_key": ANNOUNCEMENT_TEMPLATE_KEY,
        "template_version": ANNOUNCEMENT_TEMPLATE_VERSION,
        "payload": payload,
        "scheduled_at": now,
        "provider_id": "",
        "last_error_code": "",
        "created_by_membership_id": actor_id,
        "read_at": None,
    }
    if sent:
        defaults.update(
            status=Notification.Status.SENT,
            sent_at=now,
            failed_at=None,
            next_attempt_at=None,
            attempt_count=1,
        )
    else:
        defaults.update(
            status=Notification.Status.PENDING,
            sent_at=None,
            failed_at=None,
            next_attempt_at=now,
            attempt_count=0,
        )
    try:
        Notification.objects.get_or_create(
            gym=gym,
            channel=channel,
            deduplication_key=deduplication_key,
            defaults=defaults,
        )
    except IntegrityError:
        return


def _cancel_pending_pushes(*, gym: Gym, announcement_id: uuid.UUID) -> None:
    Notification.objects.filter(
        gym=gym,
        announcement_id=announcement_id,
        channel=Notification.Channel.PUSH,
        status=Notification.Status.PENDING,
    ).update(
        status=Notification.Status.CANCELLED,
        next_attempt_at=None,
        sent_at=None,
        failed_at=None,
        last_error_code="",
        updated_at=timezone.now(),
    )


def _mark_published(row: Announcement, *, actor_id: uuid.UUID, now: datetime) -> None:
    row.status = Announcement.Status.PUBLISHED
    row.published_at = now
    row.published_by_membership_id = actor_id
    row.save(update_fields=["status", "published_at", "published_by_membership", "updated_at"])


def _locked(gym: Gym, announcement_id: uuid.UUID) -> Announcement:
    row = Announcement.objects.select_for_update().filter(gym=gym, id=announcement_id).first()
    if row is None:
        raise NotFound()
    return row


def _require_schedule(starts_at: datetime | None, ends_at: datetime | None) -> None:
    now = timezone.now()
    if starts_at is not None and starts_at < now - _PAST_START_GRACE:
        raise RequestRejected({"starts_at": ["Start time is too far in the past."]})
    if ends_at is not None and ends_at <= now:
        raise RequestRejected({"ends_at": ["End time must be in the future."]})
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise RequestRejected({"ends_at": ["End time must be after the start time."]})


def _decode_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, uuid.UUID]:
    try:
        loaded = signing.loads(token, salt=_CURSOR_SALT, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected = set(scope) | {"created_at", "id"}
        if set(loaded) != expected or any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        created_raw = loaded["created_at"]
        if not isinstance(created_raw, str):
            raise TypeError
        created_at = datetime.fromisoformat(created_raw)
        if created_at.tzinfo is None:
            raise ValueError
        row_id = uuid.UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RequestRejected({"after": ["Invalid cursor."]}) from exc
    return created_at, row_id


def _audit(
    *,
    gym: Gym,
    actor_user: Any,
    actor_membership_id: uuid.UUID | None,
    action: str,
    row: Announcement,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor_membership_id,
        action=action,
        module="announcements",
        target_type="Announcement",
        target_id=row.id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        new_values={
            "type": row.type,
            "audience": row.audience,
            "status": row.status,
            "title": row.title,
        },
    )
