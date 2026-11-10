from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.cash_tools import CASH_TOOL_NAMES
from apps.assistant.execution import ExecutionRejected
from apps.assistant.expense_tools import (
    CANCEL_ROUTE,
    CATEGORY_ROUTE,
    CATEGORY_UPDATE_ROUTE,
    EXPENSE_ROUTE,
    EXPENSE_TOOL_NAMES,
    EXPENSE_UPDATE_ROUTE,
    SUBMIT_ROUTE,
    VOID_ROUTE,
    approve_expense_command,
    cancel_expense_command,
    create_category_body,
    create_category_command,
    create_expense_body,
    create_expense_command,
    execute_registered_tool,
    expense_tool_contracts,
    expense_tool_definitions,
    reject_expense_command,
    submit_expense_command,
    update_category_command,
    update_expense_command,
    void_expense_body,
    void_expense_command,
)
from apps.assistant.models import Goal
from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES
from apps.assistant.registry import production_registry
from apps.common.finance.idempotency import canonical_request_hash
from apps.expenses.serializers import (
    CancelExpenseSerializer,
    ExpenseCategoryUpdateSerializer,
    ExpenseCategoryWriteSerializer,
    ExpenseUpdateSerializer,
    ExpenseWriteSerializer,
    VoidExpenseSerializer,
)
from apps.expenses.services import category_route, command_route
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "approve_expense",
    "cancel_expense",
    "create_expense",
    "create_expense_category",
    "reject_expense",
    "submit_expense",
    "update_expense",
    "update_expense_category",
    "void_expense",
)
_APPROVAL = frozenset({"approve_expense", "reject_expense"})


def _category_raw() -> dict[str, object]:
    return {"name": "  Supplies  "}


def _expense_raw(category_id: object) -> dict[str, object]:
    return {
        "category_id": category_id,
        "business_date": "2026-11-09",
        "description": "  Paper  ",
        "amount": "25.00",
        "currency": "THB",
        "payment_method": "BANK_TRANSFER",
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="exp-unit", name="Expenses", status=Gym.Status.ACTIVE)
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


def _hashed(command: dict[str, object], *route_keys: str) -> dict[str, object]:
    return {key: value for key, value in command.items() if key not in route_keys}


