from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.activity_tools import (
    ACTIVITY_TOOL_NAMES,
    activity_tool_definitions,
    execute_registered_tool,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.registry import closed_production_names, production_registry
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User


def test_registered_activity_tools_are_confirmed_database_writes() -> None:
    tools = {tool.name: tool for tool in activity_tool_definitions()}
    assert tuple(sorted(tools)) == ACTIVITY_TOOL_NAMES
    assert set(ACTIVITY_TOOL_NAMES) <= set(closed_production_names())
    for tool in tools.values():
        assert tool.operation == "write"
        assert tool.confirmation_required is True
        assert tool.external_side_effects == ()
        assert tool.requires_recent_mfa is False
    assert tools["create_announcement"].permissions == ("announcements.manage",)
    assert tools["create_announcement"].features == ("members",)
    assert tools["publish_announcement"].features == ("members",)
    assert tools["create_manual_check_in"].permissions == ("checkins.create",)
    assert tools["create_manual_check_in"].features == ("members", "memberships")
    assert tools["check_out_check_in"].permissions == ("checkins.update",)
    names = production_registry().names()
    assert "create_payment" in names
    assert "approve_payment" in names
    assert "invite_owner" not in names


def test_missing_permission_stops_before_announcement_or_check_in() -> None:
    gym = Gym(id=uuid4(), code="act-unit", name="Desk", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    goal = Goal(id=uuid4(), gym=gym, version=1)
    announcement = {
        "type": "GENERAL",
        "title": "Holiday hours",
        "body": "The desk closes at six.",
        "audience": "ALL_ACTIVE_MEMBERS",
    }
    with pytest.raises(PermissionDenied):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_announcement",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload=announcement,
            confirmed=True,
        )
    with pytest.raises(PermissionDenied):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_manual_check_in",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"member_id": str(uuid4())},
            confirmed=True,
        )
    request.permission_codes = frozenset({"checkins.create"})
    with pytest.raises(ExecutionRejected) as mismatched:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_manual_check_in",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"member_id": str(uuid4()), "gym_id": str(uuid4())},
            confirmed=True,
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as unknown:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_payment",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"amount": "10.00"},
            confirmed=True,
        )
    assert unknown.value.code == "unknown_tool"
