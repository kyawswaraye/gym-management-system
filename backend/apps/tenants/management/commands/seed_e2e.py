from __future__ import annotations

import os
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q, QuerySet

from apps.audit.models import AuditLog, PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.members.models import (
    CheckIn,
    CheckInChallenge,
    CheckInDevice,
    Member,
    Membership,
    MembershipPlan,
    Payment,
)
from apps.members.services import (
    create_member,
    create_membership,
    create_plan,
    gym_today,
    link_member_identity,
)
from apps.notifications.models import Notification
from apps.tenants.e2e import (
    E2E_DATABASE_NAME,
    GYM_CODE_PREFIX,
    MEMBER_EMAIL,
    OWNER_EMAIL,
    PLATFORM_EMAIL,
    SEEDED_GYM_CODE,
    SEEDED_GYM_NAME,
    USER_EMAIL_PREFIX,
    USER_EMAIL_SUFFIX,
    assert_e2e_seed_allowed,
)
from apps.tenants.e2e_runtime import e2e_reuses_storage_keys
from apps.tenants.models import (
    ExpenseCategory,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    GymProvisioningRequest,
    GymSettings,
    Role,
    RolePermission,
    StaffInvitation,
    StaffInvitationRole,
    StaffProfile,
    TenantSequence,
)
from apps.tenants.services import provision_gym
from apps.users.mfa import encrypt_secret
from apps.users.models import GymSelectionTicket, MFAChallenge, MFAEnrollment, RefreshSession, User

_BASE32_SECRET = re.compile(r"[A-Z2-7]{16,64}")
# Append-only and owner-guard triggers block cleanup of the E2E prefix only.
_DISABLE_AUDIT_TRIGGERS = (
    "ALTER TABLE audit_audit_log DISABLE TRIGGER audit_log_append_only",
    "ALTER TABLE audit_platform_audit_log DISABLE TRIGGER platform_audit_log_append_only",
    "ALTER TABLE tenants_role_permission DISABLE TRIGGER role_permission_owner_guard",
    "ALTER TABLE tenants_gym_membership_role DISABLE TRIGGER membership_role_owner_guard",
    "ALTER TABLE tenants_staff_invitation_role DISABLE TRIGGER staff_invitation_role_immutable",
)
_ENABLE_AUDIT_TRIGGERS = (
    "ALTER TABLE audit_audit_log ENABLE TRIGGER audit_log_append_only",
    "ALTER TABLE audit_platform_audit_log ENABLE TRIGGER platform_audit_log_append_only",
    "ALTER TABLE tenants_role_permission ENABLE TRIGGER role_permission_owner_guard",
    "ALTER TABLE tenants_gym_membership_role ENABLE TRIGGER membership_role_owner_guard",
    "ALTER TABLE tenants_staff_invitation_role ENABLE TRIGGER staff_invitation_role_immutable",
)


def _env_secret(name: str, *, minimum: int) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) < minimum:
        raise CommandError(f"{name} must be at least {minimum} characters.")
    return value


def _totp_secret(name: str) -> str:
    value = os.environ.get(name, "").strip().upper()
    if _BASE32_SECRET.fullmatch(value) is None:
        raise CommandError(f"{name} must be an unpadded base32 E2E placeholder.")
    return value


def _e2e_users() -> QuerySet[User]:
    return User.objects.filter(
        normalized_email__startswith=USER_EMAIL_PREFIX,
        normalized_email__endswith=USER_EMAIL_SUFFIX,
    )


def _delete_payments(gym_id: Any) -> None:
    remaining = Payment.objects.filter(gym_id=gym_id)
    while remaining.exists():
        leaves = remaining.filter(corrections__isnull=True)
        deleted, _ = leaves.delete()
        if deleted == 0:
            raise CommandError("E2E payment rows could not be removed.")


