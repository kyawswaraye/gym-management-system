from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode
from apps.assistant.member_tools import (
    MEMBER_TOOL_NAMES,
    execute_registered_tool,
    member_tool_definitions,
)
from apps.assistant.models import Goal
from apps.assistant.registry import closed_production_names, production_registry
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User


def test_registered_member_tools_are_confirmed_database_writes() -> None:
    tools = {tool.name: tool for tool in member_tool_definitions()}
    assert tuple(sorted(tools)) == MEMBER_TOOL_NAMES
    assert production_registry().names() == closed_production_names()
    for _name, tool in tools.items():
        assert tool.operation == "write"
        assert tool.confirmation_required is True
        assert tool.external_side_effects == ()
        assert tool.requires_recent_mfa is False
        assert tool.permissions
    contracts = production_registry()
    for name in MEMBER_TOOL_NAMES:
        registered = contracts.get(name)
        assert registered.features
        assert name in {
            "create_member",
            "update_member",
            "archive_member",
            "restore_member",
        } or registered.features == ("memberships",)
    assert tools["create_member"].permissions == ("members.create",)
    assert tools["create_member"].features == ("members",)
    assert tools["archive_plan"].permissions == ("memberships.update",)
    assert tools["cancel_membership"].permissions == ("memberships.cancel",)
    assert tools["suspend_membership"].features == ("memberships",)
    for tool in member_tool_definitions():
        assert tool.name in MEMBER_TOOL_NAMES
    assert ExecutionMode.SAME_TRANSACTION == "same_transaction"


def test_missing_permission_and_unknown_tool_do_not_run() -> None:
    gym = Gym(id=uuid4(), code="mem-unit", name="Members", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=False)
    goal = Goal(id=uuid4(), gym=gym, version=1)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="staff@example.test", mfa_enabled=True)
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    with pytest.raises(PermissionDenied):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_member",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={
                "first_name": "Maung",
                "last_name": "Aye",
                "join_date": "2020-01-01",
            },
            confirmed=True,
        )
    with pytest.raises(ExecutionRejected) as caught:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_payment",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={},
            confirmed=True,
        )
    assert caught.value.code == "unknown_tool"


def test_plan_duration_accepts_an_everyday_word() -> None:
    from apps.assistant.member_tools import _plan_changes
    from apps.members.serializers import MembershipPlanCreateSerializer

    stored = _plan_changes(
        {
            "name": "Yoga",
            "duration_value": 1,
            "duration_unit": "month",
            "price": 50000,
        }
    )
    serializer = MembershipPlanCreateSerializer(data=stored)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["duration_unit"] == "MONTH"


def test_saved_plan_is_not_proposed_again() -> None:
    from apps.assistant.turns import _same_saved_change

    saved = (
        "create_plan",
        {"name": "Yoga", "price": 50000, "duration_unit": "month", "duration_value": 1},
    )
    assert _same_saved_change(
        "create_plan",
        {"name": "Yoga", "price": "50000", "duration_unit": "MONTH", "duration_value": "1"},
        saved,
    )
    assert not _same_saved_change(
        "create_plan",
        {"name": "Yoga", "price": "1500", "duration_unit": "MONTH", "duration_value": "1"},
        saved,
    )
