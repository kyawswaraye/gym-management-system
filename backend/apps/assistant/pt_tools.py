"""PT sale, commission, package, and booking commands through the finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Approve and reject confirm the stored sale id and
``money_text`` of the gross fee outside the view-body hash. These views do not close trainer-only
finance access, so the contracts leave ``finance_closed`` false. Each finance
body matches the view body. The route id is a separate field and is not added
to a body the view does not hash.
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
    pt_sale_snapshot,
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
from apps.classes.booking_views import EmptyCommandSerializer
from apps.common.finance.idempotency import IdempotentResponse
from apps.expenses.serializers import expense_payload
from apps.members.payment_serializers import payment_payload, receipt_payload
from apps.tenants.models import Gym
from apps.trainers.package_services import adjust_ledger, grant_package_admin
from apps.trainers.pt_booking_services import (
    attend_pt_booking,
    cancel_pt_booking,
    create_pt_booking,
    no_show_pt_booking,
)
from apps.trainers.pt_commission_services import record_pt_commission_payment
from apps.trainers.pt_sale_services import (
    approve_pt_sale,
    cancel_pt_sale,
    create_pt_sale,
    reject_pt_sale,
    submit_pt_sale,
    update_pt_sale,
)
from apps.trainers.pt_sale_void_services import void_pt_sale
from apps.trainers.serializers import (
    LedgerAdjustSerializer,
    MemberPackageGrantSerializer,
    PTBookingCancelSerializer,
    PTBookingWriteSerializer,
    PTCommissionPaymentWriteSerializer,
    PTSaleApproveSerializer,
    PTSaleCancelSerializer,
    PTSaleRejectSerializer,
    PTSaleUpdateSerializer,
    PTSaleVoidSerializer,
    PTSaleWriteSerializer,
    commission_payload,
    commission_payment_payload,
    ledger_entry_payload,
    member_package_payload,
    pt_booking_payload,
    pt_sale_payload,
)

CREATE_SALE_ROUTE = "/api/v1/pt-sales/"
UPDATE_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/"
CANCEL_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/cancel/"
SUBMIT_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/submit/"
APPROVE_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/approve/"
REJECT_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/reject/"
VOID_SALE_ROUTE = "/api/v1/pt-sales/{pt_sale_id}/void/"
COMMISSION_ROUTE = "/api/v1/pt-commissions/{commission_id}/record-payment/"
GRANT_ROUTE = "/api/v1/member-service-packages/"
LEDGER_ROUTE = "/api/v1/member-service-packages/{member_service_package_id}/ledger/adjust/"
CREATE_BOOKING_ROUTE = "/api/v1/pt-bookings/"
ATTEND_BOOKING_ROUTE = "/api/v1/pt-bookings/{pt_booking_id}/attend/"
NO_SHOW_BOOKING_ROUTE = "/api/v1/pt-bookings/{pt_booking_id}/no-show/"
CANCEL_BOOKING_ROUTE = "/api/v1/pt-bookings/{pt_booking_id}/cancel/"


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
        module="trainers",
        operation="write",
        permissions=(permission,),
        features=("pt",),
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
        finance_closed=False,
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
            raise ExecutionRejected("The PT command is not a JSON body.", code="payload")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        rendered: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExecutionRejected("The PT command is not a JSON body.", code="payload")
            rendered[key] = _json_value(item)
        return rendered
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise ExecutionRejected("The PT command is not a JSON body.", code="payload")


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


def _keep_route(
    body: dict[str, object],
    payload: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    if key not in payload:
        return body
    body[key] = _route_token(payload.get(key))
    token = body[key]
    if isinstance(token, str):
        try:
            body[key] = str(UUID(token))
        except ValueError:
            return body
    return body


def create_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the create body whose finance hash matches the HTTP view."""

    return _finance_body(PTSaleWriteSerializer, data)


def update_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the update body whose finance hash matches the HTTP view."""

    return _finance_body(PTSaleUpdateSerializer, data)


def cancel_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the cancel body whose finance hash matches the HTTP view."""

    return _finance_body(PTSaleCancelSerializer, data)


