from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from enum import StrEnum

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import DatabaseError, connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.common.db.tenant_context import TenantContextError
from apps.members.models import Member, Membership
from apps.members.normalization import normalize_member_email
from apps.notifications.models import MAX_NOTIFICATION_ATTEMPTS, Notification
from apps.tenants.models import Gym, GymFeatureEntitlement, GymSettings

EXPIRY_TEMPLATE_KEY = "membership_expiry"
EXPIRY_TEMPLATE_VERSION = 1
SCHEDULE_BATCH_MAX = 5000
DELIVERY_BATCH_DEFAULT = 100
DELIVERY_BATCH_MAX = 500
IDEMPOTENCY_HEADER = "X-Idempotency-Key"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
DELIVERY_REJECTED = "DELIVERY_REJECTED"
RETRY_BASE_SECONDS = 300
RETRY_CAP_SECONDS = 3600

_REQUIRED_PAYLOAD_KEYS = frozenset(
    {"member_name", "plan_name", "expiry_date", "warning_day", "gym_name"}
)
_OPTIONAL_PAYLOAD_KEYS = frozenset({"gym_email", "gym_phone"})
_ALLOWED_PAYLOAD_KEYS = _REQUIRED_PAYLOAD_KEYS | _OPTIONAL_PAYLOAD_KEYS
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{1,32}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_STABLE_ERROR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


class DeliveryOutcome(StrEnum):
    SENT = "SENT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    RETRIED = "RETRIED"


def retry_delay_seconds(attempt_count: int) -> int:
    """Backoff after ``attempt_count`` failed provider attempts, starting at 1."""

    if attempt_count < 1:
        raise ValueError("attempt_count must be positive")
    shift = attempt_count - 1
    if shift > 10:
        shift = 10
    delay = RETRY_BASE_SECONDS << shift
    if delay > RETRY_CAP_SECONDS:
        return RETRY_CAP_SECONDS
    return delay


def delivery_error_code(exc: BaseException) -> str:
    """Map a provider failure to a stable code. The exception text is discarded."""

    if isinstance(exc, OSError):
        return PROVIDER_UNAVAILABLE
    return DELIVERY_REJECTED


def header_safe(value: str, *, max_length: int) -> str:
    """Collapse a value onto one printable line so it cannot inject headers."""

    without_controls = _CONTROL_RE.sub(" ", value)
    collapsed = " ".join(without_controls.split())
    return collapsed[:max_length].strip()


def membership_expiry_deduplication_key(
    *,
    membership: Membership,
    warning_day: int,
) -> str:
    return (
        f"membership-expiry:{membership.id}:{membership.expiry_date.isoformat()}:"
        f"{warning_day}:v{EXPIRY_TEMPLATE_VERSION}"
    )


def validate_expiry_payload(payload: object) -> None:
    """Reject template payloads that are unbounded or contain a destination."""

    if not isinstance(payload, dict):
        raise ValueError("expiry payload must be an object")
    keys = set(payload)
    if not _REQUIRED_PAYLOAD_KEYS <= keys or not keys <= _ALLOWED_PAYLOAD_KEYS:
        raise ValueError("expiry payload contains unexpected fields")
    member_name = payload["member_name"]
    plan_name = payload["plan_name"]
    expiry_date = payload["expiry_date"]
    warning_day = payload["warning_day"]
    gym_name = payload["gym_name"]
    if not _bounded_text(member_name, max_length=201):
        raise ValueError("expiry payload member name is invalid")
    if not _bounded_text(plan_name, max_length=100):
        raise ValueError("expiry payload plan name is invalid")
    if not _bounded_text(gym_name, max_length=150):
        raise ValueError("expiry payload gym name is invalid")
    if not isinstance(expiry_date, str) or _DATE_RE.fullmatch(expiry_date) is None:
        raise ValueError("expiry payload expiry date is invalid")
    try:
        date.fromisoformat(expiry_date)
    except ValueError as exc:
        raise ValueError("expiry payload expiry date is invalid") from exc
    if isinstance(warning_day, bool) or not isinstance(warning_day, int):
        raise ValueError("expiry payload warning day is invalid")
    if not 0 <= warning_day <= 365:
        raise ValueError("expiry payload warning day is invalid")
    if "gym_email" in payload and _optional_email(payload["gym_email"]) is None:
        raise ValueError("expiry payload gym email is invalid")
    if "gym_phone" in payload and _optional_phone(payload["gym_phone"]) is None:
        raise ValueError("expiry payload gym phone is invalid")
    encoded = str(payload)
    if len(encoded) > 2000:
        raise ValueError("expiry payload is too large")


