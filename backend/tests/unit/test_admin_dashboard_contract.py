from types import SimpleNamespace
from uuid import uuid4

from django.http import QueryDict

from apps.audit.views import PlatformAuditListView
from apps.tenants.models import Gym, SubscriptionPlan
from apps.tenants.serializers import (
    SUBSCRIPTION_LIST_STATUSES,
    AdminDashboardSerializer,
    AdminSubscriptionListQuerySerializer,
    AdminSubscriptionSerializer,
    AdminSubscriptionUpdateSerializer,
    GymStatusCountsSerializer,
    GymSubscriptionStatusCountsSerializer,
    SubscriptionPlanStatusCountsSerializer,
)
from apps.tenants.services import (
    GYM_STATUS_COUNT_FIELDS,
    GYM_SUBSCRIPTION_COUNT_FIELDS,
    PLAN_METADATA_FIELDS,
    PLAN_STATUS_COUNT_FIELDS,
)
from apps.tenants.views import (
    AdminDashboardView,
    AdminGymActivateView,
    AdminGymDetailView,
    AdminGymListCreateView,
    AdminGymLoginAccountsView,
    AdminGymOwnerCreateView,
    AdminGymSuspendView,
    AdminSubscriptionDetailView,
    AdminSubscriptionListView,
    GymSettingsView,
)
from apps.users.authentication import PlatformJWTAuthentication, StaffJWTAuthentication
from apps.users.models import RefreshSession
from apps.users.permissions import RequiresPermission, RequiresPlatformPermissions

PLAN_ID = "1d44221a-b537-4f0c-8128-4dc13fca6595"


def _permission_request(principal_type: str, codes: set[str]) -> SimpleNamespace:
    return SimpleNamespace(principal_type=principal_type, permission_codes=frozenset(codes))


def test_dashboard_counts_match_explicit_status_groups() -> None:
    assert tuple(GymStatusCountsSerializer().fields) == GYM_STATUS_COUNT_FIELDS
    assert tuple(GymSubscriptionStatusCountsSerializer().fields) == GYM_SUBSCRIPTION_COUNT_FIELDS
    assert tuple(SubscriptionPlanStatusCountsSerializer().fields) == PLAN_STATUS_COUNT_FIELDS
    assert set(GYM_STATUS_COUNT_FIELDS) == {"total", *Gym.Status.values}
    assert set(GYM_SUBSCRIPTION_COUNT_FIELDS) == set(Gym.SubscriptionStatus.values)
    assert set(PLAN_STATUS_COUNT_FIELDS) == set(SubscriptionPlan.Status.values)
    assert PLAN_METADATA_FIELDS == ("name", "status", "price", "billing_interval")


def test_dashboard_serializer_drops_business_and_pii_fields() -> None:
    payload = AdminDashboardSerializer(
        {
            "gym_statuses": {
                "total": 1,
                "PENDING": 1,
                "ACTIVE": 0,
                "SUSPENDED": 0,
                "CLOSED": 0,
                "email": "hidden-owner@example.test",
            },
            "gym_subscription_statuses": {
                "TRIAL": 1,
                "ACTIVE": 0,
                "PAST_DUE": 0,
                "CANCELLED": 0,
                "name": "Hidden Gym Name",
            },
            "subscription_plan_statuses": {"ACTIVE": 1, "RETIRED": 0, "price": "99.00"},
            "members": [{"email": "member@example.test"}],
            "revenue": "100.00",
        }
    ).data

    assert set(payload) == {
        "gym_statuses",
        "gym_subscription_statuses",
        "subscription_plan_statuses",
    }
    rendered = str(payload)
    assert "hidden-owner@example.test" not in rendered
    assert "Hidden Gym Name" not in rendered
    assert "member@example.test" not in rendered
    assert "99.00" not in rendered
    assert "100.00" not in rendered


def test_subscription_update_rejects_immutable_and_empty_payloads() -> None:
    rejected = AdminSubscriptionUpdateSerializer(
        data={
            "name": "Growth",
            "id": PLAN_ID,
            "code": "GROWTH",
            "currency": "USD",
            "features": [{"feature_id": PLAN_ID, "enabled": False, "limit_value": 1}],
            "feature_entitlements": [{"feature": "members"}],
            "created_at": "2026-10-17T00:00:00Z",
            "updated_at": "2026-10-17T00:00:00Z",
            "created_by": PLAN_ID,
            "actor_user": PLAN_ID,
        }
    )
    empty = AdminSubscriptionUpdateSerializer(data={})
    negative = AdminSubscriptionUpdateSerializer(data={"price": "-0.01"})
    lowercase = AdminSubscriptionUpdateSerializer(data={"billing_interval": "monthly"})
    unknown_status = AdminSubscriptionUpdateSerializer(data={"status": "ARCHIVED"})

    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "id",
        "code",
        "currency",
        "features",
        "feature_entitlements",
        "created_at",
        "updated_at",
        "created_by",
        "actor_user",
    }
    assert not empty.is_valid()
    assert not negative.is_valid()
    assert "price" in negative.errors
    assert not lowercase.is_valid()
    assert not unknown_status.is_valid()
    assert AdminSubscriptionUpdateSerializer(
        data={"name": "Growth", "status": "RETIRED", "price": "0.00", "billing_interval": "MONTHLY"}
    ).is_valid()


