"""Tenant role, permission-catalog, and membership-role commands.

Delegation policy lives in ``apps.tenants.delegation``. Replacing permissions or
active state locks the role and every membership assigned to it, then increments
each affected ``authz_version`` once. Access tokens carrying the previous
version fail on the next request. Refresh sessions stay valid and issue a token
for the new version, which is the same rule authentication already uses.
"""

from __future__ import annotations

import uuid
from typing import Any, NoReturn

from django.core import signing
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.codes import resolve_code
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.delegation import (
    PROTECTED_ROLE_KEYS,
    UnavailableRoleError,
    custom_permission_set_is_allowed,
    delegable_permission_codes,
    is_protected_manager_role,
    is_protected_role,
    load_delegable_permission_codes,
    protected_role_permission_set_is_allowed,
    target_authority_is_manageable,
)
from apps.tenants.exceptions import (
    InvalidRolePermissions,
    RoleAssignmentInvalid,
    RoleWorkflowConflict,
    StaffDelegationDenied,
)
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from apps.tenants.staff_services import (
    _load_assignable_roles,
    _lock_active_caller,
    _membership_permission_codes,
    _require_delegable_roles,
)

ROLE_LIST_CURSOR_SALT = "role-list"
ROLE_LIST_CURSOR_ORDERING = "key,id"
PERMISSION_LIST_CURSOR_SALT = "permission-catalog"
PERMISSION_LIST_CURSOR_ORDERING = "code,id"
_CURSOR_MAX_AGE = 24 * 60 * 60


def role_queryset(gym: Gym) -> QuerySet[Role]:
    permissions = (
        RolePermission.objects.filter(gym=gym)
        .select_related("permission")
        .order_by("permission__code", "id")
    )
    return (
        Role.objects.filter(gym=gym)
        .select_related("source_template")
        .annotate(
            assignment_count=Count(
                "gymmembershiprole",
                filter=Q(gymmembershiprole__gym=gym),
            )
        )
        .prefetch_related(Prefetch("rolepermission_set", queryset=permissions))
        .order_by("key", "id")
    )


def role_payload(role: Role) -> dict[str, object]:
    codes = _prefetched_permission_codes(role)
    return {
        "id": role.id,
        "key": role.key,
        "name": role.name,
        "description": role.description,
        "is_active": role.is_active,
        "is_protected": is_protected_role(role),
        "is_template": role.source_template_id is not None,
        "source_template_id": role.source_template_id,
        "source_template_version": role.source_template_version,
        "permission_codes": codes,
        "permission_count": len(codes),
        "assignment_count": _assignment_count(role),
        "created_by_membership_id": role.created_by_membership_id,
        "created_at": role.created_at,
        "updated_at": role.updated_at,
    }


