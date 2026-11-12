from __future__ import annotations

from collections.abc import Callable
from typing import cast

from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.registry import ToolDefinition
from apps.tenants.exceptions import MFARequired
from apps.users.permissions import (
    RequiresEntitlements,
    RequiresPermissions,
    RequiresRecentMFA,
)

FULL_ACCESS_ROLE_KEYS = frozenset({"OWNER", "MANAGER", "RECEPTIONIST"})


def assistant_full_access(request: Request) -> bool:
    """Let an owner, manager, or receptionist run every assistant tool.

    Accountant, trainer, and custom roles still need each permission the tool
    names. Gym feature entitlements stay separate.
    """

    membership = getattr(request, "gym_membership", None)
    status = getattr(membership, "status", None)
    active = getattr(getattr(membership, "Status", None), "ACTIVE", None)
    if membership is None or status != active:
        return False
    if getattr(membership, "is_owner", False):
        return True
    roles = getattr(request, "role_keys", ())
    if isinstance(roles, str) or not isinstance(roles, frozenset | set | tuple | list):
        return False
    return any(role in FULL_ACCESS_ROLE_KEYS for role in roles)


class _RequirementView:
    """Carries the permission codes the original view declared."""

    def __init__(self, permissions: tuple[str, ...], features: tuple[str, ...]) -> None:
        self.required_permissions = permissions
        self.required_features = features


def enforce_tool_access(*, request: Request, view: APIView, tool: ToolDefinition) -> None:
    """Recheck the existing permission classes before a service call.

    ``RequiresRecentMFA`` ignores the view and reads the authenticated request.
    Permission and entitlement classes read the codes supplied by the tool,
    which are the codes the original views require.
    """

    requirements = cast(APIView, _RequirementView(tool.permissions, tool.features))
    if tool.permissions and not assistant_full_access(request):
        if not RequiresPermissions().has_permission(request, requirements):
            raise PermissionDenied()
    if tool.features and not RequiresEntitlements().has_permission(request, requirements):
        raise PermissionDenied()
    if tool.requires_recent_mfa:
        RequiresRecentMFA().has_permission(request, view)


def call_authorized_service[Result](
    *,
    request: Request,
    view: APIView,
    tool: ToolDefinition,
    service: Callable[[], Result],
) -> Result:
    """Run the MFA guard before the service. A denial does not call it."""

    enforce_tool_access(request=request, view=view, tool=tool)
    return service()


__all__ = [
    "FULL_ACCESS_ROLE_KEYS",
    "MFARequired",
    "assistant_full_access",
    "call_authorized_service",
    "enforce_tool_access",
]
