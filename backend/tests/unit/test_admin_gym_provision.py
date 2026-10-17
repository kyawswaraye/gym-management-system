from types import SimpleNamespace

from apps.tenants.serializers import AdminGymProvisionRequestSerializer
from apps.tenants.services import canonical_provisioning_hash
from apps.users.models import RefreshSession
from apps.users.permissions import RequiresPlatformPermissions


def valid_request() -> dict[str, object]:
    return {
        "gym": {
            "code": "YGN-01",
            "name": "Yangon Fitness",
            "timezone": "Asia/Yangon",
            "currency": "THB",
            "subscription_plan_id": "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
            "subscription_status": "TRIAL",
        },
        "owner": {
            "email": "owner@example.test",
            "first_name": "Moe",
            "last_name": "Thura",
            "send_invitation": False,
        },
    }


def test_provisioning_serializer_rejects_mass_assignment() -> None:
    body = valid_request()
    body["gym"] = {**body["gym"], "status": "ACTIVE", "id": "forbidden"}
    serializer = AdminGymProvisionRequestSerializer(data=body)
    assert not serializer.is_valid()
    assert set(serializer.errors["gym"]) == {"id", "status"}


def test_omitted_invitation_defaults_to_delivery_and_changes_the_hash() -> None:
    omitted = valid_request()
    omitted_owner = omitted["owner"]
    assert isinstance(omitted_owner, dict)
    omitted["owner"] = {
        key: value for key, value in omitted_owner.items() if key != "send_invitation"
    }
    explicit = valid_request()
    explicit["owner"] = {**explicit["owner"], "send_invitation": True}
    disabled = valid_request()

    omitted_serializer = AdminGymProvisionRequestSerializer(data=omitted)
    explicit_serializer = AdminGymProvisionRequestSerializer(data=explicit)
    disabled_serializer = AdminGymProvisionRequestSerializer(data=disabled)
    assert omitted_serializer.is_valid(), omitted_serializer.errors
    assert explicit_serializer.is_valid(), explicit_serializer.errors
    assert disabled_serializer.is_valid(), disabled_serializer.errors
    assert omitted_serializer.validated_data["owner"]["send_invitation"] is True
    assert explicit_serializer.validated_data["owner"]["send_invitation"] is True
    assert disabled_serializer.validated_data["owner"]["send_invitation"] is False

    omitted_hash = canonical_provisioning_hash(omitted_serializer.validated_data)
    explicit_hash = canonical_provisioning_hash(explicit_serializer.validated_data)
    disabled_hash = canonical_provisioning_hash(disabled_serializer.validated_data)
    assert omitted_hash == explicit_hash
    assert omitted_hash != disabled_hash


def test_provisioning_hash_is_canonical_and_semantically_sensitive() -> None:
    first = valid_request()
    reordered = {"owner": first["owner"], "gym": first["gym"]}
    changed = valid_request()
    changed["gym"] = {**changed["gym"], "name": "Different"}

    assert canonical_provisioning_hash(first) == canonical_provisioning_hash(reordered)
    assert canonical_provisioning_hash(first) != canonical_provisioning_hash(changed)


def test_platform_all_of_permission_requires_every_code_and_platform_principal() -> None:
    permission = RequiresPlatformPermissions()
    view = SimpleNamespace(required_permissions=("platform.gyms.create", "platform.owners.create"))
    allowed = SimpleNamespace(
        principal_type=RefreshSession.PrincipalType.PLATFORM,
        permission_codes=frozenset({"platform.gyms.create", "platform.owners.create"}),
    )
    missing_owner = SimpleNamespace(
        principal_type=RefreshSession.PrincipalType.PLATFORM,
        permission_codes=frozenset({"platform.gyms.create"}),
    )
    tenant = SimpleNamespace(
        principal_type=RefreshSession.PrincipalType.STAFF,
        permission_codes=frozenset({"platform.gyms.create", "platform.owners.create"}),
    )

    assert permission.has_permission(allowed, view)
    assert not permission.has_permission(missing_owner, view)
    assert not permission.has_permission(tenant, view)
