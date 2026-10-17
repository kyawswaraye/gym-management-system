from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.members.models import Member, Membership
from apps.notifications.models import Notification
from apps.tenants.models import Gym, GymFeatureEntitlement, GymSettings

EXPIRY_TEMPLATE_KEY = "membership_expiry"
EXPIRY_TEMPLATE_VERSION = 1


def _warning_days(settings: GymSettings) -> list[int]:
    values = settings.membership_expiry_warning_days
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


def membership_expiry_deduplication_key(
    *,
    membership: Membership,
    warning_day: int,
) -> str:
    return (
        f"membership-expiry:{membership.id}:{membership.expiry_date.isoformat()}:"
        f"{warning_day}:v{EXPIRY_TEMPLATE_VERSION}"
    )


@transaction.atomic
def schedule_membership_expiry_notifications(
    *,
    gym: Gym,
    today: date,
    batch_size: int = 500,
) -> int:
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch_size must be between 1 and 5000")
    settings = GymSettings.objects.get(gym=gym)
    if not settings.membership_expiry_email_enabled:
        return 0
    if not _expiry_email_feature_enabled(gym=gym):
        return 0

    warning_days = _warning_days(settings)
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
            )
            .exclude(member__email__isnull=True)
            .exclude(member__email="")
            .exclude(
                member__status__in=[
                    Member.Status.ARCHIVED,
                    Member.Status.CANCELLED,
                ]
            )
            .annotate(expiry_notice_exists=Exists(existing))
            .filter(expiry_notice_exists=False)
            .order_by("id")[:remaining]
        )
        for membership in memberships:
            processed += 1
            _, was_created = Notification.objects.get_or_create(
                gym=gym,
                channel=Notification.Channel.EMAIL,
                deduplication_key=membership_expiry_deduplication_key(
                    membership=membership,
                    warning_day=warning_day,
                ),
                defaults={
                    "recipient_member": membership.member,
                    "membership": membership,
                    "type": Notification.Type.MEMBERSHIP_EXPIRY,
                    "template_key": EXPIRY_TEMPLATE_KEY,
                    "template_version": EXPIRY_TEMPLATE_VERSION,
                    "payload": {
                        "member_name": membership.member.display_name,
                        "plan_name": membership.plan_name,
                        "expiry_date": membership.expiry_date.isoformat(),
                        "warning_day": warning_day,
                        "gym_name": gym.name,
                        "gym_email": gym.email,
                        "gym_phone": gym.phone,
                    },
                    "scheduled_at": scheduled_at,
                },
            )
            created += int(was_created)
    return created
