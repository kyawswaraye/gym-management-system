"""Payment reads through the existing payment selectors.

The session gym and actor are the only scope. These tools do not confirm,
approve, or write an execution claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from uuid import UUID

from django.http import Http404
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.approval import payment_snapshot
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.models import Payment
from apps.members.payment_selectors import get_payment as load_payment
from apps.members.payment_selectors import page_payments
from apps.members.payment_serializers import PaymentListQuerySerializer, money_text

SEARCH_PAYMENTS = "search_payments"
GET_PAYMENT = "get_payment"
PAYMENT_READ_TOOL_NAMES = (GET_PAYMENT, SEARCH_PAYMENTS)


def payment_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def payment_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered payment read. The payload does not choose the gym."""

    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    reader = _READERS[tool_name]
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=reader,
    )


def search_payments(call: ReadCall) -> dict[str, object]:
    """Return one page visible to this actor in the session gym."""

    serializer = PaymentListQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    gym_id = call.gym.id
    actor_id = call.actor.id
    if gym_id is None or actor_id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    rows, meta = page_payments(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        status=str(query.get("status", "")),
        member_id=query.get("member"),
        membership_id=query.get("membership"),
        purpose=str(query.get("purpose", "")),
        method=str(query.get("method", "")),
        business_date=query.get("business_date"),
        limit=int(query["limit"]),
        after=str(query.get("after", "")),
    )
    return {
        "results": [_search_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def get_payment(call: ReadCall) -> dict[str, object]:
    """Return one payment and the approval amount stored for that row."""

    payment_id = _payment_id(call.payload.get("payment_id"))
    gym_id = call.gym.id
    if gym_id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    row = load_payment(gym_id=gym_id, payment_id=payment_id)
    snapshot = payment_snapshot(call.gym, row.id)
    return {
        "record_id": snapshot.record_id,
        "amount": snapshot.amount,
        "status": row.status,
        "currency": row.currency,
        "method": row.method,
        "purpose": row.purpose,
        "description": row.description,
        "business_date": _date_text(row.business_date),
        "member_id": _member_text(row.member_id),
        "entry_type": row.entry_type,
        "original_payment_id": _member_text(row.original_payment_id),
    }


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="payments",
        operation="read",
        permissions=("payments.view",),
        features=("finance",),
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...] = (),
) -> ToolContract:
    prerequisite = None
    if fields:
        prerequisite = Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        )
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=prerequisite,
        finance_closed=True,
    )


def _search_row(row: Payment) -> dict[str, object]:
    return {
        "id": str(row.id),
        "status": row.status,
        "amount": money_text(row.amount),
        "currency": row.currency,
        "method": row.method,
        "purpose": row.purpose,
        "description": row.description,
        "business_date": _date_text(row.business_date),
        "member_id": _member_text(row.member_id),
        "entry_type": row.entry_type,
        "original_payment_id": _member_text(row.original_payment_id),
    }


def _payment_id(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            raise Http404 from None
    raise Http404


def _date_text(value: date) -> str:
    return value.isoformat()


def _member_text(value: UUID | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_READS = {
    SEARCH_PAYMENTS: _contract(_definition(SEARCH_PAYMENTS, "Search payments in this gym")),
    GET_PAYMENT: _contract(
        _definition(GET_PAYMENT, "Read one payment in this gym"),
        (FieldRequirement("payment_id", "Payment"),),
    ),
}
_READERS = {
    SEARCH_PAYMENTS: search_payments,
    GET_PAYMENT: get_payment,
}
