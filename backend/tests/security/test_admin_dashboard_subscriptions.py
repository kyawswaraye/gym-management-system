from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    Permission,
    SubscriptionPlan,
    SubscriptionPlanFeature,
)
from apps.tenants.services import platform_dashboard_counts
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
PLATFORM_PERMISSIONS = (
    "platform.dashboard.view",
    "platform.subscriptions.manage",
    "platform.settings.manage",
    "platform.gyms.view",
)
FORBIDDEN_DASHBOARD_SQL = (
    "tenants_gym_membership",
    "tenants_gym_feature_entitlement",
    "tenants_subscription_plan_feature",
    "members_member",
    "members_membership",
    "members_payment",
    "users_user",
)
LIST_FIELDS = {"id", "code", "name", "status", "price", "currency", "billing_interval"}
DETAIL_FIELDS = LIST_FIELDS | {"created_at", "updated_at"}
PII = {
    "email": "hidden-owner@example.test",
    "phone": "+19995550123",
    "legal_name": "Hidden Legal Entity",
    "name": "Hidden Gym Name",
    "address_line1": "Secret Address Line",
}


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
        "REFRESH_TOKEN_PEPPER": "platform-dashboard-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"platform-dashboard-key": public_pem},
        "JWT_KEY_ID": "platform-dashboard-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


def grant_platform_permissions(*active: str) -> None:
    active_codes = set(active)
    for code in PLATFORM_PERMISSIONS:
        Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "platform",
                "description": code,
                "risk_level": Permission.Risk.CRITICAL,
                "is_active": code in active_codes,
            },
        )