def void_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the void body whose finance hash matches the HTTP view."""

    fields = _validated_with(PTSaleVoidSerializer, data)
    reversals = sorted(
        fields["payout_reversals"],
        key=lambda row: str(row["commission_payment_id"]),
    )
    cash_session_id = fields.get("cash_session_id")
    return {
        "reason": str(fields["reason"]),
        "business_date": fields["business_date"].isoformat(),
        "cash_session_id": None if cash_session_id is None else str(cash_session_id),
        "payout_reversals": [
            {
                "commission_payment_id": str(row["commission_payment_id"]),
                "cash_session_id": (
                    None if row["cash_session_id"] is None else str(row["cash_session_id"])
                ),
            }
            for row in reversals
        ],
    }


def record_pt_commission_payment_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the payout body whose finance hash matches the HTTP view."""

    fields = _validated_with(PTCommissionPaymentWriteSerializer, data)
    return {
        "amount": _json_value(fields["amount"]),
        "business_date": _json_value(fields["business_date"]),
        "payment_method": str(fields["payment_method"]),
        "cash_session_id": _json_value(fields.get("cash_session_id")),
        "reference_number": str(fields.get("reference_number", "")),
        "policy_note": str(fields.get("policy_note", "")),
    }


def grant_member_package_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the grant body whose finance hash matches the HTTP view."""

    return _finance_body(MemberPackageGrantSerializer, data)


def adjust_package_ledger_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the ledger body whose finance hash matches the HTTP view."""

    return _finance_body(LedgerAdjustSerializer, data)


def create_pt_booking_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the booking body whose finance hash matches the HTTP view."""

    return _finance_body(PTBookingWriteSerializer, data)


def cancel_pt_booking_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the booking-cancel body whose finance hash matches the HTTP view."""

    return _finance_body(PTBookingCancelSerializer, data)


def create_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_pt_sale_body(_without(payload, "gym_id"))


def update_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = update_pt_sale_body(_without(payload, "gym_id", "pt_sale_id"))
    return _keep_route(body, payload, "pt_sale_id")


def cancel_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_pt_sale_body(_without(payload, "gym_id", "pt_sale_id"))
    return _keep_route(body, payload, "pt_sale_id")


def submit_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", "pt_sale_id"))
    return _keep_route({}, payload, "pt_sale_id")


_APPROVAL_STRIP = ("gym_id", "pt_sale_id", "record_id", "amount", "confirmed")


def approve_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the approve body whose finance hash matches the HTTP view."""

    fields = _validated_with(PTSaleApproveSerializer, data)
    return {"cash_session_id": _json_value(fields.get("cash_session_id"))}


def reject_pt_sale_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the reject body whose finance hash matches the HTTP view."""

    return _finance_body(PTSaleRejectSerializer, data)