def build_expiry_payload(
    *,
    member_name: str,
    plan_name: str,
    expiry_date: date,
    warning_day: int,
    gym_name: str,
    gym_email: str,
    gym_phone: str,
) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        "member_name": header_safe(member_name, max_length=201),
        "plan_name": header_safe(plan_name, max_length=100),
        "expiry_date": expiry_date.isoformat(),
        "warning_day": warning_day,
        "gym_name": header_safe(gym_name, max_length=150),
    }
    email = _optional_email(gym_email)
    if email is not None:
        payload["gym_email"] = email
    phone = _optional_phone(gym_phone)
    if phone is not None:
        payload["gym_phone"] = phone
    validate_expiry_payload(payload)
    return payload


def render_expiry_message(
    *,
    member_name: str,
    plan_name: str,
    expiry_date: date,
    gym_name: str,
    gym_email: str,
    gym_phone: str,
) -> tuple[str, str]:
    """Plain-text subject and body. None of the inputs are a destination address."""

    safe_member = header_safe(member_name, max_length=201) or "Member"
    safe_plan = header_safe(plan_name, max_length=100) or "membership"
    safe_gym = header_safe(gym_name, max_length=150) or "Your gym"
    safe_date = expiry_date.isoformat()
    subject = header_safe(
        f"Your {safe_gym} membership expires on {safe_date}",
        max_length=200,
    )
    lines = [
        f"Hello {safe_member},",
        "",
        f"Your {safe_plan} membership at {safe_gym} expires on {safe_date}.",
        "",
        safe_gym,
    ]
    contact_email = _optional_email(gym_email)
    contact_phone = _optional_phone(gym_phone)
    if contact_email is not None:
        lines.append(contact_email)
    if contact_phone is not None:
        lines.append(contact_phone)
    return subject, "\n".join(lines) + "\n"


def _warning_days(settings_row: GymSettings) -> list[int]:
    values = settings_row.membership_expiry_warning_days
    if not isinstance(values, list):
        raise ValueError("membership expiry warning days must be a list")
    days: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 365:
            raise ValueError("membership expiry warning days must contain integers from 0 to 365")
        days.add(value)
    return sorted(days)


def _expiry_email_feature_enabled(*, gym: Gym) -> bool:
    now = timezone.now()
    return GymFeatureEntitlement.objects.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(effective_until__isnull=True) | Q(effective_until__gt=now),
        gym=gym,
        feature__code="memberships",
        enabled=True,
    ).exists()


def _bounded_text(value: object, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and not _CONTROL_RE.search(value)
        and value == " ".join(value.split())
        and 1 <= len(value) <= max_length
    )


def _optional_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = header_safe(value, max_length=254)
    if not cleaned or _EMAIL_RE.fullmatch(cleaned) is None:
        return None
    return cleaned


def _optional_phone(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = header_safe(value, max_length=32)
    if _PHONE_RE.fullmatch(cleaned) is None:
        return None
    return cleaned


def current_member_email(member: Member) -> str | None:
    """Return the stored normalized address, or None when it cannot be a destination."""

    normalized = normalize_member_email(member.email)
    if normalized is None or normalized != member.email:
        return None
    if len(normalized) > 254 or _EMAIL_RE.fullmatch(normalized) is None:
        return None
    if normalized != normalized.lower() or _CONTROL_RE.search(normalized):
        return None
    return normalized


def _require_bounds(*, batch_size: int, batch_max: int, max_attempts: int | None = None) -> None:
    if batch_size < 1 or batch_size > batch_max:
        raise ValueError(f"batch_size must be between 1 and {batch_max}")
    if max_attempts is not None and not 1 <= max_attempts <= MAX_NOTIFICATION_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_NOTIFICATION_ATTEMPTS}")


