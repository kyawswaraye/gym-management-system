from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.db import models
from django.db.models.lookups import LessThanOrEqual

from apps.common.db.models import TenantModel, UUIDTimestampModel


def default_expiry_warning_days() -> list[int]:
    return [0, 3, 7, 30]


# Printed on membership, shop, and PT receipts. The gym code stays a separate id.
RECEIPT_NUMBER_PREFIX = "RCPT"
# At most three lines. One trailing newline does not add a fourth line.
PRINTER_FOOTER_LINE_PATTERN = r"^([^\r\n]*\n){0,2}[^\r\n]*\n?$"


# Fernet token for a 65,536-byte plaintext. PKCS7 adds a full block when the
# plaintext is already a multiple of 16, so the ciphertext is 65,552 bytes.
# The token is version + timestamp + IV + ciphertext + HMAC = 65,609 bytes.
# URL-safe base64 length is 4 * ceil(65609 / 3) = 87,480.
IDEMPOTENCY_CIPHERTEXT_MAX_OCTETS = 87480
_IDEMPOTENCY_CIPHERTEXT_LENGTH: models.Q = cast(
    models.Q,
    models.Q(response_body_ciphertext__isnull=True)
    | LessThanOrEqual(
        models.Func(
            "response_body_ciphertext",
            function="octet_length",
            output_field=models.IntegerField(),
        ),
        IDEMPOTENCY_CIPHERTEXT_MAX_OCTETS,
    ),
)


def normalize_role_name(name: str) -> str:
    """Collapse whitespace and case so active role names are unique per gym."""

    return " ".join(name.split()).casefold().upper()


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


class GymLogo(models.Model):
    """One private brand image for a gym. Kept off the gym row so auth reads stay small."""

    gym = models.OneToOneField(
        Gym,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="brand_logo",
    )
    content_type = models.CharField(max_length=32)
    data = models.BinaryField()

    class Meta:
        db_table = "tenants_gym_logo"

    def __str__(self) -> str:
        return f"Logo for {self.gym_id}"


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
    normalized_name = models.CharField(max_length=100, editable=False)
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
            models.UniqueConstraint(
                fields=["gym", "normalized_name"],
                condition=models.Q(is_active=True),
                name="role_active_name_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(key="") & ~models.Q(name="") & ~models.Q(normalized_name=""),
                name="role_identity_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_template__isnull=True, source_template_version__isnull=True)
                    | models.Q(
                        source_template__isnull=False,
                        source_template_version__isnull=False,
                        source_template_version__gt=0,
                    )
                ),
                name="role_source_pair_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "is_active"], name="role_gym_active_idx"),
            models.Index(fields=["gym", "key", "id"], name="role_gym_key_cursor_idx"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalized_name = normalize_role_name(self.name or "")
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "normalized_name"}
        super().save(*args, **kwargs)


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


class StaffInvitation(TenantModel):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        ACCEPTED = "ACCEPTED"
        REVOKED = "REVOKED"
        EXPIRED = "EXPIRED"

    gym_membership = models.ForeignKey(GymMembership, on_delete=models.RESTRICT)
    email = models.EmailField()
    normalized_email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    inviter_membership_id = models.UUIDField(null=True, blank=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="accepted_staff_invitations",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_membership_id = models.UUIDField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)

    class Meta:
        db_table = "tenants_staff_invitation"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="invite_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "normalized_email"],
                condition=models.Q(status="PENDING"),
                name="invite_pending_email_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status", "expires_at"], name="invite_gym_state_exp_idx")
        ]


class StaffInvitationRole(TenantModel):
    staff_invitation = models.ForeignKey(StaffInvitation, on_delete=models.RESTRICT)
    role = models.ForeignKey(Role, on_delete=models.RESTRICT)

    class Meta:
        db_table = "tenants_staff_invitation_role"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="inviterole_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "staff_invitation", "role"],
                name="invite_role_gym_uniq",
            ),
        ]


