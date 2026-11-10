from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
from apps.assistant.cash_tools import CASH_TOOL_NAMES
from apps.assistant.execution import ExecutionRejected
from apps.assistant.inventory_tools import INVENTORY_TOOL_NAMES
from apps.assistant.models import Goal
from apps.assistant.payment_tools import (
    CANCEL_ROUTE,
    CREATE_ROUTE,
    PAYMENT_TOOL_NAMES,
    RECEIPT_VOID_ROUTE,
    REFUND_ROUTE,
    SUBMIT_ROUTE,
    UPDATE_ROUTE,
    VOID_ROUTE,
    approve_payment_command,
    cancel_payment_command,
    create_payment_body,
    create_payment_command,
    execute_registered_tool,
    payment_tool_contracts,
    payment_tool_definitions,
    refund_payment_body,
    refund_payment_command,
    reject_payment_command,
    submit_payment_command,
    update_payment_command,
    void_payment_body,
    void_payment_command,
    void_receipt_command,
)
from apps.assistant.registry import production_registry
from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
from apps.assistant.training_tools import TRAINING_TOOL_NAMES
from apps.common.finance.idempotency import canonical_request_hash
from apps.members.payment_serializers import (
    CancelPaymentSerializer,
    PaymentUpdateSerializer,
    PaymentWriteSerializer,
    RefundPaymentSerializer,
    VoidPaymentSerializer,
    VoidReceiptSerializer,
)
from apps.members.payment_services import command_route
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

_NAMES = (
    "approve_payment",
    "cancel_payment",
    "create_payment",
    "refund_payment",
    "reject_payment",
    "submit_payment",
    "update_payment",
    "void_payment",
    "void_receipt",
)
_APPROVAL = frozenset({"approve_payment", "reject_payment"})


def _create_raw() -> dict[str, object]:
    return {
        "purpose": "OTHER",
        "description": "  Day pass  ",
        "amount": "40.00",
        "currency": "THB",
        "method": "BANK_TRANSFER",
        "business_date": "2026-11-09",
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="pay-unit", name="Payments", status=Gym.Status.ACTIVE)
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


