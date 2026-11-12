"""Read-only salary period and cash session tools.

These calls use the existing list selectors. A receptionist sees only the rows
those selectors already allow. The tools do not post, approve, close, or void
anything, and they do not invent a period total or a till balance.
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
from apps.cashbook.models import CashSession
from apps.cashbook.selectors import get_visible_session, page_cash_sessions
from apps.members.payment_serializers import money_text
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.salaries.selectors import get_period, page_periods
from apps.tenants.models import Gym
from apps.users.serializers import StrictSerializer

SEARCH_SALARY_PERIODS = "search_salary_periods"
SEARCH_CASH_SESSIONS = "search_cash_sessions"
OPERATIONS_READ_TOOL_NAMES = (SEARCH_SALARY_PERIODS, SEARCH_CASH_SESSIONS)
_CASH_VIEW = "cashbook.view"
_REGISTER = r"^[A-Z0-9_-]{1,32}$"


class SalaryPeriodSearchSerializer(StrictSerializer):
    period_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=SalaryPeriod.Status.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


class CashSessionSearchSerializer(StrictSerializer):
    session_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=CashSession.Status.choices, required=False)
    register_code = serializers.RegexField(_REGISTER, required=False, max_length=32)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


def operations_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def operations_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered salary or cash read. The payload does not choose the gym."""

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


