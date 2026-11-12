"""Read-only retail sale and expense tools.

These calls use the existing list selectors. A receptionist sees only the rows
those selectors already allow. The tools do not post, approve, or void anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import Http404
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.expenses.models import Expense
from apps.expenses.selectors import get_expense, page_expenses
from apps.inventory.quantities import format_quantity
from apps.members.models import Payment
from apps.members.payment_serializers import money_text
from apps.sales.models import RetailSale, RetailSaleLine
from apps.sales.selectors import get_sale, page_sales
from apps.tenants.models import Gym
from apps.users.serializers import StrictSerializer

SEARCH_SALES = "search_sales"
SEARCH_EXPENSES = "search_expenses"
FINANCE_READ_TOOL_NAMES = (SEARCH_SALES, SEARCH_EXPENSES)


class SaleSearchSerializer(StrictSerializer):
    sale_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=RetailSale.Status.choices, required=False)
    business_date = serializers.DateField(required=False)
    member_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


class ExpenseSearchSerializer(StrictSerializer):
    expense_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=Expense.Status.choices, required=False)
    category_id = serializers.UUIDField(required=False)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


def finance_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def finance_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered finance read. The payload does not choose the gym."""

    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_sales(call: ReadCall) -> dict[str, object]:
    """Return retail sales this actor can see in the session gym."""

    gym_id, actor_id = _session(call)
    query = _query(SaleSearchSerializer, call.payload)
    sale_id = query.get("sale_id")
    if isinstance(sale_id, UUID):
        row = get_sale(gym_id=gym_id, sale_id=sale_id, actor_membership_id=actor_id)
        if not _sale_matches(row, query):
            raise Http404
        detailed = _detailed_sales(call.gym, [row])
        return {
            "results": [_sale_row(detailed[0])],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_sales(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        status=str(query.get("status", "")),
        business_date=_date_or_none(query.get("business_date")),
        member_id=_uuid_or_none(query.get("member_id")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_sale_row(row) for row in _detailed_sales(call.gym, rows)],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_expenses(call: ReadCall) -> dict[str, object]:
    """Return expenses this actor can see in the session gym."""

    gym_id, actor_id = _session(call)
    query = _query(ExpenseSearchSerializer, call.payload)
    expense_id = query.get("expense_id")
    if isinstance(expense_id, UUID):
        row = get_expense(gym_id=gym_id, expense_id=expense_id, actor_membership_id=actor_id)
        if not _expense_matches(row, query):
            raise Http404
        detailed = _detailed_expenses(call.gym, [row])
        return {
            "results": [_expense_row(detailed[0])],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_expenses(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        status=str(query.get("status", "")),
        category_id=_uuid_or_none(query.get("category_id")),
        payment_method=str(query.get("payment_method", "")),
        business_date=_date_or_none(query.get("business_date")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_expense_row(row) for row in _detailed_expenses(call.gym, rows)],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition(name: str, description: str, *, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="finance",
        operation="read",
        permissions=(permission,),
        features=("finance",),
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        finance_closed=True,
    )


def _session(call: ReadCall) -> tuple[UUID, UUID]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    return call.gym.id, call.actor.id


def _query(
    serializer_type: type[serializers.Serializer[Any]], payload: Mapping[str, object]
) -> dict[str, Any]:
    serializer = serializer_type(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


def _date_or_none(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _uuid_or_none(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _sale_matches(row: RetailSale, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    business_date = query.get("business_date")
    member_id = query.get("member_id")
    if isinstance(status, str) and row.status != status:
        return False
    if isinstance(business_date, date) and row.business_date != business_date:
        return False
    return not (isinstance(member_id, UUID) and row.member_id != member_id)


def _expense_matches(row: Expense, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    category_id = query.get("category_id")
    payment_method = query.get("payment_method")
    business_date = query.get("business_date")
    if isinstance(status, str) and row.status != status:
        return False
    if isinstance(category_id, UUID) and row.category_id != category_id:
        return False
    if isinstance(payment_method, str) and row.payment_method != payment_method:
        return False
    return not (isinstance(business_date, date) and row.business_date != business_date)


def _detailed_sales(gym: Gym, rows: list[RetailSale]) -> list[RetailSale]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(
        RetailSale.objects.filter(gym_id=gym.id, id__in=order)
        .select_related("member")
        .prefetch_related("lines")
    )
    if len(detailed) != len(order):
        raise ExecutionRejected("The retail sale could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _detailed_expenses(gym: Gym, rows: list[Expense]) -> list[Expense]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(Expense.objects.filter(gym_id=gym.id, id__in=order).select_related("category"))
    if len(detailed) != len(order):
        raise ExecutionRejected("The expense could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _money(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise ExecutionRejected("The stored amount is missing.", code="read_result")
    return money_text(value)


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionRejected("The stored time is missing.", code="time")
    return value.isoformat()


def _member_name(member: Any) -> str:
    return " ".join(
        part for part in (str(member.first_name).strip(), str(member.last_name).strip()) if part
    )


def _line_row(line: RetailSaleLine) -> dict[str, object]:
    return {
        "id": str(line.id),
        "product_id": str(line.product_id),
        "sku": line.sku_snapshot,
        "product_name": line.name_snapshot,
        "quantity": format_quantity(line.quantity),
        "unit_price": _money(line.unit_price_snapshot),
        "discount": _money(line.discount),
        "line_revenue": _money(line.line_revenue),
    }


def _sale_row(row: RetailSale) -> dict[str, object]:
    member_number = None
    member_name = None
    member = row.member
    if member is not None:
        member_number = member.member_number
        member_name = _member_name(member)
    lines = [_line_row(line) for line in row.lines.all()]
    return {
        "id": str(row.id),
        "member_id": None if row.member_id is None else str(row.member_id),
        "member_number": member_number,
        "member_name": member_name,
        "business_date": row.business_date.isoformat(),
        "currency": row.currency,
        "payment_method": row.payment_method,
        "reference_number": row.reference_number,
        "status": row.status,
        "subtotal": _money(row.subtotal),
        "discount_total": _money(row.discount_total),
        "total_amount": _money(row.total_amount),
        "lines": lines,
    }


def _expense_row(row: Expense) -> dict[str, object]:
    return {
        "id": str(row.id),
        "category_id": str(row.category_id),
        "category_code": row.category.code,
        "category_name": row.category.name,
        "business_date": row.business_date.isoformat(),
        "description": row.description,
        "amount": _money(row.amount),
        "currency": row.currency,
        "payment_method": row.payment_method,
        "status": row.status,
        "original_expense_id": None
        if row.original_expense_id is None
        else str(row.original_expense_id),
        "submitted_at": _instant(row.submitted_at),
        "posted_at": _instant(row.posted_at),
        "voided_at": _instant(row.voided_at),
        "cancelled_at": _instant(row.cancelled_at),
    }


_SALE_FIELDS = (
    FieldRequirement("sale_id", "Exact retail sale id. Optional.", required=False),
    FieldRequirement(
        "status",
        "DRAFT, PENDING_APPROVAL, POSTED, REJECTED, CANCELLED, or VOID. Optional.",
        required=False,
    ),
    FieldRequirement(
        "business_date",
        "Stored gym business date. Optional.",
        required=False,
    ),
    FieldRequirement(
        "member_id",
        "Member id returned by search_members. Optional.",
        required=False,
    ),
    FieldRequirement(
        "limit",
        "Page size from 1 to 25. Optional.",
        required=False,
        kind="integer",
    ),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_EXPENSE_FIELDS = (
    FieldRequirement("expense_id", "Exact expense id. Optional.", required=False),
    FieldRequirement(
        "status",
        "DRAFT, PENDING_APPROVAL, POSTED, REJECTED, VOID, or CANCELLED. Optional.",
        required=False,
    ),
    FieldRequirement("category_id", "Expense category id. Optional.", required=False),
    FieldRequirement(
        "payment_method",
        "CASH, BANK_TRANSFER, CARD, MOBILE_PAYMENT, or OTHER. Optional.",
        required=False,
    ),
    FieldRequirement(
        "business_date",
        "Stored gym business date. Optional.",
        required=False,
    ),
    FieldRequirement(
        "limit",
        "Page size from 1 to 25. Optional.",
        required=False,
        kind="integer",
    ),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_READS = {
    SEARCH_SALES: _contract(
        _definition(
            SEARCH_SALES,
            "Search retail sales in this gym. Use the stored total, status, and business date. "
            "For a general list, omit business_date and status.",
            permission="retail.view",
        ),
        _SALE_FIELDS,
    ),
    SEARCH_EXPENSES: _contract(
        _definition(
            SEARCH_EXPENSES,
            "Search expenses in this gym. Use the stored amount, status, and business date. "
            "For a general list, omit business_date and status.",
            permission="expenses.view",
        ),
        _EXPENSE_FIELDS,
    ),
}
_READERS = {
    SEARCH_SALES: search_sales,
    SEARCH_EXPENSES: search_expenses,
}