class StaffProfile(TenantModel):
    """Gym-specific employment record for one non-owner or future staff membership.

    ``profile_photo_asset_id`` stays a nullable identifier and is not a cross-app
    foreign key. The foundational ``FileAsset`` table exists, but the staff API
    intentionally does not accept or return this identifier yet. A future service
    must validate that a referenced asset is in the same gym and has scan status
    CLEAN before it can be attached.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ON_LEAVE = "ON_LEAVE"
        TERMINATED = "TERMINATED"
        ARCHIVED = "ARCHIVED"

    gym_membership = models.ForeignKey(GymMembership, on_delete=models.RESTRICT)
    staff_code = models.CharField(max_length=32)
    display_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, blank=True)
    joined_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    profile_photo_asset_id = models.UUIDField(null=True, blank=True)
    default_commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    employment_title = models.CharField(max_length=100, blank=True)
    terminated_date = models.DateField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField(null=True, blank=True)
    updated_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_staff_profile"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="staffprof_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "staff_code"], name="staffprof_gym_code_uniq"),
            models.UniqueConstraint(
                fields=["gym", "gym_membership"], name="staffprof_gym_member_uniq"
            ),
            models.CheckConstraint(
                condition=~models.Q(staff_code="") & ~models.Q(display_name=""),
                name="staffprof_identity_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(default_commission_percent__gte=0)
                & models.Q(default_commission_percent__lte=100),
                name="staffprof_commission_range_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="TERMINATED",
                        terminated_date__isnull=False,
                        terminated_date__gte=models.F("joined_date"),
                    )
                    | (~models.Q(status="TERMINATED") & models.Q(terminated_date__isnull=True))
                ),
                name="staffprof_termination_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="ARCHIVED", archived_at__isnull=False)
                    | (~models.Q(status="ARCHIVED") & models.Q(archived_at__isnull=True))
                ),
                name="staffprof_archive_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status"], name="staffprof_gym_status_idx"),
            models.Index(fields=["gym", "display_name", "id"], name="staffprof_gym_name_idx"),
        ]


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


class GymSettings(TenantModel):
    """Tenant-owned operational defaults used by all business modules."""

    week_starts_on = models.PositiveSmallIntegerField(default=1)
    allow_manual_member_numbers = models.BooleanField(default=False)
    member_number_prefix = models.CharField(max_length=20, default="MEM")
    receipt_number_prefix = models.CharField(max_length=20, default=RECEIPT_NUMBER_PREFIX)
    check_in_duplicate_window_seconds = models.PositiveIntegerField(default=300)
    check_in_challenge_ttl_seconds = models.PositiveIntegerField(default=120)
    check_in_challenge_max_uses = models.PositiveIntegerField(default=1)
    membership_expiry_warning_days = models.JSONField(default=default_expiry_warning_days)
    membership_expiry_email_enabled = models.BooleanField(default=True)
    membership_expiry_push_enabled = models.BooleanField(default=False)
    class_cancellation_cutoff_minutes = models.PositiveIntegerField(default=60)
    class_no_show_consumes_session = models.BooleanField(default=True)
    require_separate_finance_approval = models.BooleanField(default=True)
    printer_enabled = models.BooleanField(default=False)
    printer_print_logo = models.BooleanField(default=False)
    printer_print_address = models.BooleanField(default=False)
    printer_print_phone = models.BooleanField(default=False)
    printer_footer = models.CharField(max_length=120, blank=True, default="")
    next_member_number = models.PositiveBigIntegerField(default=1)
    created_by_membership_id = models.UUIDField(null=True, blank=True)
    updated_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_gym_settings"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="settings_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym"], name="settings_one_per_gym_uniq"),
            models.CheckConstraint(
                condition=models.Q(next_member_number__gt=0),
                name="settings_next_member_number_gt_0_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(week_starts_on__lte=6),
                name="settings_week_start_range_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(check_in_duplicate_window_seconds__lte=86400),
                name="settings_duplicate_window_range_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(check_in_challenge_ttl_seconds__gte=30)
                & models.Q(check_in_challenge_ttl_seconds__lte=600),
                name="settings_challenge_ttl_range_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(check_in_challenge_max_uses__gte=1)
                & models.Q(check_in_challenge_max_uses__lte=100),
                name="settings_challenge_uses_range_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(member_number_prefix__regex=r"^[A-Z0-9-]+$"),
                name="settings_member_prefix_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(receipt_number_prefix__regex=r"^[A-Z0-9-]+$"),
                name="settings_receipt_prefix_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(printer_footer__regex=PRINTER_FOOTER_LINE_PATTERN),
                name="settings_printer_footer_lines_ck",
            ),
        ]


class ExpenseCategory(TenantModel):
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)
    system_key = models.CharField(max_length=32, null=True, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField(null=True, blank=True)
    updated_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_expense_category"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="expcat_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "code"], name="expcat_gym_code_uniq"),
            models.UniqueConstraint(fields=["gym", "normalized_name"], name="expcat_gym_name_uniq"),
            models.UniqueConstraint(
                fields=["gym", "system_key"],
                condition=models.Q(system_key__isnull=False),
                name="expcat_gym_system_key_uniq",
            ),
        ]
        indexes = [models.Index(fields=["gym", "is_active"], name="expcat_gym_active_idx")]


class TenantSequence(TenantModel):
    sequence_key = models.CharField(max_length=64)
    prefix = models.CharField(max_length=20)
    next_value = models.PositiveBigIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=6)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_tenant_sequence"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="tenantseq_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "sequence_key"], name="tenantseq_gym_key_uniq"),
            models.CheckConstraint(
                condition=models.Q(next_value__gt=0), name="tenantseq_next_value_gt_0_ck"
            ),
            models.CheckConstraint(
                condition=models.Q(padding__gte=1) & models.Q(padding__lte=18),
                name="tenantseq_padding_range_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(prefix__regex=r"^[A-Z0-9-]+$"),
                name="tenantseq_prefix_format_ck",
            ),
        ]


class IdempotencyRecord(TenantModel):
    """One finance command attempt, scoped to a gym, actor, route, and key.

    A null actor is the system identity. ``UNIQUE NULLS NOT DISTINCT`` makes
    that null collide with another null, so a system key cannot be reused for
    a different attempt. Terminal rows store ciphertext only.
    """

    class State(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    actor_membership_id = models.UUIDField(null=True, blank=True)
    actor_member_id = models.UUIDField(null=True, blank=True)
    http_method = models.CharField(max_length=8)
    route = models.CharField(max_length=200)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=State, default=State.IN_PROGRESS)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body_ciphertext = models.TextField(null=True, blank=True)
    response_body_bytes = models.PositiveIntegerField(null=True, blank=True)
    resource_type = models.CharField(max_length=64, null=True, blank=True)
    resource_id = models.UUIDField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tenants_idempotency_record"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="idem_gym_id_uniq"),
            models.UniqueConstraint(
                fields=[
                    "gym",
                    "actor_membership_id",
                    "actor_member_id",
                    "http_method",
                    "route",
                    "idempotency_key",
                ],
                name="idem_scope_key_uniq",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    actor_membership_id__isnull=False,
                    actor_member_id__isnull=False,
                ),
                name="idem_one_actor_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(idempotency_key__regex=r"^[A-Za-z0-9._:-]{16,128}$"),
                name="idem_key_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(request_hash__regex=r"^[0-9a-f]{64}$"),
                name="idem_hash_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(http_method__regex=r"^[A-Z]{3,8}$"),
                name="idem_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(route__regex=r"^/[A-Za-z0-9_./{}-]{1,199}$"),
                name="idem_route_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["IN_PROGRESS", "COMPLETED", "FAILED"]),
                name="idem_state_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="IN_PROGRESS",
                        response_status__isnull=True,
                        response_body_ciphertext__isnull=True,
                        response_body_bytes__isnull=True,
                        resource_type__isnull=True,
                        resource_id__isnull=True,
                        locked_until__isnull=False,
                    )
                    | (
                        models.Q(
                            state__in=["COMPLETED", "FAILED"],
                            response_status__isnull=False,
                            response_body_ciphertext__isnull=False,
                            response_body_bytes__isnull=False,
                            locked_until__isnull=True,
                        )
                        & (
                            models.Q(resource_type__isnull=True, resource_id__isnull=True)
                            | models.Q(resource_type__isnull=False, resource_id__isnull=False)
                        )
                    )
                ),
                name="idem_evidence_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="idem_expiry_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(response_status__isnull=True)
                | models.Q(state="COMPLETED", response_status__gte=200, response_status__lte=299)
                | models.Q(state="FAILED", response_status__gte=400, response_status__lte=499),
                name="idem_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(response_body_bytes__isnull=True)
                | (models.Q(response_body_bytes__gte=1) & models.Q(response_body_bytes__lte=65536)),
                name="idem_bytes_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(resource_type__isnull=True)
                | models.Q(resource_type__regex=r"^[A-Za-z0-9_]{1,64}$"),
                name="idem_resource_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(response_body_ciphertext__isnull=True)
                | models.Q(response_body_ciphertext__regex=r"^gAAAAA[A-Za-z0-9_=-]+$"),
                name="idem_ciphertext_ck",
            ),
            models.CheckConstraint(
                condition=_IDEMPOTENCY_CIPHERTEXT_LENGTH,
                name="idem_ciphertext_len_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "expires_at"], name="idem_gym_expires_idx"),
            models.Index(
                fields=["gym", "locked_until"],
                name="idem_gym_active_lock_idx",
                condition=models.Q(state="IN_PROGRESS"),
            ),
        ]


class GymProvisioningRequest(UUIDTimestampModel):
    class State(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS"
        COMPLETED = "COMPLETED"

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="gym_provisioning_requests",
    )
    method = models.CharField(max_length=10, default="POST")
    route = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=State, default=State.IN_PROGRESS)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(default=dict)
    gym = models.ForeignKey(Gym, on_delete=models.RESTRICT, null=True, blank=True)

    class Meta:
        db_table = "tenants_gym_provisioning_request"
        constraints = [
            models.UniqueConstraint(
                fields=["actor_user", "method", "route", "idempotency_key"],
                name="gymprov_actor_route_key_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="IN_PROGRESS",
                        response_status__isnull=True,
                        gym__isnull=True,
                    )
                    | models.Q(
                        state="COMPLETED",
                        response_status=201,
                        gym__isnull=False,
                    )
                ),
                name="gymprov_state_response_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["actor_user", "-created_at"], name="gymprov_actor_time_idx")
        ]