def encode_role_cursor(*, key: str, role_id: uuid.UUID, gym_id: uuid.UUID) -> str:
    return signing.dumps(
        {
            "key": key,
            "id": str(role_id),
            "gym_id": str(gym_id),
            "ordering": ROLE_LIST_CURSOR_ORDERING,
        },
        salt=ROLE_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_role_cursor(token: str, *, gym_id: uuid.UUID) -> tuple[str, uuid.UUID]:
    try:
        cursor = signing.loads(token, salt=ROLE_LIST_CURSOR_SALT, max_age=_CURSOR_MAX_AGE)
        if (
            not isinstance(cursor, dict)
            or cursor.get("gym_id") != str(gym_id)
            or cursor.get("ordering") != ROLE_LIST_CURSOR_ORDERING
        ):
            raise signing.BadSignature
        key = cursor["key"]
        if not isinstance(key, str):
            raise TypeError
        role_id = uuid.UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return key, role_id


def page_roles(
    queryset: QuerySet[Role], *, gym_id: uuid.UUID, limit: int, after: str
) -> tuple[list[Role], dict[str, Any]]:
    if after:
        key, role_id = decode_role_cursor(after, gym_id=gym_id)
        queryset = queryset.filter(Q(key__gt=key) | Q(key=key, id__gt=role_id))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_role_cursor(key=last.key, role_id=last.id, gym_id=gym_id)
    return rows, {"has_more": has_more, "next_after": next_after}


def encode_permission_cursor(*, code: str, permission_id: uuid.UUID) -> str:
    return signing.dumps(
        {
            "code": code,
            "id": str(permission_id),
            "catalog": "tenant",
            "ordering": PERMISSION_LIST_CURSOR_ORDERING,
        },
        salt=PERMISSION_LIST_CURSOR_SALT,
        compress=True,
    )


def decode_permission_cursor(token: str) -> tuple[str, uuid.UUID]:
    try:
        cursor = signing.loads(token, salt=PERMISSION_LIST_CURSOR_SALT, max_age=_CURSOR_MAX_AGE)
        if (
            not isinstance(cursor, dict)
            or cursor.get("catalog") != "tenant"
            or cursor.get("ordering") != PERMISSION_LIST_CURSOR_ORDERING
        ):
            raise signing.BadSignature
        code = cursor["code"]
        if not isinstance(code, str):
            raise TypeError
        permission_id = uuid.UUID(str(cursor["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return code, permission_id


def permission_queryset() -> QuerySet[Permission]:
    return Permission.objects.exclude(code__startswith="platform.").order_by("code", "id")


def page_permissions(
    queryset: QuerySet[Permission], *, limit: int, after: str
) -> tuple[list[Permission], dict[str, Any]]:
    if after:
        code, permission_id = decode_permission_cursor(after)
        queryset = queryset.filter(Q(code__gt=code) | Q(code=code, id__gt=permission_id))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_permission_cursor(code=last.code, permission_id=last.id)
    return rows, {"has_more": has_more, "next_after": next_after}


def permission_payload(permission: Permission) -> dict[str, object]:
    return {
        "id": permission.id,
        "code": permission.code,
        "module": permission.module,
        "description": permission.description,
        "risk_level": permission.risk_level,
        "is_active": permission.is_active,
    }


@transaction.atomic
def create_role(
    *,
    gym_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    key: str,
    name: str,
    description: str,
    is_active: bool,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, object]:
    gym, caller = _locked_caller(
        gym_id=gym_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        expected_authz_version=expected_authz_version,
        required_permission="roles.manage",
    )
    if not key:
        key = resolve_code(
            "",
            Role.objects.filter(gym=gym).values_list("key", flat=True),
            name,
            prefix="ROLE",
            reserved=PROTECTED_ROLE_KEYS,
        )
    if key in PROTECTED_ROLE_KEYS:
        raise RoleWorkflowConflict("This role key is reserved.")
    if gym.status != Gym.Status.ACTIVE:
        raise RoleWorkflowConflict("Roles cannot be created for an inactive gym.")
    if not caller.is_owner:
        _require_custom_authority(
            gym_id=gym.id,
            caller_permissions=_membership_permission_codes(gym_id=gym.id, membership_id=caller.id),
            permission_codes=frozenset(),
        )
    try:
        with transaction.atomic():
            role = Role.objects.create(
                gym=gym,
                key=key,
                name=name,
                description=description,
                is_active=is_active,
                created_by_membership_id=caller.id,
            )
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from exc
    except DatabaseError as exc:
        _reraise_role_guard(exc)
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action="ROLE_CREATED",
        module="roles",
        target_type="Role",
        target_id=role.id,
        request_id=request_id,
        new_values={
            "key": role.key,
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return role_payload(role_queryset(gym).get(id=role.id))


@transaction.atomic
def update_role(
    *,
    gym_id: uuid.UUID,
    role_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
    action: str = "ROLE_UPDATED",
    conflict_if_inactive: bool = False,
) -> dict[str, object]:
    gym, caller = _locked_caller(
        gym_id=gym_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        expected_authz_version=expected_authz_version,
        required_permission="roles.manage",
    )
    role = _lock_one_role(gym_id=gym.id, role_id=role_id)
    caller_permissions = _membership_permission_codes(gym_id=gym.id, membership_id=caller.id)
    if is_protected_role(role):
        if not caller.is_owner:
            raise StaffDelegationDenied("Protected roles cannot be edited.")
        if _protected_identity_changes(role, changes):
            raise RoleWorkflowConflict("Protected template roles cannot be renamed or archived.")
    elif not caller.is_owner:
        _require_custom_authority(
            gym_id=gym.id,
            caller_permissions=caller_permissions,
            permission_codes=_active_permission_codes(gym_id=gym.id, role_id=role.id),
        )
    if conflict_if_inactive and not role.is_active:
        raise RoleWorkflowConflict("The role is already archived.")
    if changes.get("key") in PROTECTED_ROLE_KEYS:
        raise RoleWorkflowConflict("This role key is reserved.")
    previous = {
        "key": role.key,
        "name": role.name,
        "description": role.description,
        "is_active": role.is_active,
    }
    applied = {field: value for field, value in changes.items() if getattr(role, field) != value}
    if not applied:
        return role_payload(role_queryset(gym).get(id=role.id))
    for field, value in applied.items():
        setattr(role, field, value)
    active_changed = "is_active" in applied
    memberships = _lock_role_memberships(gym_id=gym.id, role_id=role.id) if active_changed else []
    try:
        with transaction.atomic():
            role.save(update_fields=[*applied.keys(), "updated_at"])
            if active_changed:
                _increment_authz(memberships)
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from exc
    except DatabaseError as exc:
        _reraise_role_guard(exc)
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action=action,
        module="roles",
        target_type="Role",
        target_id=role.id,
        request_id=request_id,
        old_values=previous,
        new_values=applied,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return role_payload(role_queryset(gym).get(id=role.id))


@transaction.atomic
def archive_role(
    *,
    gym_id: uuid.UUID,
    role_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, object]:
    return update_role(
        gym_id=gym_id,
        role_id=role_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        expected_authz_version=expected_authz_version,
        changes={"is_active": False},
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        action="ROLE_ARCHIVED",
        conflict_if_inactive=True,
    )


@transaction.atomic
def replace_role_permissions(
    *,
    gym_id: uuid.UUID,
    role_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    permission_codes: list[str],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, object]:
    gym, caller = _locked_caller(
        gym_id=gym_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        expected_authz_version=expected_authz_version,
        required_permission="roles.manage",
    )
    role, delegable = _lock_role_and_delegable(gym_id=gym.id, role_id=role_id)
    requested = frozenset(permission_codes)
    resolved = _resolve_permissions(requested)
    caller_permissions = _membership_permission_codes(gym_id=gym.id, membership_id=caller.id)
    _assert_permission_replacement(
        caller_is_owner=caller.is_owner,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable,
        role=role,
        permission_codes=requested,
    )
    if not role.is_active:
        raise RoleWorkflowConflict("Archived roles cannot be changed.")
    current = frozenset(
        RolePermission.objects.filter(gym_id=gym.id, role=role).values_list(
            "permission__code", flat=True
        )
    )
    if current == requested:
        return role_payload(role_queryset(gym).get(id=role.id))
    memberships = _lock_role_memberships(gym_id=gym.id, role_id=role.id)
    desired_ids = {permission.id for permission in resolved}
    try:
        with transaction.atomic():
            RolePermission.objects.filter(gym_id=gym.id, role=role).exclude(
                permission_id__in=desired_ids
            ).delete()
            existing_ids = _existing_permission_ids(gym_id=gym.id, role_id=role.id)
            RolePermission.objects.bulk_create(
                [
                    RolePermission(
                        gym=gym,
                        role=role,
                        permission=permission,
                        created_by_membership_id=caller.id,
                    )
                    for permission in resolved
                    if permission.id not in existing_ids
                ]
            )
            _increment_authz(memberships)
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from exc
    except DatabaseError as exc:
        _reraise_role_guard(exc)
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action="ROLE_PERMISSIONS_REPLACED",
        module="roles",
        target_type="Role",
        target_id=role.id,
        request_id=request_id,
        old_values={"permission_codes": sorted(current)},
        new_values={
            "permission_codes": sorted(requested),
            "membership_ids": [str(row.id) for row in memberships],
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return role_payload(role_queryset(gym).get(id=role.id))


def membership_role_payload(membership: GymMembership, roles: list[Role]) -> dict[str, object]:
    return {
        "gym_membership_id": membership.id,
        "roles": [role_payload(role) for role in roles],
        "authz_version": membership.authz_version,
    }


@transaction.atomic
def get_membership_roles(*, gym_id: uuid.UUID, membership_id: uuid.UUID) -> dict[str, object]:
    set_gym_context(gym_id=gym_id)
    membership = _visible_staff_membership(gym_id=gym_id, membership_id=membership_id)
    role_ids = list(
        GymMembershipRole.objects.filter(gym_id=gym_id, gym_membership=membership).values_list(
            "role_id", flat=True
        )
    )
    roles = list(role_queryset(membership.gym).filter(id__in=role_ids))
    return membership_role_payload(membership, roles)


@transaction.atomic
def replace_membership_roles(
    *,
    gym_id: uuid.UUID,
    membership_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    role_ids: list[uuid.UUID],
    request_id: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, object]:
    gym, caller = _locked_caller(
        gym_id=gym_id,
        actor=actor,
        actor_membership_id=actor_membership_id,
        expected_authz_version=expected_authz_version,
        required_permission="staff.manage",
    )
    if not role_ids or len(role_ids) != len(set(role_ids)):
        raise RoleAssignmentInvalid("Role ids must be unique and nonempty.")
    try:
        roles = _load_assignable_roles(gym_id=gym.id, role_ids=role_ids)
    except UnavailableRoleError as exc:
        raise RoleAssignmentInvalid("One or more roles are unavailable.") from exc
    caller_permissions = _membership_permission_codes(gym_id=gym.id, membership_id=caller.id)
    _require_delegable_roles(
        gym_id=gym.id,
        caller_is_owner=caller.is_owner,
        caller_permissions=caller_permissions,
        roles=roles,
    )
    membership = _lock_staff_membership(gym_id=gym.id, membership_id=membership_id)
    if membership.status != GymMembership.Status.ACTIVE:
        raise RoleWorkflowConflict("Only an active staff membership can receive roles.")
    target_permissions = _membership_permission_codes(gym_id=gym.id, membership_id=membership.id)
    delegable = load_delegable_permission_codes(gym_id=gym.id)
    if not target_authority_is_manageable(
        caller_is_owner=caller.is_owner,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable,
        target_permissions=target_permissions,
    ):
        raise StaffDelegationDenied("The caller cannot manage this staff membership.")
    current_ids = list(
        GymMembershipRole.objects.filter(gym_id=gym.id, gym_membership=membership)
        .order_by("role_id")
        .values_list("role_id", flat=True)
    )
    new_ids = [role.id for role in roles]
    if set(current_ids) == set(new_ids):
        assigned = list(role_queryset(gym).filter(id__in=new_ids))
        by_id = {role.id: role for role in assigned}
        return membership_role_payload(membership, [by_id[role_id] for role_id in new_ids])
    try:
        with transaction.atomic():
            GymMembershipRole.objects.filter(gym_id=gym.id, gym_membership=membership).exclude(
                role_id__in=new_ids
            ).delete()
            GymMembershipRole.objects.bulk_create(
                [
                    GymMembershipRole(
                        gym=gym,
                        gym_membership=membership,
                        role=role,
                        created_by_membership_id=caller.id,
                    )
                    for role in roles
                ],
                ignore_conflicts=True,
            )
            membership.authz_version += 1
            membership.save(update_fields=["authz_version", "updated_at"])
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from exc
    except DatabaseError as exc:
        _reraise_role_guard(exc)
    membership.refresh_from_db(fields=["authz_version", "updated_at"])
    write_tenant_audit(
        gym=gym,
        actor_user=actor,
        actor_membership_id=caller.id,
        action="MEMBERSHIP_ROLES_REPLACED",
        module="roles",
        target_type="GymMembership",
        target_id=membership.id,
        request_id=request_id,
        old_values={"role_ids": [str(role_id) for role_id in current_ids]},
        new_values={
            "role_ids": [str(role_id) for role_id in new_ids],
            "authz_version": membership.authz_version,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    assigned = list(role_queryset(gym).filter(id__in=new_ids))
    by_id = {role.id: role for role in assigned}
    return membership_role_payload(membership, [by_id[role_id] for role_id in new_ids])


def _locked_caller(
    *,
    gym_id: uuid.UUID,
    actor: Any,
    actor_membership_id: uuid.UUID,
    expected_authz_version: int,
    required_permission: str,
) -> tuple[Gym, GymMembership]:
    gym = Gym.objects.select_for_update().get(id=gym_id)
    set_gym_context(gym_id=gym.id)
    caller = _lock_active_caller(
        gym_id=gym.id, actor=actor, actor_membership_id=actor_membership_id
    )
    if caller.authz_version != expected_authz_version:
        raise AuthenticationFailed()
    if required_permission not in _membership_permission_codes(
        gym_id=gym.id, membership_id=caller.id
    ):
        raise PermissionDenied()
    return gym, caller


def _lock_one_role(*, gym_id: uuid.UUID, role_id: uuid.UUID) -> Role:
    try:
        return (
            Role.objects.select_for_update(of=("self",))
            .select_related("source_template")
            .get(gym_id=gym_id, id=role_id)
        )
    except Role.DoesNotExist as exc:
        raise Role.DoesNotExist from exc


def _lock_role_and_delegable(
    *, gym_id: uuid.UUID, role_id: uuid.UUID
) -> tuple[Role, frozenset[str]]:
    manager_id = (
        Role.objects.filter(
            gym_id=gym_id,
            key="MANAGER",
            is_active=True,
            source_template__key="MANAGER",
        )
        .values_list("id", flat=True)
        .first()
    )
    if not Role.objects.filter(gym_id=gym_id, id=role_id).exists():
        raise Role.DoesNotExist
    ordered_ids = sorted({role_id, *([manager_id] if manager_id else [])})
    locked = list(
        Role.objects.select_for_update(of=("self",))
        .select_related("source_template")
        .filter(gym_id=gym_id, id__in=ordered_ids)
        .order_by("id")
    )
    role = next((row for row in locked if row.id == role_id), None)
    if role is None:
        raise Role.DoesNotExist
    if is_protected_manager_role(role):
        return role, delegable_permission_codes(
            _active_permission_codes(gym_id=gym_id, role_id=role.id)
        )
    manager = next((row for row in locked if row.id == manager_id), None)
    if manager is None:
        return role, frozenset()
    return role, delegable_permission_codes(
        _active_permission_codes(gym_id=gym_id, role_id=manager.id)
    )


def _assert_permission_replacement(
    *,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    delegable_permissions: frozenset[str],
    role: Role,
    permission_codes: frozenset[str],
) -> None:
    if role.key == "OWNER":
        raise RoleWorkflowConflict("OWNER role permissions are immutable.")
    if is_protected_role(role):
        if not protected_role_permission_set_is_allowed(
            caller_is_owner=caller_is_owner,
            delegable_permissions=delegable_permissions,
            role_key=role.key,
            is_protected_manager=is_protected_manager_role(role),
            permission_codes=permission_codes,
        ):
            if not caller_is_owner:
                raise StaffDelegationDenied("Protected roles cannot be edited.")
            raise StaffDelegationDenied("The permission set is outside the delegable policy.")
        return
    if not caller_is_owner:
        current = _active_permission_codes(gym_id=role.gym_id, role_id=role.id)
        if not custom_permission_set_is_allowed(
            caller_is_owner=False,
            caller_permissions=caller_permissions,
            delegable_permissions=delegable_permissions,
            permission_codes=current,
        ):
            raise StaffDelegationDenied("The caller cannot edit this role.")
    if not custom_permission_set_is_allowed(
        caller_is_owner=caller_is_owner,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable_permissions,
        permission_codes=permission_codes,
    ):
        raise StaffDelegationDenied("The permission set is outside the delegable policy.")


def _require_custom_authority(
    *,
    gym_id: uuid.UUID,
    caller_permissions: frozenset[str],
    permission_codes: frozenset[str],
) -> None:
    delegable = load_delegable_permission_codes(gym_id=gym_id)
    if not custom_permission_set_is_allowed(
        caller_is_owner=False,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable,
        permission_codes=permission_codes,
    ):
        raise StaffDelegationDenied("The caller cannot edit this role.")


def _resolve_permissions(codes: frozenset[str]) -> list[Permission]:
    if any(code.startswith("platform.") for code in codes):
        raise InvalidRolePermissions("One or more permissions are unavailable.")
    found = list(
        Permission.objects.filter(code__in=codes, is_active=True).exclude(
            code__startswith="platform."
        )
    )
    if len(found) != len(codes):
        raise InvalidRolePermissions("One or more permissions are unavailable.")
    return found


def _active_permission_codes(*, gym_id: uuid.UUID, role_id: uuid.UUID) -> frozenset[str]:
    return frozenset(
        RolePermission.objects.filter(
            gym_id=gym_id,
            role_id=role_id,
            permission__is_active=True,
        ).values_list("permission__code", flat=True)
    )


def _existing_permission_ids(*, gym_id: uuid.UUID, role_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        RolePermission.objects.filter(gym_id=gym_id, role_id=role_id).values_list(
            "permission_id", flat=True
        )
    )


def _lock_role_memberships(*, gym_id: uuid.UUID, role_id: uuid.UUID) -> list[GymMembership]:
    membership_ids = list(
        GymMembershipRole.objects.filter(gym_id=gym_id, role_id=role_id)
        .order_by("gym_membership_id")
        .values_list("gym_membership_id", flat=True)
    )
    if not membership_ids:
        return []
    return list(
        GymMembership.objects.select_for_update(of=("self",))
        .filter(gym_id=gym_id, id__in=membership_ids)
        .order_by("id")
    )


def _increment_authz(memberships: list[GymMembership]) -> None:
    if not memberships:
        return
    GymMembership.objects.filter(
        gym_id=memberships[0].gym_id,
        id__in=[membership.id for membership in memberships],
    ).update(authz_version=F("authz_version") + 1, updated_at=timezone.now())


def _visible_staff_membership(*, gym_id: uuid.UUID, membership_id: uuid.UUID) -> GymMembership:
    try:
        return GymMembership.objects.select_related("gym").get(
            id=membership_id,
            gym_id=gym_id,
            is_owner=False,
        )
    except GymMembership.DoesNotExist as exc:
        raise GymMembership.DoesNotExist from exc


def _lock_staff_membership(*, gym_id: uuid.UUID, membership_id: uuid.UUID) -> GymMembership:
    try:
        return GymMembership.objects.select_for_update(of=("self",)).get(
            id=membership_id,
            gym_id=gym_id,
            is_owner=False,
        )
    except GymMembership.DoesNotExist as exc:
        raise GymMembership.DoesNotExist from exc


def _protected_identity_changes(role: Role, changes: dict[str, Any]) -> bool:
    if "key" in changes and changes["key"] != role.key:
        return True
    if "name" in changes and changes["name"] != role.name:
        return True
    return "is_active" in changes and changes["is_active"] != role.is_active


def _prefetched_permission_codes(role: Role) -> list[str]:
    cached = getattr(role, "_prefetched_objects_cache", {}).get("rolepermission_set")
    rows = cached if cached is not None else role.rolepermission_set.select_related("permission")
    return sorted(row.permission.code for row in rows)


def _assignment_count(role: Role) -> int:
    annotated = getattr(role, "assignment_count", None)
    if isinstance(annotated, int):
        return annotated
    return role.gymmembershiprole_set.filter(gym_id=role.gym_id).count()


def _conflict_from_integrity(exc: IntegrityError) -> RoleWorkflowConflict:
    message = str(exc).lower()
    if "role_gym_key" in message:
        return RoleWorkflowConflict("The role key is already in use.")
    if "role_active_name" in message or "normalized_name" in message:
        return RoleWorkflowConflict("An active role with this name already exists.")
    if "role_guard:" in message:
        return RoleWorkflowConflict("The role change conflicts with a protected role.")
    return RoleWorkflowConflict("The role record conflicts with an existing record.")


def _reraise_role_guard(exc: DatabaseError) -> NoReturn:
    if "role_guard:" in str(exc).lower():
        raise RoleWorkflowConflict("The role change conflicts with a protected role.") from exc
    raise exc
