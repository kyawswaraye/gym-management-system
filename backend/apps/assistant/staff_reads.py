"""Staff, role, and permission reads. Credentials stay out of every row.

The staff list is the existing non-owner profile list. Effective permissions
are the codes on the roles assigned to that profile. The permission catalog
does not include platform codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.tenants.models import GymMembershipRole, RolePermission, StaffProfile
from apps.tenants.role_services import (
    page_permissions,
    page_roles,
    permission_queryset,
    role_queryset,
)
from apps.tenants.staff_services import page_staff_profiles, staff_queryset
from apps.users.serializers import StrictSerializer

SEARCH_STAFF = "search_staff"
SEARCH_ROLES = "search_roles"
SEARCH_PERMISSIONS = "search_permissions"
SEARCH_STAFF_ACCESS = "search_staff_access"
STAFF_READ_TOOL_NAMES = (
    SEARCH_STAFF,
    SEARCH_ROLES,
    SEARCH_PERMISSIONS,
    SEARCH_STAFF_ACCESS,
)


class StaffReadQuerySerializer(StrictSerializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=120)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class PageQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def staff_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def staff_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_staff(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(StaffReadQuerySerializer, call.payload)
    search = str(query.get("search") or "")
    queryset = staff_queryset(call.gym)
    if search:
        queryset = queryset.filter(display_name__icontains=search)
    rows, meta = page_staff_profiles(
        queryset,
        gym_id=call.gym.id,
        search=search,
        profile_status="",
        membership_status="",
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _staff_row)


def search_roles(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(PageQuerySerializer, call.payload)
    rows, meta = page_roles(
        role_queryset(call.gym),
        gym_id=call.gym.id,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _role_row)


def search_permissions(call: ReadCall) -> dict[str, object]:
    query = _query(PageQuerySerializer, call.payload)
    rows, meta = page_permissions(
        permission_queryset(),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [
            {"code": row.code, "module": row.module, "description": row.description} for row in rows
        ],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_staff_access(call: ReadCall) -> dict[str, object]:
    gym_id = call.gym.id
    if gym_id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(StaffReadQuerySerializer, call.payload)
    search = str(query.get("search") or "")
    queryset = staff_queryset(call.gym)
    if search:
        queryset = queryset.filter(display_name__icontains=search)
    rows, meta = page_staff_profiles(
        queryset,
        gym_id=gym_id,
        search=search,
        profile_status="",
        membership_status="",
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, lambda row: _access_row(gym_id, row))


def _staff_row(row: StaffProfile) -> dict[str, object]:
    return {
        "id": str(row.id),
        "staff_code": row.staff_code,
        "display_name": row.display_name,
        "status": row.status,
        "employment_title": row.employment_title,
        "roles": " ".join(_role_names(row)),
    }


def _role_row(row: Any) -> dict[str, object]:
    codes = getattr(row, "rolepermission_set", None)
    listed = []
    if codes is not None:
        listed = [link.permission.code for link in codes.all()]
    return {
        "id": str(row.id),
        "key": row.key,
        "name": row.name,
        "status": "ACTIVE" if row.is_active else "INACTIVE",
        "permission_codes": " ".join(listed),
    }


def _access_row(gym_id: UUID, row: StaffProfile) -> dict[str, object]:
    role_ids = [link.role_id for link in row.gym_membership.gymmembershiprole_set.all()]
    codes = RolePermission.objects.filter(gym_id=gym_id, role_id__in=role_ids).select_related(
        "permission"
    )
    return {
        "display_name": row.display_name,
        "staff_code": row.staff_code,
        "roles": " ".join(_role_names(row)),
        "permission_codes": " ".join(sorted({link.permission.code for link in codes})),
    }


def _role_names(row: StaffProfile) -> list[str]:
    names: list[str] = []
    for link in row.gym_membership.gymmembershiprole_set.all():
        role = getattr(link, "role", None)
        if role is None:
            loaded = GymMembershipRole.objects.select_related("role").filter(id=link.id).first()
            role = None if loaded is None else loaded.role
        if role is not None:
            names.append(role.name)
    return names


def _page(rows: list[Any], meta: Mapping[str, Any], render: Any) -> dict[str, object]:
    return {
        "results": [render(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="staff",
        operation="read",
        permissions=permissions,
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _query(kind: Any, payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = kind(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_PAGE = (
    FieldRequirement("limit", "Page size from 1 to 25. Optional.", required=False, kind="integer"),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_READS = {
    SEARCH_STAFF: _contract(
        _definition(
            SEARCH_STAFF,
            "Search staff profiles in this gym. Owners are not in this list. "
            "Passwords and tokens are never returned.",
            ("staff.view",),
        ),
        (
            FieldRequirement("search", "Display name text. Optional.", required=False),
            *_PAGE,
        ),
    ),
    SEARCH_ROLES: _contract(
        _definition(
            SEARCH_ROLES,
            "Search role definitions in this gym, including each role's permission codes.",
            ("roles.view",),
        ),
        _PAGE,
    ),
    SEARCH_PERMISSIONS: _contract(
        _definition(
            SEARCH_PERMISSIONS,
            "Search permission definitions. Platform permissions are excluded.",
            ("roles.view",),
        ),
        _PAGE,
    ),
    SEARCH_STAFF_ACCESS: _contract(
        _definition(
            SEARCH_STAFF_ACCESS,
            "Search effective permissions for a staff profile in this gym. "
            "The codes come from that profile's assigned roles.",
            ("staff.view", "roles.view"),
        ),
        (
            FieldRequirement("search", "Display name text. Optional.", required=False),
            *_PAGE,
        ),
    ),
}
_READERS = {
    SEARCH_STAFF: search_staff,
    SEARCH_ROLES: search_roles,
    SEARCH_PERMISSIONS: search_permissions,
    SEARCH_STAFF_ACCESS: search_staff_access,
}
