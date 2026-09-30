from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel


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
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    provider_id = models.CharField(max_length=255, blank=True)
    deduplication_key = models.CharField(max_length=200)
    attempt_count = models.PositiveSmallIntegerField(default=0)
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
                condition=(
                    models.Q(
                        status="PENDING",
                        sent_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | models.Q(
                        status="SENT",
                        sent_at__isnull=False,
                        failed_at__isnull=True,
                    )
                    | models.Q(
                        status="FAILED",
                        sent_at__isnull=True,
                        failed_at__isnull=False,
                    )
                    | models.Q(
                        status="CANCELLED",
                        sent_at__isnull=True,
                        failed_at__isnull=True,
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
        ]
