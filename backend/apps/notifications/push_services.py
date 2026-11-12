"""Schedule and deliver in-app and push notifications. Email stays untouched."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from django.db import connection, transaction
from django.db.models import CharField, Exists, OuterRef, Q, Value
from django.db.models.functions import Cast, Concat
from django.utils import timezone

from apps.announcements.models import Announcement
from apps.common.db.tenant_context import TenantContextError, set_gym_context
from apps.members.member_device_services import revoke_invalid_token
from apps.members.models import Member, MemberDevice, Membership
from apps.members.services import gym_today
from apps.notifications.models import MAX_NOTIFICATION_ATTEMPTS, Notification, PushAttempt
from apps.notifications.services import (
    DELIVERY_BATCH_MAX,
    EXPIRY_TEMPLATE_KEY,
    EXPIRY_TEMPLATE_VERSION,
    SCHEDULE_BATCH_MAX,
    DeliveryOutcome,
    _expiry_email_feature_enabled,
    _require_bounds,
    _warning_days,
    build_expiry_payload,
    expiry_reminder_still_due,
    in_app_expiry_deduplication_key,
    push_expiry_deduplication_key,
    render_expiry_message,
    retry_delay_seconds,
)
from apps.notifications.tokens import decrypt_push_token
from apps.notifications.transport import (
    DELIVERY_REJECTED,
    INVALID_TOKEN,
    PROVIDER_UNAVAILABLE,
    SENT,
    PushRequest,
    get_transport,
)
from apps.tenants.models import Gym, GymSettings

_PUSH_ERRORS = {PROVIDER_UNAVAILABLE, DELIVERY_REJECTED, INVALID_TOKEN}
_TRANSIENT_PUSH_ERRORS = {PROVIDER_UNAVAILABLE, DELIVERY_REJECTED}


def classify_push_fanout(*, eligible: int, accepted: int, retryable: int) -> DeliveryOutcome:
    """Choose the parent outcome after one device fan-out.

    The parent status set cannot record partial acceptance. ``SENT`` means at
    least one device was accepted and no eligible device still needs a retry.
    ``FAILED`` means none were accepted and nothing retryable remains.
    ``CANCELLED`` means no eligible device remains. ``RETRIED`` leaves the
    parent ``PENDING``. Acceptance is the FCM HTTP result, not handset display.
    """

    if eligible < 1:
        return DeliveryOutcome.CANCELLED
    if retryable > 0:
        return DeliveryOutcome.RETRIED
    if accepted > 0:
        return DeliveryOutcome.SENT
    return DeliveryOutcome.FAILED


def device_send_is_open(attempt: PushAttempt | None, *, max_attempts: int) -> bool:
    """True when this device has not accepted the notification and may be tried."""

    if attempt is None:
        return True
    if attempt.status == PushAttempt.Status.SENT:
        return False
    if attempt.last_error_code == INVALID_TOKEN:
        return False
    return attempt.attempt_count < max_attempts


@transaction.atomic
def schedule_membership_expiry_pushes(
    *,
    gym: Gym,
    today: date,
    batch_size: int = 500,
) -> int:
    """Create in-app expiry rows, and push rows when the gym setting is on.

    A blank email does not skip the member. The email command is not called.
    """

    _require_bounds(batch_size=batch_size, batch_max=SCHEDULE_BATCH_MAX)
    set_gym_context(gym_id=gym.id)
    gym = Gym.objects.get(pk=gym.pk)
    if gym.status != Gym.Status.ACTIVE:
        return 0
    if not _expiry_email_feature_enabled(gym=gym):
        return 0
    settings_row = GymSettings.objects.get(gym=gym)
    warning_days = _warning_days(settings_row)
    if not warning_days:
        return 0
    push_enabled = bool(settings_row.membership_expiry_push_enabled)
    created = 0
    scheduled_at = timezone.now()
    for warning_day in warning_days:
        remaining = batch_size - created
        if remaining < 1:
            break
        expiry_date = today + timedelta(days=warning_day)
        in_app_key = Concat(
            Value("in_app:membership_expiry:"),
            Cast(OuterRef("id"), CharField()),
            Value(":"),
            Cast(OuterRef("expiry_date"), CharField()),
            Value(f":{warning_day}:v{EXPIRY_TEMPLATE_VERSION}"),
            output_field=CharField(),
        )
        in_app_exists = Notification.objects.filter(
            gym_id=OuterRef("gym_id"),
            channel=Notification.Channel.IN_APP,
            deduplication_key=in_app_key,
        )
        queryset = Membership.objects.select_related("member").filter(
            gym=gym,
            status=Membership.Status.ACTIVE,
            expiry_date=expiry_date,
            member__status=Member.Status.ACTIVE,
        )
        queryset = queryset.annotate(has_in_app=Exists(in_app_exists))
        if push_enabled:
            push_key = Concat(
                Value("push:membership_expiry:"),
                Cast(OuterRef("id"), CharField()),
                Value(":"),
                Cast(OuterRef("expiry_date"), CharField()),
                Value(f":{warning_day}:v{EXPIRY_TEMPLATE_VERSION}"),
                output_field=CharField(),
            )
            push_exists = Notification.objects.filter(
                gym_id=OuterRef("gym_id"),
                channel=Notification.Channel.PUSH,
                deduplication_key=push_key,
            )
            queryset = queryset.annotate(has_push=Exists(push_exists)).filter(
                Q(has_in_app=False) | Q(has_push=False)
            )
        else:
            queryset = queryset.filter(has_in_app=False)
        for membership in queryset.order_by("id")[:remaining]:
            locked = (
                Membership.objects.select_for_update(of=("self",))
                .select_related("member")
                .get(pk=membership.pk, gym=gym)
            )
            if not _expiry_push_eligible(locked, gym=gym, today=today, warning_day=warning_day):
                continue
            payload = build_expiry_payload(
                member_name=locked.member.display_name,
                plan_name=locked.plan_name,
                expiry_date=locked.expiry_date,
                warning_day=warning_day,
                gym_name=gym.name,
                gym_email=gym.email,
                gym_phone=gym.phone,
            )
            _, in_app_created = Notification.objects.get_or_create(
                gym=gym,
                channel=Notification.Channel.IN_APP,
                deduplication_key=in_app_expiry_deduplication_key(
                    membership=locked,
                    warning_day=warning_day,
                ),
                defaults=_expiry_defaults(
                    membership=locked,
                    payload=payload,
                    scheduled_at=scheduled_at,
                    sent=True,
                ),
            )
            created += int(in_app_created)
            if push_enabled:
                _, push_created = Notification.objects.get_or_create(
                    gym=gym,
                    channel=Notification.Channel.PUSH,
                    deduplication_key=push_expiry_deduplication_key(
                        membership=locked,
                        warning_day=warning_day,
                    ),
                    defaults=_expiry_defaults(
                        membership=locked,
                        payload=payload,
                        scheduled_at=scheduled_at,
                        sent=False,
                    ),
                )
                created += int(push_created)
            if created >= batch_size:
                break
    return created


def deliver_next_push_notification(
    *,
    gym: Gym,
    max_attempts: int = MAX_NOTIFICATION_ATTEMPTS,
    now: datetime | None = None,
    today: date | None = None,
) -> DeliveryOutcome | None:
    """Lock one due push parent, fan out to consented devices, and commit later."""

    if not connection.in_atomic_block:
        raise TenantContextError("Push delivery requires an active transaction")
    _require_bounds(batch_size=1, batch_max=DELIVERY_BATCH_MAX, max_attempts=max_attempts)
    set_gym_context(gym_id=gym.id)
    gym = Gym.objects.get(pk=gym.pk)
    moment = now if now is not None else timezone.now()
    if timezone.is_naive(moment):
        raise ValueError("delivery time must be timezone-aware")
    notification = (
        Notification.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("recipient_member", "membership", "announcement")
        .filter(
            gym=gym,
            status=Notification.Status.PENDING,
            channel=Notification.Channel.PUSH,
            next_attempt_at__lte=moment,
            attempt_count__lt=MAX_NOTIFICATION_ATTEMPTS,
        )
        .order_by("next_attempt_at", "id")
        .first()
    )
    if notification is None:
        return None
    if notification.attempt_count >= max_attempts:
        return _fail_parent(notification, now=moment, code=DELIVERY_REJECTED)
    gym_day = today if today is not None else gym_today(gym)
    if _should_cancel(notification, gym=gym, today=gym_day, now=moment):
        _cancel_parent(notification)
        return DeliveryOutcome.CANCELLED
    devices = list(
        MemberDevice.objects.select_for_update()
        .filter(
            gym=gym,
            member_id=notification.recipient_member_id,
            revoked_at__isnull=True,
            push_consent=True,
            token_ciphertext__isnull=False,
        )
        .order_by("id")
    )
    if not devices:
        _cancel_parent(notification)
        return DeliveryOutcome.CANCELLED
    title, body = _message(notification, gym=gym)
    if title is None or body is None:
        _cancel_parent(notification)
        return DeliveryOutcome.CANCELLED
    transport = get_transport()
    last_code = DELIVERY_REJECTED
    for device in devices:
        existing = (
            PushAttempt.objects.select_for_update()
            .filter(gym=gym, notification=notification, member_device=device)
            .first()
        )
        if not device_send_is_open(existing, max_attempts=max_attempts):
            continue
        ciphertext = device.token_ciphertext
        if not ciphertext:
            continue
        try:
            token = decrypt_push_token(ciphertext)
        except RuntimeError:
            revoke_invalid_token(device=device)
            _write_attempt(
                existing=existing,
                gym=gym,
                notification=notification,
                device=device,
                status=PushAttempt.Status.FAILED,
                code=INVALID_TOKEN,
                provider_message_id="",
            )
            continue
        try:
            result = transport.send(
                PushRequest(
                    notification_id=str(notification.id),
                    title=title,
                    body=body,
                    token=token,
                )
            )
            code = (
                result.code
                if result.code in _PUSH_ERRORS or result.code == SENT
                else DELIVERY_REJECTED
            )
            provider_message_id = result.provider_message_id if code == SENT else ""
        except Exception:
            code = PROVIDER_UNAVAILABLE
            provider_message_id = ""
        if code == SENT:
            _write_attempt(
                existing=existing,
                gym=gym,
                notification=notification,
                device=device,
                status=PushAttempt.Status.SENT,
                code="",
                provider_message_id=provider_message_id,
            )
            continue
        if code == INVALID_TOKEN:
            revoke_invalid_token(device=device)
        elif code in _TRANSIENT_PUSH_ERRORS:
            last_code = code
        else:
            last_code = DELIVERY_REJECTED
        _write_attempt(
            existing=existing,
            gym=gym,
            notification=notification,
            device=device,
            status=PushAttempt.Status.FAILED,
            code=code if code in _PUSH_ERRORS else DELIVERY_REJECTED,
            provider_message_id="",
        )
    outcome = _fanout_outcome(gym=gym, notification=notification, max_attempts=max_attempts)
    if outcome is DeliveryOutcome.CANCELLED:
        _cancel_parent(notification)
        return outcome
    if outcome is DeliveryOutcome.SENT:
        _mark_parent_sent(notification, now=moment)
        return outcome
    if outcome is DeliveryOutcome.FAILED:
        if notification.attempt_count < MAX_NOTIFICATION_ATTEMPTS:
            notification.attempt_count += 1
        notification.last_error_code = last_code if last_code in _PUSH_ERRORS else DELIVERY_REJECTED
        return _fail_parent(notification, now=moment, code=notification.last_error_code)
    notification.attempt_count += 1
    notification.provider_id = ""
    notification.sent_at = None
    notification.last_error_code = (
        last_code if last_code in _TRANSIENT_PUSH_ERRORS else DELIVERY_REJECTED
    )
    if notification.attempt_count >= max_attempts:
        notification.status = Notification.Status.FAILED
        notification.failed_at = moment
        notification.next_attempt_at = None
        outcome = DeliveryOutcome.FAILED
    else:
        notification.status = Notification.Status.PENDING
        notification.failed_at = None
        notification.next_attempt_at = moment + timedelta(
            seconds=retry_delay_seconds(notification.attempt_count)
        )
        outcome = DeliveryOutcome.RETRIED
    notification.save(
        update_fields=[
            "attempt_count",
            "provider_id",
            "sent_at",
            "last_error_code",
            "status",
            "failed_at",
            "next_attempt_at",
            "updated_at",
        ]
    )
    return outcome


def _expiry_push_eligible(
    membership: Membership,
    *,
    gym: Gym,
    today: date,
    warning_day: int,
) -> bool:
    member = membership.member
    return (
        gym.status == Gym.Status.ACTIVE
        and membership.gym_id == gym.id
        and membership.status == Membership.Status.ACTIVE
        and membership.expiry_date == today + timedelta(days=warning_day)
        and member.gym_id == gym.id
        and member.id == membership.member_id
        and member.status == Member.Status.ACTIVE
    )


def _expiry_defaults(
    *,
    membership: Membership,
    payload: dict[str, Any],
    scheduled_at: datetime,
    sent: bool,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "recipient_member": membership.member,
        "membership": membership,
        "announcement": None,
        "type": Notification.Type.MEMBERSHIP_EXPIRY,
        "template_key": EXPIRY_TEMPLATE_KEY,
        "template_version": EXPIRY_TEMPLATE_VERSION,
        "payload": payload,
        "scheduled_at": scheduled_at,
        "provider_id": "",
        "last_error_code": "",
        "read_at": None,
    }
    if sent:
        defaults.update(
            status=Notification.Status.SENT,
            sent_at=scheduled_at,
            failed_at=None,
            next_attempt_at=None,
            attempt_count=1,
        )
    else:
        defaults.update(
            status=Notification.Status.PENDING,
            sent_at=None,
            failed_at=None,
            next_attempt_at=scheduled_at,
            attempt_count=0,
        )
    return defaults


def _should_cancel(notification: Notification, *, gym: Gym, today: date, now: datetime) -> bool:
    if gym.status != Gym.Status.ACTIVE or notification.gym_id != gym.id:
        return True
    if notification.type == Notification.Type.ANNOUNCEMENT:
        announcement = notification.announcement
        if announcement is None or announcement.gym_id != gym.id:
            return True
        if announcement.status != Announcement.Status.PUBLISHED:
            return True
        return announcement.ends_at is not None and announcement.ends_at <= now
    if notification.type != Notification.Type.MEMBERSHIP_EXPIRY:
        return True
    if not _expiry_email_feature_enabled(gym=gym):
        return True
    settings_row = GymSettings.objects.get(gym=gym)
    if not settings_row.membership_expiry_push_enabled:
        return True
    membership = notification.membership
    member = notification.recipient_member
    if membership is None or member.status != Member.Status.ACTIVE:
        return True
    if (
        membership.gym_id != gym.id
        or membership.status != Membership.Status.ACTIVE
        or membership.member_id != member.id
    ):
        return True
    return _expiry_push_outdated(notification, membership=membership, today=today)


def _expiry_push_outdated(
    notification: Notification,
    *,
    membership: Membership,
    today: date,
) -> bool:
    """Cancel when the expiry date, warning day, or gym-local window no longer matches."""

    payload = notification.payload if isinstance(notification.payload, dict) else {}
    expiry_raw = payload.get("expiry_date")
    warning_day = payload.get("warning_day")
    if not isinstance(expiry_raw, str) or expiry_raw != membership.expiry_date.isoformat():
        return True
    if isinstance(warning_day, bool) or not isinstance(warning_day, int):
        return True
    try:
        settings_row = GymSettings.objects.get(gym_id=membership.gym_id)
        configured = _warning_days(settings_row)
    except (GymSettings.DoesNotExist, ValueError):
        return True
    if warning_day not in configured:
        return True
    return not expiry_reminder_still_due(
        expiry_date=membership.expiry_date,
        today=today,
        warning_day=warning_day,
    )


def _fanout_outcome(
    *,
    gym: Gym,
    notification: Notification,
    max_attempts: int,
) -> DeliveryOutcome:
    devices = list(
        MemberDevice.objects.filter(
            gym=gym,
            member_id=notification.recipient_member_id,
            revoked_at__isnull=True,
            push_consent=True,
            token_ciphertext__isnull=False,
        ).order_by("id")
    )
    attempts = {
        row.member_device_id: row
        for row in PushAttempt.objects.filter(
            gym=gym,
            notification=notification,
            member_device__in=devices,
        )
    }
    accepted = 0
    retryable = 0
    for device in devices:
        attempt = attempts.get(device.id)
        if attempt is not None and attempt.status == PushAttempt.Status.SENT:
            accepted += 1
        elif device_send_is_open(attempt, max_attempts=max_attempts):
            retryable += 1
    return classify_push_fanout(
        eligible=len(devices),
        accepted=accepted,
        retryable=retryable,
    )


def _message(notification: Notification, *, gym: Gym) -> tuple[str | None, str | None]:
    payload = notification.payload
    if not isinstance(payload, dict):
        return None, None
    if notification.type == Notification.Type.ANNOUNCEMENT:
        title = payload.get("title")
        body = payload.get("body")
        if not isinstance(title, str) or not isinstance(body, str) or not title or not body:
            return None, None
        return title, body
    if notification.type != Notification.Type.MEMBERSHIP_EXPIRY or notification.membership is None:
        return None, None
    expiry_raw = payload.get("expiry_date")
    member_name = payload.get("member_name")
    plan_name = payload.get("plan_name")
    gym_name = payload.get("gym_name")
    if not all(isinstance(value, str) for value in (expiry_raw, member_name, plan_name, gym_name)):
        return None, None
    try:
        expiry_date = date.fromisoformat(str(expiry_raw))
    except ValueError:
        return None, None
    gym_email = payload.get("gym_email")
    gym_phone = payload.get("gym_phone")
    subject, body = render_expiry_message(
        member_name=str(member_name),
        plan_name=str(plan_name),
        expiry_date=expiry_date,
        gym_name=str(gym_name),
        gym_email=gym_email if isinstance(gym_email, str) else gym.email,
        gym_phone=gym_phone if isinstance(gym_phone, str) else gym.phone,
        language="both",
    )
    return subject, body


def _write_attempt(
    *,
    existing: PushAttempt | None,
    gym: Gym,
    notification: Notification,
    device: MemberDevice,
    status: str,
    code: str,
    provider_message_id: str,
) -> None:
    if existing is None:
        PushAttempt.objects.create(
            gym=gym,
            notification=notification,
            member_device=device,
            status=status,
            attempt_count=1,
            last_error_code=code,
            provider_message_id=provider_message_id,
        )
        return
    existing.status = status
    existing.attempt_count = min(existing.attempt_count + 1, MAX_NOTIFICATION_ATTEMPTS)
    existing.last_error_code = code
    existing.provider_message_id = provider_message_id
    existing.save(
        update_fields=[
            "status",
            "attempt_count",
            "last_error_code",
            "provider_message_id",
            "updated_at",
        ]
    )


def _cancel_parent(notification: Notification) -> None:
    notification.status = Notification.Status.CANCELLED
    notification.sent_at = None
    notification.failed_at = None
    notification.next_attempt_at = None
    notification.last_error_code = ""
    notification.provider_id = ""
    notification.save(
        update_fields=[
            "status",
            "sent_at",
            "failed_at",
            "next_attempt_at",
            "last_error_code",
            "provider_id",
            "updated_at",
        ]
    )


def _mark_parent_sent(notification: Notification, *, now: datetime) -> None:
    notification.attempt_count += 1
    notification.status = Notification.Status.SENT
    notification.sent_at = now
    notification.failed_at = None
    notification.next_attempt_at = None
    notification.last_error_code = ""
    notification.provider_id = ""
    notification.save(
        update_fields=[
            "attempt_count",
            "status",
            "sent_at",
            "failed_at",
            "next_attempt_at",
            "last_error_code",
            "provider_id",
            "updated_at",
        ]
    )


def _fail_parent(notification: Notification, *, now: datetime, code: str) -> DeliveryOutcome:
    stored = notification.last_error_code
    notification.status = Notification.Status.FAILED
    notification.failed_at = now
    notification.sent_at = None
    notification.next_attempt_at = None
    notification.provider_id = ""
    notification.last_error_code = stored if stored in _PUSH_ERRORS else code
    notification.save(
        update_fields=[
            "attempt_count",
            "status",
            "failed_at",
            "sent_at",
            "next_attempt_at",
            "provider_id",
            "last_error_code",
            "updated_at",
        ]
    )
    return DeliveryOutcome.FAILED
