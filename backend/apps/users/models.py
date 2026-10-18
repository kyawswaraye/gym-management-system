from __future__ import annotations

import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from apps.common.db.models import UUIDTimestampModel


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra: object) -> User:
        if not email:
            raise ValueError("Email is required")
        normalized = self.normalize_email(email).strip().lower()
        user = self.model(email=email.strip(), normalized_email=normalized, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra: object) -> User:
        extra.update(is_active=True, is_platform_admin=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, UUIDTimestampModel):
    email = models.EmailField()
    normalized_email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    is_platform_admin = models.BooleanField(default=False)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret_encrypted = models.TextField(null=True, blank=True)
    mfa_last_used_step = models.BigIntegerField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "normalized_email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    @property
    def is_staff(self) -> bool:
        return self.is_platform_admin

    class Meta:
        db_table = "users_user"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(mfa_enabled=False, mfa_secret_encrypted__isnull=True)
                    | models.Q(mfa_enabled=True, mfa_secret_encrypted__isnull=False)
                ),
                name="user_mfa_secret_state_ck",
            )
        ]


class GymSelectionTicket(UUIDTimestampModel):
    user = models.ForeignKey(User, on_delete=models.RESTRICT, related_name="gym_selection_tickets")
    ticket_hash = models.CharField(max_length=64, unique=True, editable=False)
    eligible_gym_membership_ids = ArrayField(models.UUIDField())
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    selected_gym_membership_id = models.UUIDField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    revocation_reason = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    mfa_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users_gym_selection_ticket"
        indexes = [
            models.Index(fields=["user", "expires_at"], name="gymsel_user_expires_idx"),
            models.Index(fields=["expires_at"], name="gymsel_expires_idx"),
        ]


class MFAEnrollment(UUIDTimestampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mfa_enrollments")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    secret_encrypted = models.TextField()
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users_mfa_enrollment"
        indexes = [models.Index(fields=["user", "expires_at"], name="mfaenroll_user_exp_idx")]


class MFAChallenge(UUIDTimestampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mfa_challenges")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    device_name = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=128, blank=True)

    class Meta:
        db_table = "users_mfa_challenge"
        indexes = [models.Index(fields=["user", "expires_at"], name="mfachal_user_exp_idx")]


class RefreshSession(UUIDTimestampModel):
    class PrincipalType(models.TextChoices):
        PLATFORM = "PLATFORM"
        STAFF = "STAFF"
        MEMBER = "MEMBER"

    principal_type = models.CharField(
        max_length=10, choices=PrincipalType, default=PrincipalType.STAFF
    )
    user = models.ForeignKey(User, on_delete=models.RESTRICT, related_name="refresh_sessions")
    selected_gym = models.ForeignKey(
        "tenants.Gym", on_delete=models.RESTRICT, null=True, blank=True
    )
    gym_membership = models.ForeignKey(
        "tenants.GymMembership", on_delete=models.RESTRICT, null=True, blank=True
    )
    member_id = models.UUIDField(null=True, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    family_id = models.UUIDField(default=uuid.uuid4, editable=False)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_by_session = models.OneToOneField(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="replaces"
    )
    revocation_reason = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    device_id = models.CharField(max_length=255, blank=True)
    mfa_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users_refresh_session"
        indexes = [
            models.Index(
                fields=["user", "revoked_at", "expires_at"], name="refresh_user_state_idx"
            ),
            models.Index(fields=["family_id"], name="refresh_family_idx"),
            models.Index(
                fields=["selected_gym", "gym_membership"], name="refresh_staff_context_idx"
            ),
            models.Index(
                fields=["selected_gym", "member_id"],
                name="refresh_member_ctx_idx",
                condition=models.Q(member_id__isnull=False),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="refresh_expiry_after_issue_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        principal_type="PLATFORM",
                        selected_gym__isnull=True,
                        gym_membership__isnull=True,
                        member_id__isnull=True,
                    )
                    | models.Q(
                        principal_type="STAFF",
                        selected_gym__isnull=False,
                        gym_membership__isnull=False,
                        member_id__isnull=True,
                    )
                    | models.Q(
                        principal_type="MEMBER",
                        selected_gym__isnull=False,
                        gym_membership__isnull=True,
                        member_id__isnull=False,
                    )
                ),
                name="refresh_principal_context_ck",
            ),
        ]


class PasswordResetToken(UUIDTimestampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    class Meta:
        db_table = "users_password_reset_token"
        indexes = [
            models.Index(fields=["user", "expires_at"], name="pwdreset_user_exp_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="pwdreset_expiry_after_issue_ck",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(consumed_at__isnull=True, revoked_at__isnull=True),
                name="pwdreset_one_live_per_user",
            ),
        ]
