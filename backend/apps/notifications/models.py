from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel

# Provider attempts stored on one row. Pending work stays strictly below this
# ceiling; the fifth failure is terminal.
MAX_NOTIFICATION_ATTEMPTS = 5


class Notification(TenantModel):
    class Type(models.TextChoices):
        MEMBERSHIP_EXPIRY = "MEMBERSHIP_EXPIRY"

    class Channel(models.TextChoices):
        EMAIL = "EMAIL"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        SENT = "SENT"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"

    recipient_member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        related_name="notifications",
    )
    membership = models.ForeignKey(
        "members.Membership",
        on_delete=models.RESTRICT,
        related_name="notifications",
    )
    type = models.CharField(max_length=32, choices=Type)
    channel = models.CharField(max_length=12, choices=Channel)
    template_key = models.CharField(max_length=64)
    template_version = models.PositiveSmallIntegerField()
    payload = models.JSONField(default=dict)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    provider_id = models.CharField(max_length=255, blank=True)
    deduplication_key = models.CharField(max_length=200)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=32, blank=True, default="", db_default="")
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "notifications_notification"
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "id"],
                name="notification_gym_id_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "channel", "deduplication_key"],
                name="notification_gym_channel_dedup_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__lte=MAX_NOTIFICATION_ATTEMPTS),
                name="notification_attempt_bound_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(last_error_code__regex=r"^[A-Z0-9_]{0,32}$"),
                name="notification_error_code_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(provider_id__regex=r"^[A-Za-z0-9_.:-]{0,255}$"),
                name="notification_provider_id_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="PENDING",
                        sent_at__isnull=True,
                        failed_at__isnull=True,
                        next_attempt_at__isnull=False,
                        attempt_count__lt=MAX_NOTIFICATION_ATTEMPTS,
                    )
                    | models.Q(
                        status="SENT",
                        sent_at__isnull=False,
                        failed_at__isnull=True,
                        next_attempt_at__isnull=True,
                        last_error_code="",
                        attempt_count__gte=1,
                        attempt_count__lte=MAX_NOTIFICATION_ATTEMPTS,
                    )
                    | models.Q(
                        status="FAILED",
                        sent_at__isnull=True,
                        failed_at__isnull=False,
                        next_attempt_at__isnull=True,
                        last_error_code__regex=r"^[A-Z][A-Z0-9_]{0,31}$",
                        attempt_count__gte=1,
                        attempt_count__lte=MAX_NOTIFICATION_ATTEMPTS,
                    )
                    | models.Q(
                        status="CANCELLED",
                        sent_at__isnull=True,
                        failed_at__isnull=True,
                        next_attempt_at__isnull=True,
                        last_error_code="",
                        attempt_count__lte=MAX_NOTIFICATION_ATTEMPTS,
                    )
                ),
                name="notification_status_timestamps_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "scheduled_at"],
                name="notif_gym_pending_idx",
            ),
            models.Index(
                fields=["gym", "recipient_member", "created_at"],
                name="notif_member_created_idx",
            ),
            models.Index(
                fields=["gym", "next_attempt_at", "id"],
                name="notif_due_pending_idx",
                condition=models.Q(status="PENDING"),
            ),
        ]