def test_subscription_list_query_defaults_to_active_and_rejects_unknown_input() -> None:
    omitted = AdminSubscriptionListQuerySerializer(data={})
    query = AdminSubscriptionListQuerySerializer(data=QueryDict("status=RETIRED"))
    everything = AdminSubscriptionListQuerySerializer(data=QueryDict("status=ALL"))
    explicit = AdminSubscriptionListQuerySerializer(data={"status": "ACTIVE"})
    invalid = AdminSubscriptionListQuerySerializer(data={"status": "ARCHIVED"})
    lowercase = AdminSubscriptionListQuerySerializer(data=QueryDict("status=all"))
    blank = AdminSubscriptionListQuerySerializer(data=QueryDict("status="))
    unknown = AdminSubscriptionListQuerySerializer(data=QueryDict("status=ACTIVE&limit=10"))

    assert omitted.is_valid(), omitted.errors
    assert omitted.validated_data["status"] == "ACTIVE"
    assert query.is_valid() and query.validated_data["status"] == "RETIRED"
    assert everything.is_valid() and everything.validated_data["status"] == "ALL"
    assert explicit.is_valid() and explicit.validated_data["status"] == "ACTIVE"
    assert set(SUBSCRIPTION_LIST_STATUSES) == {"ACTIVE", "RETIRED", "ALL"}
    assert not invalid.is_valid()
    assert not lowercase.is_valid()
    assert not blank.is_valid()
    assert not unknown.is_valid()
    assert set(unknown.errors) == {"limit"}


def test_subscription_list_output_includes_status_and_excludes_usage() -> None:
    plan = SimpleNamespace(
        id=uuid4(),
        code="TRIAL",
        name="Trial",
        status="ACTIVE",
        price="0.00",
        currency="THB",
        billing_interval="TRIAL",
        created_at="2026-10-17T00:00:00Z",
        updated_at="2026-10-17T00:00:00Z",
        email="hidden-owner@example.test",
        features=[{"code": "members"}],
    )

    data = AdminSubscriptionSerializer(plan).data
    assert set(data) == {
        "id",
        "code",
        "name",
        "status",
        "price",
        "currency",
        "billing_interval",
    }
    assert data["status"] == "ACTIVE"
    assert "hidden-owner@example.test" not in str(data)
    assert "members" not in str(data)


def test_platform_routes_reject_staff_principals_and_substitute_permissions() -> None:
    permission = RequiresPlatformPermissions()
    dashboard = AdminDashboardView()
    update = AdminSubscriptionDetailView()

    assert dashboard.authentication_classes == [PlatformJWTAuthentication]
    assert update.authentication_classes == [PlatformJWTAuthentication]
    assert AdminSubscriptionListView.authentication_classes == [PlatformJWTAuthentication]
    assert StaffJWTAuthentication not in dashboard.authentication_classes
    assert StaffJWTAuthentication not in update.authentication_classes
    assert dashboard.required_permissions == ("platform.dashboard.view",)
    assert update.required_permissions == ("platform.subscriptions.manage",)
    assert AdminSubscriptionListView.required_permissions == ("platform.subscriptions.manage",)

    staff = _permission_request(
        RefreshSession.PrincipalType.STAFF,
        {"platform.dashboard.view", "platform.subscriptions.manage"},
    )
    settings_only = _permission_request(
        RefreshSession.PrincipalType.PLATFORM,
        {"platform.settings.manage"},
    )
    dashboard_only = _permission_request(
        RefreshSession.PrincipalType.PLATFORM,
        {"platform.dashboard.view"},
    )
    subscriptions_only = _permission_request(
        RefreshSession.PrincipalType.PLATFORM,
        {"platform.subscriptions.manage"},
    )

    assert permission.has_permission(staff, dashboard) is False
    assert permission.has_permission(staff, update) is False
    assert permission.has_permission(settings_only, dashboard) is False
    assert permission.has_permission(settings_only, update) is False
    assert permission.has_permission(dashboard_only, dashboard) is True
    assert permission.has_permission(dashboard_only, update) is False
    assert permission.has_permission(subscriptions_only, dashboard) is False
    assert permission.has_permission(subscriptions_only, update) is True

    platform_views = (
        AdminGymListCreateView(),
        AdminGymDetailView(),
        AdminGymActivateView(),
        AdminGymSuspendView(),
        AdminGymLoginAccountsView(),
        AdminGymOwnerCreateView(),
        AdminSubscriptionListView(),
        PlatformAuditListView(),
    )
    for view in platform_views:
        assert "platform.settings.manage" not in view.required_permissions
        assert permission.has_permission(settings_only, view) is False

    tenant_settings = GymSettingsView()
    tenant_permission = RequiresPermission()
    assert tenant_settings.required_permission == "settings.view"
    assert tenant_permission.has_permission(settings_only, tenant_settings) is False
    tenant_settings.required_permission = "settings.manage"
    assert tenant_permission.has_permission(settings_only, tenant_settings) is False
