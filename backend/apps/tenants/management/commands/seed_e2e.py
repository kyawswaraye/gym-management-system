from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q, QuerySet

from apps.audit.models import AuditLog, PlatformAuditLog
from apps.cashbook.models import CashSession
from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.inventory.models import Product, ProductCategory, StockMovement
from apps.inventory.services import adjust_stock, create_category, create_product
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
from apps.sales.models import RetailReturn, RetailReturnLine, RetailSale, RetailSaleLine
from apps.sales.services import approve_sale, create_sale, submit_sale
from apps.tenants import staff_services
from apps.tenants.e2e import (
    BEA_EMAIL,
    E2E_DATABASE_NAME,
    GYM_CODE_PREFIX,
    MANAGER_EMAIL,
    MEMBER_EMAIL,
    OWNER_EMAIL,
    PLATFORM_EMAIL,
    RECEPTION_EMAIL,
    SEEDED_GYM_CODE,
    SEEDED_GYM_NAME,
    TRAINER_EMAIL,
    USER_EMAIL_PREFIX,
    USER_EMAIL_SUFFIX,
    assert_e2e_seed_allowed,
)
from apps.tenants.e2e_runtime import e2e_reuses_storage_keys
from apps.tenants.models import (
    RECEIPT_NUMBER_PREFIX,
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
    if _BASE32_SECRET.fullmatch(value) is None or len(value) % 8 != 0:
        raise CommandError(f"{name} must be an unpadded base32 E2E placeholder.")
    return value


def _e2e_users() -> QuerySet[User]:
    return User.objects.filter(
        normalized_email__startswith=USER_EMAIL_PREFIX,
        normalized_email__endswith=USER_EMAIL_SUFFIX,
    )


def _delete_retail(gym_id: Any) -> None:
    """Remove retail evidence only when the database still allows the delete.

    Posted sales, returns, stock, payments, and receipts are immutable in
    PostgreSQL. This does not disable those triggers. The browser reset drops
    gym_platform_e2e before migrate, so a repeatable run never needs this
    path. A later seed against an existing finance gym raises the original
    database error instead of bypassing it.
    """

    RetailReturnLine.objects.filter(gym_id=gym_id).delete()
    RetailReturn.objects.filter(gym_id=gym_id).delete()
    RetailSaleLine.objects.filter(gym_id=gym_id).delete()
    RetailSale.objects.filter(gym_id=gym_id).delete()
    StockMovement.objects.filter(gym_id=gym_id).delete()
    Product.objects.filter(gym_id=gym_id).delete()
    ProductCategory.objects.filter(gym_id=gym_id).delete()
    CashSession.objects.filter(gym_id=gym_id).delete()


def _delete_payments(gym_id: Any) -> None:
    remaining = Payment.objects.filter(gym_id=gym_id)
    while remaining.exists():
        leaves = remaining.filter(corrections__isnull=True)
        deleted, _ = leaves.delete()
        if deleted == 0:
            raise CommandError("E2E payment rows could not be removed.")


def _delete_gym_tree(gym: Gym) -> None:
    set_gym_context(gym_id=gym.id)
    _delete_retail(gym.id)
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


def _invitation_token() -> str:
    message = mail.outbox[-1].body
    marker = "token="
    token = message.split(marker, 1)[1].split()[0]
    if not token:
        raise CommandError("The E2E staff invitation did not include a token.")
    return token


def _migration_staff_invitation_bootstrap(token_hash: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve an invitation with the role that may execute the lookup.

    ``seed_e2e`` connects as ``gym_migration``. That role owns the tables but
    cannot execute ``app_staff_invitation``. It can assume ``gym_auth_bootstrap``
    for that one lookup, then return to itself for the acceptance writes.
    This does not grant a new privilege or disable a trigger.
    """

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_auth_bootstrap")
        try:
            cursor.execute(
                "SELECT invitation_id, gym_id FROM app_staff_invitation(%s)",
                [token_hash],
            )
            row = cursor.fetchone()
        finally:
            cursor.execute("RESET ROLE")
    if row is None:
        raise CommandError("The manager invitation could not be accepted.")
    return uuid.UUID(str(row[0])), uuid.UUID(str(row[1]))


def _seed_staff(
    gym: Gym,
    owner: GymMembership,
    *,
    role_key: str,
    email: str,
    first_name: str,
    last_name: str,
    staff_code: str,
    display_name: str,
    employment_title: str,
    commission_percent: Decimal,
    password: str,
    secret: str | None,
    request_prefix: str,
) -> GymMembership:
    """Invite and accept one template role through the staff workflow."""

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        role = Role.objects.get(
            gym=gym,
            key=role_key,
            is_active=True,
            source_template__key=role_key,
        )
    EmailBackend()
    mail.outbox.clear()
    staff_services.invite_staff(
        gym_id=gym.id,
        actor=owner.user,
        actor_membership_id=owner.id,
        staff_data={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "staff_code": staff_code,
            "display_name": display_name,
            "joined_date": gym_today(gym),
            "employment_title": employment_title,
            "default_commission_percent": commission_percent,
        },
        role_ids=[role.id],
        request_id=f"{request_prefix}-invite",
        user_agent="e2e-seed",
    )
    original_bootstrap = staff_services._staff_invitation_bootstrap
    staff_services._staff_invitation_bootstrap = _migration_staff_invitation_bootstrap
    try:
        membership = staff_services.accept_staff_invitation(
            plaintext_token=_invitation_token(),
            password=password,
            confirmation={"display_name": display_name},
            request_id=f"{request_prefix}-accept",
            user_agent="e2e-seed",
        )
    finally:
        staff_services._staff_invitation_bootstrap = original_bootstrap
    if secret is not None:
        _enable_mfa(membership.user, password=password, secret=secret)
    return membership


def _seed_member_record(
    gym: Gym,
    actor: GymMembership,
    *,
    first_name: str,
    last_name: str,
    plan_code: str,
    plan_name: str,
    with_membership: bool,
) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member = create_member(
            gym=gym,
            actor=actor,
            member_number="",
            first_name=first_name,
            last_name=last_name,
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
        if not with_membership:
            return
        plan = create_plan(
            gym=gym,
            actor=actor,
            code=plan_code,
            name=plan_name,
            description=plan_name,
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


def _seed_posted_retail_sale(
    gym: Gym,
    *,
    manager: GymMembership,
    owner: GymMembership,
) -> None:
    """Post one card sale through catalog, stock, submit, and approval services.

    The manager creates and submits it. The owner approves it. The browser
    journey then returns one unit as the manager.
    """

    request_id = "e2e-retail-seed"
    user_agent = "e2e-seed"
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=manager,
            fields={"code": "E2EDRINKS", "name": "E2E Drinks", "is_active": True},
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )
        product = create_product(
            gym=gym,
            actor=manager,
            fields={
                "category_id": category.id,
                "sku": "E2EBOTTLE",
                "name": "E2E Bottle",
                "unit": "PIECE",
                "sale_price": Decimal("10.00"),
                "current_unit_cost": Decimal("4.00"),
                "default_unit_cost": Decimal("4.00"),
                "low_stock_threshold": Decimal("1.000"),
                "is_active": True,
            },
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )
        adjust_stock(
            gym=gym,
            actor=manager,
            fields={
                "product_id": product.id,
                "movement_type": StockMovement.Type.OPENING,
                "quantity": Decimal("5.000"),
                "reason": "E2E opening stock",
                "business_date": today,
            },
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )
        sale = create_sale(
            gym=gym,
            actor=manager,
            fields={
                "member_id": None,
                "business_date": today,
                "payment_method": Payment.Method.CARD,
                "reference_number": "E2E-RETAIL",
                "lines": [
                    {
                        "product_id": product.id,
                        "quantity": Decimal("3.000"),
                        "discount": Decimal("0.00"),
                    }
                ],
            },
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )
        submit_sale(
            gym=gym,
            actor=manager,
            sale_id=sale.id,
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )
        approve_sale(
            gym=gym,
            actor=owner,
            sale_id=sale.id,
            cash_session_id=None,
            request_id=request_id,
            ip_address=None,
            user_agent=user_agent,
        )


def _seed_owner_gym(
    password: str,
    secret: str,
    member_password: str,
    manager_password: str,
    manager_secret: str,
    reception_password: str,
    reception_secret: str,
    trainer_password: str,
    trainer_secret: str,
    bea_password: str,
) -> None:
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
        settings.receipt_number_prefix = RECEIPT_NUMBER_PREFIX
        settings.check_in_duplicate_window_seconds = 300
        settings.check_in_challenge_ttl_seconds = 300
        settings.check_in_challenge_max_uses = 1
        settings.membership_expiry_warning_days = [0, 3, 7, 30]
        settings.membership_expiry_email_enabled = True
        settings.membership_expiry_push_enabled = False
        settings.class_cancellation_cutoff_minutes = 60
        settings.class_no_show_consumes_session = True
        settings.require_separate_finance_approval = True
        settings.save()
    _seed_linked_member(gym, result.owner_membership, member_password)
    _seed_member_record(
        gym,
        result.owner_membership,
        first_name="E2Eassigned",
        last_name="Member",
        plan_code="E2EASSIGN",
        plan_name="E2E Assigned Plan",
        with_membership=True,
    )
    _seed_member_record(
        gym,
        result.owner_membership,
        first_name="E2Eother",
        last_name="Guest",
        plan_code="",
        plan_name="",
        with_membership=False,
    )
    manager = _seed_staff(
        gym,
        result.owner_membership,
        role_key="MANAGER",
        email=MANAGER_EMAIL,
        first_name="E2E",
        last_name="Manager",
        staff_code="E2EMGR",
        display_name="E2E Manager",
        employment_title="Manager",
        commission_percent=Decimal("0"),
        password=manager_password,
        secret=manager_secret,
        request_prefix="e2e-manager",
    )
    _seed_staff(
        gym,
        result.owner_membership,
        role_key="RECEPTIONIST",
        email=RECEPTION_EMAIL,
        first_name="E2E",
        last_name="Reception",
        staff_code="E2EREC",
        display_name="E2E Reception",
        employment_title="Reception",
        commission_percent=Decimal("0"),
        password=reception_password,
        secret=reception_secret,
        request_prefix="e2e-reception",
    )
    _seed_staff(
        gym,
        result.owner_membership,
        role_key="TRAINER",
        email=TRAINER_EMAIL,
        first_name="E2E",
        last_name="Trainer",
        staff_code="E2ETRN",
        display_name="E2E Trainer",
        employment_title="Trainer",
        commission_percent=Decimal("10.00"),
        password=trainer_password,
        secret=trainer_secret,
        request_prefix="e2e-trainer",
    )
    _seed_staff(
        gym,
        result.owner_membership,
        role_key="TRAINER",
        email=BEA_EMAIL,
        first_name="E2E",
        last_name="Bea",
        staff_code="E2EBEA",
        display_name="E2E Bea",
        employment_title="Trainer",
        commission_percent=Decimal("25.00"),
        password=bea_password,
        secret=None,
        request_prefix="e2e-bea",
    )
    _seed_posted_retail_sale(gym, manager=manager, owner=result.owner_membership)


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
        manager_password = _env_secret("E2E_MANAGER_PASSWORD", minimum=12)
        reception_password = _env_secret("E2E_RECEPTION_PASSWORD", minimum=12)
        trainer_password = _env_secret("E2E_TRAINER_PASSWORD", minimum=12)
        bea_password = _env_secret("E2E_BEA_PASSWORD", minimum=12)
        platform_secret = _totp_secret("E2E_PLATFORM_TOTP_SECRET")
        owner_secret = _totp_secret("E2E_OWNER_TOTP_SECRET")
        manager_secret = _totp_secret("E2E_MANAGER_TOTP_SECRET")
        reception_secret = _totp_secret("E2E_RECEPTION_TOTP_SECRET")
        trainer_secret = _totp_secret("E2E_TRAINER_TOTP_SECRET")
        removed = _purge_e2e_rows()
        _seed_platform_admin(platform_password, platform_secret)
        _seed_owner_gym(
            owner_password,
            owner_secret,
            member_password,
            manager_password,
            manager_secret,
            reception_password,
            reception_secret,
            trainer_password,
            trainer_secret,
            bea_password,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {PLATFORM_EMAIL} and gym {SEEDED_GYM_CODE} "
                f"after removing {removed} previous E2E gym(s)."
            )
        )