def platform_client(email: str) -> tuple[APIClient, str, User]:
    admin = User.objects.create_superuser(email=email, password=PASSWORD)
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {"email": admin.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    return client, login.json()["data"]["access_token"], admin


def auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def reset_plan_catalog() -> None:
    """Remove migration-seeded plans so status counts are exact."""

    SubscriptionPlanFeature.objects.all().delete()
    SubscriptionPlan.objects.all().delete()


def create_plan(code: str, *, status: str = SubscriptionPlan.Status.ACTIVE) -> SubscriptionPlan:
    return SubscriptionPlan.objects.create(
        code=code,
        name=code.title(),
        status=status,
        price=Decimal("10.00"),
        currency="THB",
        billing_interval="MONTHLY",
    )


def create_gym(
    *,
    code: str,
    status: str,
    subscription_status: str,
    plan: SubscriptionPlan | None,
) -> Gym:
    return Gym.objects.create(
        code=code,
        name=PII["name"],
        legal_name=PII["legal_name"],
        email=PII["email"],
        phone=PII["phone"],
        address_line1=PII["address_line1"],
        status=status,
        subscription_status=subscription_status,
        subscription_plan=plan,
    )


def staff_access_token() -> str:
    gym = create_gym(
        code="STAFF-DASH",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
        plan=create_plan("STAFF-PLAN"),
    )
    user = User.objects.create_user("staff-dashboard@example.test", PASSWORD)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    return create_staff_session(user=user, membership=membership).access_token


@override_settings(**jwt_settings())
def test_dashboard_counts_are_exact_and_contain_no_tenant_business_data() -> None:
    reset_plan_catalog()
    grant_platform_permissions("platform.dashboard.view")
    client, token, _admin = platform_client("dashboard-counts@example.test")
    empty = client.get("/api/v1/admin/dashboard/", **auth(token))
    assert empty.status_code == 200, empty.json()
    assert empty.json()["data"] == {
        "gym_statuses": {"total": 0, "PENDING": 0, "ACTIVE": 0, "SUSPENDED": 0, "CLOSED": 0},
        "gym_subscription_statuses": {"TRIAL": 0, "ACTIVE": 0, "PAST_DUE": 0, "CANCELLED": 0},
        "subscription_plan_statuses": {"ACTIVE": 0, "RETIRED": 0},
    }

    active_a = create_plan("DASH-A")
    create_plan("DASH-B")
    retired = create_plan("DASH-R", status=SubscriptionPlan.Status.RETIRED)
    create_gym(
        code="DASH-1",
        status=Gym.Status.PENDING,
        subscription_status=Gym.SubscriptionStatus.TRIAL,
        plan=active_a,
    )
    create_gym(
        code="DASH-2",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
        plan=active_a,
    )
    create_gym(
        code="DASH-3",
        status=Gym.Status.SUSPENDED,
        subscription_status=Gym.SubscriptionStatus.PAST_DUE,
        plan=active_a,
    )
    create_gym(
        code="DASH-4",
        status=Gym.Status.CLOSED,
        subscription_status=Gym.SubscriptionStatus.CANCELLED,
        plan=active_a,
    )
    create_gym(
        code="DASH-5",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.TRIAL,
        plan=retired,
    )
    create_gym(
        code="DASH-6",
        status=Gym.Status.SUSPENDED,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
        plan=retired,
    )

    with CaptureQueriesContext(connection) as captured:
        counts = platform_dashboard_counts()
    assert len(captured) == 2
    sql = " ".join(query["sql"] for query in captured).lower()
    assert "count(" in sql
    assert all(table not in sql for table in FORBIDDEN_DASHBOARD_SQL)

    response = client.get("/api/v1/admin/dashboard/", **auth(token))
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert data == counts
    assert data == {
        "gym_statuses": {"total": 6, "PENDING": 1, "ACTIVE": 2, "SUSPENDED": 2, "CLOSED": 1},
        "gym_subscription_statuses": {"TRIAL": 2, "ACTIVE": 2, "PAST_DUE": 1, "CANCELLED": 1},
        "subscription_plan_statuses": {"ACTIVE": 2, "RETIRED": 1},
    }
    rendered = json.dumps(response.json())
    for secret in (*PII.values(), "DASH-A", "DASH-1", "hidden-owner"):
        assert secret not in rendered
    assert PlatformAuditLog.objects.filter(action="SUBSCRIPTION_PLAN_UPDATED").count() == 0


@override_settings(**jwt_settings())
def test_dashboard_and_subscription_permissions_are_isolated() -> None:
    reset_plan_catalog()
    grant_platform_permissions("platform.subscriptions.manage")
    client, token, _admin = platform_client("dashboard-isolation@example.test")
    plan = create_plan("ISO-PLAN")
    staff_token = staff_access_token()
    headers = auth(token)

    dashboard = client.get("/api/v1/admin/dashboard/", **headers)
    assert dashboard.status_code == 403
    assert dashboard.json()["error"]["code"] == "PERMISSION_DENIED"

    renamed = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"name": "Isolated Plan"},
        format="json",
        **headers,
    )
    assert renamed.status_code == 200, renamed.json()

    Permission.objects.filter(code="platform.subscriptions.manage").update(is_active=False)
    Permission.objects.filter(code="platform.dashboard.view").update(is_active=True)
    dashboard_only = client.get("/api/v1/admin/dashboard/", **headers)
    subscription_denied = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"name": "Should Fail"},
        format="json",
        **headers,
    )
    assert dashboard_only.status_code == 200
    assert subscription_denied.status_code == 403
    assert subscription_denied.json()["error"]["code"] == "PERMISSION_DENIED"
    plan.refresh_from_db()
    assert plan.name == "Isolated Plan"

    Permission.objects.filter(code__startswith="platform.").update(is_active=False)
    Permission.objects.filter(code="platform.settings.manage").update(is_active=True)
    settings_dashboard = client.get("/api/v1/admin/dashboard/", **headers)
    settings_subscription = client.get("/api/v1/admin/subscriptions/", **headers)
    settings_all = client.get("/api/v1/admin/subscriptions/?status=ALL", **headers)
    assert settings_dashboard.status_code == 403
    assert settings_subscription.status_code == 403
    assert settings_all.status_code == 403
    assert settings_all.json()["error"]["code"] == "PERMISSION_DENIED"

    staff_dashboard = client.get("/api/v1/admin/dashboard/", **auth(staff_token))
    staff_subscription = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"name": "Staff Override"},
        format="json",
        **auth(staff_token),
    )
    anonymous = APIClient().get("/api/v1/admin/dashboard/")
    assert staff_dashboard.status_code == 401
    assert staff_dashboard.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert staff_subscription.status_code == 401
    assert anonymous.status_code == 403
    assert anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"
    plan.refresh_from_db()
    assert plan.name == "Isolated Plan"


