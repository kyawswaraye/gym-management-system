from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.registry import closed_production_names, production_registry
from apps.assistant.training_tools import (
    TRAINING_TOOL_NAMES,
    execute_registered_tool,
    training_tool_definitions,
)
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User


def test_registered_training_tools_are_confirmed_database_writes() -> None:
    tools = {tool.name: tool for tool in training_tool_definitions()}
    assert tuple(sorted(tools)) == TRAINING_TOOL_NAMES
    assert set(TRAINING_TOOL_NAMES) <= set(closed_production_names())
    for tool in tools.values():
        assert tool.operation == "write"
        assert tool.confirmation_required is True
        assert tool.external_side_effects == ()
        assert tool.features == ("pt",)
        assert tool.requires_recent_mfa is False
    assert tools["create_assignment"].permissions == ("pt.assignments.manage",)
    assert tools["create_service_package"].permissions == ("pt.schedule.manage",)
    assert tools["create_class_type"].permissions == ("classes.manage",)
    assert tools["update_class_type"].permissions == ("classes.manage",)
    names = production_registry().names()
    assert "create_payment" in names
    assert "approve_payment" in names
    assert "create_class_booking" not in TRAINING_TOOL_NAMES
    assert "create_class_booking" in names
    assert "create_class_schedule" in names


def test_missing_permission_stops_before_a_training_service() -> None:
    gym = Gym(id=uuid4(), code="trn-unit", name="Training", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=False)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    goal = Goal(id=uuid4(), gym=gym, version=1)
    with pytest.raises(PermissionDenied):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_assignment",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={
                "trainer_staff_profile_id": str(uuid4()),
                "member_id": str(uuid4()),
                "effective_from": "2026-11-16",
            },
            confirmed=True,
        )
    request.permission_codes = frozenset({"classes.manage"})
    with pytest.raises(ExecutionRejected) as mismatched:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_class_type",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={
                "name": "Yoga",
                "default_duration_minutes": 45,
                "default_capacity": 8,
                "gym_id": str(uuid4()),
            },
            confirmed=True,
        )
    assert mismatched.value.code == "gym_mismatch"
