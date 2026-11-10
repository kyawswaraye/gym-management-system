from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.cash_tools import CASH_TOOL_NAMES
from apps.assistant.execution import ExecutionRejected
from apps.assistant.expense_tools import EXPENSE_TOOL_NAMES
from apps.assistant.models import Goal
from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES
from apps.assistant.registry import production_registry
from apps.assistant.retail_tools import RETAIL_TOOL_NAMES
from apps.assistant.salary_tools import (
    CANCEL_ROUTE,
    CREATE_ROUTE,
    LINES_ROUTE,
    SALARY_TOOL_NAMES,
    SUBMIT_ROUTE,
    UPDATE_ROUTE,
    VOID_ROUTE,
    cancel_salary_period_command,
    create_salary_period_body,
    create_salary_period_command,
    execute_registered_tool,
    replace_salary_lines_body,
    replace_salary_lines_command,
    salary_tool_contracts,
    salary_tool_definitions,
    submit_salary_period_command,
    update_salary_period_command,
    void_salary_period_body,
    void_salary_period_command,
)
from apps.common.finance.idempotency import canonical_request_hash
from apps.salaries.serializers import (
    CancelSalarySerializer,
    SalaryLinesReplaceSerializer,
    SalaryPeriodUpdateSerializer,
    SalaryPeriodWriteSerializer,
    VoidSalarySerializer,
)
from apps.salaries.services import command_route
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "cancel_salary_period",
    "create_salary_period",
    "replace_salary_lines",
    "submit_salary_period",
    "update_salary_period",
    "void_salary_period",
)


def _ordered_pair() -> tuple[str, str]:
    earlier = uuid4()
    later = uuid4()
    if earlier > later:
        earlier, later = later, earlier
    return str(earlier), str(later)


def _line(staff_id: str, amount: str, description: str) -> dict[str, str]:
    return {
        "staff_profile_id": staff_id,
        "amount": amount,
        "currency": "THB",
        "payment_method": "BANK_TRANSFER",
        "description": description,
    }


def _create_raw() -> dict[str, object]:
    return {
        "period_start": "2026-11-01",
        "period_end": "2026-11-10",
        "business_date": "2026-11-10",
        "description": "  November payroll  ",
    }


def _lines_raw() -> dict[str, object]:
    earlier, later = _ordered_pair()
    return {
        "lines": [
            _line(later, "250.50", "  Bonus  "),
            _line(earlier.upper(), "1500.00", "  Base pay  "),
        ]
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="sal-unit", name="Salaries", status=Gym.Status.ACTIVE)
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


def _hashed(command: dict[str, object], route_key: str) -> dict[str, object]:
    return {key: value for key, value in command.items() if key != route_key}