def _schedule_eligible(
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
        and current_member_email(member) is not None
    )


@transaction.atomic
def schedule_membership_expiry_notifications(
    *,
    gym: Gym,
    today: date,
    batch_size: int = 500,
) -> int:
    """Create deduplicated expiry emails for one active gym's local date.

    A missing or blank member email is a normal skip: no row is inserted and
    nothing is sent by SMS or phone. The payload never contains a destination
    address. Repeated calls insert no second row for the same deduplication key.
    """

    _require_bounds(batch_size=batch_size, batch_max=SCHEDULE_BATCH_MAX)
    gym = Gym.objects.get(pk=gym.pk)
    if gym.status != Gym.Status.ACTIVE:
        return 0
    settings_row = GymSettings.objects.get(gym=gym)
    if not settings_row.membership_expiry_email_enabled:
        return 0
    if not _expiry_email_feature_enabled(gym=gym):
        return 0

    warning_days = _warning_days(settings_row)
    if not warning_days:
        return 0
    created = 0
    processed = 0
    scheduled_at = timezone.now()
    for warning_day in warning_days:
        remaining = batch_size - processed
        if remaining <= 0:
            break
        expiry_date = today + timedelta(days=warning_day)
        existing = Notification.objects.filter(
            gym=gym,
            membership_id=OuterRef("pk"),
            type=Notification.Type.MEMBERSHIP_EXPIRY,
            channel=Notification.Channel.EMAIL,
            template_key=EXPIRY_TEMPLATE_KEY,
            template_version=EXPIRY_TEMPLATE_VERSION,
            payload__expiry_date=expiry_date.isoformat(),
            payload__warning_day=warning_day,
        )
        memberships = (
            Membership.objects.select_related("member")
            .filter(
                gym=gym,
                status=Membership.Status.ACTIVE,
                expiry_date=expiry_date,
                member__status=Member.Status.ACTIVE,
            )
            .exclude(member__email__isnull=True)
            .exclude(member__email="")
            .annotate(expiry_notice_exists=Exists(existing))
            .filter(expiry_notice_exists=False)
            .order_by("id")[:remaining]
        )
        for membership in memberships:
            processed += 1
            locked = (
                Membership.objects.select_for_update(of=("self",))
                .select_related("member")
                .get(pk=membership.pk, gym=gym)
            )
            if not _schedule_eligible(locked, gym=gym, today=today, warning_day=warning_day):
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
            _, was_created = Notification.objects.get_or_create(
                gym=gym,
                channel=Notification.Channel.EMAIL,
                deduplication_key=membership_expiry_deduplication_key(
                    membership=locked,
                    warning_day=warning_day,
                ),
                defaults={
                    "recipient_member": locked.member,
                    "membership": locked,
                    "type": Notification.Type.MEMBERSHIP_EXPIRY,
                    "template_key": EXPIRY_TEMPLATE_KEY,
                    "template_version": EXPIRY_TEMPLATE_VERSION,
                    "payload": payload,
                    "scheduled_at": scheduled_at,
                    "next_attempt_at": scheduled_at,
                    "last_error_code": "",
                },
            )
            created += int(was_created)
    return created


