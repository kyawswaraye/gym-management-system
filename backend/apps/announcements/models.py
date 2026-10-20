from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel


class Announcement(TenantModel):
    """Gym-authored text announcement. Recipients are resolved on the server."""

    class Type(models.TextChoices):
        EVENT = "EVENT"
        PROMOTION = "PROMOTION"
        GENERAL = "GENERAL"

    class Audience(models.TextChoices):
        ALL_ACTIVE_MEMBERS = "ALL_ACTIVE_MEMBERS"
        PT_CLIENTS = "PT_CLIENTS"
        EXPIRING_WITHIN_7_DAYS = "EXPIRING_WITHIN_7_DAYS"
        EXPIRING_WITHIN_30_DAYS = "EXPIRING_WITHIN_30_DAYS"

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        SCHEDULED = "SCHEDULED"
        PUBLISHED = "PUBLISHED"
        CANCELLED = "CANCELLED"
        EXPIRED = "EXPIRED"

    type = models.CharField(max_length=16, choices=Type)
    title = models.CharField(max_length=120)
    body = models.TextField()
    audience = models.CharField(max_length=32, choices=Audience)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    created_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="created_announcements",
    )
    published_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="published_announcements",
        null=True,
        blank=True,
    )
    cancelled_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="cancelled_announcements",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "announcements_announcement"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="announcement_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(type__in=["EVENT", "PROMOTION", "GENERAL"]),
                name="announcement_type_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    audience__in=[
                        "ALL_ACTIVE_MEMBERS",
                        "PT_CLIENTS",
                        "EXPIRING_WITHIN_7_DAYS",
                        "EXPIRING_WITHIN_30_DAYS",
                    ]
                ),
                name="announcement_audience_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(title__regex=r"^[^[:cntrl:]]{1,120}$"),
                name="announcement_title_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ends_at__isnull=True)
                    | models.Q(starts_at__isnull=True)
                    | models.Q(ends_at__gt=models.F("starts_at"))
                ),
                name="announcement_schedule_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["DRAFT", "SCHEDULED"],
                        published_at__isnull=True,
                        published_by_membership__isnull=True,
                        cancelled_at__isnull=True,
                        cancelled_by_membership__isnull=True,
                        expired_at__isnull=True,
                    )
                    | models.Q(
                        status="PUBLISHED",
                        published_at__isnull=False,
                        published_by_membership__isnull=False,
                        cancelled_at__isnull=True,
                        cancelled_by_membership__isnull=True,
                        expired_at__isnull=True,
                    )
                    | models.Q(
                        status="CANCELLED",
                        cancelled_at__isnull=False,
                        cancelled_by_membership__isnull=False,
                        expired_at__isnull=True,
                    )
                    | models.Q(
                        status="EXPIRED",
                        expired_at__isnull=False,
                        published_at__isnull=False,
                        published_by_membership__isnull=False,
                        cancelled_at__isnull=True,
                        cancelled_by_membership__isnull=True,
                    )
                ),
                name="announcement_status_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "-created_at", "-id"],
                name="announcement_list_idx",
            ),
            models.Index(
                fields=["gym", "starts_at"],
                name="announcement_scheduled_idx",
                condition=models.Q(status="SCHEDULED"),
            ),
        ]
