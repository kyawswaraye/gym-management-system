from types import SimpleNamespace

from apps.tenants.serializers import (
    AdminGymLifecycleInputSerializer,
    AdminGymPatchSerializer,
)
from apps.tenants.views import AdminGymDetailView


def test_gym_patch_rejects_immutable_and_unknown_fields() -> None:
    serializer = AdminGymPatchSerializer(
        data={
            "name": "Updated",
            "code": "FORBIDDEN",
            "status": "SUSPENDED",
            "id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
        }
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {"code", "id", "status"}


def test_gym_patch_rejects_empty_payload_and_invalid_date_order() -> None:
    empty = AdminGymPatchSerializer(data={})
    dates = AdminGymPatchSerializer(
        data={
            "subscription_starts_at": "2026-10-02T00:00:00Z",
            "subscription_ends_at": "2026-10-01T00:00:00Z",
        }
    )

    assert not empty.is_valid()
    assert not dates.is_valid()
    assert "subscription_ends_at" in dates.errors


def test_gym_detail_patch_splits_profile_and_subscription_permissions() -> None:
    profile = AdminGymDetailView()
    profile.request = SimpleNamespace(method="PATCH", data={"name": "Updated"})
    profile.get_permissions()
    assert profile.required_permissions == ("platform.gyms.update",)

    subscription = AdminGymDetailView()
    subscription.request = SimpleNamespace(method="PATCH", data={"subscription_status": "PAST_DUE"})
    subscription.get_permissions()
    assert subscription.required_permissions == ("platform.subscriptions.manage",)

    mixed = AdminGymDetailView()
    mixed.request = SimpleNamespace(
        method="PATCH",
        data={"name": "Updated", "subscription_status": "ACTIVE"},
    )
    mixed.get_permissions()
    assert mixed.required_permissions == (
        "platform.gyms.update",
        "platform.subscriptions.manage",
    )


def test_lifecycle_reason_is_required_and_bounded() -> None:
    assert not AdminGymLifecycleInputSerializer(data={"reason": "  "}).is_valid()
    assert not AdminGymLifecycleInputSerializer(data={"reason": "x" * 501}).is_valid()
    assert AdminGymLifecycleInputSerializer(data={"reason": "Billing restored"}).is_valid()
