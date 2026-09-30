from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.apps import apps
from django.core.cache import cache
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan, Payment
from apps.members.services import (
    cancel_membership,
    create_membership,
    gym_today,
    resume_membership,
    suspend_membership,
)
from apps.notifications.models import Notification
from apps.notifications.services import schedule_membership_expiry_notifications
from apps.tenants.models import (
    GymFeatureEntitlement,
    GymSettings,
    Permission,
    SystemRoleTemplate,
)
from apps.tenants.services import provision_gym
from apps.users.models import User

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


def _jwt_settings() -> dict[str, object]:
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
        "REFRESH_TOKEN_PEPPER": "tenant-isolation-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"isolation-key": public_pem},
        "JWT_KEY_ID": "isolation-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


JWT_SETTINGS = _jwt_settings()


def _client() -> APIClient:
    api = APIClient()
    api.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return api


def _token(email: str) -> str:
    cache.clear()
    response = _client().post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


def _auth_get(api: APIClient, path: str, token: str, **headers: str):
    return api.get(path, HTTP_AUTHORIZATION=f"Bearer {token}", **headers)


def _ensure_owner_permission_catalog() -> None:
    codes = [
        "members.view",
        "members.create",
        "members.update",
        "members.archive",
        "memberships.view",
        "memberships.cancel",
        "payments.view",
        "payments.void",
        "reports.view",
    ]
    permissions = [
        Permission.objects.get_or_create(
            code=code,
            defaults={
                "module": code.split(".", 1)[0],
                "description": code,
                "risk_level": Permission.Risk.LOW,
            },
        )[0]
        for code in codes
    ]
    template, _ = SystemRoleTemplate.objects.get_or_create(
        key="OWNER",
        defaults={
            "name": "Owner",
            "description": "Protected owner role",
            "version": 1,
        },
    )
    template.permissions.add(*permissions)


