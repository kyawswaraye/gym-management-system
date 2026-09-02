from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.db.models import TenantModel, UUIDTimestampModel


class Permission(UUIDTimestampModel):
    class Risk(models.TextChoices):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        CRITICAL = "CRITICAL"

    code = models.CharField(max_length=100, unique=True)
    module = models.CharField(max_length=50)
    description = models.CharField(max_length=255)
    risk_level = models.CharField(max_length=10, choices=Risk, default=Risk.LOW)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "tenants_permission"


class SystemRoleTemplate(UUIDTimestampModel):
    key = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(Permission, through="SystemRoleTemplatePermission")

    class Meta:
        db_table = "tenants_system_role_template"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="template_version_gt_0_ck"
            )
        ]


class SystemRoleTemplatePermission(UUIDTimestampModel):
    system_role_template = models.ForeignKey(SystemRoleTemplate, on_delete=models.RESTRICT)
    permission = models.ForeignKey(Permission, on_delete=models.RESTRICT)

    class Meta:
        db_table = "tenants_system_role_template_permission"
        constraints = [
            models.UniqueConstraint(
                fields=["system_role_template", "permission"], name="template_permission_uniq"
            )
        ]


class Feature(UUIDTimestampModel):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "tenants_feature"


class SubscriptionPlan(UUIDTimestampModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        RETIRED = "RETIRED"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=Status, default=Status.ACTIVE)
    price = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="THB")
    billing_interval = models.CharField(max_length=20)
    features = models.ManyToManyField(Feature, through="SubscriptionPlanFeature")

    class Meta:
        db_table = "tenants_subscription_plan"


class SubscriptionPlanFeature(UUIDTimestampModel):
    subscription_plan = models.ForeignKey(SubscriptionPlan, on_delete=models.RESTRICT)
    feature = models.ForeignKey(Feature, on_delete=models.RESTRICT)
    enabled = models.BooleanField(default=True)
    limit_value = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "tenants_subscription_plan_feature"
        constraints = [
            models.UniqueConstraint(
                fields=["subscription_plan", "feature"], name="plan_feature_uniq"
            )
        ]


class Gym(UUIDTimestampModel):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"
        CLOSED = "CLOSED"

    class SubscriptionStatus(models.TextChoices):
        TRIAL = "TRIAL"
        ACTIVE = "ACTIVE"
        PAST_DUE = "PAST_DUE"
        CANCELLED = "CANCELLED"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Yangon")
    currency = models.CharField(max_length=3, default="THB")
    subscription_plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.RESTRICT, null=True, blank=True
    )
    subscription_status = models.CharField(
        max_length=12, choices=SubscriptionStatus, default=SubscriptionStatus.TRIAL
    )
    subscription_starts_at = models.DateTimeField(null=True, blank=True)
    subscription_ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenants_gym"
        constraints = [
            models.CheckConstraint(condition=~models.Q(name=""), name="gym_name_nonblank_ck"),
            models.CheckConstraint(condition=~models.Q(code=""), name="gym_code_nonblank_ck"),
        ]


class GymMembership(TenantModel):
    class Status(models.TextChoices):
        INVITED = "INVITED"
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"
        REVOKED = "REVOKED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT)
    status = models.CharField(max_length=12, choices=Status, default=Status.INVITED)
    is_owner = models.BooleanField(default=False)
    authz_version = models.PositiveIntegerField(default=1)
    joined_date = models.DateField()
    invited_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_gym_membership"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="membership_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "user"], name="membership_gym_user_uniq"),
            models.CheckConstraint(
                condition=models.Q(authz_version__gt=0), name="membership_authz_version_gt_0_ck"
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status"], name="membership_gym_status_idx"),
            models.Index(
                fields=["gym", "user"],
                condition=models.Q(is_owner=True, status="ACTIVE"),
                name="membership_active_owner_idx",
            ),
        ]


class Role(TenantModel):
    key = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    source_template = models.ForeignKey(
        SystemRoleTemplate, on_delete=models.RESTRICT, null=True, blank=True
    )
    source_template_version = models.PositiveIntegerField(null=True, blank=True)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_role"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="role_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "key"], name="role_gym_key_uniq"),
        ]
        indexes = [models.Index(fields=["gym", "is_active"], name="role_gym_active_idx")]


class RolePermission(TenantModel):
    role = models.ForeignKey(Role, on_delete=models.RESTRICT)
    permission = models.ForeignKey(Permission, on_delete=models.RESTRICT)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_role_permission"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="roleperm_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "role", "permission"], name="role_permission_gym_uniq"
            ),
        ]
        indexes = [models.Index(fields=["gym", "role"], name="roleperm_gym_role_idx")]


class GymMembershipRole(TenantModel):
    gym_membership = models.ForeignKey(GymMembership, on_delete=models.RESTRICT)
    role = models.ForeignKey(Role, on_delete=models.RESTRICT)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_gym_membership_role"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="memberrole_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "gym_membership", "role"], name="membership_role_gym_uniq"
            ),
        ]
        indexes = [models.Index(fields=["gym", "gym_membership"], name="memberrole_gym_member_idx")]


class GymFeatureEntitlement(TenantModel):
    class Source(models.TextChoices):
        PLAN = "PLAN"
        OVERRIDE = "OVERRIDE"

    feature = models.ForeignKey(Feature, on_delete=models.RESTRICT)
    enabled = models.BooleanField(default=True)
    limit_value = models.PositiveIntegerField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source)
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_until = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_gym_feature_entitlement"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="entitlement_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "feature"], name="entitlement_gym_feature_uniq"),
        ]
        indexes = [models.Index(fields=["gym", "enabled"], name="entitlement_gym_enabled_idx")]
