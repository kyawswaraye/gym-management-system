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
from apps.assistant.inventory_tools import INVENTORY_TOOL_NAMES
from apps.assistant.models import Goal
from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES
from apps.assistant.registry import production_registry
from apps.assistant.retail_tools import (
    CANCEL_ROUTE,
    CREATE_ROUTE,
    RETAIL_TOOL_NAMES,
    RETURN_ROUTE,
    SUBMIT_ROUTE,
    UPDATE_ROUTE,
    VOID_ROUTE,
    approve_retail_sale_command,
    cancel_retail_sale_command,
    create_retail_sale_body,
    create_retail_sale_command,
    execute_registered_tool,
    reject_retail_sale_command,
    retail_tool_contracts,
    retail_tool_definitions,
    return_retail_sale_body,
    return_retail_sale_command,
    submit_retail_sale_command,
    update_retail_sale_command,
    void_retail_sale_body,
    void_retail_sale_command,
)
from apps.common.finance.idempotency import canonical_request_hash
from apps.sales.serializers import (
    CancelRetailSaleSerializer,
    RetailSaleUpdateSerializer,
    RetailSaleWriteSerializer,
    ReturnRetailSaleSerializer,
    VoidRetailSaleSerializer,
)
from apps.sales.services import command_route
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "approve_retail_sale",
    "cancel_retail_sale",
    "create_retail_sale",
    "reject_retail_sale",
    "return_retail_sale",
    "submit_retail_sale",
    "update_retail_sale",
    "void_retail_sale",
)
_APPROVAL = frozenset({"approve_retail_sale", "reject_retail_sale"})


def _line(product_id: str, quantity: str, discount: str) -> dict[str, str]:
    return {"product_id": product_id, "quantity": quantity, "discount": discount}


def _create_raw() -> dict[str, object]:
    later = uuid4()
    earlier = uuid4()
    if earlier > later:
        earlier, later = later, earlier
    return {
        "business_date": "2026-11-10",
        "payment_method": "CARD",
        "reference_number": "  POS-1  ",
        "lines": [
            _line(str(later), "2.000", "1.00"),
            _line(str(earlier).upper(), "1.250", "0.50"),
        ],
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="rtl-unit", name="Retail", status=Gym.Status.ACTIVE)
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