@override_settings(**JWT_SETTINGS)
def test_tenant_a_api_cannot_access_tenant_b_core_operations() -> None:
    _ensure_owner_permission_catalog()
    owner_a = User.objects.create_user("owner-a@example.test", PASSWORD)
    owner_b = User.objects.create_user("owner-b@example.test", PASSWORD)
    tenant_a = provision_gym(code="ISO-A", name="Tenant A", owner_email=owner_a.email)
    tenant_b = provision_gym(code="ISO-B", name="Tenant B", owner_email=owner_b.email)

    with transaction.atomic():
        set_gym_context(gym_id=tenant_b.gym.id)
        member_b = Member.objects.create(
            gym=tenant_b.gym,
            member_number="B-000001",
            first_name="Tenant B Member",
            join_date=date.today(),
            created_by_membership_id=tenant_b.owner_membership.id,
            updated_by_membership_id=tenant_b.owner_membership.id,
        )
        plan_b = MembershipPlan.objects.create(
            gym=tenant_b.gym,
            code="B-MONTH",
            name="Tenant B Monthly",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("1000.00"),
            currency=tenant_b.gym.currency,
            created_by_membership_id=tenant_b.owner_membership.id,
            updated_by_membership_id=tenant_b.owner_membership.id,
        )
        membership_b = Membership.objects.create(
            gym=tenant_b.gym,
            member=member_b,
            plan=plan_b,
            plan_code=plan_b.code,
            plan_name=plan_b.name,
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            start_date=date.today(),
            expiry_date=date.today(),
            list_price=Decimal("1000.00"),
            discount_amount=Decimal("0.00"),
            final_amount=Decimal("1000.00"),
            currency=tenant_b.gym.currency,
            payment_status=Membership.PaymentStatus.PAID,
            status=Membership.Status.ACTIVE,
            revenue_source=Membership.RevenueSource.NEW_MEMBER,
            created_by_membership_id=tenant_b.owner_membership.id,
        )
        payment_b = Payment.objects.create(
            gym=tenant_b.gym,
            member=member_b,
            membership=membership_b,
            purpose=Payment.Purpose.MEMBERSHIP,
            description="Tenant B private revenue",
            amount=Decimal("1000.00"),
            currency=tenant_b.gym.currency,
            method=Payment.Method.CASH,
            business_date=date.today(),
            received_at=timezone.now(),
            status=Payment.Status.POSTED,
            created_by_membership_id=tenant_b.owner_membership.id,
            updated_by_membership_id=tenant_b.owner_membership.id,
        )

    # Forced RLS independently hides the rows and turns direct update/delete
    # attempts into no-ops when the runtime role is scoped to Tenant A.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(tenant_a.gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        for table, row_id in (
            ("members_member", member_b.id),
            ("members_membership", membership_b.id),
            ("members_payment", payment_b.id),
        ):
            cursor.execute(f"SELECT count(*) FROM {table} WHERE id = %s", [row_id])
            assert cursor.fetchone()[0] == 0
            cursor.execute(f"UPDATE {table} SET updated_at = now() WHERE id = %s", [row_id])
            assert cursor.rowcount == 0
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(f"DELETE FROM {table} WHERE id = %s", [row_id])

    token_a = _token(owner_a.email)
    token_b = _token(owner_b.email)
    api = _client()

    # List selectors reject caller-supplied tenant parameters, and every other
    # selector remains bound to the signed token despite a forged tenant header.
    members_a = _auth_get(
        api,
        f"/api/v1/members/?gym_id={tenant_b.gym.id}",
        token_a,
        HTTP_X_GYM_ID=str(tenant_b.gym.id),
    )
    assert members_a.status_code == 400, members_a.json()
    assert "gym_id" in members_a.json()["error"]["fields"]
    members_a_with_forged_header = _auth_get(
        api,
        "/api/v1/members/",
        token_a,
        HTTP_X_GYM_ID=str(tenant_b.gym.id),
    )
    assert members_a_with_forged_header.status_code == 200
    assert members_a_with_forged_header.json()["data"] == []
    assert _auth_get(api, "/api/v1/memberships/", token_a).json()["data"] == []
    assert _auth_get(api, "/api/v1/payments/", token_a).json()["data"] == []
    assert _auth_get(api, "/api/v1/members/", token_b).json()["data"][0]["id"] == str(member_b.id)

    dashboard_a = _auth_get(api, "/api/v1/dashboard/?range=today", token_a)
    dashboard_b = _auth_get(api, "/api/v1/dashboard/?range=today", token_b)
    assert dashboard_a.status_code == dashboard_b.status_code == 200
    assert Decimal(str(dashboard_a.json()["data"]["income"])) == Decimal("0")
    assert Decimal(str(dashboard_b.json()["data"]["income"])) == payment_b.amount

    # Guessed Tenant B identifiers are hidden from Tenant A for reads and all
    # mutation/deletion-equivalent lifecycle actions.
    assert _auth_get(api, f"/api/v1/members/{member_b.id}/", token_a).status_code == 404
    assert (
        api.patch(
            f"/api/v1/members/{member_b.id}/",
            {"first_name": "Stolen"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_a}",
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/members/{member_b.id}/archive/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_a}",
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/memberships/{membership_b.id}/cancel/",
            {"reason": "cross-tenant attempt"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_a}",
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/payments/{payment_b.id}/void/",
            {"reason": "cross-tenant attempt"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_a}",
        ).status_code
        == 404
    )

    with transaction.atomic():
        set_gym_context(gym_id=tenant_b.gym.id)
        member_b.refresh_from_db()
        membership_b.refresh_from_db()
        payment_b.refresh_from_db()
        assert member_b.first_name == "Tenant B Member"
        assert member_b.status == Member.Status.ACTIVE
        assert membership_b.status == Membership.Status.ACTIVE
        assert payment_b.status == Payment.Status.POSTED


@override_settings(**JWT_SETTINGS)
def test_tenant_cannot_mass_assign_or_switch_gym_context() -> None:
    _ensure_owner_permission_catalog()
    owner_a = User.objects.create_user("switch-a@example.test", PASSWORD)
    owner_b = User.objects.create_user("switch-b@example.test", PASSWORD)
    tenant_a = provision_gym(code="SWITCH-A", name="Switch A", owner_email=owner_a.email)
    tenant_b = provision_gym(code="SWITCH-B", name="Switch B", owner_email=owner_b.email)
    token_a = _token(owner_a.email)
    api = _client()
    me = _auth_get(api, "/api/v1/auth/me/", token_a)
    assert "members.create" in me.json()["data"]["permissions"], me.json()

    forged_create = api.post(
        "/api/v1/members/",
        {
            "first_name": "Forged tenant",
            "join_date": str(date.today()),
            "gym_id": str(tenant_b.gym.id),
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token_a}",
        HTTP_X_GYM_ID=str(tenant_b.gym.id),
    )
    assert forged_create.status_code == 400, forged_create.json()

    # A single-gym login cannot use select-gym to jump to another user's
    # membership without a valid, user-bound selection ticket.
    switched = api.post(
        "/api/v1/auth/select-gym/",
        {"gym_membership_id": str(tenant_b.owner_membership.id)},
        format="json",
    )
    assert switched.status_code == 401

    with transaction.atomic():
        set_gym_context(gym_id=tenant_a.gym.id)
        assert not Member.objects.filter(gym=tenant_a.gym).exists()
    with transaction.atomic():
        set_gym_context(gym_id=tenant_b.gym.id)
        assert not Member.objects.filter(gym=tenant_b.gym).exists()