def _delete_gym_tree(gym: Gym) -> None:
    set_gym_context(gym_id=gym.id)
    Notification.objects.filter(gym_id=gym.id).delete()
    CheckIn.objects.filter(gym_id=gym.id).delete()
    _delete_payments(gym.id)
    Membership.objects.filter(gym_id=gym.id).delete()
    MembershipPlan.objects.filter(gym_id=gym.id).delete()
    Member.objects.filter(gym_id=gym.id).delete()
    # Members reference profile photos. Delete those rows only after the
    # members. Object keys are random per asset, so this seed does not reuse
    # or address a MinIO object left by a previous run.
    FileAsset.objects.filter(gym_id=gym.id).delete()
    AuditLog.objects.filter(gym_id=gym.id).delete()
    StaffInvitationRole.objects.filter(gym_id=gym.id).delete()
    StaffInvitation.objects.filter(gym_id=gym.id).delete()
    StaffProfile.objects.filter(gym_id=gym.id).delete()
    GymMembershipRole.objects.filter(gym_id=gym.id).delete()
    RolePermission.objects.filter(gym_id=gym.id).delete()
    Role.objects.filter(gym_id=gym.id).delete()
    GymFeatureEntitlement.objects.filter(gym_id=gym.id).delete()
    ExpenseCategory.objects.filter(gym_id=gym.id).delete()
    TenantSequence.objects.filter(gym_id=gym.id).delete()
    GymSettings.objects.filter(gym_id=gym.id).delete()
    RefreshSession.objects.filter(selected_gym_id=gym.id).update(replaced_by_session=None)
    RefreshSession.objects.filter(selected_gym_id=gym.id).delete()
    PlatformAuditLog.objects.filter(Q(target_gym_id=gym.id) | Q(effective_gym_id=gym.id)).delete()
    GymProvisioningRequest.objects.filter(gym_id=gym.id).delete()
    CheckInChallenge.objects.filter(gym_id=gym.id).delete()
    CheckInDevice.objects.filter(gym_id=gym.id).delete()
    GymMembership.objects.filter(gym_id=gym.id).delete()
    gym.delete()


def _execute_statements(statements: tuple[str, ...]) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _assert_e2e_cleanup_allowed() -> None:
    assert_e2e_seed_allowed(
        database_name=str(connection.settings_dict["NAME"]),
        settings_module=os.environ.get("DJANGO_SETTINGS_MODULE", ""),
        seed_flag=os.environ.get("E2E_SEED", ""),
    )


def _with_e2e_cleanup_triggers(operation: Callable[[], None]) -> None:
    """Disable append-only cleanup triggers only for a successful E2E transaction.

    The first database error aborts the PostgreSQL transaction. Running the
    ENABLE statements afterward raises TransactionManagementError and hides
    that error. Rollback restores the disabled triggers. Finance, cash,
    receipt, salary, inventory, and idempotency protections are not disabled.
    """

    _assert_e2e_cleanup_allowed()
    with transaction.atomic():
        _execute_statements(_DISABLE_AUDIT_TRIGGERS)
        operation()
        _execute_statements(_ENABLE_AUDIT_TRIGGERS)


def _delete_e2e_prefix(gyms: list[Gym], users: list[User]) -> None:
    for gym in gyms:
        _delete_gym_tree(gym)
    user_ids = [user.id for user in users]
    if not user_ids:
        return
    RefreshSession.objects.filter(user_id__in=user_ids).update(replaced_by_session=None)
    RefreshSession.objects.filter(user_id__in=user_ids).delete()
    GymSelectionTicket.objects.filter(user_id__in=user_ids).delete()
    MFAChallenge.objects.filter(user_id__in=user_ids).delete()
    MFAEnrollment.objects.filter(user_id__in=user_ids).delete()
    PlatformAuditLog.objects.filter(
        Q(actor_user_id__in=user_ids) | Q(effective_user_id__in=user_ids)
    ).delete()
    GymProvisioningRequest.objects.filter(actor_user_id__in=user_ids).delete()
    User.objects.filter(id__in=user_ids).delete()


def _purge_e2e_rows() -> int:
    """Remove previous E2E-prefixed rows without touching any other tenant.

    Immutable and restricted finance rows are left to the database. The
    repeatable browser reset drops and recreates gym_platform_e2e before
    migrate; this prefix delete is not that reset.
    """

    _assert_e2e_cleanup_allowed()
    gyms = list(Gym.objects.filter(code__startswith=GYM_CODE_PREFIX))
    users = list(_e2e_users())

    def remove() -> None:
        _delete_e2e_prefix(gyms, users)

    _with_e2e_cleanup_triggers(remove)
    return len(gyms)


def _enable_mfa(user: User, *, password: str, secret: str) -> None:
    user.set_password(password)
    user.mfa_enabled = True
    user.mfa_secret_encrypted = encrypt_secret(secret)
    user.mfa_last_used_step = None
    user.is_active = True
    user.save(
        update_fields=[
            "password",
            "mfa_enabled",
            "mfa_secret_encrypted",
            "mfa_last_used_step",
            "is_active",
            "updated_at",
        ]
    )


def _seed_platform_admin(password: str, secret: str) -> None:
    admin, _ = User.objects.get_or_create(
        normalized_email=PLATFORM_EMAIL,
        defaults={"email": PLATFORM_EMAIL, "is_active": True},
    )
    admin.email = PLATFORM_EMAIL
    admin.first_name = "E2E"
    admin.last_name = "Platform"
    admin.is_platform_admin = True
    admin.is_superuser = True
    admin.save(
        update_fields=[
            "email",
            "first_name",
            "last_name",
            "is_platform_admin",
            "is_superuser",
            "updated_at",
        ]
    )
    _enable_mfa(admin, password=password, secret=secret)


