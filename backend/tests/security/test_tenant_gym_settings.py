import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.apps import apps as django_apps
from django.db import DatabaseError, ProgrammingError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Feature,
    Gym,
    GymMembership,
    GymMembershipRole,
    GymSettings,
    Permission,
    Role,
    RolePermission,
    SubscriptionPlan,
    SubscriptionPlanFeature,
    SystemRoleTemplate,
    SystemRoleTemplatePermission,
)
from apps.tenants.services import update_gym_settings
from apps.tenants.validation import GYM_PROFILE_FIELDS
from apps.users.models import User
from apps.users.services import create_staff_session

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"
BFF_SECRET = "test-bff-shared-secret-32-chars-min"


def jwt_settings() -> dict[str, object]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return {
        "REFRESH_TOKEN_PEPPER": "tenant-gym-settings-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"tenant-gym-key": public_pem},
        "JWT_KEY_ID": "tenant-gym-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


def ensure_authorization_catalog() -> SubscriptionPlan:
    """Restore the historical catalog, then the later forward data migrations.

    Transaction tests flush rows inserted by migrations. The 0003 constants stay
    the historical catalog. ``retail.approve`` is applied only by the 0016
    backfill. ``platform.settings.manage`` is then marked inactive by the 0020
    retirement. ``checkins.challenge.issue`` is applied only by the 0022
    backfill, for OWNER and MANAGER templates and copied roles. A clean install
    runs those operations after the historical seed, and provisioning copies the
    restored template permissions.
    """

    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    backfill = importlib.import_module("apps.tenants.migrations.0016_manager_retail_approve")
    permissions: dict[str, Permission] = {}
    for code in seed.PERMISSION_CODES:
        permission, _ = Permission.objects.get_or_create(
            code=code,
            defaults={
                "module": code.split(".", 1)[0],
                "description": code,
                "risk_level": Permission.Risk.LOW,
                "is_active": True,
            },
        )
        permissions[code] = permission
    for key, codes in seed.TEMPLATES.items():
        template, _ = SystemRoleTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name": key.replace("_", " ").title(),
                "description": f"Protected {key} role template",
                "version": 1,
                "is_active": True,
            },
        )
        for code in codes:
            SystemRoleTemplatePermission.objects.get_or_create(
                system_role_template=template,
                permission=permissions[code],
            )
    backfill.grant_manager_retail_approve(django_apps, None)
    retirement = importlib.import_module(
        "apps.tenants.migrations.0020_retire_platform_settings_manage"
    )
    retirement.retire_platform_settings_manage(django_apps, None)
    challenge = importlib.import_module(
        "apps.tenants.migrations.0022_check_in_challenge_permission"
    )
    challenge.grant_check_in_challenge_issue(django_apps, None)
    feature, _ = Feature.objects.get_or_create(
        code="members",
        defaults={"name": "Members", "description": "Members module"},
    )
    plan, _ = SubscriptionPlan.objects.get_or_create(
        code="TRIAL",
        defaults={
            "name": "Trial",
            "status": SubscriptionPlan.Status.ACTIVE,
            "price": "0.00",
            "currency": "THB",
            "billing_interval": "TRIAL",
        },
    )
    SubscriptionPlanFeature.objects.get_or_create(
        subscription_plan=plan,
        feature=feature,
        defaults={"enabled": True},
    )
    return plan