def test_expense_tools_are_closed_finance_commands() -> None:
    contracts = expense_tool_contracts()
    definitions = {tool.name: tool for tool in expense_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == EXPENSE_TOOL_NAMES == _NAMES
    assert "approve_expense" in names
    assert "reject_expense" in names
    assert set(EXPENSE_TOOL_NAMES).isdisjoint(PAYMENT_TOOL_NAMES)
    assert set(EXPENSE_TOOL_NAMES).isdisjoint(CASH_TOOL_NAMES)
    assert set(PAYMENT_TOOL_NAMES) <= set(names)
    assert set(EXPENSE_TOOL_NAMES) <= set(names)
    category = contracts["create_expense_category"]
    category_update = contracts["update_expense_category"]
    created = contracts["create_expense"]
    updated = contracts["update_expense"]
    submitted = contracts["submit_expense"]
    cancelled = contracts["cancel_expense"]
    voided = contracts["void_expense"]
    assert category.http_method == "POST"
    assert category.route == CATEGORY_ROUTE == category_route(category_id=None)
    assert category.route_fields == ()
    assert category_update.http_method == "PATCH"
    assert category_update.route == CATEGORY_UPDATE_ROUTE
    assert category_update.route_fields == ("category_route_id",)
    assert created.route == EXPENSE_ROUTE == command_route(expense_id=None, action="")
    assert updated.route == EXPENSE_UPDATE_ROUTE
    assert updated.route_fields == ("expense_route_id",)
    assert submitted.route == SUBMIT_ROUTE
    assert cancelled.route == CANCEL_ROUTE
    assert voided.route == VOID_ROUTE
    assert category.definition.permissions == ("expense_categories.manage",)
    assert category_update.definition.permissions == ("expense_categories.manage",)
    assert created.definition.permissions == ("expenses.create",)
    assert updated.definition.permissions == ("expenses.update",)
    assert submitted.definition.permissions == ("expenses.submit",)
    assert cancelled.definition.permissions == ("expenses.update",)
    assert voided.definition.permissions == ("expenses.void",)
    for contract in contracts.values():
        assert contract.mode == "finance_idempotent"
        assert contract.finance_closed is True
        assert contract.permission_mode == "all"
        assert contract.definition.features == ("finance",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        approval = contract.definition.name in _APPROVAL
        assert contract.definition.approval_required is approval
        assert contract.confirmation_fields == (("record_id", "amount") if approval else ())
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "expenses"


def test_finance_bodies_match_the_view_request_hash() -> None:
    category_raw = _category_raw()
    category_serializer = ExpenseCategoryWriteSerializer(data=category_raw)
    category_serializer.is_valid(raise_exception=True)
    category_body = create_category_body(category_raw)
    assert create_category_command(category_raw) == category_body
    assert category_body["name"] == "Supplies"
    assert category_body["code"] == ""
    _assert_same_hash(
        category_body,
        dict(category_serializer.validated_data),
        method="POST",
        route=CATEGORY_ROUTE,
    )

    category_id = uuid4()
    update_raw = {"category_id": str(category_id).upper(), "name": "  Office supplies  "}
    update_command = update_category_command(update_raw)
    update_serializer = ExpenseCategoryUpdateSerializer(data={"name": "  Office supplies  "})
    update_serializer.is_valid(raise_exception=True)
    update_view = {"category_id": str(category_id), **update_serializer.validated_data}
    assert update_command["category_route_id"] == str(category_id)
    assert _hashed(update_command, "category_route_id")["name"] == "Office supplies"
    category_update_route = category_route(category_id=category_id)
    _assert_same_hash(
        _hashed(update_command, "category_route_id"),
        update_view,
        method="PATCH",
        route=category_update_route,
    )
    invalid = update_category_command({**update_raw, "category_id": "not-a-uuid"})
    assert invalid["category_route_id"] == "not-a-uuid"
    assert invalid["category_id"] == "not-a-uuid"

    expense_raw = _expense_raw(str(category_id).upper())
    expense_serializer = ExpenseWriteSerializer(data=expense_raw)
    expense_serializer.is_valid(raise_exception=True)
    expense_body = create_expense_body(expense_raw)
    assert create_expense_command(expense_raw) == expense_body
    assert expense_body["description"] == "Paper"
    assert expense_body["amount"] == "25.00"
    assert expense_body["category_id"] == str(category_id)
    _assert_same_hash(
        expense_body,
        dict(expense_serializer.validated_data),
        method="POST",
        route=EXPENSE_ROUTE,
    )

    expense_id = uuid4()
    expense_update = {"expense_id": str(expense_id).upper(), "description": "  Toner  "}
    expense_command = update_expense_command(expense_update)
    expense_update_serializer = ExpenseUpdateSerializer(data={"description": "  Toner  "})
    expense_update_serializer.is_valid(raise_exception=True)
    expense_update_view = {
        "expense_id": str(expense_id),
        **expense_update_serializer.validated_data,
    }
    assert expense_command["expense_route_id"] == str(expense_id)
    _assert_same_hash(
        _hashed(expense_command, "expense_route_id"),
        expense_update_view,
        method="PATCH",
        route=command_route(expense_id=expense_id, action=""),
    )

    submit = submit_expense_command({"expense_id": str(expense_id)})
    assert _hashed(submit, "expense_route_id") == {"expense_id": str(expense_id)}
    _assert_same_hash(
        _hashed(submit, "expense_route_id"),
        {"expense_id": str(expense_id)},
        method="POST",
        route=command_route(expense_id=expense_id, action="submit"),
    )

    cancel_raw = {"expense_id": str(expense_id), "reason": "  Duplicate  "}
    cancel_command = cancel_expense_command(cancel_raw)
    cancel_serializer = CancelExpenseSerializer(data={"reason": "  Duplicate  "})
    cancel_serializer.is_valid(raise_exception=True)
    cancel_view = {"expense_id": str(expense_id), **cancel_serializer.validated_data}
    assert cancel_command["reason"] == "Duplicate"
    _assert_same_hash(
        _hashed(cancel_command, "expense_route_id"),
        cancel_view,
        method="POST",
        route=command_route(expense_id=expense_id, action="cancel"),
    )

    void_raw = {"reason": "  Wrong vendor  "}
    void_serializer = VoidExpenseSerializer(data=void_raw)
    void_serializer.is_valid(raise_exception=True)
    void_view = {
        "expense_id": str(expense_id),
        "reason": void_serializer.validated_data["reason"],
        "business_date": void_serializer.validated_data.get("business_date"),
        "cash_session_id": void_serializer.validated_data.get("cash_session_id"),
    }
    void_body = void_expense_body(void_raw, expense_id=str(expense_id))
    void_command = void_expense_command({**void_raw, "expense_id": str(expense_id).upper()})
    assert void_body["business_date"] is None
    assert void_body["cash_session_id"] is None
    assert _hashed(void_command, "expense_route_id") == void_body
    _assert_same_hash(
        void_body,
        void_view,
        method="POST",
        route=command_route(expense_id=expense_id, action="void"),
    )


def test_permission_and_gym_stop_before_an_expense_service() -> None:
    request, goal = _session()
    category_id = str(uuid4())
    expense_id = str(uuid4())
    payloads = (
        ("create_expense_category", _category_raw()),
        ("update_expense_category", {"category_id": category_id, "name": "Office supplies"}),
        ("create_expense", _expense_raw(category_id)),
        ("update_expense", {"expense_id": expense_id, "description": "Toner"}),
        ("submit_expense", {"expense_id": expense_id}),
        ("cancel_expense", {"expense_id": expense_id, "reason": "Duplicate"}),
        ("void_expense", {"expense_id": expense_id, "reason": "Wrong vendor"}),
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
        {
            "expense_categories.manage",
            "expenses.create",
            "expenses.update",
            "expenses.submit",
            "expenses.void",
        }
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
            tool_name="create_expense_category",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"name": "   "},
            confirmed=True,
        )
    with pytest.raises(ExecutionRejected) as unknown:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="print_expense",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"expense_id": expense_id},
            confirmed=True,
        )
    assert unknown.value.code == "unknown_tool"


def test_approval_confirmation_stays_out_of_the_view_hash() -> None:
    expense_id = uuid4()
    approved = approve_expense_command(
        {
            "expense_id": str(expense_id).upper(),
            "record_id": str(expense_id),
            "amount": "25.0",
            "confirmed": True,
        }
    )
    assert approved["amount"] == "25.0"
    assert "confirmed" not in approved
    _assert_same_hash(
        _hashed(approved, "expense_route_id", "record_id", "amount"),
        {"expense_id": str(expense_id), "cash_session_id": None},
        method="POST",
        route=command_route(expense_id=expense_id, action="approve"),
    )
    rejected = reject_expense_command(
        {
            "expense_id": str(expense_id),
            "rejection_reason": "  Duplicate  ",
            "record_id": str(expense_id),
            "amount": "25.00",
        }
    )
    _assert_same_hash(
        _hashed(rejected, "expense_route_id", "record_id", "amount"),
        {"expense_id": str(expense_id), "reason": "Duplicate"},
        method="POST",
        route=command_route(expense_id=expense_id, action="reject"),
    )