def deliver_next_membership_expiry_email(
    *,
    gym: Gym,
    today: date,
    max_attempts: int = MAX_NOTIFICATION_ATTEMPTS,
    now: datetime | None = None,
) -> DeliveryOutcome | None:
    """Lock and finalize at most one due expiry email.

    Callers own the transaction and the tenant context. The row lock uses
    ``skip_locked`` so a second transaction takes a different due row, or none.
    The provider send happens before this transaction commits. A crash after
    the provider accepts the message and before commit can leave the row
    pending; closing that window requires a provider that honors
    ``X-Idempotency-Key``. This function does not choose a vendor.

    Due pending rows are selected below the stored attempt maximum. A row
    whose count already meets this call's ``max_attempts`` is marked failed
    without a provider call and without another attempt increment.
    """

    if not connection.in_atomic_block:
        raise TenantContextError("Expiry delivery requires an active transaction")
    _require_bounds(batch_size=1, batch_max=DELIVERY_BATCH_MAX, max_attempts=max_attempts)
    gym = Gym.objects.get(pk=gym.pk)
    moment = now if now is not None else timezone.now()
    if timezone.is_naive(moment):
        raise ValueError("delivery time must be timezone-aware")

    notification = (
        Notification.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("recipient_member", "membership")
        .filter(
            gym=gym,
            status=Notification.Status.PENDING,
            type=Notification.Type.MEMBERSHIP_EXPIRY,
            channel=Notification.Channel.EMAIL,
            template_key=EXPIRY_TEMPLATE_KEY,
            template_version=EXPIRY_TEMPLATE_VERSION,
            next_attempt_at__lte=moment,
            attempt_count__lt=MAX_NOTIFICATION_ATTEMPTS,
        )
        .order_by("next_attempt_at", "id")
        .first()
    )
    if notification is None:
        return None
    if notification.attempt_count >= max_attempts:
        return _finalize_attempt_limit(notification, now=moment)
    if _cancellation_reason(notification, gym=gym, today=today) is not None:
        _mark_cancelled(notification)
        return DeliveryOutcome.CANCELLED

    member = notification.recipient_member
    destination = current_member_email(member)
    if destination is None:
        _mark_cancelled(notification)
        return DeliveryOutcome.CANCELLED
    subject, body = render_expiry_message(
        member_name=member.display_name,
        plan_name=notification.membership.plan_name,
        expiry_date=notification.membership.expiry_date,
        gym_name=gym.name,
        gym_email=gym.email,
        gym_phone=gym.phone,
    )
    reply_to = _optional_email(gym.email)
    message = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[destination],
        reply_to=[reply_to] if reply_to is not None else None,
        headers={IDEMPOTENCY_HEADER: notification.deduplication_key},
        connection=get_connection(fail_silently=False),
    )
    try:
        sent_count = message.send(fail_silently=False)
    except DatabaseError:
        raise
    except OSError:
        return _record_provider_failure(
            notification,
            now=moment,
            max_attempts=max_attempts,
            code=PROVIDER_UNAVAILABLE,
        )
    except Exception:
        return _record_provider_failure(
            notification,
            now=moment,
            max_attempts=max_attempts,
            code=DELIVERY_REJECTED,
        )
    if sent_count != 1:
        return _record_provider_failure(
            notification,
            now=moment,
            max_attempts=max_attempts,
            code=DELIVERY_REJECTED,
        )
    return _record_sent(notification, now=moment)


def _cancellation_reason(
    notification: Notification,
    *,
    gym: Gym,
    today: date,
) -> str | None:
    if gym.status != Gym.Status.ACTIVE or notification.gym_id != gym.id:
        return "gym"
    try:
        payload_error = False
        validate_expiry_payload(notification.payload)
    except ValueError:
        payload_error = True
    if payload_error:
        return "payload"
    if (
        notification.type != Notification.Type.MEMBERSHIP_EXPIRY
        or notification.channel != Notification.Channel.EMAIL
        or notification.template_key != EXPIRY_TEMPLATE_KEY
        or notification.template_version != EXPIRY_TEMPLATE_VERSION
    ):
        return "template"
    settings_row = GymSettings.objects.get(gym=gym)
    if not settings_row.membership_expiry_email_enabled:
        return "setting"
    if not _expiry_email_feature_enabled(gym=gym):
        return "entitlement"
    membership = notification.membership
    member = notification.recipient_member
    if (
        membership.gym_id != gym.id
        or member.gym_id != gym.id
        or membership.member_id != member.id
        or notification.recipient_member_id != member.id
        or notification.membership_id != membership.id
    ):
        return "relationship"
    if membership.status != Membership.Status.ACTIVE or member.status != Member.Status.ACTIVE:
        return "eligibility"
    if current_member_email(member) is None:
        return "email"
    payload = notification.payload
    warning_day = payload.get("warning_day")
    if not isinstance(warning_day, int) or isinstance(warning_day, bool):
        return "payload"
    if warning_day not in _warning_days(settings_row):
        return "warning-day"
    if membership.expiry_date != today + timedelta(days=warning_day):
        return "warning-day"
    if payload.get("expiry_date") != membership.expiry_date.isoformat():
        return "payload"
    if payload.get("plan_name") != header_safe(membership.plan_name, max_length=100):
        return "payload"
    expected_key = membership_expiry_deduplication_key(
        membership=membership,
        warning_day=warning_day,
    )
    if notification.deduplication_key != expected_key:
        return "dedup"
    if _CONTROL_RE.search(notification.deduplication_key):
        return "dedup"
    return None


