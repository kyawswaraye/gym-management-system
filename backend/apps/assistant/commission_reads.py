"""Read-only PT commission and payout tools.

The list uses the existing commission selector. Payout rows come from the
existing commission detail helper. A receptionist does not hold
``pt.commissions.view``, so assistant full access does not open this tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.payment_serializers import money_text
from apps.tenants.models import Gym
from apps.trainers.models import PTCommission, PTCommissionPayment
from apps.trainers.selectors import commission_payments, get_visible_commission, page_pt_commissions
from apps.users.serializers import StrictSerializer

SEARCH_PT_COMMISSIONS = "search_pt_commissions"
COMMISSION_READ_TOOL_NAMES = (SEARCH_PT_COMMISSIONS,)
_VIEW = "pt.commissions.view"


class CommissionSearchSerializer(StrictSerializer):
    commission_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=PTCommission.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    service_date_from = serializers.DateField(required=False)
    service_date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("service_date_from")
        end = attrs.get("service_date_to")
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError(
                {"service_date_to": ["Must be on or after service_date_from."]}
            )
        return attrs


def commission_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def commission_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered commission read. The payload does not choose the gym."""

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


def search_pt_commissions(call: ReadCall) -> dict[str, object]:
    """Return commissions and their stored payouts for this actor and gym."""

    _require_view(call)
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(call.payload)
    commission_id = query.get("commission_id")
    if isinstance(commission_id, UUID):
        row = get_visible_commission(gym=call.gym, actor=call.actor, commission_id=commission_id)
        if not _matches(row, query):
            raise Http404
        detailed = _detailed(call.gym, [row])
        return {
            "results": [_commission_row(detailed[0])],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_pt_commissions(
        gym=call.gym,
        actor=call.actor,
        status=str(query.get("status", "")),
        trainer_staff_profile_id=_uuid_or_none(query.get("trainer_staff_profile_id")),
        service_date_from=_date_or_none(query.get("service_date_from")),
        service_date_to=_date_or_none(query.get("service_date_to")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_commission_row(row) for row in _detailed(call.gym, rows)],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition() -> ToolDefinition:
    return ToolDefinition(
        name=SEARCH_PT_COMMISSIONS,
        description=(
            "Search PT commissions in this gym. Use the stored commission amounts and payouts."
        ),
        module="trainers",
        operation="read",
        permissions=(_VIEW,),
        features=("pt",),
    )


def _contract(definition: ToolDefinition) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=_FIELDS,
        ),
        finance_closed=False,
    )


def _require_view(call: ReadCall) -> None:
    if _VIEW not in call.permission_codes:
        raise PermissionDenied()


def _query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = CommissionSearchSerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


def _date_or_none(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _uuid_or_none(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _matches(row: PTCommission, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    trainer_id = query.get("trainer_staff_profile_id")
    start = query.get("service_date_from")
    end = query.get("service_date_to")
    service_date = row.pt_sale.service_date
    if isinstance(status, str) and row.status != status:
        return False
    if isinstance(trainer_id, UUID) and row.trainer_staff_profile_id != trainer_id:
        return False
    if isinstance(start, date) and service_date < start:
        return False
    return not (isinstance(end, date) and service_date > end)


def _detailed(gym: Gym, rows: list[PTCommission]) -> list[PTCommission]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(
        PTCommission.objects.filter(gym_id=gym.id, id__in=order).select_related(
            "pt_sale", "trainer_staff_profile"
        )
    )
    if len(detailed) != len(order):
        raise ExecutionRejected("The commission could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _money(value: Decimal | None) -> str:
    if not isinstance(value, Decimal):
        raise ExecutionRejected("The stored amount is missing.", code="read_result")
    return money_text(value)


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionRejected("The stored time is missing.", code="time")
    return value.isoformat()


def _payment_row(row: PTCommissionPayment) -> dict[str, object]:
    return {
        "id": str(row.id),
        "amount": _money(row.amount),
        "business_date": row.business_date.isoformat(),
        "payment_method": row.payment_method,
        "status": row.status,
        "expense_id": str(row.expense_id),
        "cash_session_id": None if row.cash_session_id is None else str(row.cash_session_id),
    }


def _commission_row(row: PTCommission) -> dict[str, object]:
    profile = row.trainer_staff_profile
    return {
        "id": str(row.id),
        "pt_sale_id": str(row.pt_sale_id),
        "service_date": row.pt_sale.service_date.isoformat(),
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "staff_code": profile.staff_code,
        "display_name": profile.display_name,
        "commission_percent_snapshot": _money(row.commission_percent_snapshot),
        "commission_amount": _money(row.commission_amount),
        "paid_amount": _money(row.paid_amount),
        "outstanding_amount": _money(row.outstanding_amount),
        "currency": row.currency,
        "status": row.status,
        "paid_at": _instant(row.paid_at),
        "payments": [_payment_row(item) for item in commission_payments(row)],
    }


_FIELDS = (
    FieldRequirement("commission_id", "Exact PT commission id. Optional.", required=False),
    FieldRequirement(
        "status",
        "ACCRUED, PARTIALLY_PAID, PAID, or VOID. Optional.",
        required=False,
    ),
    FieldRequirement(
        "trainer_staff_profile_id",
        "Trainer staff profile id. Optional.",
        required=False,
    ),
    FieldRequirement(
        "service_date_from",
        "Earliest stored PT sale service date. Optional.",
        required=False,
    ),
    FieldRequirement(
        "service_date_to",
        "Latest stored PT sale service date. Optional.",
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
_READS = {SEARCH_PT_COMMISSIONS: _contract(_definition())}
_READERS = {SEARCH_PT_COMMISSIONS: search_pt_commissions}