def _seed_linked_member(gym: Gym, actor: GymMembership, password: str) -> None:
    """Seed one already-linked member. Public linking stays unpublished."""

    user = User.objects.create_user(
        email=MEMBER_EMAIL,
        password=password,
        first_name="E2Escan",
        last_name="Visitor",
    )
    user.is_platform_admin = False
    user.is_superuser = False
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.save(
        update_fields=[
            "is_platform_admin",
            "is_superuser",
            "mfa_enabled",
            "mfa_secret_encrypted",
            "updated_at",
        ]
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = create_member(
            gym=gym,
            actor=actor,
            member_number="",
            first_name="E2Escan",
            last_name="Visitor",
            phone=None,
            email=None,
            gender=None,
            date_of_birth=None,
            address={},
            join_date=gym_today(gym),
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
        )
        link_member_identity(gym=gym, member_id=member.id, user=user)
        plan = create_plan(
            gym=gym,
            actor=actor,
            code="E2ESCAN",
            name="E2E Scan Plan",
            description="E2E linked member plan",
            duration_value=30,
            duration_unit="DAY",
            price=Decimal("0.00"),
        )
        create_membership(
            gym=gym,
            actor=actor,
            member=member,
            plan=plan,
            start_date=gym_today(gym),
            discount_amount=Decimal("0.00"),
        )


def _seed_owner_gym(password: str, secret: str, member_password: str) -> None:
    result = provision_gym(
        code=SEEDED_GYM_CODE,
        name=SEEDED_GYM_NAME,
        owner_email=OWNER_EMAIL,
    )
    gym = result.gym
    gym.name = SEEDED_GYM_NAME
    gym.legal_name = ""
    gym.status = Gym.Status.ACTIVE
    gym.timezone = "Asia/Yangon"
    gym.currency = "THB"
    gym.subscription_plan = None
    gym.subscription_status = Gym.SubscriptionStatus.TRIAL
    gym.save(
        update_fields=[
            "name",
            "legal_name",
            "status",
            "timezone",
            "currency",
            "subscription_plan",
            "subscription_status",
            "updated_at",
        ]
    )
    owner = User.objects.get(normalized_email=OWNER_EMAIL)
    owner.email = OWNER_EMAIL
    owner.first_name = "E2E"
    owner.last_name = "Owner"
    owner.is_platform_admin = False
    owner.is_superuser = False
    owner.save(
        update_fields=[
            "email",
            "first_name",
            "last_name",
            "is_platform_admin",
            "is_superuser",
            "updated_at",
        ]
    )
    _enable_mfa(owner, password=password, secret=secret)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        settings = GymSettings.objects.get(gym=gym)
        settings.week_starts_on = 1
        settings.allow_manual_member_numbers = False
        settings.member_number_prefix = "MEM"
        settings.receipt_number_prefix = SEEDED_GYM_CODE
        settings.check_in_duplicate_window_seconds = 300
        settings.check_in_challenge_ttl_seconds = 120
        settings.check_in_challenge_max_uses = 1
        settings.membership_expiry_warning_days = [0, 3, 7, 30]
        settings.membership_expiry_email_enabled = True
        settings.membership_expiry_push_enabled = False
        settings.class_cancellation_cutoff_minutes = 60
        settings.class_no_show_consumes_session = True
        settings.require_separate_finance_approval = True
        settings.save()
    _seed_linked_member(gym, result.owner_membership, member_password)


class Command(BaseCommand):
    help = (
        "Reset and seed the dedicated gym_platform_e2e database for browser smoke "
        "tests. Refuses every other database."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        assert_e2e_seed_allowed(
            database_name=str(connection.settings_dict["NAME"]),
            settings_module=os.environ.get("DJANGO_SETTINGS_MODULE", ""),
            seed_flag=os.environ.get("E2E_SEED", ""),
        )
        if str(connection.settings_dict["NAME"]) != E2E_DATABASE_NAME:
            raise CommandError("Refusing to seed a database other than gym_platform_e2e.")
        if e2e_reuses_storage_keys():
            raise CommandError("E2E object keys must stay unique across seeds.")
        platform_password = _env_secret("E2E_PLATFORM_PASSWORD", minimum=12)
        owner_password = _env_secret("E2E_OWNER_PASSWORD", minimum=12)
        member_password = _env_secret("E2E_MEMBER_PASSWORD", minimum=12)
        platform_secret = _totp_secret("E2E_PLATFORM_TOTP_SECRET")
        owner_secret = _totp_secret("E2E_OWNER_TOTP_SECRET")
        removed = _purge_e2e_rows()
        _seed_platform_admin(platform_password, platform_secret)
        _seed_owner_gym(owner_password, owner_secret, member_password)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {PLATFORM_EMAIL} and gym {SEEDED_GYM_CODE} "
                f"after removing {removed} previous E2E gym(s)."
            )
        )