def _approval_command(
    body: dict[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    return attach_confirmation(_keep_route(body, payload, "pt_sale_id"), payload)


def approve_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return _approval_command(approve_pt_sale_body(_without(payload, *_APPROVAL_STRIP)), payload)


def reject_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    return _approval_command(reject_pt_sale_body(_without(payload, *_APPROVAL_STRIP)), payload)


def void_pt_sale_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = void_pt_sale_body(_without(payload, "gym_id", "pt_sale_id"))
    return _keep_route(body, payload, "pt_sale_id")


def record_pt_commission_payment_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = record_pt_commission_payment_body(_without(payload, "gym_id", "commission_id"))
    return _keep_route(body, payload, "commission_id")


def grant_member_package_command(payload: Mapping[str, object]) -> dict[str, object]:
    return grant_member_package_body(_without(payload, "gym_id"))


def adjust_package_ledger_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = adjust_package_ledger_body(_without(payload, "gym_id", "member_service_package_id"))
    return _keep_route(body, payload, "member_service_package_id")


def create_pt_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    return create_pt_booking_body(_without(payload, "gym_id"))


def attend_pt_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", "pt_booking_id"))
    return _keep_route({}, payload, "pt_booking_id")


def mark_pt_booking_no_show_command(payload: Mapping[str, object]) -> dict[str, object]:
    _validated_with(EmptyCommandSerializer, _without(payload, "gym_id", "pt_booking_id"))
    return _keep_route({}, payload, "pt_booking_id")


def cancel_pt_booking_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = cancel_pt_booking_body(_without(payload, "gym_id", "pt_booking_id"))
    return _keep_route(body, payload, "pt_booking_id")


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


def _render_sale(row: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": pt_sale_payload(row), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="PTSale",
        resource_id=row.id,
    )


def _render_void(result: dict[str, Any]) -> IdempotentResponse:
    result["sale"].granted_package_id = None if result["package"] is None else result["package"].id
    payment = result["payment"]
    rendered = {
        "success": True,
        "data": {
            "sale": pt_sale_payload(result["sale"]),
            "commission": commission_payload(result["commission"]),
            "payouts": [commission_payment_payload(row) for row in result["payouts"]],
            "payment": None
            if payment is None
            else {
                "original": payment_payload(payment["original"]),
                "correction": payment_payload(payment["correction"]),
                "original_receipt": (
                    None
                    if payment["original_receipt"] is None
                    else receipt_payload(payment["original_receipt"])
                ),
                "correction_receipt": receipt_payload(payment["correction_receipt"]),
                "cash_transaction": (
                    None
                    if payment["cash_transaction"] is None
                    else transaction_payload(payment["cash_transaction"])
                ),
            },
            "member_service_package": (
                None if result["package"] is None else member_package_payload(result["package"])
            ),
            "ledger_entry": (
                None if result["refund"] is None else ledger_entry_payload(result["refund"])
            ),
        },
        "message": "PT sale voided",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="PTSale",
        resource_id=result["sale"].id,
    )


def _render_booking(row: Any, message: str, status: int) -> IdempotentResponse:
    rendered = {"success": True, "data": pt_booking_payload(row), "message": message}
    return IdempotentResponse(
        status=status,
        body=JSONRenderer().render(rendered),
        resource_type="PTBooking",
        resource_id=row.id,
    )


def _create_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleWriteSerializer, call.payload)
    row = create_pt_sale(
        gym=call.gym,
        actor=call.actor,
        member_id=fields["member_id"],
        trainer_staff_profile_id=fields["trainer_staff_profile_id"],
        service_date=fields["service_date"],
        description=str(fields["description"]),
        gross_fee=fields["gross_fee"],
        currency=str(fields["currency"]),
        payment=fields.get("payment"),
        service_package_id=fields.get("service_package_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(row, "PT sale draft created", 201)


def _update_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleUpdateSerializer, _without(call.payload, "pt_sale_id"))
    row = update_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        fields=fields,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(row, "PT sale draft updated", 200)


def _cancel_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleCancelSerializer, _without(call.payload, "pt_sale_id"))
    row = cancel_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(row, "PT sale cancelled", 200)


def _submit_sale(call: ToolCall) -> IdempotentResponse:
    _validated_with(EmptyCommandSerializer, _without(call.payload, "pt_sale_id"))
    row = submit_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(row, "PT sale submitted", 200)


_APPROVAL_KEYS = ("pt_sale_id", "record_id", "amount")


def _approve_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleApproveSerializer, _without(call.payload, *_APPROVAL_KEYS))
    row, payment, receipt, movement, commission, granted = approve_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        cash_session_id=fields.get("cash_session_id"),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    if granted is not None:
        row.__dict__["granted_package_id"] = granted.id
    rendered = {
        "success": True,
        "data": {
            "sale": pt_sale_payload(row),
            "payment": None if payment is None else payment_payload(payment),
            "receipt": None if receipt is None else receipt_payload(receipt),
            "cash_session": None if movement is None else session_payload(movement.cash_session),
            "cash_transaction": None if movement is None else transaction_payload(movement),
            "commission": commission_payload(commission),
            "member_service_package": (
                None if granted is None else member_package_payload(granted)
            ),
        },
        "message": "PT sale approved",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="PTSale",
        resource_id=row.id,
    )


