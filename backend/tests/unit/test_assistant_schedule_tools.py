from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.registry import production_registry
from apps.assistant.schedule_tools import (
    SCHEDULE_METHOD,
    SCHEDULE_ROUTE,
    execute_registered_tool,
    schedule_finance_body,
    schedule_tool_contract,
    schedule_tool_definitions,
)
from apps.classes.serializers import ClassScheduleWriteSerializer
from apps.classes.views import schedule_command_body
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User


def _payload() -> dict[str, object]:
    return {
        "class_type_id": str(uuid4()),
        "trainer_staff_profile_id": str(uuid4()),
        "weekdays": [2, 0],
        "local_start_time": "18:00",
        "local_end_time": "18:45",
        "effective_from": "2026-11-16",
        "effective_until": "2026-11-16",
        "location": "  Studio  ",
        "location_is_exclusive": True,
        "capacity": 8,
    }


def test_schedule_tool_uses_the_finance_command_contract() -> None:
    tool = schedule_tool_definitions()[0]
    contract = schedule_tool_contract()
    assert tool.name == "create_class_schedule"
    assert tool.operation == "write"
    assert tool.confirmation_required is True
    assert tool.external_side_effects == ()
    assert tool.requires_recent_mfa is False
    assert tool.permissions == ("classes.manage",)
    assert tool.features == ("pt",)
    assert contract.mode == "finance_idempotent"
    assert contract.http_method == SCHEDULE_METHOD == "POST"
    assert contract.route == SCHEDULE_ROUTE == "/api/v1/class-schedules/"
    assert contract.finance_closed is False
    assert "create_class_schedule" in production_registry().names()


def test_finance_body_matches_the_view_request_hash() -> None:
    raw = _payload()
    serializer = ClassScheduleWriteSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    tool_body = schedule_finance_body(raw)
    gym_id = uuid4()
    actor_id = uuid4()
    view_hash = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=SCHEDULE_ROUTE,
        body=view_body,
    )
    tool_hash = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=SCHEDULE_ROUTE,
        body=tool_body,
    )
    assert tool_hash == view_hash
    assert tool_body["local_start_time"] == "18:00:00"
    assert tool_body["weekdays"] == [0, 2]
    assert tool_body["location"] == "Studio"


def test_permission_and_gym_stop_before_a_schedule_service() -> None:
    gym = Gym(id=uuid4(), code="sch-unit", name="Schedule", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
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
            tool_name="create_class_schedule",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload=_payload(),
            confirmed=True,
        )
    request.permission_codes = frozenset({"classes.manage"})
    with pytest.raises(ExecutionRejected) as mismatched:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_class_schedule",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_payload(), "gym_id": str(uuid4())},
            confirmed=True,
        )
    assert mismatched.value.code == "gym_mismatch"


def test_payload_confirmed_flag_is_not_a_schedule_field() -> None:
    gym = Gym(id=uuid4(), code="sch-confirm", name="Schedule", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset({"classes.manage"})
    goal = Goal(id=uuid4(), gym=gym, version=1)
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_class_schedule",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_payload(), "confirmed": True},
            confirmed=True,
        )