def search_salary_periods(call: ReadCall) -> dict[str, object]:
    """Return salary periods this actor can see in the session gym."""

    gym_id, actor_id = _session(call)
    query = _query(SalaryPeriodSearchSerializer, call.payload)
    period_id = query.get("period_id")
    if isinstance(period_id, UUID):
        row = get_period(gym_id=gym_id, period_id=period_id, actor_membership_id=actor_id)
        if not _period_matches(row, query):
            raise Http404
        detailed = _detailed_periods(call.gym, [row])
        return {
            "results": [_period_row(detailed[0])],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_periods(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        status=str(query.get("status", "")),
        business_date=_date_or_none(query.get("business_date")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_period_row(row) for row in _detailed_periods(call.gym, rows)],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_cash_sessions(call: ReadCall) -> dict[str, object]:
    """Return cash sessions this actor can see in the session gym."""

    gym_id, _actor_id = _session(call)
    query = _query(CashSessionSearchSerializer, call.payload)
    session_id = query.get("session_id")
    if isinstance(session_id, UUID):
        row = get_visible_session(
            gym_id=gym_id,
            actor=call.actor,
            session_id=session_id,
            permission_code=_CASH_VIEW,
        )
        if not _session_matches(row, query):
            raise Http404
        detailed = _detailed_sessions(call.gym, [row])
        return {
            "results": [_session_row(detailed[0])],
            "has_more": False,
            "next_after": None,
        }
    rows, meta = page_cash_sessions(
        gym_id=gym_id,
        actor=call.actor,
        status=str(query.get("status", "")),
        register_code=str(query.get("register_code", "")),
        business_date=_date_or_none(query.get("business_date")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_session_row(row) for row in _detailed_sessions(call.gym, rows)],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition(
    name: str,
    description: str,
    *,
    module: str,
    permission: str,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module=module,
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


def _period_matches(row: SalaryPeriod, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    business_date = query.get("business_date")
    if isinstance(status, str) and row.status != status:
        return False
    return not (isinstance(business_date, date) and row.business_date != business_date)


def _session_matches(row: CashSession, query: Mapping[str, Any]) -> bool:
    status = query.get("status")
    register_code = query.get("register_code")
    business_date = query.get("business_date")
    if isinstance(status, str) and row.status != status:
        return False
    if isinstance(register_code, str) and row.register_code != register_code:
        return False
    return not (isinstance(business_date, date) and row.business_date != business_date)


def _detailed_periods(gym: Gym, rows: list[SalaryPeriod]) -> list[SalaryPeriod]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(
        SalaryPeriod.objects.filter(gym_id=gym.id, id__in=order).prefetch_related(
            "lines__staff_profile"
        )
    )
    if len(detailed) != len(order):
        raise ExecutionRejected("The salary period could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _detailed_sessions(gym: Gym, rows: list[CashSession]) -> list[CashSession]:
    if not rows:
        return []
    order = {row.id: index for index, row in enumerate(rows)}
    detailed = list(CashSession.objects.filter(gym_id=gym.id, id__in=order))
    if len(detailed) != len(order):
        raise ExecutionRejected("The cash session could not be read.", code="read_result")
    detailed.sort(key=lambda row: order[row.id])
    return detailed


def _money(value: Decimal | None) -> str:
    if not isinstance(value, Decimal):
        raise ExecutionRejected("The stored amount is missing.", code="read_result")
    return money_text(value)


def _optional_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _money(value)


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionRejected("The stored time is missing.", code="time")
    return value.isoformat()


def _line_row(line: StaffSalaryLine) -> dict[str, object]:
    profile = line.staff_profile
    return {
        "id": str(line.id),
        "staff_profile_id": str(line.staff_profile_id),
        "staff_code": profile.staff_code,
        "display_name": profile.display_name,
        "amount": _money(line.amount),
        "currency": line.currency,
        "payment_method": line.payment_method,
    }


def _period_row(row: SalaryPeriod) -> dict[str, object]:
    lines = sorted(row.lines.all(), key=lambda line: str(line.id))
    return {
        "id": str(row.id),
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "business_date": row.business_date.isoformat(),
        "description": row.description,
        "status": row.status,
        "submitted_at": _instant(row.submitted_at),
        "approved_at": _instant(row.approved_at),
        "rejected_at": _instant(row.rejected_at),
        "posted_at": _instant(row.posted_at),
        "voided_at": _instant(row.voided_at),
        "cancelled_at": _instant(row.cancelled_at),
        "lines": [_line_row(line) for line in lines],
    }


def _session_row(row: CashSession) -> dict[str, object]:
    return {
        "id": str(row.id),
        "business_date": row.business_date.isoformat(),
        "register_code": row.register_code,
        "currency": row.currency,
        "status": row.status,
        "opening_cash": _money(row.opening_cash),
        "cash_sales": _money(row.cash_sales),
        "cash_expenses": _money(row.cash_expenses),
        "manual_net": _money(row.manual_net),
        "expected_closing": _money(row.expected_closing),
        "actual_closing": _optional_money(row.actual_closing),
        "difference": _optional_money(row.difference),
        "closed_at": _instant(row.closed_at),
    }


_PERIOD_FIELDS = (
    FieldRequirement("period_id", "Exact salary period id. Optional.", required=False),
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
        "limit",
        "Page size from 1 to 25. Optional.",
        required=False,
        kind="integer",
    ),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_SESSION_FIELDS = (
    FieldRequirement("session_id", "Exact cash session id. Optional.", required=False),
    FieldRequirement("status", "OPEN, CLOSED, or VOID. Optional.", required=False),
    FieldRequirement(
        "register_code",
        "Register code using letters, digits, underscore, or hyphen. Optional.",
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
    SEARCH_SALARY_PERIODS: _contract(
        _definition(
            SEARCH_SALARY_PERIODS,
            "Search salary periods in this gym. Use the stored status and period dates. "
            "For a general list, omit business_date and status so every matching "
            "period is returned.",
            module="salaries",
            permission="salaries.view",
        ),
        _PERIOD_FIELDS,
    ),
    SEARCH_CASH_SESSIONS: _contract(
        _definition(
            SEARCH_CASH_SESSIONS,
            "Search cash sessions in this gym. Use the stored status and business date. "
            "For a general list, omit status so open and closed sessions are both returned.",
            module="cashbook",
            permission="cashbook.view",
        ),
        _SESSION_FIELDS,
    ),
}
_READERS = {
    SEARCH_SALARY_PERIODS: search_salary_periods,
    SEARCH_CASH_SESSIONS: search_cash_sessions,
}
