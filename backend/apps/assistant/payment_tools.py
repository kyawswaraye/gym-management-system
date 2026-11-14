"""Payment and receipt commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Approve and reject confirm the stored payment id and
``money_text`` amount outside the view-body hash. Print and download stay out.
Each finance body matches the view body, and the route id is a separate field
so the view's payment or receipt id remains in that hash.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.approval import (
    CONFIRMATION_FIELDS,
    approval_loader,
    attach_confirmation,
    payment_snapshot,
)
from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import (
    ApprovalSnapshot,
    ExecutionMode,
    ToolCall,
    ToolContract,
    execute_confirmed_tool,
)
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.cashbook.serializers import session_payload, transaction_payload
from apps.common.finance.idempotency import IdempotentResponse
from apps.members.payment_serializers import (
    ApprovePaymentSerializer,
    CancelPaymentSerializer,
    EmptyCommandSerializer,
    PaymentUpdateSerializer,
    PaymentWriteSerializer,
    RefundPaymentSerializer,
    RejectPaymentSerializer,
    VoidPaymentSerializer,
    VoidReceiptSerializer,
    money_text,
    payment_payload,
    receipt_payload,
    settlement_payload,
)
from apps.members.payment_services import (
    approve_payment,
    cancel_payment,
    create_payment_draft,
    refund_payment,
    reject_payment,
    submit_payment,
    update_payment_draft,
    void_payment,
    void_receipt,
)
from apps.tenants.models import Gym

CREATE_ROUTE = "/api/v1/payments/"
UPDATE_ROUTE = "/api/v1/payments/{payment_route_id}/"
SUBMIT_ROUTE = "/api/v1/payments/{payment_route_id}/submit/"
APPROVE_ROUTE = "/api/v1/payments/{payment_route_id}/approve/"
REJECT_ROUTE = "/api/v1/payments/{payment_route_id}/reject/"
CANCEL_ROUTE = "/api/v1/payments/{payment_route_id}/cancel/"
VOID_ROUTE = "/api/v1/payments/{payment_route_id}/void/"
REFUND_ROUTE = "/api/v1/payments/{payment_route_id}/refund/"
RECEIPT_VOID_ROUTE = "/api/v1/receipts/{receipt_route_id}/void/"
_ROUTE_KEYS = ("payment_route_id", "receipt_route_id", "payment_id", "receipt_id")


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any
    load_approval: Any = None


def _definition(
    name: str,
    description: str,
    permission: str,
    *,
    approval: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="payments",
        operation="write",
        permissions=(permission,),
        features=("finance",),
        confirmation_required=True,
        approval_required=approval,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    http_method: str,
    route: str,
    route_fields: tuple[str, ...] = (),
    confirmation_fields: tuple[str, ...] = (),
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        finance_closed=True,
        http_method=http_method,
        route=route,
        route_fields=route_fields,
        confirmation_fields=confirmation_fields,
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExecutionRejected("The payment command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise ExecutionRejected("The payment command is not a JSON body.", code="payload")


def _validated_with(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _finance_body(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, object]:
    validated = _validated_with(serializer_type, data)
    return {key: _json_value(value) for key, value in validated.items()}


def _route_token(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return value
    return value


def _passes_gym_to_executor(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> bool:
    if confirmed is not True:
        return True
    if "gym_id" not in payload:
        return False
    gym = getattr(request, "gym", None)
    if not isinstance(gym, Gym) or gym.id is None:
        return False
    return str(payload["gym_id"]) != str(gym.id)


def _without(payload: Mapping[str, object], *keys: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in keys}


def _bind_route(
    body: dict[str, object],
    payload: Mapping[str, object],
    *,
    source: str,
    route_key: str,
    body_key: str,
) -> dict[str, object]:
    if source not in payload:
        body.pop(body_key, None)
        return body
    token = _route_token(payload.get(source))
    if not isinstance(token, str):
        body.pop(body_key, None)
        body[route_key] = token
        return body
    try:
        canonical = str(UUID(token))
    except ValueError:
        body[body_key] = token
        body[route_key] = token
        return body
    body[body_key] = canonical
    body[route_key] = canonical
    return body


def create_payment_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the create body whose finance hash matches the HTTP view."""

    return _finance_body(PaymentWriteSerializer, data)


def update_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the update body whose finance hash matches the HTTP view."""

    return {"payment_id": payment_id, **_finance_body(PaymentUpdateSerializer, data)}


def cancel_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    return {"payment_id": payment_id, **_finance_body(CancelPaymentSerializer, data)}


def void_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the void body whose finance hash matches the HTTP view."""

    fields = _validated_with(VoidPaymentSerializer, data)
    return {
        "payment_id": payment_id,
        "reason": str(fields["reason"]),
        "business_date": _json_value(fields.get("business_date")),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
    }


