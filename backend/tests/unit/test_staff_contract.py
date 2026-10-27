from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from rest_framework.exceptions import ValidationError

from apps.tenants.delegation import (
    HARD_RESERVED_APPROVALS as RESERVED_APPROVAL_PERMISSIONS,
)
from apps.tenants.delegation import (
    RoleGrant,
    role_grant_is_allowed,
)
from apps.tenants.serializers import OwnerAcceptInputSerializer
from apps.tenants.staff_serializers import (
    StaffAcceptInputSerializer,
    StaffInviteInputSerializer,
    StaffListQuerySerializer,
    StaffUpdateSerializer,
)
from apps.tenants.staff_services import (
    decode_staff_list_cursor,
    encode_staff_list_cursor,
    staff_list_cursor_scope,
)

MANAGER_PERMISSIONS = frozenset(
    {
        "staff.view",
        "staff.manage",
        "members.view",
        "payments.approve",
        "expenses.approve",
        "pt.sales.approve",
        "salaries.approve",
        "settings.view",
    }
)
OWNER_PERMISSIONS = MANAGER_PERMISSIONS | frozenset({"settings.manage", "retail.approve"})


def _invite(**overrides: object) -> StaffInviteInputSerializer:
    payload: dict[str, object] = {
        "email": "coach@example.test",
        "first_name": "Cara",
        "last_name": "Coach",
        "staff_code": "CO-01",
        "display_name": "Cara Coach",
        "joined_date": "2026-10-01",
        "role_ids": [str(uuid4())],
    }
    payload.update(overrides)
    return StaffInviteInputSerializer(data=payload)


def test_invitation_serializer_rejects_mass_assignment_and_owner_controls() -> None:
    serializer = _invite(
        gym_id=str(uuid4()),
        is_owner=True,
        status="ACTIVE",
        authz_version=1,
        invitation_token="secret",
        profile_photo_asset_id=str(uuid4()),
        user_id=str(uuid4()),
        id=str(uuid4()),
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) >= {
        "gym_id",
        "is_owner",
        "status",
        "authz_version",
        "invitation_token",
        "profile_photo_asset_id",
        "user_id",
        "id",
    }


def test_invitation_serializer_requires_unique_roles_and_exact_commission() -> None:
    role_id = str(uuid4())
    duplicate = _invite(role_ids=[role_id, role_id])
    empty = _invite(role_ids=[])
    high = _invite(default_commission_percent="100.01")
    negative = _invite(default_commission_percent="-0.01")
    exact = _invite(default_commission_percent="100.00")
    spaced = _invite(staff_code="CO 01")

    assert not duplicate.is_valid()
    assert not empty.is_valid()
    assert not high.is_valid()
    assert not negative.is_valid()
    assert not spaced.is_valid()
    assert exact.is_valid(), exact.errors
    assert exact.validated_data["default_commission_percent"] == Decimal("100.00")


def test_acceptance_serializer_owns_gym_and_password_policy() -> None:
    rejected = StaffAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "correct horse battery staple",
            "password_confirmation": "correct horse battery staple",
            "gym_id": str(uuid4()),
            "role_ids": [str(uuid4())],
            "is_owner": True,
        }
    )
    mismatch = StaffAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "correct horse battery staple",
            "password_confirmation": "different password",
        }
    )
    short = StaffAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "short",
            "password_confirmation": "short",
        }
    )
    accepted = StaffAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "correct horse battery staple",
            "password_confirmation": "correct horse battery staple",
            "display_name": "Confirmed Name",
        }
    )

    assert not rejected.is_valid()
    assert set(rejected.errors) >= {"gym_id", "role_ids", "is_owner"}
    assert not mismatch.is_valid()
    assert "password_confirmation" in mismatch.errors
    assert not short.is_valid()
    assert "too short" in str(short.errors["password"])
    common = StaffAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "Password1234",
            "password_confirmation": "Password1234",
        }
    )
    assert not common.is_valid()
    assert common.errors["password"] == ["This password is too common."]
    owner = OwnerAcceptInputSerializer(
        data={
            "invitation_token": "token",
            "password": "Password1234",
            "password_confirmation": "Password1234",
        }
    )
    assert not owner.is_valid()
    assert owner.errors["password"] == ["This password is too common."]
    assert accepted.is_valid(), accepted.errors


def test_profile_update_is_limited_to_employment_fields() -> None:
    rejected = StaffUpdateSerializer(
        data={
            "display_name": "New Name",
            "status": "TERMINATED",
            "is_owner": False,
            "role_ids": [str(uuid4())],
            "gym_id": str(uuid4()),
            "authz_version": 2,
            "profile_photo_asset_id": str(uuid4()),
        }
    )
    empty = StaffUpdateSerializer(data={})
    query = StaffListQuerySerializer(data={"ordering": "-created_at", "gym_id": str(uuid4())})

    assert not rejected.is_valid()
    assert set(rejected.errors) >= {
        "status",
        "is_owner",
        "role_ids",
        "gym_id",
        "authz_version",
        "profile_photo_asset_id",
    }
    assert not empty.is_valid()
    assert not query.is_valid()
    default_limit = StaffListQuerySerializer(data={})
    assert default_limit.is_valid(), default_limit.errors
    assert default_limit.validated_data["limit"] == 25
    assert not StaffListQuerySerializer(data={"limit": 0}).is_valid()
    assert not StaffListQuerySerializer(data={"limit": 101}).is_valid()
    assert StaffListQuerySerializer(data={"limit": 100}).is_valid()