def _mark_cancelled(notification: Notification) -> None:
    notification.status = Notification.Status.CANCELLED
    notification.sent_at = None
    notification.failed_at = None
    notification.next_attempt_at = None
    notification.last_error_code = ""
    notification.save(
        update_fields=[
            "status",
            "sent_at",
            "failed_at",
            "next_attempt_at",
            "last_error_code",
            "updated_at",
        ]
    )


def _redacted_error_code(value: str) -> str:
    if _STABLE_ERROR_RE.fullmatch(value):
        return value
    return DELIVERY_REJECTED


def _finalize_attempt_limit(notification: Notification, *, now: datetime) -> DeliveryOutcome:
    """Close a due row that already reached the command's attempt ceiling.

    No provider is called and ``attempt_count`` stays unchanged. A stored
    stable error code is kept; anything else is replaced with the redacted
    delivery code.
    """

    notification.status = Notification.Status.FAILED
    notification.failed_at = now
    notification.sent_at = None
    notification.next_attempt_at = None
    notification.last_error_code = _redacted_error_code(notification.last_error_code)
    notification.save(
        update_fields=[
            "status",
            "failed_at",
            "sent_at",
            "next_attempt_at",
            "last_error_code",
            "updated_at",
        ]
    )
    return DeliveryOutcome.FAILED


def _record_sent(notification: Notification, *, now: datetime) -> DeliveryOutcome:
    notification.attempt_count += 1
    notification.status = Notification.Status.SENT
    notification.sent_at = now
    notification.failed_at = None
    notification.next_attempt_at = None
    notification.last_error_code = ""
    notification.save(
        update_fields=[
            "attempt_count",
            "status",
            "sent_at",
            "failed_at",
            "next_attempt_at",
            "last_error_code",
            "updated_at",
        ]
    )
    return DeliveryOutcome.SENT


def _record_provider_failure(
    notification: Notification,
    *,
    now: datetime,
    max_attempts: int,
    code: str,
) -> DeliveryOutcome:
    if code not in {PROVIDER_UNAVAILABLE, DELIVERY_REJECTED}:
        code = DELIVERY_REJECTED
    notification.attempt_count += 1
    notification.last_error_code = code
    notification.provider_id = ""
    notification.sent_at = None
    if notification.attempt_count >= max_attempts:
        notification.status = Notification.Status.FAILED
        notification.failed_at = now
        notification.next_attempt_at = None
        outcome = DeliveryOutcome.FAILED
    else:
        notification.status = Notification.Status.PENDING
        notification.failed_at = None
        notification.next_attempt_at = now + timedelta(
            seconds=retry_delay_seconds(notification.attempt_count)
        )
        outcome = DeliveryOutcome.RETRIED
    notification.save(
        update_fields=[
            "attempt_count",
            "last_error_code",
            "provider_id",
            "sent_at",
            "status",
            "failed_at",
            "next_attempt_at",
            "updated_at",
        ]
    )
    return outcome
