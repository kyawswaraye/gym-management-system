from unittest.mock import patch
from uuid import uuid4

from rest_framework.exceptions import ValidationError

from apps.tenants.delegation import (
    HARD_RESERVED_APPROVALS,
    OWNER_ONLY_PERMISSIONS,
    RoleGrant,
    assignment_is_allowed,
    custom_permission_set_is_allowed,
    delegable_permission_codes,
    protected_role_permission_set_is_allowed,
    role_grant_is_allowed,
    target_authority_is_manageable,
)
from apps.tenants.role_serializers import (
    MembershipRoleReplaceSerializer,
    RoleArchiveSerializer,
    RoleCreateSerializer,
    RolePermissionReplaceSerializer,
    RoleUpdateSerializer,
)
from apps.tenants.role_services import (
    decode_permission_cursor,
    decode_role_cursor,
    encode_permission_cursor,
    encode_role_cursor,
)

DELEGABLE = frozenset({"members.view", "members.create", "staff.manage", "roles.manage"})
CALLER = DELEGABLE | HARD_RESERVED_APPROVALS


def test_role_serializers_reject_mass_assignment_and_reserved_keys() -> None:
    created = RoleCreateSerializer(
        data={
            "key": "FLOOR",
            "name": "Floor",
            "gym_id": str(uuid4()),
            "is_owner": True,
            "source_template_id": str(uuid4()),
            "source_template_version": 1,
            "permission_codes": ["members.view"],
            "created_by_membership_id": str(uuid4()),
        }
    )
    reserved = RoleCreateSerializer(data={"key": "OWNER", "name": "Owner"})
    renamed = RoleUpdateSerializer(
        data={"name": "New", "is_active": False, "gym_id": str(uuid4()), "is_protected": False}
    )
    empty = RoleUpdateSerializer(data={})
    duplicate_codes = RolePermissionReplaceSerializer(
        data={"permission_codes": ["members.view", "members.view"], "role_id": str(uuid4())}
    )
    platform = RolePermissionReplaceSerializer(data={"permission_codes": ["platform.gyms.view"]})
    duplicate_roles = MembershipRoleReplaceSerializer(
        data={"role_ids": [str(uuid4())] * 2, "is_owner": True}
    )
    empty_roles = MembershipRoleReplaceSerializer(data={"role_ids": []})
    archive = RoleArchiveSerializer(data={"reason": "no"})

    assert not created.is_valid()
    assert set(created.errors) >= {
        "gym_id",
        "is_owner",
        "source_template_id",
        "source_template_version",
        "permission_codes",
        "created_by_membership_id",
    }
    assert not reserved.is_valid()
    assert not renamed.is_valid()
    assert "gym_id" in renamed.errors
    assert not empty.is_valid()
    assert not duplicate_codes.is_valid()
    assert platform.is_valid(), platform.errors
    assert not duplicate_roles.is_valid()
    assert not empty_roles.is_valid()
    assert not archive.is_valid()
    assert RoleArchiveSerializer(data={}).is_valid()


def test_delegable_set_excludes_reserved_and_owner_controls() -> None:
    codes = (
        DELEGABLE
        | HARD_RESERVED_APPROVALS
        | OWNER_ONLY_PERMISSIONS
        | frozenset({"platform.gyms.view"})
    )

    assert delegable_permission_codes(codes) == DELEGABLE
    assert HARD_RESERVED_APPROVALS <= codes
    assert "settings.manage" in OWNER_ONLY_PERMISSIONS
    assert "platform.settings.manage" not in delegable_permission_codes(
        DELEGABLE | frozenset({"platform.settings.manage"})
    )