def platform_token(email: str) -> tuple[APIClient, str]:
    admin = User.objects.create_superuser(email=email, password=PASSWORD)
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {"email": admin.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.json()
    return client, login.json()["data"]["access_token"]


def provision(client: APIClient, token: str, *, code: str, owner_email: str) -> dict[str, object]:
    plan = ensure_authorization_catalog()
    response = client.post(
        "/api/v1/admin/gyms/",
        {
            "gym": {
                "code": code,
                "name": f"{code} Gym",
                "timezone": "Asia/Yangon",
                "currency": "THB",
                "subscription_plan_id": str(plan.id),
                "subscription_status": "ACTIVE",
            },
            "owner": {
                "email": owner_email,
                "first_name": "Gym",
                "last_name": "Owner",
            },
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_IDEMPOTENCY_KEY=f"tenant-settings-{uuid4()}",
    )
    assert response.status_code == 201, response.json()
    return response.json()["data"]


def staff_token(membership: GymMembership) -> str:
    return create_staff_session(user=membership.user, membership=membership).access_token


def auth_headers(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def add_staff(gym: Gym, *, email: str, role_key: str) -> GymMembership:
    user = User.objects.create_user(email, PASSWORD)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        role = Role.objects.get(gym=gym, key=role_key)
        GymMembershipRole.objects.create(gym=gym, gym_membership=membership, role=role)
    membership.user = user
    return membership


@override_settings(**jwt_settings())
def test_tenant_gym_and_settings_are_scoped_and_owner_gated() -> None:
    client, token = platform_token("tenant-settings-platform@example.test")
    gym_a_payload = provision(
        client,
        token,
        code="SET-A",
        owner_email="owner-a@example.test",
    )
    gym_b_payload = provision(
        client,
        token,
        code="SET-B",
        owner_email="owner-b@example.test",
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym_a, email="manager-a@example.test", role_key="MANAGER")
    receptionist = add_staff(gym_a, email="reception-a@example.test", role_key="RECEPTIONIST")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    original_name = gym_b.name
    original_next = GymSettings.objects.get(gym=gym_a).next_member_number

    anonymous = APIClient().get("/api/v1/gym/")
    assert anonymous.status_code == 403
    assert anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"

    platform = client.get("/api/v1/gym/", **auth_headers(token))
    assert platform.status_code == 401
    assert platform.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    profile = client.get("/api/v1/gym/", **owner_headers)
    assert profile.status_code == 200, profile.json()
    profile_data = profile.json()["data"]
    assert set(profile_data) == set(GYM_PROFILE_FIELDS) | {
        "id",
        "code",
        "status",
        "subscription_plan_id",
        "subscription_plan_name",
        "subscription_status",
        "subscription_starts_at",
        "subscription_ends_at",
        "created_at",
        "updated_at",
    }
    assert profile_data["id"] == str(gym_a.id)
    assert profile_data["code"] == "SET-A"
    assert profile_data["status"] == Gym.Status.ACTIVE
    assert profile_data["currency"] == "THB"
    assert profile_data["subscription_plan_id"] == str(gym_a.subscription_plan_id)
    assert profile_data["subscription_plan_name"] == "Trial"
    assert profile_data["subscription_status"] == Gym.SubscriptionStatus.ACTIVE

    reception_read = client.get("/api/v1/gym/", **reception_headers)
    assert reception_read.status_code == 200
    reception_write = client.patch(
        "/api/v1/gym/",
        {"name": "Front Desk Rename"},
        format="json",
        **reception_headers,
    )
    assert reception_write.status_code == 403

    manager_profile = client.patch(
        "/api/v1/gym/",
        {
            "name": "Manager Rename",
            "timezone": "Asia/Rangoon",
            "code": "NOPE",
            "status": "SUSPENDED",
        },
        format="json",
        **manager_headers,
    )
    assert manager_profile.status_code == 400
    assert set(manager_profile.json()["error"]["fields"]) >= {"code", "status"}

    manager_profile = client.patch(
        "/api/v1/gym/",
        {"name": "Manager Rename", "timezone": "Asia/Bangkok", "phone": "091111111"},
        format="json",
        **manager_headers,
    )
    assert manager_profile.status_code == 200, manager_profile.json()
    assert manager_profile.json()["data"]["timezone"] == "Asia/Bangkok"
    assert manager_profile.json()["data"]["code"] == "SET-A"
    assert manager_profile.json()["data"]["status"] == Gym.Status.ACTIVE
    assert manager_profile.json()["data"]["subscription_status"] == Gym.SubscriptionStatus.ACTIVE
    gym_a.refresh_from_db()
    assert gym_a.name == "Manager Rename"
    assert gym_a.code == "SET-A"
    assert gym_a.status == Gym.Status.ACTIVE
    assert gym_a.subscription_status == Gym.SubscriptionStatus.ACTIVE
    gym_b.refresh_from_db()
    assert gym_b.name == original_name

    other_profile = client.get("/api/v1/gym/", **other_headers)
    assert other_profile.status_code == 200
    assert other_profile.json()["data"]["name"] == original_name

    currency = client.patch(
        "/api/v1/gym/",
        {"currency": "USD"},
        format="json",
        **owner_headers,
    )
    assert currency.status_code == 400
    gym_a.refresh_from_db()
    assert gym_a.currency == "THB"

    settings_read = client.get("/api/v1/settings/", **manager_headers)
    assert settings_read.status_code == 200, settings_read.json()
    assert settings_read.json()["data"]["gym_id"] == str(gym_a.id)
    assert settings_read.json()["data"]["require_separate_finance_approval"] is True
    assert settings_read.json()["data"]["next_member_number"] == original_next
    assert client.get("/api/v1/settings/", **reception_headers).status_code == 403

    denied = client.patch(
        "/api/v1/settings/",
        {"week_starts_on": 0},
        format="json",
        **manager_headers,
    )
    assert denied.status_code == 403
    manager_role = Role.objects.get(gym=gym_a, key="MANAGER")
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        with pytest.raises(ProgrammingError, match=r"role_guard: settings\.manage"):
            with transaction.atomic():
                RolePermission.objects.create(
                    gym=gym_a,
                    role=manager_role,
                    permission=Permission.objects.get(code="settings.manage"),
                    created_by_membership_id=owner_a.id,
                )
    assert not RolePermission.objects.filter(
        gym=gym_a, role=manager_role, permission__code="settings.manage"
    ).exists()
    still_denied = client.patch(
        "/api/v1/settings/",
        {"week_starts_on": 0, "require_separate_finance_approval": False},
        format="json",
        **manager_headers,
    )
    assert still_denied.status_code == 403
    assert GymSettings.objects.get(gym=gym_a).week_starts_on == 1
    assert GymSettings.objects.get(gym=gym_a).require_separate_finance_approval is True

    owner_denied = client.patch(
        "/api/v1/settings/",
        {
            "week_starts_on": 0,
            "next_member_number": 50,
            "gym_id": str(gym_b.id),
        },
        format="json",
        **owner_headers,
    )
    assert owner_denied.status_code == 400
    assert set(owner_denied.json()["error"]["fields"]) >= {"gym_id", "next_member_number"}
    approval_denied = client.patch(
        "/api/v1/settings/",
        {"require_separate_finance_approval": False, "week_starts_on": 0},
        format="json",
        **owner_headers,
    )
    assert approval_denied.status_code == 400
    assert "require_separate_finance_approval" in approval_denied.json()["error"]["fields"]
    assert GymSettings.objects.get(gym=gym_a).week_starts_on == 1

    before_audits = AuditLog.objects.filter(gym=gym_a, action="GYM_SETTINGS_UPDATED").count()
    updated = client.patch(
        "/api/v1/settings/",
        {
            "week_starts_on": 0,
            "member_number_prefix": "NORTH",
            "membership_expiry_warning_days": [30, 0, 7],
            "require_separate_finance_approval": True,
        },
        format="json",
        **owner_headers,
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["membership_expiry_warning_days"] == [0, 7, 30]
    assert updated.json()["data"]["updated_by_membership_id"] == str(owner_a.id)
    assert updated.json()["data"]["next_member_number"] == original_next
    settings_row = GymSettings.objects.get(gym=gym_a)
    assert settings_row.week_starts_on == 0
    assert settings_row.member_number_prefix == "NORTH"
    assert settings_row.updated_by_membership_id == owner_a.id
    assert settings_row.require_separate_finance_approval is True
    assert GymSettings.objects.get(gym=gym_b).week_starts_on == 1

    audit = AuditLog.objects.get(gym=gym_a, action="GYM_SETTINGS_UPDATED")
    assert audit.module == "settings"
    assert audit.actor_membership_id == owner_a.id
    assert set(audit.new_values) == {
        "week_starts_on",
        "member_number_prefix",
        "membership_expiry_warning_days",
    }
    assert audit.new_values["membership_expiry_warning_days"] == [0, 7, 30]
    assert "next_member_number" not in audit.new_values
    assert "password" not in audit.new_values
    assert AuditLog.objects.filter(gym=gym_a, action="GYM_SETTINGS_UPDATED").count() == (
        before_audits + 1
    )

    unchanged = client.patch(
        "/api/v1/settings/",
        {"week_starts_on": 0},
        format="json",
        **owner_headers,
    )
    assert unchanged.status_code == 200
    assert (
        AuditLog.objects.filter(gym=gym_a, action="GYM_SETTINGS_UPDATED").count()
        == before_audits + 1
    )

    profile_audit = AuditLog.objects.get(gym=gym_a, action="GYM_PROFILE_UPDATED")
    assert profile_audit.module == "gym"
    assert set(profile_audit.new_values) == {"name", "timezone", "phone"}
    assert "code" not in profile_audit.new_values
    assert "status" not in profile_audit.new_values

    gym_a.status = Gym.Status.SUSPENDED
    gym_a.save(update_fields=["status", "updated_at"])
    suspended_gym = client.get("/api/v1/gym/", **owner_headers)
    assert suspended_gym.status_code == 401
    assert suspended_gym.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    gym_a.status = Gym.Status.ACTIVE
    gym_a.save(update_fields=["status", "updated_at"])
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        receptionist.status = GymMembership.Status.SUSPENDED
        receptionist.save(update_fields=["status", "updated_at"])
    suspended_membership = client.get("/api/v1/gym/", **reception_headers)
    assert suspended_membership.status_code == 401
    assert suspended_membership.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@override_settings(**jwt_settings())
def test_concurrent_settings_updates_keep_both_fields() -> None:
    client, token = platform_token("tenant-settings-concurrent@example.test")
    payload = provision(
        client,
        token,
        code="SET-C",
        owner_email="owner-c@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    original_next = GymSettings.objects.get(gym=gym).next_member_number
    barrier = Barrier(2, timeout=15)

    def update(changes: dict[str, object]) -> None:
        connections.close_all()
        try:
            barrier.wait()
            update_gym_settings(
                gym_id=gym.id,
                data=changes,
                actor=owner.user,
                actor_membership_id=owner.id,
                request_id="req_concurrent_settings",
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(update, {"week_starts_on": 0}),
            pool.submit(update, {"receipt_number_prefix": "NORTH"}),
        ]
        for future in futures:
            future.result()

    settings_row = GymSettings.objects.get(gym=gym)
    assert settings_row.week_starts_on == 0
    assert settings_row.receipt_number_prefix == "NORTH"
    assert settings_row.next_member_number == original_next
    assert settings_row.require_separate_finance_approval is True
    assert settings_row.updated_by_membership_id == owner.id


def test_runtime_role_cannot_cross_gyms_or_change_lifecycle() -> None:
    gym_a = Gym.objects.create(
        code="RUN-A",
        name="Runtime A",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
        currency="THB",
    )
    gym_b = Gym.objects.create(
        code="RUN-B",
        name="Runtime B",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
        currency="THB",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymSettings.objects.create(gym=gym_a, receipt_number_prefix="RUN-A")
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        GymSettings.objects.create(gym=gym_b, receipt_number_prefix="RUN-B", week_starts_on=1)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_column_privilege('gym_runtime', 'tenants_gym', 'name', 'UPDATE'),
              has_column_privilege('gym_runtime', 'tenants_gym', 'status', 'UPDATE'),
              has_column_privilege('gym_runtime', 'tenants_gym', 'code', 'UPDATE'),
              has_column_privilege('gym_runtime', 'tenants_gym', 'currency', 'UPDATE')
            """
        )
        assert cursor.fetchone() == (True, False, False, True)

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SELECT id FROM tenants_gym WHERE id = %s FOR UPDATE", [gym_a.id])
        assert cursor.fetchone()[0] == gym_a.id
        cursor.execute(
            "UPDATE tenants_gym SET name = %s WHERE id = %s",
            ["Runtime Renamed", gym_a.id],
        )
        cursor.execute("SELECT count(*) FROM tenants_gym_settings")
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "UPDATE tenants_gym_settings SET week_starts_on = 4 WHERE gym_id = %s",
            [gym_b.id],
        )
        cursor.execute("SELECT count(*) FROM tenants_gym_settings WHERE gym_id = %s", [gym_b.id])
        assert cursor.fetchone()[0] == 0

    gym_a.refresh_from_db()
    gym_b.refresh_from_db()
    assert gym_a.name == "Runtime Renamed"
    assert gym_b.name == "Runtime B"
    assert GymSettings.objects.get(gym=gym_b).week_starts_on == 1

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute(
                "UPDATE tenants_gym SET status = 'SUSPENDED' WHERE id = %s",
                [gym_a.id],
            )

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("UPDATE tenants_gym SET name = 'Hijack' WHERE id = %s", [gym_b.id])

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("UPDATE tenants_gym SET name = 'No Context' WHERE id = %s", [gym_a.id])

    gym_a.refresh_from_db()
    gym_b.refresh_from_db()
    assert gym_a.name == "Runtime Renamed"
    assert gym_a.status == Gym.Status.ACTIVE
    assert gym_b.name == "Runtime B"
