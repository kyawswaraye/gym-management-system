"""Confirmation values kept outside the finance request hash.

The stored record id and ``money_text`` amount are compared before a finance
claim. They are not part of the view body.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.db import transaction
from django.db.models import Model, Sum
from rest_framework.request import Request

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ApprovalSnapshot
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.models import Payment
from apps.members.payment_serializers import money_text
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.sales.models import RetailSale
from apps.tenants.models import Gym
from apps.trainers.models import PTSale

CONFIRMATION_FIELDS = ("record_id", "amount")


def approval_loader(
    request: Request,
    payload: Mapping[str, object],
    load: Callable[[Gym, Mapping[str, object]], ApprovalSnapshot] | None,
) -> Callable[[], ApprovalSnapshot] | None:
    """Bind a stored-record loader to the session gym. It does not open a claim."""

    if load is None:
        return None

    def loader() -> ApprovalSnapshot:
        gym = getattr(request, "gym", None)
        if not isinstance(gym, Gym) or gym.id is None:
            raise _mismatch()
        return load(gym, payload)

    return loader


def attach_confirmation(
    body: dict[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Copy the confirmation pair onto a command without changing the view body."""

    if "record_id" in payload:
        body["record_id"] = _record_token(payload.get("record_id"))
    if "amount" in payload:
        body["amount"] = payload.get("amount")
    return body


def payment_snapshot(gym: Gym, raw_id: object) -> ApprovalSnapshot:
    return _amount_snapshot(gym, Payment, raw_id, "amount")


def expense_snapshot(gym: Gym, raw_id: object) -> ApprovalSnapshot:
    return _amount_snapshot(gym, Expense, raw_id, "amount")


def retail_snapshot(gym: Gym, raw_id: object) -> ApprovalSnapshot:
    return _amount_snapshot(gym, RetailSale, raw_id, "total_amount")


def pt_sale_snapshot(gym: Gym, raw_id: object) -> ApprovalSnapshot:
    return _amount_snapshot(gym, PTSale, raw_id, "gross_fee")


def salary_snapshot(gym: Gym, raw_id: object) -> ApprovalSnapshot:
    """Confirm the period id and the money text of the stored line-amount sum."""

    period_id = _required_id(raw_id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = (
            SalaryPeriod.objects.filter(gym_id=gym.id, id=period_id).only("id", "status").first()
        )
        if period is None:
            raise _mismatch()
        total = StaffSalaryLine.objects.filter(
            gym_id=gym.id,
            salary_period_id=period.id,
        ).aggregate(total=Sum("amount"))["total"]
    amount = Decimal("0.00") if total is None else total
    return ApprovalSnapshot(
        record_id=str(period.id),
        amount=money_text(amount),
        status=str(period.status),
    )


def _amount_snapshot(
    gym: Gym,
    model: type[Model],
    raw_id: object,
    amount_attr: str,
) -> ApprovalSnapshot:
    record_id = _required_id(raw_id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row: Any = cast(Any, model).objects.filter(gym_id=gym.id, id=record_id).first()
    if row is None:
        raise _mismatch()
    return ApprovalSnapshot(
        record_id=str(row.id),
        amount=money_text(getattr(row, amount_attr)),
        status=str(getattr(row, "status", "")),
    )


def _required_id(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            raise _mismatch() from None
    raise _mismatch()


def _record_token(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return value
    return value


def _mismatch() -> ExecutionRejected:
    return ExecutionRejected(
        "The confirmed record and amount do not match the stored record.",
        code="approval_mismatch",
    )
