from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import (
    ATTEND_BOOKING_ROUTE,
    BOOKING_ACTION_METHOD,
    BOOKING_TOOL_NAMES,
    CANCEL_BOOKING_ROUTE,
    CREATE_BOOKING_METHOD,
    CREATE_BOOKING_ROUTE,
    NO_SHOW_BOOKING_ROUTE,
    attend_booking_command,
    booking_cancel_finance_body,
    booking_create_finance_body,
    booking_empty_finance_body,
    booking_tool_contracts,
    booking_tool_definitions,
    cancel_booking_command,
    create_booking_command,
    execute_registered_tool,
    no_show_booking_command,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.registry import production_registry
from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
from apps.assistant.training_tools import TRAINING_TOOL_NAMES
from apps.assistant.turns import _offered
from apps.classes.booking_views import EmptyCommandSerializer
from apps.classes.serializers import ClassBookingCancelSerializer, ClassBookingWriteSerializer
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_BOOKING_NAMES = (
    "attend_class_booking",
    "cancel_class_booking",
    "create_class_booking",
    "mark_class_booking_no_show",
)


def _create_raw() -> dict[str, object]:
    return {
        "class_occurrence_id": str(uuid4()).upper(),
        "member_id": str(uuid4()).upper(),
        "member_service_package_id": str(uuid4()).upper(),
        "unmetered_reason": "  not used  ",
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="book-unit", name="Booking", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset() if codes is None else codes
    return request, Goal(id=uuid4(), gym=gym, version=1)


def test_booking_tools_are_registered_finance_commands() -> None:
    contracts = booking_tool_contracts()
    definitions = {tool.name: tool for tool in booking_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == BOOKING_TOOL_NAMES == _BOOKING_NAMES
    assert set(BOOKING_TOOL_NAMES).isdisjoint(TRAINING_TOOL_NAMES)
    assert set(BOOKING_TOOL_NAMES).isdisjoint(SCHEDULE_TOOL_NAMES)
    assert set(TRAINING_TOOL_NAMES) <= set(names)
    assert set(SCHEDULE_TOOL_NAMES) <= set(names)
    assert set(BOOKING_TOOL_NAMES) <= set(names)
    create = contracts["create_class_booking"]
    attend = contracts["attend_class_booking"]
    no_show = contracts["mark_class_booking_no_show"]
    cancel = contracts["cancel_class_booking"]
    assert create.mode == attend.mode == no_show.mode == cancel.mode == "finance_idempotent"
    assert create.finance_closed is attend.finance_closed is False
    assert no_show.finance_closed is cancel.finance_closed is False
    assert create.http_method == CREATE_BOOKING_METHOD == "POST"
    assert create.route == CREATE_BOOKING_ROUTE == "/api/v1/class-bookings/"
    assert create.route_fields == ()
    assert create.permission_mode == "any"
    assert create.definition.permissions == ("classes.book", "classes.manage")
    assert attend.permission_mode == no_show.permission_mode == "all"
    assert cancel.permission_mode == "any"
    assert cancel.definition.permissions == ("classes.book", "classes.manage")
    assert attend.definition.permissions == ("classes.attendance",)
    assert no_show.definition.permissions == ("classes.attendance",)
    for contract in contracts.values():
        assert contract.definition.features == ("pt",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "classes"
    assert attend.http_method == no_show.http_method == cancel.http_method == BOOKING_ACTION_METHOD
    assert attend.route == ATTEND_BOOKING_ROUTE
    assert no_show.route == NO_SHOW_BOOKING_ROUTE
    assert cancel.route == CANCEL_BOOKING_ROUTE
    assert attend.route_fields == no_show.route_fields == cancel.route_fields == ("booking_id",)


def test_finance_bodies_match_the_view_request_hash() -> None:
    raw = _create_raw()
    serializer = ClassBookingWriteSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    view_body = dict(serializer.validated_data)
    tool_body = booking_create_finance_body(raw)
    command = create_booking_command(raw)
    gym_id = uuid4()
    actor_id = uuid4()
    assert command == tool_body
    assert tool_body["unmetered"] is False
    assert tool_body["unmetered_reason"] == "not used"
    assert tool_body["class_occurrence_id"] == str(serializer.validated_data["class_occurrence_id"])
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=CREATE_BOOKING_ROUTE,
        body=tool_body,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=CREATE_BOOKING_ROUTE,
        body=view_body,
    )
    omitted = {
        "class_occurrence_id": raw["class_occurrence_id"],
        "member_id": raw["member_id"],
    }
    omitted_serializer = ClassBookingWriteSerializer(data=omitted)
    omitted_serializer.is_valid(raise_exception=True)
    omitted_body = booking_create_finance_body(omitted)
    assert "member_service_package_id" not in omitted_body
    assert omitted_body["unmetered"] is False
    assert omitted_body["unmetered_reason"] == ""
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=CREATE_BOOKING_ROUTE,
        body=omitted_body,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=CREATE_BOOKING_ROUTE,
        body=dict(omitted_serializer.validated_data),
    )
    booking_id = uuid4()
    attend = attend_booking_command({"booking_id": str(booking_id).upper()})
    no_show = no_show_booking_command({"booking_id": str(booking_id)})
    empty = EmptyCommandSerializer(data={})
    empty.is_valid(raise_exception=True)
    assert attend == no_show == {"booking_id": str(booking_id)}
    assert booking_empty_finance_body({}) == dict(empty.validated_data) == {}
    attend_route = f"/api/v1/class-bookings/{booking_id}/attend/"
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=attend_route,
        body={},
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=attend_route,
        body=dict(empty.validated_data),
    )
    cancel_raw = {"booking_id": str(booking_id).upper(), "reason": "  Rain day  "}
    cancel_command = cancel_booking_command(cancel_raw)
    cancel_body = booking_cancel_finance_body({"reason": "  Rain day  "})
    cancel_serializer = ClassBookingCancelSerializer(data={"reason": "  Rain day  "})
    cancel_serializer.is_valid(raise_exception=True)
    assert cancel_command["booking_id"] == str(booking_id)
    assert cancel_body == {"reason": "Rain day"}
    cancel_route = f"/api/v1/class-bookings/{booking_id}/cancel/"
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
        body=dict(cancel_serializer.validated_data),
    )