def test_permission_replacement_policy_preserves_owner_controls() -> None:
    assert protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="MANAGER",
        is_protected_manager=True,
        permission_codes=DELEGABLE | HARD_RESERVED_APPROVALS,
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="MANAGER",
        is_protected_manager=True,
        permission_codes=DELEGABLE,
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=False,
        delegable_permissions=DELEGABLE,
        role_key="MANAGER",
        is_protected_manager=True,
        permission_codes=DELEGABLE | HARD_RESERVED_APPROVALS,
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="MANAGER",
        is_protected_manager=True,
        permission_codes=DELEGABLE | HARD_RESERVED_APPROVALS | OWNER_ONLY_PERMISSIONS,
    )
    assert protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="RECEPTIONIST",
        is_protected_manager=False,
        permission_codes=frozenset({"members.view"}),
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="RECEPTIONIST",
        is_protected_manager=False,
        permission_codes=frozenset({"payments.approve"}),
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="OWNER",
        is_protected_manager=False,
        permission_codes=DELEGABLE,
    )
    assert custom_permission_set_is_allowed(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        permission_codes=frozenset({"members.view"}),
    )
    assert not custom_permission_set_is_allowed(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        permission_codes=frozenset({"payments.approve"}),
    )
    assert not custom_permission_set_is_allowed(
        caller_is_owner=True,
        caller_permissions=CALLER | OWNER_ONLY_PERMISSIONS,
        delegable_permissions=DELEGABLE | OWNER_ONLY_PERMISSIONS,
        permission_codes=OWNER_ONLY_PERMISSIONS,
    )
    assert not custom_permission_set_is_allowed(
        caller_is_owner=False,
        caller_permissions=frozenset({"members.view"}),
        delegable_permissions=DELEGABLE,
        permission_codes=frozenset({"members.create"}),
    )
    assert not custom_permission_set_is_allowed(
        caller_is_owner=True,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        permission_codes=frozenset({"platform.settings.manage"}),
    )
    assert not protected_role_permission_set_is_allowed(
        caller_is_owner=True,
        delegable_permissions=DELEGABLE,
        role_key="MANAGER",
        is_protected_manager=True,
        permission_codes=DELEGABLE
        | HARD_RESERVED_APPROVALS
        | frozenset({"platform.settings.manage"}),
    )
    assert not role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        role=RoleGrant(
            key="FLOOR",
            permission_codes=frozenset({"platform.settings.manage"}),
            is_protected_manager=False,
        ),
    )


def test_assignment_revalidates_the_complete_set_and_target_authority() -> None:
    floor = RoleGrant(
        key="FLOOR",
        permission_codes=frozenset({"members.view"}),
        is_protected_manager=False,
    )
    manager = RoleGrant(
        key="MANAGER",
        permission_codes=frozenset({"staff.manage", "payments.approve"}),
        is_protected_manager=True,
    )
    assert assignment_is_allowed(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        roles=[floor],
    )
    assert not assignment_is_allowed(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        roles=[floor, manager],
    )
    assert assignment_is_allowed(
        caller_is_owner=True,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        roles=[manager],
    )
    assert not role_grant_is_allowed(
        caller_is_owner=True,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        role=RoleGrant(
            key="OLD",
            permission_codes=frozenset({"members.view"}),
            is_protected_manager=False,
            is_active=False,
        ),
    )
    assert target_authority_is_manageable(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        target_permissions=frozenset({"members.view"}),
    )
    assert not target_authority_is_manageable(
        caller_is_owner=False,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        target_permissions=frozenset({"members.view", "payments.approve"}),
    )
    assert target_authority_is_manageable(
        caller_is_owner=True,
        caller_permissions=CALLER,
        delegable_permissions=DELEGABLE,
        target_permissions=HARD_RESERVED_APPROVALS,
    )


def test_role_and_permission_cursors_are_bound_and_expire() -> None:
    gym_id = uuid4()
    role_id = uuid4()
    permission_id = uuid4()
    issued_at = 1_700_000_000
    with patch("django.core.signing.time.time", return_value=issued_at):
        role_token = encode_role_cursor(key="FLOOR", role_id=role_id, gym_id=gym_id)
        permission_token = encode_permission_cursor(
            code="members.view", permission_id=permission_id
        )
    with patch("django.core.signing.time.time", return_value=issued_at + 60):
        assert decode_role_cursor(role_token, gym_id=gym_id) == ("FLOOR", role_id)
        assert decode_permission_cursor(permission_token) == ("members.view", permission_id)
        try:
            decode_role_cursor(role_token, gym_id=uuid4())
        except ValidationError as exc:
            assert exc.detail == {"after": ["Invalid cursor."]}
        else:
            raise AssertionError("cursor from another gym was accepted")
    with patch("django.core.signing.time.time", return_value=issued_at + 24 * 60 * 60 + 5):
        try:
            decode_permission_cursor(permission_token)
        except ValidationError as exc:
            assert exc.detail == {"after": ["Invalid cursor."]}
        else:
            raise AssertionError("expired permission cursor was accepted")