def test_staff_list_cursor_is_bound_to_filters_and_expires() -> None:
    gym_id = uuid4()
    scope = staff_list_cursor_scope(
        gym_id=gym_id,
        search="alex",
        profile_status="ACTIVE",
        membership_status="",
    )
    issued_at = 1_700_000_000
    with patch("django.core.signing.time.time", return_value=issued_at):
        token = encode_staff_list_cursor(
            display_name="Alex Shared",
            staff_id=uuid4(),
            scope=scope,
        )
    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        display_name, _staff_id = decode_staff_list_cursor(token, scope=scope)
    assert display_name == "Alex Shared"

    substituted = staff_list_cursor_scope(
        gym_id=gym_id,
        search="other",
        profile_status="ACTIVE",
        membership_status="",
    )
    status_substituted = staff_list_cursor_scope(
        gym_id=gym_id,
        search="alex",
        profile_status="ON_LEAVE",
        membership_status="",
    )
    membership_substituted = staff_list_cursor_scope(
        gym_id=gym_id,
        search="alex",
        profile_status="ACTIVE",
        membership_status="SUSPENDED",
    )
    other_gym = staff_list_cursor_scope(
        gym_id=uuid4(),
        search="alex",
        profile_status="ACTIVE",
        membership_status="",
    )
    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        for changed in (substituted, status_substituted, membership_substituted, other_gym):
            try:
                decode_staff_list_cursor(token, scope=changed)
            except ValidationError as exc:
                assert exc.detail == {"after": ["Invalid cursor."]}
            else:
                raise AssertionError("substituted cursor was accepted")
        tampered = f"{token[:-1]}A" if not token.endswith("A") else f"{token[:-1]}B"
        try:
            decode_staff_list_cursor(tampered, scope=scope)
        except ValidationError as exc:
            assert exc.detail == {"after": ["Invalid cursor."]}
        else:
            raise AssertionError("tampered cursor was accepted")
    with patch(
        "django.core.signing.time.time",
        return_value=issued_at + 24 * 60 * 60 + 5,
    ):
        try:
            decode_staff_list_cursor(token, scope=scope)
        except ValidationError as exc:
            assert exc.detail == {"after": ["Invalid cursor."]}
        else:
            raise AssertionError("expired cursor was accepted")


def test_delegation_uses_subset_and_fails_closed_on_reserved_permissions() -> None:
    delegable = MANAGER_PERMISSIONS - RESERVED_APPROVAL_PERMISSIONS
    receptionist = RoleGrant(
        key="RECEPTIONIST",
        permission_codes=frozenset({"members.view", "staff.view"}),
        is_protected_manager=False,
    )
    manager = RoleGrant(
        key="MANAGER",
        permission_codes=frozenset({"staff.manage", "payments.approve"}),
        is_protected_manager=True,
    )
    custom_approval = RoleGrant(
        key="APPROVER",
        permission_codes=frozenset({"payments.approve"}),
        is_protected_manager=False,
    )
    custom_settings = RoleGrant(
        key="FLOOR",
        permission_codes=frozenset({"settings.manage"}),
        is_protected_manager=False,
    )
    owner_role = RoleGrant(
        key="OWNER", permission_codes=OWNER_PERMISSIONS, is_protected_manager=False
    )

    assert role_grant_is_allowed(
        caller_is_owner=False,
        caller_permissions=MANAGER_PERMISSIONS,
        delegable_permissions=delegable,
        role=receptionist,
    )
    assert not role_grant_is_allowed(
        caller_is_owner=False,
        caller_permissions=MANAGER_PERMISSIONS,
        delegable_permissions=delegable,
        role=manager,
    )
    assert not role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=OWNER_PERMISSIONS,
        delegable_permissions=delegable,
        role=custom_approval,
    )
    assert role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=OWNER_PERMISSIONS,
        delegable_permissions=delegable,
        role=manager,
    )
    assert not role_grant_is_allowed(
        caller_is_owner=False,
        caller_permissions=MANAGER_PERMISSIONS,
        delegable_permissions=delegable,
        role=custom_settings,
    )
    assert not role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=OWNER_PERMISSIONS,
        delegable_permissions=delegable,
        role=custom_settings,
    )
    assert not role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=OWNER_PERMISSIONS,
        delegable_permissions=delegable,
        role=owner_role,
    )
    assert RESERVED_APPROVAL_PERMISSIONS >= {"payments.approve", "retail.approve"}