def test_permission_and_gym_stop_before_a_booking_service() -> None:
    request, goal = _session()
    request.gym_membership.is_owner = False
    create_payload = _create_raw()
    attend_payload = {"booking_id": str(uuid4())}
    no_show_payload = {"booking_id": str(uuid4())}
    cancel_payload = {"booking_id": str(uuid4()), "reason": "Rain"}
    for tool_name, payload in (
        ("create_class_booking", create_payload),
        ("attend_class_booking", attend_payload),
        ("mark_class_booking_no_show", no_show_payload),
        ("cancel_class_booking", cancel_payload),
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
    request.permission_codes = frozenset({"classes.book", "classes.attendance"})
    for tool_name, payload in (
        ("create_class_booking", {**create_payload, "gym_id": str(uuid4())}),
        ("attend_class_booking", {**attend_payload, "gym_id": str(uuid4())}),
        ("mark_class_booking_no_show", {**no_show_payload, "gym_id": str(uuid4())}),
        ("cancel_class_booking", {**cancel_payload, "gym_id": str(uuid4())}),
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
            tool_name="create_class_booking",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**create_payload, "confirmed": True},
            confirmed=True,
        )
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="attend_class_booking",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**attend_payload, "reason": "extra"},
            confirmed=True,
        )


def _offer_request(codes: frozenset[str]) -> Request:
    gym = Gym(id=uuid4(), code="offer", name="Offer", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=False)
    request = Request(APIRequestFactory().get("/api/v1/assistant/turns/"))
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    return request


def test_booking_offer_accepts_either_book_or_manage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.assistant.turns._entitled", lambda request, features: True)
    booked = _offered(_offer_request(frozenset({"classes.book"})))
    managed = _offered(_offer_request(frozenset({"classes.manage"})))
    attendance = _offered(_offer_request(frozenset({"classes.attendance"})))
    assert "create_class_booking" in booked
    assert "cancel_class_booking" in booked
    assert "attend_class_booking" not in booked
    assert "mark_class_booking_no_show" not in booked
    assert "create_class_booking" in managed
    assert "cancel_class_booking" in managed
    assert "attend_class_booking" not in managed
    assert "create_class_booking" not in attendance
    assert "cancel_class_booking" not in attendance
    assert "attend_class_booking" in attendance
    assert "mark_class_booking_no_show" in attendance
