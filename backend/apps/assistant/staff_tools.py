"""Staff, role, and gym tools. Each one calls the existing service.

Owner revoke and invitation cancellation use the same active-owner and
recent-MFA checks as the current views. Invitation creation stays
unregistered because it sends mail before the database commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.common.api.exceptions import AuthenticationFailed
from apps.tenants.role_serializers import (
    MembershipRoleReplaceSerializer,
    RoleArchiveSerializer,
    RoleCreateSerializer,
    RolePermissionReplaceSerializer,
    RoleUpdateSerializer,
)
from apps.tenants.role_services import (
    archive_role,
    create_role,
    replace_membership_roles,
    replace_role_permissions,
    update_role,
)
from apps.tenants.serializers import (
    GymProfileUpdateSerializer,
    GymSettingsUpdateSerializer,
    OwnerRevokeInputSerializer,
)
from apps.tenants.services import (
    cancel_owner_invitation,
    revoke_owner,
    update_gym_profile,
    update_gym_settings,
)
from apps.tenants.staff_serializers import StaffLifecycleSerializer, StaffUpdateSerializer
from apps.tenants.staff_services import activate_staff, suspend_staff, update_staff_profile


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    effect: Any


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
    module: str,
    *,
    owner: bool = False,
    mfa: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module=module,
        operation="write",
        permissions=permissions,
        requires_active_owner=owner,
        requires_recent_mfa=mfa,
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...] | None,
) -> ToolContract:
    prerequisite = None
    if fields is not None:
        prerequisite = Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        )
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.SAME_TRANSACTION,
        prerequisite=prerequisite,
    )


def _body(payload: Mapping[str, object], *omit: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in omit}


def _validated(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _identifier(payload: Mapping[str, object], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValidationError({key: ["Enter a valid identifier."]})
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError({key: ["Enter a valid identifier."]}) from exc


def _reference(value: object) -> str:
    if isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ExecutionRejected(
            "The database effect needs a reference.",
            code="missing_effect_reference",
        )
    if not text or len(text) > 64:
        raise ExecutionRejected(
            "The database effect needs a reference.",
            code="missing_effect_reference",
        )
    return text


def _authz_version(call: ToolCall) -> int:
    if call.authz_version is None:
        raise AuthenticationFailed()
    return call.authz_version


def _update_staff_profile(call: ToolCall) -> str:
    staff_id = _identifier(call.payload, "staff_id")
    changes = _validated(StaffUpdateSerializer, _body(call.payload, "staff_id"))
    profile = update_staff_profile(
        gym_id=call.gym.id,
        staff_id=staff_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        changes=changes,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(profile.id)


def _activate_staff(call: ToolCall) -> str:
    return _staff_lifecycle(call, activate_staff)


def _suspend_staff(call: ToolCall) -> str:
    return _staff_lifecycle(call, suspend_staff)


def _staff_lifecycle(call: ToolCall, command: Any) -> str:
    staff_id = _identifier(call.payload, "staff_id")
    payload = _validated(StaffLifecycleSerializer, _body(call.payload, "staff_id"))
    profile = command(
        gym_id=call.gym.id,
        staff_id=staff_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        reason=str(payload["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(profile.id)


def _create_role(call: ToolCall) -> str:
    version = _authz_version(call)
    payload = _validated(RoleCreateSerializer, call.payload)
    role = create_role(
        gym_id=call.gym.id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        expected_authz_version=version,
        key=str(payload["key"]),
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        is_active=bool(payload.get("is_active", True)),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(role["id"])


def _update_role(call: ToolCall) -> str:
    version = _authz_version(call)
    role_id = _identifier(call.payload, "role_id")
    changes = _validated(RoleUpdateSerializer, _body(call.payload, "role_id"))
    role = update_role(
        gym_id=call.gym.id,
        role_id=role_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        expected_authz_version=version,
        changes=changes,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(role["id"])


def _archive_role(call: ToolCall) -> str:
    version = _authz_version(call)
    role_id = _identifier(call.payload, "role_id")
    _validated(RoleArchiveSerializer, _body(call.payload, "role_id"))
    role = archive_role(
        gym_id=call.gym.id,
        role_id=role_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        expected_authz_version=version,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(role["id"])


def _replace_role_permissions(call: ToolCall) -> str:
    version = _authz_version(call)
    role_id = _identifier(call.payload, "role_id")
    payload = _validated(RolePermissionReplaceSerializer, _body(call.payload, "role_id"))
    role = replace_role_permissions(
        gym_id=call.gym.id,
        role_id=role_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        expected_authz_version=version,
        permission_codes=list(payload["permission_codes"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(role["id"])


def _replace_membership_roles(call: ToolCall) -> str:
    version = _authz_version(call)
    membership_id = _identifier(call.payload, "membership_id")
    payload = _validated(MembershipRoleReplaceSerializer, _body(call.payload, "membership_id"))
    updated = replace_membership_roles(
        gym_id=call.gym.id,
        membership_id=membership_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        expected_authz_version=version,
        role_ids=list(payload["role_ids"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(updated["gym_membership_id"])


def _update_gym_profile(call: ToolCall) -> str:
    payload = _validated(GymProfileUpdateSerializer, call.payload)
    gym = update_gym_profile(
        gym_id=call.gym.id,
        data=payload,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(gym.id)


def _update_gym_settings(call: ToolCall) -> str:
    payload = _validated(GymSettingsUpdateSerializer, call.payload)
    settings_row = update_gym_settings(
        gym_id=call.gym.id,
        data=payload,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(settings_row.id)


def _revoke_owner(call: ToolCall) -> str:
    membership_id = _identifier(call.payload, "membership_id")
    payload = _validated(OwnerRevokeInputSerializer, _body(call.payload, "membership_id"))
    owner = revoke_owner(
        gym_id=call.gym.id,
        target_membership_id=membership_id,
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        reason=str(payload["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(owner.id)


def _cancel_owner_invitation(call: ToolCall) -> str:
    owner = cancel_owner_invitation(
        gym_id=call.gym.id,
        target_membership_id=_identifier(call.payload, "membership_id"),
        actor=call.actor.user,
        actor_membership_id=call.actor.id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _reference(owner.id)


def _tools() -> tuple[RegisteredTool, ...]:
    staff = "staff"
    roles = "roles"
    gym = "gym"
    owners = "owners"
    specs: tuple[tuple[ToolDefinition, tuple[FieldRequirement, ...] | None, Any], ...] = (
        (
            _definition(
                "update_staff_profile",
                "Update a staff profile",
                ("staff.manage",),
                staff,
            ),
            (FieldRequirement("staff_id", "Staff profile"),),
            _update_staff_profile,
        ),
        (
            _definition(
                "activate_staff",
                "Activate a suspended staff membership",
                ("staff.manage",),
                staff,
            ),
            (
                FieldRequirement("staff_id", "Staff profile"),
                FieldRequirement("reason", "Reason"),
            ),
            _activate_staff,
        ),
        (
            _definition(
                "suspend_staff",
                "Suspend an active staff membership",
                ("staff.manage",),
                staff,
            ),
            (
                FieldRequirement("staff_id", "Staff profile"),
                FieldRequirement("reason", "Reason"),
            ),
            _suspend_staff,
        ),
        (
            _definition("create_role", "Create a custom role", ("roles.manage",), roles),
            (FieldRequirement("name", "Role name"),),
            _create_role,
        ),
        (
            _definition("update_role", "Update a custom role", ("roles.manage",), roles),
            (FieldRequirement("role_id", "Role"),),
            _update_role,
        ),
        (
            _definition("archive_role", "Archive a custom role", ("roles.manage",), roles),
            (FieldRequirement("role_id", "Role"),),
            _archive_role,
        ),
        (
            _definition(
                "replace_role_permissions",
                "Replace a role's permissions",
                ("roles.manage",),
                roles,
            ),
            (FieldRequirement("role_id", "Role"),),
            _replace_role_permissions,
        ),
        (
            _definition(
                "replace_membership_roles",
                "Replace a staff membership's roles",
                ("staff.manage",),
                roles,
            ),
            (FieldRequirement("membership_id", "Staff membership"),),
            _replace_membership_roles,
        ),
        (
            _definition("update_gym_profile", "Update the gym profile", ("gym.update",), gym),
            None,
            _update_gym_profile,
        ),
        (
            _definition(
                "update_gym_settings",
                "Update gym settings",
                ("settings.manage",),
                gym,
                owner=True,
            ),
            None,
            _update_gym_settings,
        ),
        (
            _definition(
                "revoke_owner",
                "Revoke another active owner",
                (),
                owners,
                owner=True,
                mfa=True,
            ),
            (
                FieldRequirement("membership_id", "Owner membership"),
                FieldRequirement("reason", "Reason"),
            ),
            _revoke_owner,
        ),
        (
            _definition(
                "cancel_owner_invitation",
                "Cancel a pending owner invitation",
                (),
                owners,
                owner=True,
                mfa=True,
            ),
            (FieldRequirement("membership_id", "Invited owner"),),
            _cancel_owner_invitation,
        ),
    )
    return tuple(
        RegisteredTool(contract=_contract(definition, fields), effect=effect)
        for definition, fields, effect in specs
    )


_REGISTERED = {tool.contract.definition.name: tool for tool in _tools()}
STAFF_TOOL_NAMES = tuple(sorted(_REGISTERED))


def staff_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def staff_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool.contract for name, tool in _REGISTERED.items()}


def execute_registered_tool(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    goal: Goal,
    step_id: UUID,
    expected_version: int,
    payload: Mapping[str, object],
    confirmed: bool,
) -> ExecutionResult:
    """Run one registered staff, role, or gym tool as the signed-in staff member."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_confirmed_tool(
        request=request,
        view=view,
        contract=tool.contract,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
        effect=tool.effect,
    )