@override_settings(**jwt_settings())
def test_subscription_patch_audits_real_changes_and_preserves_entitlements() -> None:
    reset_plan_catalog()
    grant_platform_permissions("platform.subscriptions.manage")
    client, token, admin = platform_client("subscription-patch@example.test")
    headers = auth(token)
    plan = create_plan("PATCH-PLAN")
    other = create_plan("PATCH-OTHER", status=SubscriptionPlan.Status.RETIRED)
    feature = Feature.objects.create(code="patch-feature", name="Patch Feature")
    mapping = SubscriptionPlanFeature.objects.create(
        subscription_plan=plan,
        feature=feature,
        enabled=True,
        limit_value=9,
    )
    trial_gym = create_gym(
        code="PATCH-TRIAL",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.TRIAL,
        plan=plan,
    )
    active_gym = create_gym(
        code="PATCH-ACTIVE",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
        plan=plan,
    )
    past_due_gym = create_gym(
        code="PATCH-DUE",
        status=Gym.Status.SUSPENDED,
        subscription_status=Gym.SubscriptionStatus.PAST_DUE,
        plan=other,
    )
    with transaction.atomic():
        set_gym_context(gym_id=trial_gym.id)
        entitlement = GymFeatureEntitlement.objects.create(
            gym=trial_gym,
            feature=feature,
            enabled=True,
            limit_value=9,
            source=GymFeatureEntitlement.Source.PLAN,
        )
    entitlement_before = (
        entitlement.enabled,
        entitlement.limit_value,
        entitlement.source,
        entitlement.feature_id,
        entitlement.updated_at,
        entitlement.gym_id,
    )
    mapping_before = (mapping.enabled, mapping.limit_value, mapping.feature_id, mapping.updated_at)

    rejected = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {
            "name": "Hijacked",
            "id": str(uuid4()),
            "code": "HIJACK",
            "currency": "USD",
            "features": [{"feature_id": str(feature.id), "enabled": False}],
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
            "actor_user": str(admin.id),
        },
        format="json",
        **headers,
    )
    assert rejected.status_code == 400
    plan.refresh_from_db()
    assert plan.name == "Patch-Plan"
    assert plan.code == "PATCH-PLAN"
    assert plan.currency == "THB"
    assert PlatformAuditLog.objects.filter(action="SUBSCRIPTION_PLAN_UPDATED").count() == 0

    empty = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {},
        format="json",
        **headers,
    )
    negative = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"price": "-1.00"},
        format="json",
        **headers,
    )
    assert empty.status_code == 400
    assert negative.status_code == 400

    missing = client.patch(
        f"/api/v1/admin/subscriptions/{uuid4()}/",
        {"name": "Missing"},
        format="json",
        **headers,
    )
    assert missing.status_code == 404

    unchanged_at = plan.updated_at
    noop = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"name": plan.name, "price": "10.00", "billing_interval": "MONTHLY", "status": "ACTIVE"},
        format="json",
        **headers,
    )
    assert noop.status_code == 200, noop.json()
    assert noop.json()["message"] == "Subscription plan unchanged"
    assert set(noop.json()["data"]) == DETAIL_FIELDS
    plan.refresh_from_db()
    assert plan.updated_at == unchanged_at
    assert PlatformAuditLog.objects.filter(action="SUBSCRIPTION_PLAN_UPDATED").count() == 0

    blocked = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"status": "RETIRED", "name": "Should Not Apply"},
        format="json",
        **headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SUBSCRIPTION_PLAN_IN_USE"
    assert blocked.json()["error"]["fields"] == {}
    blocked_body = json.dumps(blocked.json())
    assert PII["email"] not in blocked_body
    assert str(trial_gym.id) not in blocked_body
    assert str(active_gym.id) not in blocked_body
    plan.refresh_from_db()
    assert plan.status == SubscriptionPlan.Status.ACTIVE
    assert plan.name == "Patch-Plan"
    assert PlatformAuditLog.objects.filter(action="SUBSCRIPTION_PLAN_UPDATED").count() == 0

    updated = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"name": "Growth", "price": "25.50"},
        format="json",
        **headers,
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["message"] == "Subscription plan updated"
    body = updated.json()["data"]
    assert set(body) == DETAIL_FIELDS
    assert body["name"] == "Growth"
    assert body["price"] == "25.50"
    assert body["code"] == "PATCH-PLAN"
    assert body["currency"] == "THB"
    assert body["status"] == "ACTIVE"
    assert body["billing_interval"] == "MONTHLY"
    plan.refresh_from_db()
    audit = PlatformAuditLog.objects.get(action="SUBSCRIPTION_PLAN_UPDATED", target_id=plan.id)
    assert audit.actor_user_id == admin.id
    assert audit.module == "platform"
    assert audit.target_type == "SubscriptionPlan"
    assert audit.target_gym_id is None
    assert audit.outcome == "SUCCESS"
    assert audit.old_values == {"name": "Patch-Plan", "price": "10.00"}
    assert audit.new_values == {"name": "Growth", "price": "25.50"}
    assert set(audit.old_values) == set(audit.new_values) == {"name", "price"}

    listed = client.get("/api/v1/admin/subscriptions/", **headers)
    assert listed.status_code == 200, listed.json()
    items = listed.json()["data"]
    assert [item["id"] for item in items] == [str(plan.id)]
    assert items[0]["name"] == "Growth"
    assert items[0]["code"] == "PATCH-PLAN"
    assert items[0]["status"] == "ACTIVE"
    assert set(items[0]) == LIST_FIELDS
    assert str(other.id) not in json.dumps(items)

    retired = client.patch(
        f"/api/v1/admin/subscriptions/{other.id}/",
        {"status": "RETIRED"},
        format="json",
        **headers,
    )
    assert retired.status_code == 200, retired.json()
    assert retired.json()["message"] == "Subscription plan unchanged"
    past_due_gym.subscription_plan = plan
    past_due_gym.save(update_fields=["subscription_plan", "updated_at"])
    trial_gym.subscription_status = Gym.SubscriptionStatus.PAST_DUE
    active_gym.subscription_status = Gym.SubscriptionStatus.CANCELLED
    trial_gym.save(update_fields=["subscription_status", "updated_at"])
    active_gym.save(update_fields=["subscription_status", "updated_at"])
    retire = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"status": "RETIRED"},
        format="json",
        **headers,
    )
    assert retire.status_code == 200, retire.json()
    assert retire.json()["data"]["status"] == "RETIRED"
    retire_audit = PlatformAuditLog.objects.get(
        action="SUBSCRIPTION_PLAN_UPDATED",
        target_id=plan.id,
        new_values__status="RETIRED",
    )
    assert retire_audit.old_values == {"status": "ACTIVE"}
    assert retire_audit.new_values == {"status": "RETIRED"}

    entitlement.refresh_from_db()
    mapping.refresh_from_db()
    trial_gym.refresh_from_db()
    assert (
        entitlement.enabled,
        entitlement.limit_value,
        entitlement.source,
        entitlement.feature_id,
        entitlement.updated_at,
        entitlement.gym_id,
    ) == entitlement_before
    assert (
        mapping.enabled,
        mapping.limit_value,
        mapping.feature_id,
        mapping.updated_at,
    ) == mapping_before
    assert trial_gym.subscription_plan_id == plan.id
    assert trial_gym.subscription_status == Gym.SubscriptionStatus.PAST_DUE

    hidden = client.get("/api/v1/admin/subscriptions/", **headers)
    active_only = client.get("/api/v1/admin/subscriptions/?status=ACTIVE", **headers)
    retired_only = client.get("/api/v1/admin/subscriptions/?status=RETIRED", **headers)
    everything = client.get("/api/v1/admin/subscriptions/?status=ALL", **headers)
    invalid_status = client.get("/api/v1/admin/subscriptions/?status=ARCHIVED", **headers)
    lowercase_status = client.get("/api/v1/admin/subscriptions/?status=all", **headers)
    blank_status = client.get("/api/v1/admin/subscriptions/?status=", **headers)
    unknown_param = client.get("/api/v1/admin/subscriptions/?status=ALL&limit=10", **headers)
    assert hidden.status_code == 200, hidden.json()
    assert hidden.json()["data"] == []
    assert active_only.status_code == 200
    assert active_only.json()["data"] == []
    assert retired_only.status_code == 200, retired_only.json()
    assert everything.status_code == 200, everything.json()
    expected_ids = [str(plan.id), str(other.id)]
    assert [item["id"] for item in retired_only.json()["data"]] == expected_ids
    assert [item["id"] for item in everything.json()["data"]] == expected_ids
    assert [item["status"] for item in everything.json()["data"]] == ["RETIRED", "RETIRED"]
    assert all(set(item) == LIST_FIELDS for item in everything.json()["data"])
    catalog = json.dumps(everything.json()["data"])
    assert "patch-feature" not in catalog
    assert PII["email"] not in catalog
    assert invalid_status.status_code == 400
    assert lowercase_status.status_code == 400
    assert blank_status.status_code == 400
    assert unknown_param.status_code == 400
