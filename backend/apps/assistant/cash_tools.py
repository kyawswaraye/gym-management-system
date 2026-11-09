"""Cash-session commands through the existing finance runner.

Handlers do not call ``run_idempotent``. The executor does, outside the
assistant transaction. Open checks the gym business date before that claim.
Adjustment checks the session currency, business date, and owner rule before
the claim. The finance body is the view body, with money as ``money_text``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.cashbook.selectors import get_visible_session
from apps.cashbook.serializers import (
    AdjustCashSessionSerializer,
    OpenCashSessionSerializer,
    ReconcileCashSessionSerializer,
    money_text,
)
from apps.cashbook.services import (
    assert_current_business_date,
    assert_owner_may_adjust,
    open_cash_session,
    reconcile_cash_session,
    record_manual_adjustment,
)
from apps.cashbook.views import _render_command
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership

OPEN_ROUTE = "/api/v1/cash-sessions/open/"
OPEN_METHOD = "POST"
ADJUST_ROUTE = "/api/v1/cash-sessions/{session_id}/adjustments/"
RECONCILE_ROUTE = "/api/v1/cash-sessions/{session_id}/reconcile/"
ACTION_METHOD = "POST"


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    finance_mutate: Any
    prepare: Any
    preclaim: Any


def _definition(name: str, description: str, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="cashbook",
        operation="write",
        permissions=(permission,),
        features=("finance",),
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
    *,
    route: str,
    route_fields: tuple[str, ...] = (),
) -> ToolContract:
    method = OPEN_METHOD if route_fields == () else ACTION_METHOD
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
        finance_closed=True,
        http_method=method,
        route=route,
        route_fields=route_fields,
    )


def _validated_with(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


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


def open_finance_body(data: Mapping[str, object]) -> dict[str, object]:
    """Return the open body whose finance hash matches the HTTP view."""

    fields = _validated_with(OpenCashSessionSerializer, data)
    return {
        "register_code": str(fields["register_code"]),
        "business_date": cast(date, fields["business_date"]).isoformat(),
        "opening_cash": money_text(cast(Decimal, fields["opening_cash"])),
    }


def adjust_finance_body(data: Mapping[str, object], *, session_id: str) -> dict[str, object]:
    """Return the adjustment body whose finance hash matches the HTTP view."""

    fields = _validated_with(AdjustCashSessionSerializer, data)
    return {
        "cash_session_id": session_id,
        "direction": str(fields["direction"]),
        "amount": money_text(cast(Decimal, fields["amount"])),
        "currency": str(fields["currency"]),
        "business_date": cast(date, fields["business_date"]).isoformat(),
        "description": str(fields["description"]),
        "reason": str(fields["reason"]),
    }


def reconcile_finance_body(data: Mapping[str, object], *, session_id: str) -> dict[str, object]:
    """Return the reconcile body whose finance hash matches the HTTP view."""

    fields = _validated_with(ReconcileCashSessionSerializer, data)
    return {
        "cash_session_id": session_id,
        "actual_closing": money_text(cast(Decimal, fields["actual_closing"])),
        "difference_reason": str(fields.get("difference_reason", "")),
    }


def open_cash_command(payload: Mapping[str, object]) -> dict[str, object]:
    return open_finance_body(_without(payload, "gym_id"))


def _with_session(body: dict[str, object], payload: Mapping[str, object]) -> dict[str, object]:
    if "session_id" not in payload:
        body.pop("cash_session_id", None)
        return body
    token = _route_token(payload.get("session_id"))
    if not isinstance(token, str):
        body.pop("cash_session_id", None)
        body["session_id"] = token
        return body
    try:
        canonical = str(UUID(token))
    except ValueError:
        body.pop("cash_session_id", None)
        body["session_id"] = token
        return body
    body["cash_session_id"] = canonical
    body["session_id"] = canonical
    return body


def adjust_cash_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = adjust_finance_body(
        _without(payload, "gym_id", "session_id", "cash_session_id"),
        session_id="",
    )
    return _with_session(body, payload)


def reconcile_cash_command(payload: Mapping[str, object]) -> dict[str, object]:
    body = reconcile_finance_body(
        _without(payload, "gym_id", "session_id", "cash_session_id"),
        session_id="",
    )
    return _with_session(body, payload)


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


def _session(request: Request) -> tuple[Gym, GymMembership]:
    gym = getattr(request, "gym", None)
    actor = getattr(request, "gym_membership", None)
    if not isinstance(gym, Gym) or not isinstance(actor, GymMembership):
        raise ExecutionRejected("The session has no gym.", code="missing_gym")
    return gym, actor


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


def _preclaim_open(request: Request, command: Mapping[str, object]) -> None:
    gym, _actor = _session(request)
    business_date = date.fromisoformat(str(command["business_date"]))
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert_current_business_date(gym=gym, business_date=business_date)


def _preclaim_adjust(request: Request, command: Mapping[str, object]) -> None:
    session_id = _required_uuid(command.get("session_id"))
    gym, actor = _session(request)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        session = get_visible_session(
            gym_id=gym.id,
            actor=actor,
            session_id=session_id,
            permission_code="cashbook.adjust",
        )
        errors: dict[str, list[str]] = {}
        if str(command["currency"]) != session.currency:
            errors["currency"] = ["Use the cash session currency."]
        if date.fromisoformat(str(command["business_date"])) != session.business_date:
            errors["business_date"] = ["Use the cash session business date."]
        if errors:
            raise ValidationError(errors)
        assert_owner_may_adjust(actor)


def _preclaim_reconcile(request: Request, command: Mapping[str, object]) -> None:
    session_id = _required_uuid(command.get("session_id"))
    gym, actor = _session(request)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        get_visible_session(
            gym_id=gym.id,
            actor=actor,
            session_id=session_id,
            permission_code="cashbook.reconcile",
        )


def _open_cash_session(call: ToolCall) -> Any:
    fields = _validated_with(OpenCashSessionSerializer, call.payload)
    session = open_cash_session(
        gym=call.gym,
        actor=call.actor,
        register_code=str(fields["register_code"]),
        business_date=cast(date, fields["business_date"]),
        opening_cash=cast(Decimal, fields["opening_cash"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_command(session, [], message="Cash session opened", status=201)


def _adjust_cash_session(call: ToolCall) -> Any:
    session_id = _required_uuid(call.payload.get("session_id"))
    fields = _validated_with(
        AdjustCashSessionSerializer,
        _without(call.payload, "session_id", "cash_session_id"),
    )
    session, movement = record_manual_adjustment(
        gym=call.gym,
        actor=call.actor,
        session_id=session_id,
        direction=str(fields["direction"]),
        amount=cast(Decimal, fields["amount"]),
        currency=str(fields["currency"]),
        business_date=cast(date, fields["business_date"]),
        description=str(fields["description"]),
        reason=str(fields["reason"]),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_command(session, [movement], message="Manual adjustment recorded", status=201)


def _reconcile_cash_session(call: ToolCall) -> Any:
    session_id = _required_uuid(call.payload.get("session_id"))
    fields = _validated_with(
        ReconcileCashSessionSerializer,
        _without(call.payload, "session_id", "cash_session_id"),
    )
    session = reconcile_cash_session(
        gym=call.gym,
        actor=call.actor,
        session_id=session_id,
        actual_closing=cast(Decimal, fields["actual_closing"]),
        difference_reason=str(fields.get("difference_reason", "")),
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return _render_command(session, [], message="Cash session reconciled", status=200)


def _open_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=open_cash_command)


def _adjust_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=adjust_cash_command)


def _reconcile_prepare(
    request: Request,
    payload: Mapping[str, object],
    *,
    confirmed: bool,
) -> dict[str, object]:
    return _prepare(request, payload, confirmed=confirmed, command=reconcile_cash_command)


_OPEN = RegisteredTool(
    contract=_contract(
        _definition("open_cash_session", "Open a cash session", "cashbook.open"),
        (
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("opening_cash", "Opening cash"),
        ),
        route=OPEN_ROUTE,
    ),
    finance_mutate=_open_cash_session,
    prepare=_open_prepare,
    preclaim=_preclaim_open,
)
_ADJUST = RegisteredTool(
    contract=_contract(
        _definition("adjust_cash_session", "Adjust a cash session", "cashbook.adjust"),
        (
            FieldRequirement("session_id", "Cash session"),
            FieldRequirement("direction", "Direction"),
            FieldRequirement("amount", "Amount"),
            FieldRequirement("currency", "Currency"),
            FieldRequirement("business_date", "Business date"),
            FieldRequirement("reason", "Reason"),
        ),
        route=ADJUST_ROUTE,
        route_fields=("session_id",),
    ),
    finance_mutate=_adjust_cash_session,
    prepare=_adjust_prepare,
    preclaim=_preclaim_adjust,
)
_RECONCILE = RegisteredTool(
    contract=_contract(
        _definition("reconcile_cash_session", "Reconcile a cash session", "cashbook.reconcile"),
        (
            FieldRequirement("session_id", "Cash session"),
            FieldRequirement("actual_closing", "Actual closing"),
        ),
        route=RECONCILE_ROUTE,
        route_fields=("session_id",),
    ),
    finance_mutate=_reconcile_cash_session,
    prepare=_reconcile_prepare,
    preclaim=_preclaim_reconcile,
)
_REGISTERED = {tool.contract.definition.name: tool for tool in (_OPEN, _ADJUST, _RECONCILE)}
CASH_TOOL_NAMES = tuple(sorted(_REGISTERED))


def cash_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def cash_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool.contract for name, tool in _REGISTERED.items()}


def _route_ready(contract: ToolContract, command: Mapping[str, object]) -> bool:
    for field in contract.route_fields:
        value = command.get(field)
        if not isinstance(value, str):
            return False
        try:
            UUID(value)
        except ValueError:
            return False
    return True


def _should_preclaim(
    request: Request,
    contract: ToolContract,
    command: Mapping[str, object],
    *,
    confirmed: bool,
) -> bool:
    if confirmed is not True or not _route_ready(contract, command):
        return False
    if _passes_gym_to_executor(request, command, confirmed=confirmed):
        return False
    granted: object = getattr(request, "permission_codes", frozenset())
    if isinstance(granted, str) or not isinstance(granted, frozenset | set):
        return False
    return any(code in granted for code in contract.definition.permissions)


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
    """Run one registered cash command. Production registration follows its tests."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    command: Mapping[str, object] = payload
    if isinstance(payload, dict):
        command = tool.prepare(request, payload, confirmed=confirmed)
    if _should_preclaim(request, tool.contract, command, confirmed=confirmed):
        tool.preclaim(request, command)
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
    )
