from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.registry import (
    ToolDefinition,
    ToolRegistry,
    closed_production_names,
    production_registry,
)
from apps.assistant.staff_tools import (
    STAFF_TOOL_NAMES,
    execute_registered_tool,
    staff_tool_definitions,
)
from apps.tenants.exceptions import MFARequired
from apps.tenants.models import Gym, GymMembership
from apps.users.models import RefreshSession, User


def test_registered_staff_tools_stay_inside_the_database() -> None:
    tools = {tool.name: tool for tool in staff_tool_definitions()}
    assert tuple(sorted(tools)) == STAFF_TOOL_NAMES
    assert set(STAFF_TOOL_NAMES) <= set(closed_production_names())
    for tool in tools.values():
        assert tool.operation == "write"
        assert tool.confirmation_required is True
        assert tool.external_side_effects == ()
        assert tool.approval_required is False
    assert tools["update_staff_profile"].permissions == ("staff.manage",)
    assert tools["update_staff_profile"].requires_recent_mfa is False
    assert tools["replace_role_permissions"].permissions == ("roles.manage",)
    assert tools["update_gym_profile"].permissions == ("gym.update",)
    assert tools["update_gym_profile"].requires_active_owner is False
    assert tools["update_gym_settings"].permissions == ("settings.manage",)
    assert tools["update_gym_settings"].requires_active_owner is True
    assert tools["update_gym_settings"].requires_recent_mfa is False
    for name in ("revoke_owner", "cancel_owner_invitation"):
        assert tools[name].permissions == ()
        assert tools[name].requires_active_owner is True
        assert tools[name].requires_recent_mfa is True
    names = production_registry().names()
    assert "invite_owner" not in names
    assert "invite_staff" not in names
    granted = production_registry().authorized(
        permission_codes=frozenset({"staff.manage", "roles.manage", "settings.manage"})
    )
    granted_names = {tool.name for tool in granted}
    assert "revoke_owner" not in granted_names
    assert "cancel_owner_invitation" not in granted_names
    assert "update_gym_settings" in granted_names


def test_registry_rejects_a_tool_with_no_permission_and_no_owner_gate() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="permission"):
        registry.register(
            ToolDefinition(
                name="open_gate",
                description="Open",
                module="staff",
                operation="write",
                permissions=(),
            )
        )


def _request(
    gym: Gym,
    actor: GymMembership,
    codes: frozenset[str],
    *,
    verified_at: object | None = None,
    session: bool = True,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="owner@example.test", mfa_enabled=True)
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.refresh_session = RefreshSession(mfa_verified_at=verified_at) if session else None
    return request


def _run(
    request: Request,
    tool_name: str,
    payload: dict[str, object],
    *,
    confirmed: bool = True,
) -> None:
    gym = request.gym
    execute_registered_tool(
        request=request,
        view=APIView(),
        tool_name=tool_name,
        goal=Goal(id=uuid4(), gym=gym, version=1),
        step_id=uuid4(),
        expected_version=1,
        payload=payload,
        confirmed=confirmed,
    )


def test_staff_permission_confirmation_and_gym_are_checked_before_the_service() -> None:
    gym = Gym(id=uuid4(), code="stf-unit", name="Staff", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = _request(gym, actor, frozenset())
    with pytest.raises(PermissionDenied):
        _run(request, "update_staff_profile", {"staff_id": str(uuid4()), "display_name": "Ada"})
    allowed = _request(gym, actor, frozenset({"staff.manage"}))
    with pytest.raises(ExecutionRejected) as missing:
        _run(allowed, "update_staff_profile", {"display_name": "Ada"})
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as unconfirmed:
        _run(
            allowed,
            "update_staff_profile",
            {"staff_id": str(uuid4()), "display_name": "Ada"},
            confirmed=False,
        )
    assert unconfirmed.value.code == "confirmation_required"
    with pytest.raises(ExecutionRejected) as other_gym:
        _run(
            allowed,
            "update_staff_profile",
            {"staff_id": str(uuid4()), "display_name": "Ada", "gym_id": str(uuid4())},
        )
    assert other_gym.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as unknown:
        _run(allowed, "invite_owner", {"email": "new@example.test"})
    assert unknown.value.code == "unknown_tool"


def test_owner_mfa_failure_stops_before_the_service() -> None:
    gym = Gym(id=uuid4(), code="own-unit", name="Owners", status=Gym.Status.ACTIVE)
    owner = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    staff = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=False)
    payload = {"membership_id": str(uuid4()), "reason": "Left the gym"}
    stale = _request(gym, owner, frozenset(), verified_at=timezone.now() - timedelta(hours=2))
    missing = _request(gym, owner, frozenset(), session=False)
    outsider = _request(gym, staff, frozenset({"staff.manage"}), session=False)
    settings_request = _request(gym, staff, frozenset({"settings.manage"}))
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        _run(stale, "revoke_owner", payload)
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        _run(missing, "cancel_owner_invitation", {"membership_id": str(uuid4())})
    with override_settings(MFA_ENFORCED=True), pytest.raises(PermissionDenied):
        _run(outsider, "revoke_owner", payload)
    with pytest.raises(PermissionDenied):
        _run(settings_request, "update_gym_settings", {"week_starts_on": 0})
