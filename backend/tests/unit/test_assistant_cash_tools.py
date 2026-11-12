from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
from apps.assistant.cash_tools import (
    ACTION_METHOD,
    ADJUST_ROUTE,
    CASH_TOOL_NAMES,
    OPEN_METHOD,
    OPEN_ROUTE,
    RECONCILE_ROUTE,
    adjust_cash_command,
    adjust_finance_body,
    cash_tool_contracts,
    cash_tool_definitions,
    execute_registered_tool,
    open_cash_command,
    open_finance_body,
    reconcile_cash_command,
    reconcile_finance_body,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.inventory_tools import INVENTORY_TOOL_NAMES
from apps.assistant.models import Goal
from apps.assistant.registry import production_registry
from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
from apps.assistant.training_tools import TRAINING_TOOL_NAMES
from apps.cashbook.serializers import (
    AdjustCashSessionSerializer,
    OpenCashSessionSerializer,
    ReconcileCashSessionSerializer,
    money_text,
)
from apps.cashbook.services import command_route
from apps.common.codes import DEFAULT_REGISTER
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "adjust_cash_session",
    "open_cash_session",
    "reconcile_cash_session",
)
_OPEN = frozenset({"cashbook.open"})
_ADJUST = frozenset({"cashbook.adjust"})
_RECONCILE = frozenset({"cashbook.reconcile"})


def _open_raw() -> dict[str, object]:
    return {"business_date": "2026-11-09", "opening_cash": "100.00"}


def _adjust_raw(session_id: object) -> dict[str, object]:
    return {
        "session_id": session_id,
        "direction": "IN",
        "amount": "5.00",
        "currency": "THB",
        "business_date": "2026-11-09",
        "description": "  Float  ",
        "reason": "  Counted  ",
    }


def _reconcile_raw(session_id: object) -> dict[str, object]:
    return {"session_id": session_id, "actual_closing": "105.00"}


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="cash-unit", name="Cash", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset() if codes is None else codes
    return request, Goal(id=uuid4(), gym=gym, version=1)


def _assert_same_hash(
    left: dict[str, object],
    right: dict[str, object],
    *,
    method: str,
    route: str,
) -> None:
    gym_id = uuid4()
    actor_id = uuid4()
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=left,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=right,
    )


def _view_open(raw: dict[str, object]) -> dict[str, object]:
    serializer = OpenCashSessionSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "register_code": fields["register_code"],
        "business_date": fields["business_date"].isoformat(),
        "opening_cash": money_text(fields["opening_cash"]),
    }


def _view_adjust(raw: dict[str, object], session_id: str) -> dict[str, object]:
    serializer = AdjustCashSessionSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "cash_session_id": session_id,
        "direction": fields["direction"],
        "amount": money_text(fields["amount"]),
        "currency": fields["currency"],
        "business_date": fields["business_date"].isoformat(),
        "description": fields["description"],
        "reason": fields["reason"],
    }


def _view_reconcile(raw: dict[str, object], session_id: str) -> dict[str, object]:
    serializer = ReconcileCashSessionSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "cash_session_id": session_id,
        "actual_closing": money_text(fields["actual_closing"]),
        "difference_reason": str(fields.get("difference_reason", "")),
    }