def test_payment_tools_are_closed_finance_commands() -> None:
    contracts = payment_tool_contracts()
    definitions = {tool.name: tool for tool in payment_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == PAYMENT_TOOL_NAMES == _NAMES
    assert "approve_payment" in names
    assert "reject_payment" in names
    assert set(PAYMENT_TOOL_NAMES).isdisjoint(CASH_TOOL_NAMES)
    assert set(PAYMENT_TOOL_NAMES).isdisjoint(INVENTORY_TOOL_NAMES)
    assert set(PAYMENT_TOOL_NAMES).isdisjoint(BOOKING_TOOL_NAMES)
    assert set(PAYMENT_TOOL_NAMES).isdisjoint(SCHEDULE_TOOL_NAMES)
    assert set(PAYMENT_TOOL_NAMES).isdisjoint(TRAINING_TOOL_NAMES)
    assert set(CASH_TOOL_NAMES) <= set(names)
    assert set(INVENTORY_TOOL_NAMES) <= set(names)
    assert set(PAYMENT_TOOL_NAMES) <= set(names)
    created = contracts["create_payment"]
    updated = contracts["update_payment"]
    submitted = contracts["submit_payment"]
    cancelled = contracts["cancel_payment"]
    voided = contracts["void_payment"]
    refunded = contracts["refund_payment"]
    receipt = contracts["void_receipt"]
    assert created.http_method == "POST"
    assert created.route == CREATE_ROUTE == command_route(payment_id=None, action="")
    assert created.route_fields == ()
    assert updated.http_method == "PATCH"
    assert updated.route == UPDATE_ROUTE
    assert updated.route_fields == ("payment_route_id",)
    assert submitted.route == SUBMIT_ROUTE
    assert cancelled.route == CANCEL_ROUTE
    assert voided.route == VOID_ROUTE
    assert refunded.route == REFUND_ROUTE
    assert receipt.route == RECEIPT_VOID_ROUTE
    assert receipt.route_fields == ("receipt_route_id",)
    assert created.definition.permissions == ("payments.create",)
    assert updated.definition.permissions == ("payments.update",)
    assert submitted.definition.permissions == ("payments.submit",)
    assert cancelled.definition.permissions == ("payments.update",)
    assert voided.definition.permissions == ("payments.void",)
    assert refunded.definition.permissions == ("payments.refund",)
    assert receipt.definition.permissions == ("receipts.void",)
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
        assert contract.definition.module == "payments"


def test_finance_bodies_match_the_view_request_hash() -> None:
    created_raw = _create_raw()
    created_serializer = PaymentWriteSerializer(data=created_raw)
    created_serializer.is_valid(raise_exception=True)
    created_body = create_payment_body(created_raw)
    assert create_payment_command(created_raw) == created_body
    assert created_body["description"] == "Day pass"
    assert created_body["amount"] == "40.00"
    _assert_same_hash(
        created_body,
        dict(created_serializer.validated_data),
        method="POST",
        route=CREATE_ROUTE,
    )

    payment_id = uuid4()
    update_raw = {"payment_id": str(payment_id).upper(), "description": "  Desk fee  "}
    update_command = update_payment_command(update_raw)
    update_serializer = PaymentUpdateSerializer(data={"description": "  Desk fee  "})
    update_serializer.is_valid(raise_exception=True)
    update_view = {"payment_id": str(payment_id), **update_serializer.validated_data}
    assert update_command["payment_route_id"] == str(payment_id)
    assert _hashed(update_command, "payment_route_id")["description"] == "Desk fee"
    route = command_route(payment_id=payment_id, action="")
    assert route == f"/api/v1/payments/{payment_id}/"
    _assert_same_hash(
        _hashed(update_command, "payment_route_id"),
        update_view,
        method="PATCH",
        route=route,
    )
    invalid = update_payment_command({**update_raw, "payment_id": "not-a-uuid"})
    assert invalid["payment_route_id"] == "not-a-uuid"
    assert invalid["payment_id"] == "not-a-uuid"

    submit = submit_payment_command({"payment_id": str(payment_id)})
    assert _hashed(submit, "payment_route_id") == {"payment_id": str(payment_id)}
    _assert_same_hash(
        _hashed(submit, "payment_route_id"),
        {"payment_id": str(payment_id)},
        method="POST",
        route=command_route(payment_id=payment_id, action="submit"),
    )

    cancel_raw = {"payment_id": str(payment_id), "reason": "  Duplicate  "}
    cancel_command = cancel_payment_command(cancel_raw)
    cancel_serializer = CancelPaymentSerializer(data={"reason": "  Duplicate  "})
    cancel_serializer.is_valid(raise_exception=True)
    cancel_view = {"payment_id": str(payment_id), **cancel_serializer.validated_data}
    assert cancel_command["reason"] == "Duplicate"
    _assert_same_hash(
        _hashed(cancel_command, "payment_route_id"),
        cancel_view,
        method="POST",
        route=command_route(payment_id=payment_id, action="cancel"),
    )

    void_raw = {"reason": "  Wrong member  "}
    void_serializer = VoidPaymentSerializer(data=void_raw)
    void_serializer.is_valid(raise_exception=True)
    void_view = {
        "payment_id": str(payment_id),
        "reason": void_serializer.validated_data["reason"],
        "business_date": void_serializer.validated_data.get("business_date"),
        "cash_session_id": void_serializer.validated_data.get("cash_session_id"),
    }
    void_body = void_payment_body(void_raw, payment_id=str(payment_id))
    void_command = void_payment_command({**void_raw, "payment_id": str(payment_id).upper()})
    assert void_body["business_date"] is None
    assert void_body["cash_session_id"] is None
    assert _hashed(void_command, "payment_route_id") == void_body
    _assert_same_hash(
        void_body,
        void_view,
        method="POST",
        route=command_route(payment_id=payment_id, action="void"),
    )

    refund_raw = {
        "amount": "15.00",
        "reason": "  Partial  ",
        "method": "BANK_TRANSFER",
        "business_date": "2026-11-09",
    }
    refund_serializer = RefundPaymentSerializer(data=refund_raw)
    refund_serializer.is_valid(raise_exception=True)
    refund_fields = refund_serializer.validated_data
    refund_view = {
        "payment_id": str(payment_id),
        "amount": refund_fields["amount"],
        "reason": refund_fields["reason"],
        "method": refund_fields["method"],
        "business_date": refund_fields["business_date"],
        "reference_number": str(refund_fields.get("reference_number") or ""),
        "cash_session_id": refund_fields.get("cash_session_id"),
    }
    refund_body = refund_payment_body(refund_raw, payment_id=str(payment_id))
    refund_command = refund_payment_command({**refund_raw, "payment_id": str(payment_id)})
    assert refund_body["amount"] == "15.00"
    assert refund_body["reference_number"] == ""
    assert refund_body["cash_session_id"] is None
    assert _hashed(refund_command, "payment_route_id") == refund_body
    _assert_same_hash(
        refund_body,
        refund_view,
        method="POST",
        route=command_route(payment_id=payment_id, action="refund"),
    )

    receipt_id = uuid4()
    receipt_command = void_receipt_command(
        {"receipt_id": str(receipt_id).upper(), "reason": "  Reprint  "}
    )
    receipt_serializer = VoidReceiptSerializer(data={"reason": "  Reprint  "})
    receipt_serializer.is_valid(raise_exception=True)
    receipt_view = {"receipt_id": str(receipt_id), **receipt_serializer.validated_data}
    assert receipt_command["receipt_route_id"] == str(receipt_id)
    assert receipt_command["reason"] == "Reprint"
    _assert_same_hash(
        _hashed(receipt_command, "receipt_route_id"),
        receipt_view,
        method="POST",
        route=f"/api/v1/receipts/{receipt_id}/void/",
    )


def test_permission_and_gym_stop_before_a_payment_service() -> None:
    request, goal = _session()
    payment_id = str(uuid4())
    payloads = (
        ("create_payment", _create_raw()),
        ("update_payment", {"payment_id": payment_id, "description": "Desk fee"}),
        ("submit_payment", {"payment_id": payment_id}),
        ("cancel_payment", {"payment_id": payment_id, "reason": "Duplicate"}),
        ("void_payment", {"payment_id": payment_id, "reason": "Wrong member"}),
        (
            "refund_payment",
            {
                "payment_id": payment_id,
                "amount": "15.00",
                "reason": "Partial",
                "method": "BANK_TRANSFER",
                "business_date": "2026-11-09",
            },
        ),
        ("void_receipt", {"receipt_id": str(uuid4()), "reason": "Reprint"}),
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
            "payments.create",
            "payments.update",
            "payments.submit",
            "payments.void",
            "payments.refund",
            "receipts.void",
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
            tool_name="create_payment",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_create_raw(), "purpose": "PRODUCT"},
            confirmed=True,
        )
    with pytest.raises(ExecutionRejected) as unknown:
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="print_payment",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={"payment_id": payment_id},
            confirmed=True,
        )
    assert unknown.value.code == "unknown_tool"