def refund_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the refund body whose finance hash matches the HTTP view."""

    fields = _validated_with(RefundPaymentSerializer, data)
    return {
        "payment_id": payment_id,
        "amount": _json_value(fields["amount"]),
        "reason": str(fields["reason"]),
        "method": str(fields["method"]),
        "business_date": _json_value(fields["business_date"]),
        "reference_number": str(fields.get("reference_number") or ""),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
    }


def void_receipt_body(data: Mapping[str, object], *, receipt_id: str) -> dict[str, object]:
    """Return the receipt-void body whose finance hash matches the HTTP view."""

    return {"receipt_id": receipt_id, **_finance_body(VoidReceiptSerializer, data)}


def create_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_payment_body(_without(payload, "gym_id"))


def update_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_payment_body(
        _without(payload, "gym_id", "payment_id", "payment_route_id"),
        payment_id="",
    )
    return _bind_route(
        body,
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )


def submit_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", *_ROUTE_KEYS))
    return _bind_route(
        {},
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )


def cancel_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_payment_body(
        _without(payload, "gym_id", "payment_id", "payment_route_id"),
        payment_id="",
    )
    return _bind_route(
        body,
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )


def approve_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the approve body whose finance hash matches the HTTP view."""

    fields = _validated_with(ApprovePaymentSerializer, data)
    cash_session_id = fields.get("cash_session_id")
    return {
        "payment_id": payment_id,
        "cash_session_id": None if cash_session_id is None else str(cash_session_id),
    }


def reject_payment_body(data: Mapping[str, object], *, payment_id: str) -> dict[str, object]:
    """Return the reject body whose finance hash matches the HTTP view."""

    fields = _validated_with(RejectPaymentSerializer, data)
    return {"payment_id": payment_id, "reason": str(fields["rejection_reason"])}


def _approval_command(
    body: dict[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    bound = _bind_route(
        body,
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )
    return attach_confirmation(bound, payload)


def approve_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = approve_payment_body(
        _without(
            payload,
            "gym_id",
            "payment_id",
            "payment_route_id",
            "record_id",
            "amount",
            "confirmed",
        ),
        payment_id="",
    )
    return _approval_command(body, payload)


def reject_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = reject_payment_body(
        _without(
            payload,
            "gym_id",
            "payment_id",
            "payment_route_id",
            "record_id",
            "amount",
            "confirmed",
        ),
        payment_id="",
    )
    return _approval_command(body, payload)


def void_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_payment_body(
        _without(payload, "gym_id", "payment_id", "payment_route_id"),
        payment_id="",
    )
    return _bind_route(
        body,
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )


def refund_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = refund_payment_body(
        _without(payload, "gym_id", "payment_id", "payment_route_id"),
        payment_id="",
    )
    return _bind_route(
        body,
        payload,
        source="payment_id",
        route_key="payment_route_id",
        body_key="payment_id",
    )


def void_receipt_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_receipt_body(
        _without(payload, "gym_id", "receipt_id", "receipt_route_id"),
        receipt_id="",
    )
    return _bind_route(
        body,
        payload,
        source="receipt_id",
        route_key="receipt_route_id",
        body_key="receipt_id",
    )


def _prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
    command: Callable[[Mapping[str, object]], dict[str, object]],
) -> dict[str, object]:
    if _passes_gym_to_executor(request, payload, confirmed=confirmed):
        return dict(payload)
    return command(payload)


def _required_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ExecutionRejected("The finance route resource is missing.", code="finance_route")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ExecutionRejected(
            "The finance route resource is not a valid identifier.",
            code="finance_route",
        ) from exc


def _present(payload: Mapping[str, object], *keys: str) -> dict[str, object]:
    return {key: value for key, value in _without(payload, *keys).items() if value is not None}


def _render_payment(payment: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": payment_payload(payment), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="Payment",
        resource_id=payment.id,
    )


def _render_receipt(receipt: Any) -> IdempotentResponse:
    rendered = {"success": True, "data": receipt_payload(receipt), "message": "Receipt voided"}
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="Receipt",
        resource_id=receipt.id,
    )


def _render_correction(result: Mapping[str, Any], message: str) -> IdempotentResponse:
    settlement = result["settlement"]
    original_receipt = result["original_receipt"]
    rendered = {
        "success": True,
        "data": {
            "original_payment": payment_payload(result["original"]),
            "correction_payment": payment_payload(result["correction"]),
            "original_receipt": (
                None if original_receipt is None else receipt_payload(original_receipt)
            ),
            "correction_receipt": receipt_payload(result["correction_receipt"]),
            "cash_transactions": [
                transaction_payload(movement) for movement in result["cash_transactions"]
            ],
            "refundable_amount": money_text(result["refundable_amount"]),
            "membership_settlement": (
                None
                if settlement is None
                else settlement_payload(
                    membership_id=settlement["membership_id"],
                    payment_status=str(settlement["payment_status"]),
                    paid_amount=settlement["paid_amount"],
                    balance_amount=settlement["balance_amount"],
                )
            ),
        },
        "message": message,
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="Payment",
        resource_id=result["correction"].id,
    )