def test_cash_tools_are_closed_finance_commands() -> None:
    contracts = cash_tool_contracts()
    definitions = {tool.name: tool for tool in cash_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == CASH_TOOL_NAMES == _NAMES
    assert set(CASH_TOOL_NAMES).isdisjoint(INVENTORY_TOOL_NAMES)
    assert set(CASH_TOOL_NAMES).isdisjoint(BOOKING_TOOL_NAMES)
    assert set(CASH_TOOL_NAMES).isdisjoint(SCHEDULE_TOOL_NAMES)
    assert set(CASH_TOOL_NAMES).isdisjoint(TRAINING_TOOL_NAMES)
    assert set(INVENTORY_TOOL_NAMES) <= set(names)
    assert set(BOOKING_TOOL_NAMES) <= set(names)
    assert set(SCHEDULE_TOOL_NAMES) <= set(names)
    assert set(TRAINING_TOOL_NAMES) <= set(names)
    assert set(CASH_TOOL_NAMES) <= set(names)
    opened = contracts["open_cash_session"]
    adjusted = contracts["adjust_cash_session"]
    reconciled = contracts["reconcile_cash_session"]
    assert opened.http_method == OPEN_METHOD == "POST"
    assert opened.route == OPEN_ROUTE == "/api/v1/cash-sessions/open/"
    assert opened.route_fields == ()
    assert adjusted.http_method == ACTION_METHOD == "POST"
    assert adjusted.route == ADJUST_ROUTE
    assert adjusted.route_fields == ("session_id",)
    assert reconciled.http_method == ACTION_METHOD == "POST"
    assert reconciled.route == RECONCILE_ROUTE
    assert reconciled.route_fields == ("session_id",)
    assert opened.definition.permissions == ("cashbook.open",)
    assert adjusted.definition.permissions == ("cashbook.adjust",)
    assert reconciled.definition.permissions == ("cashbook.reconcile",)
    for contract in contracts.values():
        assert contract.mode == "finance_idempotent"
        assert contract.finance_closed is True
        assert contract.permission_mode == "all"
        assert contract.definition.features == ("finance",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "cashbook"


def test_finance_bodies_match_the_view_request_hash() -> None:
    opened = _view_open(_open_raw())
    assert open_finance_body(_open_raw()) == opened
    assert open_cash_command(_open_raw()) == opened
    assert opened["register_code"] == DEFAULT_REGISTER == "MAIN"
    assert opened["opening_cash"] == "100.00"
    assert opened["business_date"] == "2026-11-09"
    _assert_same_hash(opened, _view_open(_open_raw()), method="POST", route=OPEN_ROUTE)

    session_id = uuid4()
    adjust_raw = _adjust_raw(str(session_id).upper())
    command = adjust_cash_command(adjust_raw)
    view = _view_adjust(
        {key: value for key, value in adjust_raw.items() if key != "session_id"},
        str(session_id),
    )
    assert command["session_id"] == str(session_id)
    assert (
        adjust_finance_body(
            {key: value for key, value in adjust_raw.items() if key != "session_id"},
            session_id=str(session_id),
        )
        == view
    )
    body = {key: value for key, value in command.items() if key != "session_id"}
    assert body == view
    assert body["description"] == "Float"
    assert body["reason"] == "Counted"
    assert body["amount"] == "5.00"
    route = command_route(session_id=session_id, action="adjustments")
    assert route == f"/api/v1/cash-sessions/{session_id}/adjustments/"
    _assert_same_hash(body, view, method="POST", route=route)
    invalid = adjust_cash_command({**adjust_raw, "session_id": "not-a-uuid"})
    assert invalid["session_id"] == "not-a-uuid"
    assert "cash_session_id" not in invalid

    reconcile_raw = _reconcile_raw(str(session_id).upper())
    reconcile_command = reconcile_cash_command(reconcile_raw)
    reconcile_view = _view_reconcile({"actual_closing": "105.00"}, str(session_id))
    assert reconcile_command["session_id"] == str(session_id)
    assert reconcile_finance_body({"actual_closing": "105.00"}, session_id=str(session_id)) == (
        reconcile_view
    )
    reconcile_body = {key: value for key, value in reconcile_command.items() if key != "session_id"}
    assert reconcile_body == reconcile_view
    assert reconcile_body["difference_reason"] == ""
    assert reconcile_body["actual_closing"] == "105.00"
    reconcile_route = command_route(session_id=session_id, action="reconcile")
    _assert_same_hash(reconcile_body, reconcile_view, method="POST", route=reconcile_route)


def test_permission_and_gym_stop_before_a_cash_service() -> None:
    request, goal = _session()
    request.gym_membership.is_owner = False
    session_id = str(uuid4())
    payloads = (
        ("open_cash_session", _open_raw()),
        ("adjust_cash_session", _adjust_raw(session_id)),
        ("reconcile_cash_session", _reconcile_raw(session_id)),
    )
    for tool_name, payload in payloads:
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
    codes = {
        "open_cash_session": _OPEN,
        "adjust_cash_session": _ADJUST,
        "reconcile_cash_session": _RECONCILE,
    }
    for tool_name, payload in payloads:
        request.permission_codes = codes[tool_name]
        with pytest.raises(ExecutionRejected) as mismatched:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={**payload, "gym_id": str(uuid4())},
                confirmed=True,
            )
        assert mismatched.value.code == "gym_mismatch"
    request.permission_codes = _OPEN
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="open_cash_session",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"business_date": "2026-11-09", "opening_cash": "-1.00"},
            confirmed=True,
        )
    request.permission_codes = _ADJUST
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="adjust_cash_session",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_adjust_raw(session_id), "reason": "   "},
            confirmed=True,
        )