def test_approval_confirmation_stays_out_of_the_view_hash() -> None:
    payment_id = uuid4()
    session_id = uuid4()
    approve = {
        "payment_id": str(payment_id).upper(),
        "cash_session_id": str(session_id),
        "record_id": str(payment_id),
        "amount": "40.0",
        "confirmed": True,
    }
    command = approve_payment_command(approve)
    assert command["record_id"] == str(payment_id)
    assert command["amount"] == "40.0"
    assert "confirmed" not in command
    _assert_same_hash(
        _hashed(command, "payment_route_id", "record_id", "amount"),
        {"payment_id": str(payment_id), "cash_session_id": session_id},
        method="POST",
        route=command_route(payment_id=payment_id, action="approve"),
    )
    omitted = approve_payment_command(
        {"payment_id": str(payment_id), "record_id": str(payment_id), "amount": "40.00"}
    )
    _assert_same_hash(
        _hashed(omitted, "payment_route_id", "record_id", "amount"),
        {"payment_id": str(payment_id), "cash_session_id": None},
        method="POST",
        route=command_route(payment_id=payment_id, action="approve"),
    )
    rejected = reject_payment_command(
        {
            "payment_id": str(payment_id),
            "rejection_reason": "  Duplicate  ",
            "record_id": str(payment_id),
            "amount": "40.00",
            "confirmed": True,
        }
    )
    assert "confirmed" not in rejected
    _assert_same_hash(
        _hashed(rejected, "payment_route_id", "record_id", "amount"),
        {"payment_id": str(payment_id), "reason": "Duplicate"},
        method="POST",
        route=command_route(payment_id=payment_id, action="reject"),
    )