def _create_payment(call: ToolCall) -> IdempotentResponse:
    payment = create_payment_draft(
        gym=call.gym,
        actor=call.actor,
        fields=_validated_with(PaymentWriteSerializer, call.payload),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_payment(payment, "Payment draft created", 201)


def _update_payment(call: ToolCall) -> IdempotentResponse:
    payment = update_payment_draft(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        fields=_validated_with(
            PaymentUpdateSerializer,
            _without(call.payload, "payment_id", "payment_route_id"),
        ),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_payment(payment, "Payment draft updated", 200)


def _submit_payment(call: ToolCall) -> IdempotentResponse:
    _validated_with(
        EmptyCommandSerializer,
        _without(call.payload, "payment_id", "payment_route_id"),
    )
    payment = submit_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_payment(payment, "Payment submitted", 200)


_APPROVAL_KEYS = ("payment_id", "payment_route_id", "record_id", "amount")


def _approve_payment(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(ApprovePaymentSerializer, _without(call.payload, *_APPROVAL_KEYS))
    payment, receipt, session, movement, settlement = approve_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": {
            "payment": payment_payload(payment),
            "receipt": receipt_payload(receipt),
            "cash_session": None if session is None else session_payload(session),
            "cash_transaction": None if movement is None else transaction_payload(movement),
            "membership_settlement": (
                None
                if settlement is None
                else settlement_payload(
                    membership_id=settlement["membership_id"],
                    payment_status=str(settlement["payment_status"]),
                    paid_amount=cast(Decimal, settlement["paid_amount"]),
                    balance_amount=cast(Decimal, settlement["balance_amount"]),
                )
            ),
        },
        "message": "Payment approved",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="Payment",
        resource_id=payment.id,
    )


def _reject_payment(call: ToolCall) -> IdempotentResponse:
    body = _without(call.payload, *_APPROVAL_KEYS)
    reason = body.pop("reason")
    fields = _validated_with(RejectPaymentSerializer, {**body, "rejection_reason": reason})
    payment = reject_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        reason=str(fields["rejection_reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_payment(payment, "Payment rejected", 200)


def _load_payment_approval(gym: Gym, payload: Mapping[str, object]) -> ApprovalSnapshot:
    return payment_snapshot(gym, payload.get("payment_route_id"))


def _cancel_payment(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        CancelPaymentSerializer,
        _without(call.payload, "payment_id", "payment_route_id"),
    )
    payment = cancel_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_payment(payment, "Payment cancelled", 200)


def _void_payment(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(VoidPaymentSerializer, _present(call.payload, *_ROUTE_KEYS))
    result = void_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        reason=str(fields["reason"]),
        business_date=fields.get("business_date"),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_correction(result, "Payment voided")


def _refund_payment(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(RefundPaymentSerializer, _present(call.payload, *_ROUTE_KEYS))
    result = refund_payment(
        gym=call.gym,
        actor=call.actor,
        payment_id=_required_uuid(call.payload.get("payment_route_id")),
        amount=cast(Decimal, fields["amount"]),
        reason=str(fields["reason"]),
        method=str(fields["method"]),
        business_date=cast(date, fields["business_date"]),
        reference_number=str(fields.get("reference_number") or ""),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_correction(result, "Payment refunded")


def _void_receipt(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        VoidReceiptSerializer,
        _without(call.payload, "receipt_id", "receipt_route_id"),
    )
    receipt = void_receipt(
        gym=call.gym,
        actor=call.actor,
        receipt_id=_required_uuid(call.payload.get("receipt_route_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_receipt(receipt)


def _command_prepare(command: Callable[[Mapping[str, object]], dict[str, object]]) -> Any:
    def prepare(
        request: Request,
        payload: Mapping[str, object],
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        return _prepare(request, payload, confirmed=confirmed, command=command)

    return prepare


_CREATE = RegisteredTool(
    contract=_contract(
        _definition("create_payment", "Create a payment draft", "payments.create"),
        (
            FieldRequirement("purpose", "Purpose"),
            FieldRequirement("description", "Description"),
            FieldRequirement("amount", "Amount"),
            FieldRequirement("currency", "Currency"),
            FieldRequirement("method", "Method"),
            FieldRequirement("business_date", "Business date"),
        ),
        http_method="POST",
        route=CREATE_ROUTE,
    ),
    finance_mutate=_create_payment,
    prepare=_command_prepare(create_payment_command),
)
_UPDATE = RegisteredTool(
    contract=_contract(
        _definition("update_payment", "Update a payment draft", "payments.update"),
        (FieldRequirement("payment_id", "Payment"),),
        http_method="PATCH",
        route=UPDATE_ROUTE,
        route_fields=("payment_route_id",),
    ),
    finance_mutate=_update_payment,
    prepare=_command_prepare(update_payment_command),
)
_SUBMIT = RegisteredTool(
    contract=_contract(
        _definition("submit_payment", "Submit a payment draft", "payments.submit"),
        (FieldRequirement("payment_id", "Payment"),),
        http_method="POST",
        route=SUBMIT_ROUTE,
        route_fields=("payment_route_id",),
    ),
    finance_mutate=_submit_payment,
    prepare=_command_prepare(submit_payment_command),
)
_CANCEL = RegisteredTool(
    contract=_contract(
        _definition("cancel_payment", "Cancel a payment draft", "payments.update"),
        (
            FieldRequirement("payment_id", "Payment"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=CANCEL_ROUTE,
        route_fields=("payment_route_id",),
    ),
    finance_mutate=_cancel_payment,
    prepare=_command_prepare(cancel_payment_command),
)
_VOID = RegisteredTool(
    contract=_contract(
        _definition("void_payment", "Void a posted payment", "payments.void"),
        (
            FieldRequirement("payment_id", "Payment"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=VOID_ROUTE,
        route_fields=("payment_route_id",),
    ),
    finance_mutate=_void_payment,
    prepare=_command_prepare(void_payment_command),
)
_REFUND = RegisteredTool(
    contract=_contract(
        _definition("refund_payment", "Refund a posted payment", "payments.refund"),
        (
            FieldRequirement("payment_id", "Payment"),
            FieldRequirement("amount", "Amount"),
            FieldRequirement("reason", "Reason"),
            FieldRequirement("method", "Method"),
            FieldRequirement("business_date", "Business date"),
        ),
        http_method="POST",
        route=REFUND_ROUTE,
        route_fields=("payment_route_id",),
    ),
    finance_mutate=_refund_payment,
    prepare=_command_prepare(refund_payment_command),
)
_VOID_RECEIPT = RegisteredTool(
    contract=_contract(
        _definition("void_receipt", "Void an issued receipt", "receipts.void"),
        (
            FieldRequirement("receipt_id", "Receipt"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=RECEIPT_VOID_ROUTE,
        route_fields=("receipt_route_id",),
    ),
    finance_mutate=_void_receipt,
    prepare=_command_prepare(void_receipt_command),
)
_APPROVE = RegisteredTool(
    contract=_contract(
        _definition(
            "approve_payment",
            "Approve a submitted payment that is waiting for approval",
            "payments.approve",
            approval=True,
        ),
        (
            FieldRequirement("payment_id", "Payment"),
            FieldRequirement("record_id", "Stored payment"),
            FieldRequirement("amount", "Stored amount"),
        ),
        http_method="POST",
        route=APPROVE_ROUTE,
        route_fields=("payment_route_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_approve_payment,
    prepare=_command_prepare(approve_payment_command),
    load_approval=_load_payment_approval,
)
_REJECT = RegisteredTool(
    contract=_contract(
        _definition(
            "reject_payment",
            "Reject a submitted payment that is waiting for approval",
            "payments.approve",
            approval=True,
        ),
        (
            FieldRequirement("payment_id", "Payment"),
            FieldRequirement("record_id", "Stored payment"),
            FieldRequirement("amount", "Stored amount"),
            FieldRequirement("reason", "Rejection reason"),
        ),
        http_method="POST",
        route=REJECT_ROUTE,
        route_fields=("payment_route_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_reject_payment,
    prepare=_command_prepare(reject_payment_command),
    load_approval=_load_payment_approval,
)
_REGISTERED = {
    tool.contract.definition.name: tool
    for tool in (
        _CREATE,
        _UPDATE,
        _SUBMIT,
        _APPROVE,
        _REJECT,
        _CANCEL,
        _VOID,
        _REFUND,
        _VOID_RECEIPT,
    )
}
PAYMENT_TOOL_NAMES = tuple(sorted(_REGISTERED))


def payment_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def payment_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool.contract for name, tool in _REGISTERED.items()}


def execute_registered_tool(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    goal: Goal,
    step_id: UUID,
    expected_version: int,
    payload: Mapping[str, object],
    confirmed: bool,
) -> ExecutionResult:
    """Run one registered payment command. Production registration follows its tests."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    command: Mapping[str, object] = payload
    if isinstance(payload, dict):
        command = tool.prepare(request, payload, confirmed=confirmed)
    return execute_confirmed_tool(
        request=request,
        view=view,
        contract=tool.contract,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=command,
        confirmed=confirmed,
        finance_mutate=tool.finance_mutate,
        load_approval=approval_loader(request, command, tool.load_approval),
    )