def _reject_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleRejectSerializer, _without(call.payload, *_APPROVAL_KEYS))
    row = reject_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        rejection_reason=str(fields["rejection_reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_sale(row, "PT sale rejected", 200)


def _load_pt_approval(gym: Gym, payload: Mapping[str, object]) -> ApprovalSnapshot:
    return pt_sale_snapshot(gym, payload.get("pt_sale_id"))


def _void_sale(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTSaleVoidSerializer, _without(call.payload, "pt_sale_id"))
    reversals = sorted(
        fields["payout_reversals"],
        key=lambda row: str(row["commission_payment_id"]),
    )
    result = void_pt_sale(
        gym=call.gym,
        actor=call.actor,
        sale_id=_required_uuid(call.payload.get("pt_sale_id")),
        reason=str(fields["reason"]),
        business_date=fields["business_date"],
        cash_session_id=fields.get("cash_session_id"),
        payout_reversals=reversals,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_void(result)


def _record_commission(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        PTCommissionPaymentWriteSerializer,
        _without(call.payload, "commission_id"),
    )
    before, after, payment, expense, movement = record_pt_commission_payment(
        gym=call.gym,
        actor=call.actor,
        commission_id=_required_uuid(call.payload.get("commission_id")),
        amount=fields["amount"],
        business_date=fields["business_date"],
        payment_method=str(fields["payment_method"]),
        cash_session_id=fields.get("cash_session_id"),
        reference_number=str(fields.get("reference_number", "")),
        policy_note=str(fields.get("policy_note", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": {
            "commission_before": commission_payload(before),
            "commission_after": commission_payload(after),
            "payment": commission_payment_payload(payment),
            "expense": expense_payload(expense),
            "cash_session": None if movement is None else session_payload(movement.cash_session),
            "cash_transaction": None if movement is None else transaction_payload(movement),
        },
        "message": "Commission payout recorded",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="PTCommissionPayment",
        resource_id=payment.id,
    )


def _grant_package(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(MemberPackageGrantSerializer, call.payload)
    grant, entry = grant_package_admin(
        gym=call.gym,
        actor=call.actor,
        member_id=fields["member_id"],
        service_package_id=fields["service_package_id"],
        business_date=fields["business_date"],
        complimentary=bool(fields["complimentary"]),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": {
            "member_service_package": member_package_payload(grant),
            "ledger_entry": ledger_entry_payload(entry),
        },
        "message": "Member service package granted",
    }
    return IdempotentResponse(
        status=201,
        body=JSONRenderer().render(rendered),
        resource_type="MemberServicePackage",
        resource_id=grant.id,
    )


def _adjust_ledger(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(
        LedgerAdjustSerializer,
        _without(call.payload, "member_service_package_id"),
    )
    grant, entry = adjust_ledger(
        gym=call.gym,
        actor=call.actor,
        package_id=_required_uuid(call.payload.get("member_service_package_id")),
        reverses_entry_id=fields["reverses_entry_id"],
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    rendered = {
        "success": True,
        "data": {
            "member_service_package": member_package_payload(grant),
            "ledger_entry": ledger_entry_payload(entry),
        },
        "message": "Session ledger corrected",
    }
    return IdempotentResponse(
        status=200,
        body=JSONRenderer().render(rendered),
        resource_type="ServiceSessionLedger",
        resource_id=entry.id,
    )


def _create_booking(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTBookingWriteSerializer, call.payload)
    row = create_pt_booking(
        gym=call.gym,
        actor=call.actor,
        trainer_staff_profile_id=fields["trainer_staff_profile_id"],
        member_id=fields["member_id"],
        starts_at=fields["starts_at"],
        ends_at=fields["ends_at"],
        member_service_package_id=fields.get("member_service_package_id"),
        unmetered=bool(fields.get("unmetered", False)),
        unmetered_reason=str(fields.get("unmetered_reason", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_booking(row, "Personal training booking created", 201)


def _attend_booking(call: ToolCall) -> IdempotentResponse:
    _validated_with(EmptyCommandSerializer, _without(call.payload, "pt_booking_id"))
    row = attend_pt_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=_required_uuid(call.payload.get("pt_booking_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_booking(row, "Personal training booking attended", 200)


def _no_show_booking(call: ToolCall) -> IdempotentResponse:
    _validated_with(EmptyCommandSerializer, _without(call.payload, "pt_booking_id"))
    row = no_show_pt_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=_required_uuid(call.payload.get("pt_booking_id")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_booking(row, "Personal training booking marked no-show", 200)


def _cancel_booking(call: ToolCall) -> IdempotentResponse:
    fields = _validated_with(PTBookingCancelSerializer, _without(call.payload, "pt_booking_id"))
    row = cancel_pt_booking(
        gym=call.gym,
        actor=call.actor,
        booking_id=_required_uuid(call.payload.get("pt_booking_id")),
        reason=str(fields.get("reason", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_booking(row, "Personal training booking cancelled", 200)


def _command_prepare(command: Callable[[Mapping[str, object]], dict[str, object]]) -> Any:
    def prepare(
        request: Request,
        payload: Mapping[str, object],
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        return _prepare(request, payload, confirmed=confirmed, command=command)

    return prepare


_SALE_FIELDS = (
    FieldRequirement("member_id", "Member"),
    FieldRequirement("trainer_staff_profile_id", "Trainer"),
    FieldRequirement("service_date", "Service date"),
    FieldRequirement("description", "Description"),
    FieldRequirement("gross_fee", "Gross fee"),
    FieldRequirement("currency", "Currency"),
)
_CREATE_SALE = RegisteredTool(
    contract=_contract(
        _definition("create_pt_sale", "Create a PT sale draft", "pt.sales.create"),
        _SALE_FIELDS,
        http_method="POST",
        route=CREATE_SALE_ROUTE,
    ),
    finance_mutate=_create_sale,
    prepare=_command_prepare(create_pt_sale_command),
)
_UPDATE_SALE = RegisteredTool(
    contract=_contract(
        _definition("update_pt_sale", "Update a PT sale draft", "pt.sales.update"),
        (FieldRequirement("pt_sale_id", "PT sale"),),
        http_method="PATCH",
        route=UPDATE_SALE_ROUTE,
        route_fields=("pt_sale_id",),
    ),
    finance_mutate=_update_sale,
    prepare=_command_prepare(update_pt_sale_command),
)
_CANCEL_SALE = RegisteredTool(
    contract=_contract(
        _definition("cancel_pt_sale", "Cancel a PT sale draft", "pt.sales.update"),
        (
            FieldRequirement("pt_sale_id", "PT sale"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=CANCEL_SALE_ROUTE,
        route_fields=("pt_sale_id",),
    ),
    finance_mutate=_cancel_sale,
    prepare=_command_prepare(cancel_pt_sale_command),
)
_SUBMIT_SALE = RegisteredTool(
    contract=_contract(
        _definition("submit_pt_sale", "Submit a PT sale draft", "pt.sales.submit"),
        (FieldRequirement("pt_sale_id", "PT sale"),),
        http_method="POST",
        route=SUBMIT_SALE_ROUTE,
        route_fields=("pt_sale_id",),
    ),
    finance_mutate=_submit_sale,
    prepare=_command_prepare(submit_pt_sale_command),
)
_VOID_SALE = RegisteredTool(
    contract=_contract(
        _definition("void_pt_sale", "Void a posted PT sale", "pt.sales.void"),
        (
            FieldRequirement("pt_sale_id", "PT sale"),
            FieldRequirement("reason", "Reason"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("payout_reversals", "Payout reversals"),
        ),
        http_method="POST",
        route=VOID_SALE_ROUTE,
        route_fields=("pt_sale_id",),
    ),
    finance_mutate=_void_sale,
    prepare=_command_prepare(void_pt_sale_command),
)
_COMMISSION = RegisteredTool(
    contract=_contract(
        _definition(
            "record_pt_commission_payment",
            "Record a PT commission payout",
            "pt.commissions.pay",
        ),
        (
            FieldRequirement("commission_id", "Commission"),
            FieldRequirement("amount", "Amount"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("payment_method", "Payment method"),
        ),
        http_method="POST",
        route=COMMISSION_ROUTE,
        route_fields=("commission_id",),
    ),
    finance_mutate=_record_commission,
    prepare=_command_prepare(record_pt_commission_payment_command),
)
_GRANT = RegisteredTool(
    contract=_contract(
        _definition("grant_member_package", "Grant a member service package", "pt.sessions.adjust"),
        (
            FieldRequirement("member_id", "Member"),
            FieldRequirement("service_package_id", "Service package"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("complimentary", "Complimentary"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=GRANT_ROUTE,
    ),
    finance_mutate=_grant_package,
    prepare=_command_prepare(grant_member_package_command),
)
_LEDGER = RegisteredTool(
    contract=_contract(
        _definition(
            "adjust_package_ledger",
            "Correct a member package ledger entry",
            "pt.sessions.adjust",
        ),
        (
            FieldRequirement("member_service_package_id", "Member package"),
            FieldRequirement("reverses_entry_id", "Ledger entry"),
            FieldRequirement("reason", "Reason"),
        ),
        http_method="POST",
        route=LEDGER_ROUTE,
        route_fields=("member_service_package_id",),
    ),
    finance_mutate=_adjust_ledger,
    prepare=_command_prepare(adjust_package_ledger_command),
)
_CREATE_BOOKING = RegisteredTool(
    contract=_contract(
        _definition("create_pt_booking", "Create a PT booking", "pt.schedule.manage"),
        (
            FieldRequirement("trainer_staff_profile_id", "Trainer"),
            FieldRequirement("member_id", "Member"),
            FieldRequirement("starts_at", "Start"),
            FieldRequirement("ends_at", "End"),
        ),
        http_method="POST",
        route=CREATE_BOOKING_ROUTE,
    ),
    finance_mutate=_create_booking,
    prepare=_command_prepare(create_pt_booking_command),
)
_ATTEND_BOOKING = RegisteredTool(
    contract=_contract(
        _definition("attend_pt_booking", "Attend a PT booking", "pt.schedule.manage"),
        (FieldRequirement("pt_booking_id", "PT booking"),),
        http_method="POST",
        route=ATTEND_BOOKING_ROUTE,
        route_fields=("pt_booking_id",),
    ),
    finance_mutate=_attend_booking,
    prepare=_command_prepare(attend_pt_booking_command),
)
_NO_SHOW_BOOKING = RegisteredTool(
    contract=_contract(
        _definition("mark_pt_booking_no_show", "Mark a PT booking no-show", "pt.schedule.manage"),
        (FieldRequirement("pt_booking_id", "PT booking"),),
        http_method="POST",
        route=NO_SHOW_BOOKING_ROUTE,
        route_fields=("pt_booking_id",),
    ),
    finance_mutate=_no_show_booking,
    prepare=_command_prepare(mark_pt_booking_no_show_command),
)
_CANCEL_BOOKING = RegisteredTool(
    contract=_contract(
        _definition("cancel_pt_booking", "Cancel a PT booking", "pt.schedule.manage"),
        (FieldRequirement("pt_booking_id", "PT booking"),),
        http_method="POST",
        route=CANCEL_BOOKING_ROUTE,
        route_fields=("pt_booking_id",),
    ),
    finance_mutate=_cancel_booking,
    prepare=_command_prepare(cancel_pt_booking_command),
)
_APPROVE_SALE = RegisteredTool(
    contract=_contract(
        _definition("approve_pt_sale", "Approve a PT sale", "pt.sales.approve", approval=True),
        (
            FieldRequirement("pt_sale_id", "PT sale"),
            FieldRequirement("record_id", "Stored sale"),
            FieldRequirement("amount", "Stored gross fee"),
        ),
        http_method="POST",
        route=APPROVE_SALE_ROUTE,
        route_fields=("pt_sale_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_approve_sale,
    prepare=_command_prepare(approve_pt_sale_command),
    load_approval=_load_pt_approval,
)
_REJECT_SALE = RegisteredTool(
    contract=_contract(
        _definition("reject_pt_sale", "Reject a PT sale", "pt.sales.approve", approval=True),
        (
            FieldRequirement("pt_sale_id", "PT sale"),
            FieldRequirement("record_id", "Stored sale"),
            FieldRequirement("amount", "Stored gross fee"),
            FieldRequirement("rejection_reason", "Rejection reason"),
        ),
        http_method="POST",
        route=REJECT_SALE_ROUTE,
        route_fields=("pt_sale_id",),
        confirmation_fields=CONFIRMATION_FIELDS,
    ),
    finance_mutate=_reject_sale,
    prepare=_command_prepare(reject_pt_sale_command),
    load_approval=_load_pt_approval,
)
_REGISTERED = {
    tool.contract.definition.name: tool
    for tool in (
        _CREATE_SALE,
        _UPDATE_SALE,
        _CANCEL_SALE,
        _SUBMIT_SALE,
        _APPROVE_SALE,
        _REJECT_SALE,
        _VOID_SALE,
        _COMMISSION,
        _GRANT,
        _LEDGER,
        _CREATE_BOOKING,
        _ATTEND_BOOKING,
        _NO_SHOW_BOOKING,
        _CANCEL_BOOKING,
    )
}
PT_TOOL_NAMES = tuple(sorted(_REGISTERED))


def pt_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def pt_tool_contracts() -> dict[str, ToolContract]:
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
    """Run one registered PT command. Production registration follows its tests."""

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
