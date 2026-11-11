"""Connect a turn to the existing read and confirmed-write executors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.activity_tools import activity_tool_contracts
from apps.assistant.activity_tools import execute_registered_tool as execute_activity
from apps.assistant.approval import (
    expense_snapshot,
    payment_snapshot,
    pt_sale_snapshot,
    retail_snapshot,
    salary_snapshot,
)
from apps.assistant.booking_tools import booking_tool_contracts
from apps.assistant.booking_tools import execute_registered_tool as execute_booking
from apps.assistant.cash_tools import cash_tool_contracts
from apps.assistant.cash_tools import execute_registered_tool as execute_cash
from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ApprovalSnapshot, ToolContract
from apps.assistant.expense_tools import execute_registered_tool as execute_expense
from apps.assistant.expense_tools import expense_tool_contracts
from apps.assistant.inventory_tools import execute_registered_tool as execute_inventory
from apps.assistant.inventory_tools import inventory_tool_contracts
from apps.assistant.member_tools import execute_registered_tool as execute_member
from apps.assistant.member_tools import member_tool_contracts
from apps.assistant.models import Goal
from apps.assistant.payment_reads import (
    PAYMENT_READ_TOOL_NAMES,
    execute_registered_read,
    payment_read_tool_contracts,
)
from apps.assistant.payment_tools import execute_registered_tool as execute_payment
from apps.assistant.payment_tools import payment_tool_contracts
from apps.assistant.pt_tools import execute_registered_tool as execute_pt
from apps.assistant.pt_tools import pt_tool_contracts
from apps.assistant.retail_tools import execute_registered_tool as execute_retail
from apps.assistant.retail_tools import retail_tool_contracts
from apps.assistant.salary_tools import execute_registered_tool as execute_salary
from apps.assistant.salary_tools import salary_tool_contracts
from apps.assistant.schedule_tools import execute_registered_tool as execute_schedule
from apps.assistant.schedule_tools import schedule_tool_contracts
from apps.assistant.staff_tools import execute_registered_tool as execute_staff
from apps.assistant.staff_tools import staff_tool_contracts
from apps.assistant.training_tools import execute_registered_tool as execute_training
from apps.assistant.training_tools import training_tool_contracts
from apps.tenants.models import Gym

Writer = Callable[..., ExecutionResult]
ApprovalLoader = Callable[[Gym, object], ApprovalSnapshot]

_CONTRACTS: dict[str, ToolContract] | None = None


def tool_contracts() -> dict[str, ToolContract]:
    global _CONTRACTS
    if _CONTRACTS is None:
        loaded: dict[str, ToolContract] = {}
        for group in (
            member_tool_contracts(),
            staff_tool_contracts(),
            activity_tool_contracts(),
            training_tool_contracts(),
            schedule_tool_contracts(),
            booking_tool_contracts(),
            inventory_tool_contracts(),
            cash_tool_contracts(),
            payment_tool_contracts(),
            payment_read_tool_contracts(),
            expense_tool_contracts(),
            retail_tool_contracts(),
            salary_tool_contracts(),
            pt_tool_contracts(),
        ):
            loaded.update(group)
        _CONTRACTS = loaded
    return _CONTRACTS


def execute_offered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered read. The payload does not choose the gym."""

    if tool_name not in PAYMENT_READ_TOOL_NAMES:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_registered_read(
        request=request,
        view=view,
        tool_name=tool_name,
        payload=payload,
    )


def execute_confirmed_write(
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
    """Run one registered write through its existing executor."""

    writer = _WRITERS.get(tool_name)
    if writer is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return writer(
        request=request,
        view=view,
        tool_name=tool_name,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def approval_source(tool_name: str) -> tuple[str, ApprovalLoader] | None:
    return _APPROVAL_SOURCES.get(tool_name)


_WRITERS: dict[str, Writer] = {}


def _bind(names: tuple[str, ...], writer: Writer) -> None:
    for name in names:
        _WRITERS[name] = writer


def _load_writers() -> None:
    from apps.assistant.activity_tools import ACTIVITY_TOOL_NAMES
    from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
    from apps.assistant.cash_tools import CASH_TOOL_NAMES
    from apps.assistant.expense_tools import EXPENSE_TOOL_NAMES
    from apps.assistant.inventory_tools import INVENTORY_TOOL_NAMES
    from apps.assistant.member_tools import MEMBER_TOOL_NAMES
    from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES
    from apps.assistant.pt_tools import PT_TOOL_NAMES
    from apps.assistant.retail_tools import RETAIL_TOOL_NAMES
    from apps.assistant.salary_tools import SALARY_TOOL_NAMES
    from apps.assistant.schedule_tools import SCHEDULE_TOOL_NAMES
    from apps.assistant.staff_tools import STAFF_TOOL_NAMES
    from apps.assistant.training_tools import TRAINING_TOOL_NAMES

    _bind(MEMBER_TOOL_NAMES, execute_member)
    _bind(STAFF_TOOL_NAMES, execute_staff)
    _bind(ACTIVITY_TOOL_NAMES, execute_activity)
    _bind(TRAINING_TOOL_NAMES, execute_training)
    _bind(SCHEDULE_TOOL_NAMES, execute_schedule)
    _bind(BOOKING_TOOL_NAMES, execute_booking)
    _bind(INVENTORY_TOOL_NAMES, execute_inventory)
    _bind(CASH_TOOL_NAMES, execute_cash)
    _bind(PAYMENT_TOOL_NAMES, execute_payment)
    _bind(EXPENSE_TOOL_NAMES, execute_expense)
    _bind(RETAIL_TOOL_NAMES, execute_retail)
    _bind(SALARY_TOOL_NAMES, execute_salary)
    _bind(PT_TOOL_NAMES, execute_pt)


_APPROVAL_SOURCES: dict[str, tuple[str, ApprovalLoader]] = {
    "approve_payment": ("payment_id", payment_snapshot),
    "reject_payment": ("payment_id", payment_snapshot),
    "approve_expense": ("expense_id", expense_snapshot),
    "reject_expense": ("expense_id", expense_snapshot),
    "approve_retail_sale": ("retail_sale_id", retail_snapshot),
    "reject_retail_sale": ("retail_sale_id", retail_snapshot),
    "approve_salary_period": ("salary_period_id", salary_snapshot),
    "reject_salary_period": ("salary_period_id", salary_snapshot),
    "approve_pt_sale": ("pt_sale_id", pt_sale_snapshot),
    "reject_pt_sale": ("pt_sale_id", pt_sale_snapshot),
}

_load_writers()
