from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.db.models import TenantModel, UUIDTimestampModel


class Outcome(models.TextChoices):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


class AuditLog(TenantModel):
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    actor_membership_id = models.UUIDField(null=True, blank=True)
    impersonator_membership_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=100)
    module = models.CharField(max_length=50)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField()
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    request_id = models.CharField(max_length=128)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome)
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "audit_audit_log"
        constraints = [models.UniqueConstraint(fields=["gym", "id"], name="audit_gym_id_uniq")]
        indexes = [
            models.Index(
                fields=["gym", "target_type", "target_id", "-occurred_at"],
                name="audit_gym_target_time_idx",
            )
        ]


class PlatformAuditLog(UUIDTimestampModel):
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name="platform_audit_actions"
    )
    action = models.CharField(max_length=100)
    module = models.CharField(max_length=50)
    target_gym = models.ForeignKey("tenants.Gym", on_delete=models.RESTRICT, null=True, blank=True)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    effective_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="platform_audit_effective_actions",
    )
    effective_gym = models.ForeignKey(
        "tenants.Gym",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="effective_platform_audits",
    )
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    request_id = models.CharField(max_length=128)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome)
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "audit_platform_audit_log"
        indexes = [
            models.Index(fields=["-occurred_at"], name="platform_audit_time_idx"),
            models.Index(fields=["actor_user", "-occurred_at"], name="platform_audit_actor_idx"),
            models.Index(fields=["target_gym", "-occurred_at"], name="platform_audit_gym_idx"),
            models.Index(fields=["request_id"], name="platform_audit_request_idx"),
        ]
