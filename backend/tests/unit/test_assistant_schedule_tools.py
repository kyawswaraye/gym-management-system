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
    CANCEL_OCCURRENCE_METHOD,
    CANCEL_OCCURRENCE_ROUTE,
    SCHEDULE_METHOD,
    SCHEDULE_ROUTE,
    UPDATE_SCHEDULE_METHOD,
    UPDATE_SCHEDULE_ROUTE,
    cancel_occurrence_command,
    execute_registered_tool,
    occurrence_cancel_finance_body,
    schedule_finance_body,
    schedule_patch_finance_body,
    schedule_tool_contract,
    schedule_tool_contracts,
    schedule_tool_definitions,
    update_schedule_command,
)
from apps.classes.serializers import (
    ClassOccurrenceCancelSerializer,
    ClassSchedulePatchSerializer,
    ClassScheduleWriteSerializer,
)
from apps.classes.views import schedule_command_body, schedule_route
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


def test_update_and_cancel_use_per_call_finance_routes() -> None:
    contracts = schedule_tool_contracts()
    update = contracts["update_class_schedule"]
    cancel = contracts["cancel_class_occurrence"]
    names = production_registry().names()
    assert update.mode == "finance_idempotent"
    assert update.http_method == UPDATE_SCHEDULE_METHOD == "PATCH"
    assert update.route == UPDATE_SCHEDULE_ROUTE == "/api/v1/class-schedules/{schedule_id}/"
    assert update.route_fields == ("schedule_id",)
    assert update.definition.permissions == ("classes.manage",)
    assert update.definition.features == ("pt",)
    assert update.definition.confirmation_required is True
    assert update.definition.external_side_effects == ()
    assert update.finance_closed is False
    assert cancel.mode == "finance_idempotent"
    assert cancel.http_method == CANCEL_OCCURRENCE_METHOD == "POST"
    assert cancel.route == CANCEL_OCCURRENCE_ROUTE
    assert cancel.route_fields == ("occurrence_id",)
    assert cancel.definition.permissions == ("classes.manage",)
    assert cancel.definition.features == ("pt",)
    assert cancel.definition.confirmation_required is True
    assert cancel.definition.external_side_effects == ()
    assert cancel.finance_closed is False
    assert "update_class_schedule" in names
    assert "cancel_class_occurrence" in names


def test_update_and_cancel_bodies_match_the_view_request_hash() -> None:
    schedule_id = uuid4()
    occurrence_id = uuid4()
    raw_update = {
        "schedule_id": str(schedule_id).upper(),
        "weekdays": [2, 0],
        "local_start_time": "18:00",
        "location": "  Studio  ",
        "capacity": 4,
    }
    command = update_schedule_command(raw_update)
    serializer = ClassSchedulePatchSerializer(
        data={key: value for key, value in raw_update.items() if key != "schedule_id"}
    )
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    tool_body = schedule_patch_finance_body(
        {key: value for key, value in raw_update.items() if key != "schedule_id"}
    )
    gym_id = uuid4()
    actor_id = uuid4()
    route = schedule_route(schedule_id)
    assert command["schedule_id"] == str(schedule_id)
    assert "schedule_id" not in tool_body
    assert set(command) == set(tool_body) | {"schedule_id"}
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="PATCH",
        route=route,
        body=tool_body,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="PATCH",
        route=route,
        body=view_body,
    )
    assert tool_body["local_start_time"] == "18:00:00"
    assert tool_body["weekdays"] == [0, 2]
    assert tool_body["location"] == "Studio"
    raw_cancel = {"occurrence_id": str(occurrence_id).upper(), "reason": "  Rain  "}
    cancel_command = cancel_occurrence_command(raw_cancel)
    cancel_serializer = ClassOccurrenceCancelSerializer(data={"reason": "  Rain  "})
    cancel_serializer.is_valid(raise_exception=True)
    view_reason = {"reason": str(cancel_serializer.validated_data["reason"])}
    cancel_body = occurrence_cancel_finance_body({"reason": "  Rain  "})
    cancel_route = f"/api/v1/class-occurrences/{occurrence_id}/cancel/"
    assert cancel_command["occurrence_id"] == str(occurrence_id)
    assert cancel_body == {"reason": "Rain"}
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=cancel_route,
        body=cancel_body,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=cancel_route,
        body=view_reason,
    )


def _session() -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="sch-unit", name="Schedule", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    return request, Goal(id=uuid4(), gym=gym, version=1)


def test_update_and_cancel_stop_before_a_service() -> None:
    request, goal = _session()
    update_payload = {"schedule_id": str(uuid4()), "location": "Hall"}
    cancel_payload = {"occurrence_id": str(uuid4()), "reason": "Rain"}
    for tool_name, payload in (
        ("update_class_schedule", update_payload),
        ("cancel_class_occurrence", cancel_payload),
    ):
        with pytest.raises(PermissionDenied):
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload=payload,
                confirmed=True,
            )
    request.permission_codes = frozenset({"classes.manage"})
    for tool_name, payload in (
        ("update_class_schedule", {**update_payload, "gym_id": str(uuid4())}),
        ("cancel_class_occurrence", {**cancel_payload, "gym_id": str(uuid4())}),
    ):
        with pytest.raises(ExecutionRejected) as mismatched:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload=payload,
                confirmed=True,
            )
        assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="update_class_schedule",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**update_payload, "confirmed": True},
            confirmed=True,
        )
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="cancel_class_occurrence",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**cancel_payload, "confirmed": True},
            confirmed=True,
        )


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