@override_settings(**JWT_SETTINGS)
def test_feature_entitlement_controls_access_independently_of_permissions() -> None:
    _ensure_owner_permission_catalog()
    owner = User.objects.create_user("entitlement-owner@example.test", PASSWORD)
    tenant = provision_gym(
        code="ENTITLEMENT",
        name="Entitlement Gym",
        owner_email=owner.email,
    )
    token = _token(owner.email)
    api = _client()

    def feature_codes() -> set[str]:
        response = _auth_get(api, "/api/v1/auth/me/", token)
        assert response.status_code == 200, response.json()
        data = response.json()["data"]
        assert "members.view" in data["permissions"]
        return {feature["feature__code"] for feature in data["features"]}

    assert "members" in feature_codes()
    assert _auth_get(api, "/api/v1/members/", token).status_code == 200

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        entitlement = GymFeatureEntitlement.objects.get(
            gym=tenant.gym,
            feature__code="members",
        )
        entitlement.enabled = False
        entitlement.save(update_fields=["enabled", "updated_at"])

    assert "members" not in feature_codes()
    assert _auth_get(api, "/api/v1/members/", token).status_code == 403

    now = timezone.now()
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        entitlement.enabled = True
        entitlement.effective_from = now + timedelta(days=1)
        entitlement.effective_until = None
        entitlement.save(
            update_fields=["enabled", "effective_from", "effective_until", "updated_at"]
        )

    assert "members" not in feature_codes()
    assert _auth_get(api, "/api/v1/members/", token).status_code == 403

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        entitlement.effective_from = now - timedelta(days=2)
        entitlement.effective_until = now - timedelta(days=1)
        entitlement.save(update_fields=["effective_from", "effective_until", "updated_at"])

    assert "members" not in feature_codes()
    assert _auth_get(api, "/api/v1/members/", token).status_code == 403

    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        entitlement.effective_until = now + timedelta(days=1)
        entitlement.save(update_fields=["effective_until", "updated_at"])

    assert "members" in feature_codes()
    assert _auth_get(api, "/api/v1/members/", token).status_code == 200