def test_retail_tools_are_closed_finance_commands() -> None:
    contracts = retail_tool_contracts()
    definitions = {tool.name: tool for tool in retail_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == RETAIL_TOOL_NAMES == _NAMES
    assert "approve_retail_sale" in names
    assert "reject_retail_sale" in names
    assert set(RETAIL_TOOL_NAMES).isdisjoint(EXPENSE_TOOL_NAMES)
    assert set(RETAIL_TOOL_NAMES).isdisjoint(PAYMENT_TOOL_NAMES)
    assert set(RETAIL_TOOL_NAMES).isdisjoint(CASH_TOOL_NAMES)
    assert set(RETAIL_TOOL_NAMES).isdisjoint(INVENTORY_TOOL_NAMES)
    assert set(EXPENSE_TOOL_NAMES) <= set(names)
    assert set(PAYMENT_TOOL_NAMES) <= set(names)
    assert set(RETAIL_TOOL_NAMES) <= set(names)
    created = contracts["create_retail_sale"]
    updated = contracts["update_retail_sale"]
    submitted = contracts["submit_retail_sale"]
    cancelled = contracts["cancel_retail_sale"]
    voided = contracts["void_retail_sale"]
    returned = contracts["return_retail_sale"]
    assert created.http_method == "POST"
    assert created.route == CREATE_ROUTE == command_route(sale_id=None, action="")
    assert created.route_fields == ()
    assert updated.http_method == "PATCH"
    assert updated.route == UPDATE_ROUTE
    assert updated.route_fields == ("sale_route_id",)
    assert submitted.route == SUBMIT_ROUTE
    assert cancelled.route == CANCEL_ROUTE
    assert voided.route == VOID_ROUTE
    assert returned.route == RETURN_ROUTE
    assert created.definition.permissions == ("retail.create",)
    assert updated.definition.permissions == ("retail.update",)
    assert submitted.definition.permissions == ("retail.submit",)
    assert cancelled.definition.permissions == ("retail.update",)
    assert voided.definition.permissions == ("retail.void",)
    assert returned.definition.permissions == ("retail.void",)
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
        assert contract.definition.module == "retail"


def test_finance_bodies_match_the_view_request_hash() -> None:
    created_raw = _create_raw()
    created_serializer = RetailSaleWriteSerializer(data=created_raw)
    created_serializer.is_valid(raise_exception=True)
    created_body = create_retail_sale_body(created_raw)
    created_lines = list(created_body["lines"])
    assert create_retail_sale_command(created_raw) == created_body
    assert created_body["reference_number"] == "POS-1"
    assert "member_id" not in created_body
    assert created_lines[0]["quantity"] == "1.250"
    assert created_lines[0]["discount"] == "0.50"
    assert created_lines[0]["product_id"] < created_lines[1]["product_id"]
    _assert_same_hash(
        created_body,
        dict(created_serializer.validated_data),
        method="POST",
        route=CREATE_ROUTE,
    )

    sale_id = uuid4()
    product_id = uuid4()
    update_raw = {
        "retail_sale_id": str(sale_id).upper(),
        "reference_number": "  POS-2  ",
        "lines": [_line(str(product_id), "1.000", "0.00")],
    }
    update_command = update_retail_sale_command(update_raw)
    update_serializer = RetailSaleUpdateSerializer(
        data={"reference_number": "  POS-2  ", "lines": [_line(str(product_id), "1.000", "0.00")]}
    )
    update_serializer.is_valid(raise_exception=True)
    update_view = {"retail_sale_id": str(sale_id), **update_serializer.validated_data}
    assert update_command["sale_route_id"] == str(sale_id)
    assert _hashed(update_command, "sale_route_id")["reference_number"] == "POS-2"
    route = command_route(sale_id=sale_id, action="")
    assert route == f"/api/v1/retail-sales/{sale_id}/"
    _assert_same_hash(
        _hashed(update_command, "sale_route_id"),
        update_view,
        method="PATCH",
        route=route,
    )
    invalid = update_retail_sale_command({**update_raw, "retail_sale_id": "not-a-uuid"})
    assert invalid["sale_route_id"] == "not-a-uuid"
    assert invalid["retail_sale_id"] == "not-a-uuid"

    submit = submit_retail_sale_command({"retail_sale_id": str(sale_id)})
    assert _hashed(submit, "sale_route_id") == {"retail_sale_id": str(sale_id)}
    _assert_same_hash(
        _hashed(submit, "sale_route_id"),
        {"retail_sale_id": str(sale_id)},
        method="POST",
        route=command_route(sale_id=sale_id, action="submit"),
    )

    cancel_raw = {"retail_sale_id": str(sale_id), "reason": "  Duplicate  "}
    cancel_command = cancel_retail_sale_command(cancel_raw)
    cancel_serializer = CancelRetailSaleSerializer(data={"reason": "  Duplicate  "})
    cancel_serializer.is_valid(raise_exception=True)
    cancel_view = {"retail_sale_id": str(sale_id), **cancel_serializer.validated_data}
    assert cancel_command["reason"] == "Duplicate"
    _assert_same_hash(
        _hashed(cancel_command, "sale_route_id"),
        cancel_view,
        method="POST",
        route=command_route(sale_id=sale_id, action="cancel"),
    )

    void_raw = {"reason": "  Wrong item  "}
    void_serializer = VoidRetailSaleSerializer(data=void_raw)
    void_serializer.is_valid(raise_exception=True)
    void_view = {
        "retail_sale_id": str(sale_id),
        "reason": void_serializer.validated_data["reason"],
        "business_date": void_serializer.validated_data.get("business_date"),
        "cash_session_id": void_serializer.validated_data.get("cash_session_id"),
    }
    void_body = void_retail_sale_body(void_raw, sale_id=str(sale_id))
    void_command = void_retail_sale_command({**void_raw, "retail_sale_id": str(sale_id).upper()})
    assert void_body["business_date"] is None
    assert void_body["cash_session_id"] is None
    assert _hashed(void_command, "sale_route_id") == void_body
    _assert_same_hash(
        void_body,
        void_view,
        method="POST",
        route=command_route(sale_id=sale_id, action="void"),
    )

    later_line = uuid4()
    earlier_line = uuid4()
    if earlier_line > later_line:
        earlier_line, later_line = later_line, earlier_line
    return_raw = {
        "reason": "  Damaged  ",
        "lines": [
            {"line_id": str(later_line), "quantity": "1.000"},
            {"line_id": str(earlier_line).upper(), "quantity": "0.500"},
        ],
    }
    return_serializer = ReturnRetailSaleSerializer(data=return_raw)
    return_serializer.is_valid(raise_exception=True)
    return_fields = return_serializer.validated_data
    return_view = {
        "retail_sale_id": str(sale_id),
        "reason": return_fields["reason"],
        "business_date": return_fields.get("business_date"),
        "cash_session_id": return_fields.get("cash_session_id"),
        "lines": return_fields["lines"],
    }
    return_body = return_retail_sale_body(return_raw, sale_id=str(sale_id))
    return_command = return_retail_sale_command({**return_raw, "retail_sale_id": str(sale_id)})
    return_lines = list(return_body["lines"])
    assert return_body["reason"] == "Damaged"
    assert return_body["business_date"] is None
    assert return_body["cash_session_id"] is None
    assert return_lines[0]["line_id"] == str(earlier_line)
    assert return_lines[0]["quantity"] == "0.500"
    assert return_lines[0]["line_id"] < return_lines[1]["line_id"]
    assert _hashed(return_command, "sale_route_id") == return_body
    _assert_same_hash(
        return_body,
        return_view,
        method="POST",
        route=command_route(sale_id=sale_id, action="return"),
    )


def test_permission_and_gym_stop_before_a_retail_service() -> None:
    request, goal = _session()
    sale_id = str(uuid4())
    product_id = str(uuid4())
    line_id = str(uuid4())
    payloads = (
        ("create_retail_sale", _create_raw()),
        (
            "update_retail_sale",
            {"retail_sale_id": sale_id, "reference_number": "POS-2"},
        ),
        ("submit_retail_sale", {"retail_sale_id": sale_id}),
        ("cancel_retail_sale", {"retail_sale_id": sale_id, "reason": "Duplicate"}),
        ("void_retail_sale", {"retail_sale_id": sale_id, "reason": "Wrong item"}),
        (
            "return_retail_sale",
            {
                "retail_sale_id": sale_id,
                "reason": "Damaged",
                "lines": [{"line_id": line_id, "quantity": "1.000"}],
            },
        ),
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
        {"retail.create", "retail.update", "retail.submit", "retail.void"}
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
            tool_name="create_retail_sale",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={
                "business_date": "2026-11-10",
                "payment_method": "CARD",
                "lines": [_line(product_id, "1.0000", "0.00")],
            },
            confirmed=True,
        )
    for tool_name in ("print_retail_sale", "download_retail_receipt"):
        with pytest.raises(ExecutionRejected) as unknown:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={"retail_sale_id": sale_id},
                confirmed=True,
            )
        assert unknown.value.code == "unknown_tool"


def test_approval_confirmation_stays_out_of_the_view_hash() -> None:
    sale_id = uuid4()
    approved = approve_retail_sale_command(
        {
            "retail_sale_id": str(sale_id).upper(),
            "record_id": str(sale_id),
            "amount": "10.0",
            "confirmed": True,
        }
    )
    assert approved["amount"] == "10.0"
    assert "confirmed" not in approved
    _assert_same_hash(
        _hashed(approved, "sale_route_id", "record_id", "amount"),
        {"retail_sale_id": str(sale_id), "cash_session_id": None},
        method="POST",
        route=command_route(sale_id=sale_id, action="approve"),
    )
    rejected = reject_retail_sale_command(
        {
            "retail_sale_id": str(sale_id),
            "rejection_reason": "  Duplicate  ",
            "record_id": str(sale_id),
            "amount": "10.00",
        }
    )
    _assert_same_hash(
        _hashed(rejected, "sale_route_id", "record_id", "amount"),
        {"retail_sale_id": str(sale_id), "reason": "Duplicate"},
        method="POST",
        route=command_route(sale_id=sale_id, action="reject"),
    )
