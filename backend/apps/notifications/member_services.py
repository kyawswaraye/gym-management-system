"""Member inbox reads. Only in-app rows for the token member are visible."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.notifications.models import Notification
from apps.notifications.services import render_expiry_message
from apps.tenants.models import Gym

_CURSOR_SALT = "member-notification-inbox"
_CURSOR_MAX_AGE = 24 * 60 * 60
_ORDERING = "created_at DESC, id DESC"


def notification_item(row: Notification) -> dict[str, Any]:
    title, body, announcement_type = _content(row)
    return {
        "id": row.id,
        "type": row.type,
        "title": title,
        "body": body,
        "announcement_type": announcement_type,
        "read_at": row.read_at,
        "created_at": row.created_at,
    }


def page_member_notifications(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    limit: int,
    after: str,
) -> tuple[list[Notification], dict[str, Any]]:
    set_gym_context(gym_id=gym.id)
    scope = {
        "gym_id": str(gym.id),
        "member_id": str(member_id),
        "ordering": _ORDERING,
    }
    queryset = Notification.objects.filter(
        gym=gym,
        recipient_member_id=member_id,
        channel=Notification.Channel.IN_APP,
    ).order_by("-created_at", "-id")
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
def mark_notification_read(
    *,
    gym: Gym,
    member_id: uuid.UUID,
    notification_id: uuid.UUID,
    actor_user: Any,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Notification:
    set_gym_context(gym_id=gym.id)
    row = (
        Notification.objects.select_for_update()
        .filter(
            gym=gym,
            id=notification_id,
            recipient_member_id=member_id,
            channel=Notification.Channel.IN_APP,
            status=Notification.Status.SENT,
        )
        .first()
    )
    if row is None:
        raise Http404
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at", "updated_at"])
        write_tenant_audit(
            gym=gym,
            actor_user=actor_user,
            actor_membership_id=None,
            action="MEMBER_NOTIFICATION_READ",
            module="notifications",
            target_type="Notification",
            target_id=row.id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            new_values={"type": row.type, "read_at": row.read_at.isoformat()},
        )
    return row


def _content(row: Notification) -> tuple[str, str, str | None]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    if row.type == Notification.Type.ANNOUNCEMENT:
        title = payload.get("title")
        body = payload.get("body")
        announcement_type = payload.get("announcement_type")
        if not isinstance(title, str) or not isinstance(body, str):
            raise Http404
        if announcement_type not in {"EVENT", "PROMOTION", "GENERAL"}:
            announcement_type = None
        return title, body, announcement_type if isinstance(announcement_type, str) else None
    expiry_raw = payload.get("expiry_date")
    member_name = payload.get("member_name")
    plan_name = payload.get("plan_name")
    gym_name = payload.get("gym_name")
    if not all(isinstance(value, str) for value in (expiry_raw, member_name, plan_name, gym_name)):
        raise Http404
    try:
        expiry_date = date.fromisoformat(str(expiry_raw))
    except ValueError as exc:
        raise Http404 from exc
    gym_email = payload.get("gym_email")
    gym_phone = payload.get("gym_phone")
    title, body = render_expiry_message(
        member_name=str(member_name),
        plan_name=str(plan_name),
        expiry_date=expiry_date,
        gym_name=str(gym_name),
        gym_email=gym_email if isinstance(gym_email, str) else "",
        gym_phone=gym_phone if isinstance(gym_phone, str) else "",
    )
    return title, body, None


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
