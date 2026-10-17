"""Authoritative tenant delegation policy.

There is no public delegable-set resource. The tenant delegable set is the
current permission set on the gym's active protected MANAGER role, excluding
the five hard-reserved approval codes and the OWNER-only ``settings.manage``
control. An active Owner changes that set by replacing the protected MANAGER
role's permissions. OWNER permissions stay immutable. Custom roles and the
protected RECEPTIONIST, ACCOUNTANT, and TRAINER roles cannot receive
hard-reserved approvals or ``settings.manage``.

Staff invitation and membership-role replacement both call these helpers so
the two assignment paths cannot drift.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.tenants.models import Role, RolePermission

HARD_RESERVED_APPROVALS = frozenset(
    {
        "payments.approve",
        "expenses.approve",
        "retail.approve",
        "pt.sales.approve",
        "salaries.approve",
    }
)
OWNER_ONLY_PERMISSIONS = frozenset({"settings.manage"})
PROTECTED_ROLE_KEYS = frozenset({"OWNER", "MANAGER", "RECEPTIONIST", "ACCOUNTANT", "TRAINER"})


class UnavailableRoleError(Exception):
    """The requested role cannot be assigned in this gym."""


@dataclass(frozen=True)
class RoleGrant:
    key: str
    permission_codes: frozenset[str]
    is_protected_manager: bool
    is_active: bool = True


def is_protected_role(role: Role) -> bool:
    return role.source_template_id is not None or role.key in PROTECTED_ROLE_KEYS


def is_protected_manager_role(role: Role) -> bool:
    template = role.source_template
    return role.key == "MANAGER" and template is not None and template.key == "MANAGER"


def delegable_permission_codes(manager_permission_codes: frozenset[str]) -> frozenset[str]:
    """Return the delegable subset of one protected MANAGER permission set."""

    return frozenset(
        code
        for code in manager_permission_codes
        if code not in HARD_RESERVED_APPROVALS
        and code not in OWNER_ONLY_PERMISSIONS
        and not code.startswith("platform.")
    )


def load_delegable_permission_codes(*, gym_id: uuid.UUID) -> frozenset[str]:
    """Lock and read the active protected MANAGER role's delegable codes.

    The caller must already be inside a transaction. A missing or inactive
    protected MANAGER role fails closed to an empty delegable set.
    """

    manager = (
        Role.objects.select_for_update(of=("self",))
        .filter(
            gym_id=gym_id,
            key="MANAGER",
            is_active=True,
            source_template__key="MANAGER",
        )
        .order_by("id")
        .first()
    )
    if manager is None:
        return frozenset()
    codes = frozenset(
        RolePermission.objects.filter(
            gym_id=gym_id,
            role=manager,
            permission__is_active=True,
        ).values_list("permission__code", flat=True)
    )
    return delegable_permission_codes(codes)


def role_grant_is_allowed(
    *,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    delegable_permissions: frozenset[str],
    role: RoleGrant,
) -> bool:
    """Return whether this caller may assign the role's current permission set."""

    if role.key == "OWNER" or not role.is_active:
        return False
    codes = role.permission_codes
    if any(code.startswith("platform.") for code in codes):
        return False
    if role.is_protected_manager:
        return caller_is_owner and not (codes & OWNER_ONLY_PERMISSIONS)
    if codes & HARD_RESERVED_APPROVALS or codes & OWNER_ONLY_PERMISSIONS:
        return False
    if caller_is_owner:
        return codes <= delegable_permissions
    return codes <= (caller_permissions & delegable_permissions)


def custom_permission_set_is_allowed(
    *,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    delegable_permissions: frozenset[str],
    permission_codes: frozenset[str],
) -> bool:
    """Return whether a custom role may end with this complete permission set."""

    if permission_codes & HARD_RESERVED_APPROVALS or permission_codes & OWNER_ONLY_PERMISSIONS:
        return False
    if any(code.startswith("platform.") for code in permission_codes):
        return False
    if caller_is_owner:
        return permission_codes <= delegable_permissions
    return permission_codes <= (caller_permissions & delegable_permissions)


def protected_role_permission_set_is_allowed(
    *,
    caller_is_owner: bool,
    delegable_permissions: frozenset[str],
    role_key: str,
    is_protected_manager: bool,
    permission_codes: frozenset[str],
) -> bool:
    """Return whether an Owner may store this set on a protected non-OWNER role.

    MANAGER must keep every hard-reserved approval. Those codes are the
    protected baseline and are not part of the delegable set. Other protected
    roles may use only the current delegable set. ``settings.manage`` and
    ``platform.*`` stay off every non-OWNER role. Non-owners cannot edit a
    protected role.
    """

    if role_key == "OWNER" or not caller_is_owner:
        return False
    if permission_codes & OWNER_ONLY_PERMISSIONS:
        return False
    if any(code.startswith("platform.") for code in permission_codes):
        return False
    if is_protected_manager:
        return HARD_RESERVED_APPROVALS <= permission_codes
    if permission_codes & HARD_RESERVED_APPROVALS:
        return False
    return permission_codes <= delegable_permissions


def target_authority_is_manageable(
    *,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    delegable_permissions: frozenset[str],
    target_permissions: frozenset[str],
) -> bool:
    """Return whether the caller may replace the target's current authority.

    A non-owner cannot manage a peer whose live permissions exceed the
    caller's permissions or the current delegable set, including anyone who
    already holds a hard-reserved approval or ``settings.manage``.
    """

    if caller_is_owner:
        return True
    if target_permissions & HARD_RESERVED_APPROVALS or target_permissions & OWNER_ONLY_PERMISSIONS:
        return False
    if any(code.startswith("platform.") for code in target_permissions):
        return False
    return target_permissions <= (caller_permissions & delegable_permissions)


def assignment_is_allowed(
    *,
    caller_is_owner: bool,
    caller_permissions: frozenset[str],
    delegable_permissions: frozenset[str],
    roles: list[RoleGrant],
) -> bool:
    """Revalidate every role and the complete resulting permission union."""

    if not roles:
        return False
    union: set[str] = set()
    for role in roles:
        if not role_grant_is_allowed(
            caller_is_owner=caller_is_owner,
            caller_permissions=caller_permissions,
            delegable_permissions=delegable_permissions,
            role=role,
        ):
            return False
        union.update(role.permission_codes)
    if caller_is_owner:
        return True
    return custom_permission_set_is_allowed(
        caller_is_owner=False,
        caller_permissions=caller_permissions,
        delegable_permissions=delegable_permissions,
        permission_codes=frozenset(union),
    )