def test_membership_lifecycle_rejects_suspended_overlap() -> None:
    owner = User.objects.create_user("lifecycle-owner@example.test", PASSWORD)
    tenant = provision_gym(
        code="LIFECYCLE",
        name="Lifecycle Gym",
        owner_email=owner.email,
    )
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        today = gym_today(tenant.gym)
        member = Member.objects.create(
            gym=tenant.gym,
            member_number="LIFE-000001",
            first_name="Lifecycle",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        plan = MembershipPlan.objects.create(
            gym=tenant.gym,
            code="LIFE-30",
            name="Lifecycle 30",
            duration_value=30,
            duration_unit=MembershipPlan.DurationUnit.DAY,
            price=Decimal("100.00"),
            currency=tenant.gym.currency,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        membership = create_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            member=member,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
        )
        suspended = suspend_membership(
            gym=tenant.gym,
            membership_id=membership.id,
        )
        assert suspended.status == Membership.Status.SUSPENDED

        with pytest.raises(ValidationError, match="overlap"):
            create_membership(
                gym=tenant.gym,
                actor=tenant.owner_membership,
                member=member,
                plan=plan,
                start_date=today,
                discount_amount=Decimal("0"),
            )

        resumed = resume_membership(gym=tenant.gym, membership_id=membership.id)
        assert resumed.status == Membership.Status.ACTIVE


def test_future_membership_and_cancel_are_state_safe() -> None:
    owner = User.objects.create_user("future-owner@example.test", PASSWORD)
    tenant = provision_gym(
        code="FUTURE-LIFECYCLE",
        name="Future Lifecycle Gym",
        owner_email=owner.email,
    )
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        today = gym_today(tenant.gym)
        member = Member.objects.create(
            gym=tenant.gym,
            member_number="FUTURE-000001",
            first_name="Future",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        plan = MembershipPlan.objects.create(
            gym=tenant.gym,
            code="FUTURE-30",
            name="Future 30",
            duration_value=30,
            duration_unit=MembershipPlan.DurationUnit.DAY,
            price=Decimal("100.00"),
            currency=tenant.gym.currency,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        membership = create_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            member=member,
            plan=plan,
            start_date=today + timedelta(days=5),
            discount_amount=Decimal("0"),
        )
        assert membership.status == Membership.Status.PENDING

        cancelled = cancel_membership(
            gym=tenant.gym,
            membership_id=membership.id,
            reason="Changed plans",
        )
        original_cancelled_at = cancelled.cancelled_at
        with pytest.raises(ValidationError, match="cannot be cancelled"):
            cancel_membership(
                gym=tenant.gym,
                membership_id=membership.id,
                reason="Overwrite attempt",
            )
        cancelled.refresh_from_db()
        assert cancelled.cancelled_at == original_cancelled_at
        assert cancelled.cancellation_reason == "Changed plans"


def test_membership_status_migration_sets_rls_context() -> None:
    owner = User.objects.create_user("migration-owner@example.test", PASSWORD)
    tenant = provision_gym(
        code="MIGRATION-LIFECYCLE",
        name="Migration Lifecycle Gym",
        owner_email=owner.email,
    )
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        today = gym_today(tenant.gym)
        member = Member.objects.create(
            gym=tenant.gym,
            member_number="MIGRATION-000001",
            first_name="Migration",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        plan = MembershipPlan.objects.create(
            gym=tenant.gym,
            code="MIGRATION-30",
            name="Migration 30",
            duration_value=30,
            duration_unit=MembershipPlan.DurationUnit.DAY,
            price=Decimal("100.00"),
            currency=tenant.gym.currency,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        membership = Membership.objects.create(
            gym=tenant.gym,
            member=member,
            plan=plan,
            plan_code=plan.code,
            plan_name=plan.name,
            duration_value=plan.duration_value,
            duration_unit=plan.duration_unit,
            start_date=today + timedelta(days=5),
            expiry_date=today + timedelta(days=34),
            list_price=plan.price,
            discount_amount=Decimal("0"),
            final_amount=plan.price,
            currency=plan.currency,
            status=Membership.Status.ACTIVE,
            revenue_source=Membership.RevenueSource.NEW_MEMBER,
            created_by_membership_id=tenant.owner_membership.id,
        )

        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT ON tenants_gym TO gym_migration")
            cursor.execute("GRANT SELECT, UPDATE ON members_membership TO gym_migration")
            cursor.execute("GRANT EXECUTE ON FUNCTION app_current_gym_id() TO gym_migration")
            cursor.execute("SET LOCAL ROLE gym_migration")
        migration = import_module("apps.members.migrations.0006_membership_suspended_overlap")
        migration.normalize_statuses_and_check_overlaps(
            apps,
            SimpleNamespace(connection=connection),
        )
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        set_gym_context(gym_id=tenant.gym.id)
        membership.refresh_from_db()
        assert membership.status == Membership.Status.PENDING


def test_membership_expiry_notifications_are_deduplicated_and_email_only() -> None:
    owner = User.objects.create_user("expiry-owner@example.test", PASSWORD)
    tenant = provision_gym(
        code="EXPIRY-NOTICE",
        name="Expiry Notice Gym",
        owner_email=owner.email,
    )
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        today = gym_today(tenant.gym)
        settings = GymSettings.objects.get(gym=tenant.gym)
        settings.membership_expiry_email_enabled = True
        settings.membership_expiry_warning_days = [7]
        settings.save(
            update_fields=[
                "membership_expiry_email_enabled",
                "membership_expiry_warning_days",
                "updated_at",
            ]
        )
        plan = MembershipPlan.objects.create(
            gym=tenant.gym,
            code="NOTICE-8",
            name="Notice 8",
            duration_value=8,
            duration_unit=MembershipPlan.DurationUnit.DAY,
            price=Decimal("100.00"),
            currency=tenant.gym.currency,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        member = Member.objects.create(
            gym=tenant.gym,
            member_number="NOTICE-000001",
            first_name="Notice",
            email="notice@example.test",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        create_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            member=member,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
        )
        second_member = Member.objects.create(
            gym=tenant.gym,
            member_number="NOTICE-000002",
            first_name="Second Notice",
            email="second-notice@example.test",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        create_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            member=second_member,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
        )
        no_email = Member.objects.create(
            gym=tenant.gym,
            member_number="NOTICE-000003",
            first_name="No Email",
            join_date=today,
            created_by_membership_id=tenant.owner_membership.id,
            updated_by_membership_id=tenant.owner_membership.id,
        )
        create_membership(
            gym=tenant.gym,
            actor=tenant.owner_membership,
            member=no_email,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
        )

        assert (
            schedule_membership_expiry_notifications(
                gym=tenant.gym,
                today=today,
                batch_size=1,
            )
            == 1
        )
        assert (
            schedule_membership_expiry_notifications(
                gym=tenant.gym,
                today=today,
                batch_size=1,
            )
            == 1
        )
        assert (
            schedule_membership_expiry_notifications(
                gym=tenant.gym,
                today=today,
                batch_size=1,
            )
            == 0
        )
        assert Notification.objects.filter(gym=tenant.gym).count() == 2
        notification = Notification.objects.get(
            gym=tenant.gym,
            recipient_member=member,
        )
        assert notification.recipient_member_id == member.id
        assert notification.status == Notification.Status.PENDING
        assert notification.payload["warning_day"] == 7
        assert "email" not in notification.payload