def test_salary_tools_are_closed_finance_commands() -> None:
    contracts = salary_tool_contracts()
    definitions = {tool.name: tool for tool in salary_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == SALARY_TOOL_NAMES == _NAMES
    assert "approve_salary_period" not in names
    assert "reject_salary_period" not in names
    assert set(SALARY_TOOL_NAMES).isdisjoint(RETAIL_TOOL_NAMES)
    assert set(SALARY_TOOL_NAMES).isdisjoint(EXPENSE_TOOL_NAMES)
    assert set(SALARY_TOOL_NAMES).isdisjoint(PAYMENT_TOOL_NAMES)
    assert set(SALARY_TOOL_NAMES).isdisjoint(CASH_TOOL_NAMES)
    assert set(RETAIL_TOOL_NAMES) <= set(names)
    assert set(SALARY_TOOL_NAMES) <= set(names)
    created = contracts["create_salary_period"]
    updated = contracts["update_salary_period"]
    lines = contracts["replace_salary_lines"]
    submitted = contracts["submit_salary_period"]
    cancelled = contracts["cancel_salary_period"]
    voided = contracts["void_salary_period"]
    assert created.http_method == "POST"
    assert created.route == CREATE_ROUTE == command_route(period_id=None, action="")
    assert created.route_fields == ()
    assert updated.http_method == "PATCH"
    assert updated.route == UPDATE_ROUTE
    assert updated.route_fields == ("period_route_id",)
    assert lines.http_method == "PUT"
    assert lines.route == LINES_ROUTE
    assert lines.route_fields == ("period_route_id",)
    assert submitted.route == SUBMIT_ROUTE
    assert cancelled.route == CANCEL_ROUTE
    assert voided.route == VOID_ROUTE
    assert created.definition.permissions == ("salaries.create",)
    assert updated.definition.permissions == ("salaries.update",)
    assert lines.definition.permissions == ("salaries.update",)
    assert submitted.definition.permissions == ("salaries.submit",)
    assert cancelled.definition.permissions == ("salaries.update",)
    assert voided.definition.permissions == ("salaries.void",)
    for contract in contracts.values():
        assert contract.mode == "finance_idempotent"
        assert contract.finance_closed is True
        assert contract.permission_mode == "all"
        assert contract.definition.features == ("finance",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        assert contract.definition.approval_required is False
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "salaries"


def test_finance_bodies_match_the_view_request_hash() -> None:
    created_raw = _create_raw()
    created_serializer = SalaryPeriodWriteSerializer(data=created_raw)
    created_serializer.is_valid(raise_exception=True)
    created_body = create_salary_period_body(created_raw)
    assert create_salary_period_command(created_raw) == created_body
    assert created_body["description"] == "November payroll"
    _assert_same_hash(
        created_body,
        dict(created_serializer.validated_data),
        method="POST",
        route=CREATE_ROUTE,
    )

    period_id = uuid4()
    update_raw = {"salary_period_id": str(period_id).upper(), "description": "  Desk payroll  "}
    update_command = update_salary_period_command(update_raw)
    update_serializer = SalaryPeriodUpdateSerializer(data={"description": "  Desk payroll  "})
    update_serializer.is_valid(raise_exception=True)
    update_view = {"salary_period_id": str(period_id), **update_serializer.validated_data}
    assert update_command["period_route_id"] == str(period_id)
    assert _hashed(update_command, "period_route_id")["description"] == "Desk payroll"
    route = command_route(period_id=period_id, action="")
    assert route == f"/api/v1/salary-periods/{period_id}/"
    _assert_same_hash(
        _hashed(update_command, "period_route_id"),
        update_view,
        method="PATCH",
        route=route,
    )
    invalid = update_salary_period_command({**update_raw, "salary_period_id": "not-a-uuid"})
    assert invalid["period_route_id"] == "not-a-uuid"
    assert invalid["salary_period_id"] == "not-a-uuid"

    lines_raw = _lines_raw()
    lines_command = replace_salary_lines_command({**lines_raw, "salary_period_id": str(period_id)})
    lines_serializer = SalaryLinesReplaceSerializer(data=lines_raw)
    lines_serializer.is_valid(raise_exception=True)
    lines_view = {
        "salary_period_id": str(period_id),
        "lines": lines_serializer.validated_data["lines"],
    }
    lines_body = replace_salary_lines_body(lines_raw, period_id=str(period_id))
    stored_lines = list(lines_body["lines"])
    assert lines_command["period_route_id"] == str(period_id)
    assert _hashed(lines_command, "period_route_id") == lines_body
    assert stored_lines[0]["amount"] == "1500.00"
    assert stored_lines[0]["description"] == "Base pay"
    assert stored_lines[0]["staff_profile_id"] < stored_lines[1]["staff_profile_id"]
    assert "external_reference" not in stored_lines[0]
    lines_route = command_route(period_id=period_id, action="lines")
    assert lines_route == f"/api/v1/salary-periods/{period_id}/lines/"
    _assert_same_hash(
        _hashed(lines_command, "period_route_id"),
        lines_view,
        method="PUT",
        route=lines_route,
    )

    submit = submit_salary_period_command({"salary_period_id": str(period_id)})
    assert _hashed(submit, "period_route_id") == {"salary_period_id": str(period_id)}
    _assert_same_hash(
        _hashed(submit, "period_route_id"),
        {"salary_period_id": str(period_id)},
        method="POST",
        route=command_route(period_id=period_id, action="submit"),
    )

    cancel_raw = {"salary_period_id": str(period_id), "reason": "  Duplicate  "}
    cancel_command = cancel_salary_period_command(cancel_raw)
    cancel_serializer = CancelSalarySerializer(data={"reason": "  Duplicate  "})
    cancel_serializer.is_valid(raise_exception=True)
    cancel_view = {"salary_period_id": str(period_id), **cancel_serializer.validated_data}
    assert cancel_command["reason"] == "Duplicate"
    _assert_same_hash(
        _hashed(cancel_command, "period_route_id"),
        cancel_view,
        method="POST",
        route=command_route(period_id=period_id, action="cancel"),
    )

    void_raw = {"reason": "  Wrong period  "}
    void_serializer = VoidSalarySerializer(data=void_raw)
    void_serializer.is_valid(raise_exception=True)
    void_view = {
        "salary_period_id": str(period_id),
        "reason": void_serializer.validated_data["reason"],
        "business_date": void_serializer.validated_data.get("business_date"),
        "cash_sessions": list(void_serializer.validated_data.get("cash_sessions", [])),
    }
    void_body = void_salary_period_body(void_raw, period_id=str(period_id))
    void_command = void_salary_period_command(
        {**void_raw, "salary_period_id": str(period_id).upper()}
    )
    assert void_body["business_date"] is None
    assert void_body["cash_sessions"] == []
    assert _hashed(void_command, "period_route_id") == void_body
    _assert_same_hash(
        void_body,
        void_view,
        method="POST",
        route=command_route(period_id=period_id, action="void"),
    )

    earlier, later = _ordered_pair()
    session_a = uuid4()
    session_b = uuid4()
    mapped_raw = {
        "reason": "  Cash correction  ",
        "business_date": "2026-11-10",
        "cash_sessions": [
            {"line_id": later, "cash_session_id": str(session_b)},
            {"line_id": earlier.upper(), "cash_session_id": str(session_a).upper()},
        ],
    }
    mapped_serializer = VoidSalarySerializer(data=mapped_raw)
    mapped_serializer.is_valid(raise_exception=True)
    mapped_fields = mapped_serializer.validated_data
    mapped_view = {
        "salary_period_id": str(period_id),
        "reason": mapped_fields["reason"],
        "business_date": mapped_fields.get("business_date"),
        "cash_sessions": list(mapped_fields.get("cash_sessions", [])),
    }
    mapped_body = void_salary_period_body(mapped_raw, period_id=str(period_id))
    mapped_sessions = list(mapped_body["cash_sessions"])
    assert mapped_body["reason"] == "Cash correction"
    assert mapped_sessions[0]["line_id"] == earlier
    assert mapped_sessions[0]["line_id"] < mapped_sessions[1]["line_id"]
    _assert_same_hash(
        mapped_body,
        mapped_view,
        method="POST",
        route=command_route(period_id=period_id, action="void"),
    )


def test_permission_and_gym_stop_before_a_salary_service() -> None:
    request, goal = _session()
    period_id = str(uuid4())
    payloads = (
        ("create_salary_period", _create_raw()),
        ("update_salary_period", {"salary_period_id": period_id, "description": "Desk payroll"}),
        ("replace_salary_lines", {"salary_period_id": period_id, **_lines_raw()}),
        ("submit_salary_period", {"salary_period_id": period_id}),
        ("cancel_salary_period", {"salary_period_id": period_id, "reason": "Duplicate"}),
        ("void_salary_period", {"salary_period_id": period_id, "reason": "Wrong period"}),
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
    request.permission_codes = frozenset(
        {"salaries.create", "salaries.update", "salaries.submit", "salaries.void"}
    )
    for tool_name, payload in payloads:
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
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_salary_period",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={
                "period_start": "2026-11-10",
                "period_end": "2026-11-01",
                "business_date": "2026-11-10",
                "description": "Reversed",
            },
            confirmed=True,
        )
    for tool_name in ("approve_salary_period", "reject_salary_period"):
        with pytest.raises(ExecutionRejected) as unknown:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={"salary_period_id": period_id},
                confirmed=True,
            )
        assert unknown.value.code == "unknown_tool"
