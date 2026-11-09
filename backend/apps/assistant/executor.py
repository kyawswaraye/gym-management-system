"""Run one confirmed tool in exactly one of the three execution modes.

Same-transaction tools run inside ``commit_database_effect``. Finance tools call
the existing ``run_idempotent`` runner outside that transaction. A logical claim
fences that call across staff actors, then bookkeeping writes the assistant row.
A tool with an external side effect is rejected before any service, claim, or
finance key is touched.

The gym and the actor come from the authenticated request. ``confirmed`` is a
server argument. A payload field cannot set it, and a payload gym id cannot
select the row.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from django.db import connection, transaction
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.configuration import lock_timeout_ms
from apps.assistant.execution import (
    ExecutionRejected,
    ExecutionResult,
    _clear_lock_timeout,
    _lock_goal,
    _set_lock_timeout,
    commit_bookkeeping,
    commit_database_effect,
)
from apps.assistant.identity import execution_identity
from apps.assistant.logical import (
    acquire_claim,
    complete_owned_claim,
    fail_owned_claim,
    peek_claim,
    release_owned_claim,
    require_current_owner,
)
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.prerequisites import Prerequisite, missing_fields
from apps.assistant.registry import ToolDefinition
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.exceptions import (
    CacheableIdempotentFailure,
    IdempotencyInProgress,
    IdempotencyRequestInvalid,
)
from apps.common.finance.idempotency import (
    IdempotentResponse,
    canonical_request_hash,
    run_idempotent,
)
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
from apps.users.permissions import (
    IsActiveOwner,
    RequiresEntitlements,
    RequiresPermissions,
    RequiresRecentMFA,
    TrainerFinanceClosed,
)

_MODES = frozenset({"same_transaction", "finance_idempotent", "external"})


class ExecutionMode:
    SAME_TRANSACTION = "same_transaction"
    FINANCE_IDEMPOTENT = "finance_idempotent"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ApprovalSnapshot:
    """The persisted record and amount an approval tool must match."""

    record_id: str
    amount: str


@dataclass(frozen=True)
class ToolContract:
    """How one tool is authorized and which execution mode it uses.

    The handler stays with the caller. This contract does not register the tool.
    """

    definition: ToolDefinition
    mode: str
    prerequisite: Prerequisite | None = None
    permission_mode: str = "all"
    finance_closed: bool = False
    http_method: str = ""
    route: str = ""


@dataclass(frozen=True)
class ToolCall:
    """The session values a handler may use. The payload is not a session."""

    gym: Gym
    actor: GymMembership
    goal: Goal
    step: WorkflowStep
    payload: Mapping[str, object]
    attempt_number: int
    request_id: str
    ip_address: str | None
    user_agent: str
    authz_version: int | None = None
    permission_codes: frozenset[str] = frozenset()
    execution_identity: str = ""


def execute_confirmed_tool(
    *,
    request: Request,
    view: APIView,
    contract: ToolContract,
    goal: Goal,
    step_id: UUID,
    expected_version: int,
    payload: Mapping[str, object],
    confirmed: bool,
    effect: Callable[[ToolCall], str] | None = None,
    finance_mutate: Callable[[ToolCall], IdempotentResponse] | None = None,
    load_approval: Callable[[], ApprovalSnapshot] | None = None,
) -> ExecutionResult:
    """Authorize the session, then run the tool's one mode.

    A finished execution identity returns the stored reference and does not run
    the handler again. Finance money stays in the finance idempotency record.
    The assistant row is bookkeeping for that result.
    """

    _reject_unsupported_contract(contract, effect=effect, finance_mutate=finance_mutate)
    if not isinstance(payload, dict):
        raise ExecutionRejected("Confirmed payload must be a JSON object.", code="payload")
    gym, actor = _session(request)
    if goal.gym_id != gym.id:
        raise ExecutionRejected("The goal is not in this gym.", code="gym_mismatch")
    _reject_payload_gym(payload, gym_id=gym.id)
    _authorize(request=request, view=view, contract=contract)
    if contract.prerequisite is not None:
        missing = missing_fields(contract.prerequisite, payload)
        if missing:
            raise ExecutionRejected(
                "Required fields are missing: " + ", ".join(missing),
                code="missing_fields",
            )
    if contract.definition.confirmation_required and confirmed is not True:
        raise ExecutionRejected(
            "The staff member has not confirmed this operation.",
            code="confirmation_required",
        )
    if contract.definition.approval_required:
        _match_approval(payload, load_approval)
    step, identity, stored = _load_step(
        gym=gym,
        goal=goal,
        step_id=step_id,
        tool_name=contract.definition.name,
        payload=payload,
    )
    if stored is not None:
        return stored
    request_id, ip_address, user_agent = _request_context(request)
    call = ToolCall(
        gym=gym,
        actor=actor,
        goal=goal,
        step=step,
        payload=payload,
        attempt_number=step.attempt_number,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        authz_version=_session_authz_version(request),
        permission_codes=_permission_codes(request),
        execution_identity=identity,
    )
    if contract.mode == ExecutionMode.SAME_TRANSACTION:
        return _run_same_transaction(
            call=call,
            effect=effect,
            expected_version=expected_version,
            identity=identity,
        )
    return _run_finance(
        call=call,
        contract=contract,
        finance_mutate=finance_mutate,
        expected_version=expected_version,
        identity=identity,
    )


def _reject_unsupported_contract(
    contract: ToolContract,
    *,
    effect: Callable[[ToolCall], str] | None,
    finance_mutate: Callable[[ToolCall], IdempotentResponse] | None,
) -> None:
    if contract.mode not in _MODES:
        raise ExecutionRejected("The execution mode is not supported.", code="execution_mode")
    if contract.mode == ExecutionMode.EXTERNAL or contract.definition.external_side_effects:
        raise ExecutionRejected(
            "External side effects stay outside the assistant executor.",
            code="external_side_effects",
        )
    if contract.definition.operation != "write":
        raise ExecutionRejected("This executor runs write tools.", code="operation")
    if contract.permission_mode not in {"all", "any"}:
        raise ExecutionRejected("The permission mode is not supported.", code="permission_mode")
    same = contract.mode == ExecutionMode.SAME_TRANSACTION
    if same and finance_mutate is not None:
        raise ExecutionRejected(
            "A same-transaction tool cannot use finance idempotency.",
            code="execution_mode",
        )
    if same and effect is None:
        raise ExecutionRejected("The same-transaction tool has no effect.", code="missing_handler")
    if not same and effect is not None:
        raise ExecutionRejected(
            "A finance tool cannot use the database claim effect.",
            code="execution_mode",
        )
    if not same and finance_mutate is None:
        raise ExecutionRejected("The finance tool has no mutate.", code="missing_handler")
    if not same and (contract.http_method == "" or contract.route == ""):
        raise ExecutionRejected(
            "A finance tool names the existing command method and route.",
            code="finance_route",
        )


def _request_context(request: Request) -> tuple[str, str | None, str]:
    request_id = getattr(request, "request_id", "")
    meta = getattr(request, "META", {})
    address = meta.get("REMOTE_ADDR") if isinstance(meta, Mapping) else None
    return (
        str(request_id) if request_id else "req_unknown",
        str(address) if address else None,
        str(meta.get("HTTP_USER_AGENT", "")) if isinstance(meta, Mapping) else "",
    )


def _permission_codes(request: Request) -> frozenset[str]:
    granted: object = getattr(request, "permission_codes", frozenset())
    if isinstance(granted, frozenset):
        return granted
    if isinstance(granted, set):
        return frozenset(code for code in granted if isinstance(code, str))
    return frozenset()


def _session_authz_version(request: Request) -> int | None:
    """Read the token version the role services already compare.

    The confirmed payload cannot supply this value. A missing or unusable
    claim stays unset, and the role handler stops before the service.
    """

    claims = getattr(request, "auth", None)
    if not isinstance(claims, Mapping):
        return None
    version = claims.get("authz_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None
    return version


def _session(request: Request) -> tuple[Gym, GymMembership]:
    gym = getattr(request, "gym", None)
    actor = getattr(request, "gym_membership", None)
    if not isinstance(gym, Gym) or gym.id is None:
        raise ExecutionRejected("The session has no gym.", code="missing_gym")
    if not isinstance(actor, GymMembership) or actor.id is None:
        raise ExecutionRejected("The session has no staff membership.", code="missing_actor")
    if actor.gym_id != gym.id:
        raise ExecutionRejected("The staff membership is not in this gym.", code="gym_mismatch")
    return gym, actor


def _reject_payload_gym(payload: Mapping[str, object], *, gym_id: UUID) -> None:
    if "gym_id" not in payload:
        return
    if str(payload["gym_id"]) != str(gym_id):
        raise ExecutionRejected(
            "The payload gym does not select the session gym.",
            code="gym_mismatch",
        )


def _authorize(*, request: Request, view: APIView, contract: ToolContract) -> None:
    tool = contract.definition
    if tool.permissions:
        granted: object = getattr(request, "permission_codes", frozenset())
        if isinstance(granted, str) or not isinstance(granted, frozenset | set):
            raise PermissionDenied()
        if contract.permission_mode == "any":
            if not any(code in granted for code in tool.permissions):
                raise PermissionDenied()
        else:
            requirements = _requirements(tool.permissions, tool.features)
            if not RequiresPermissions().has_permission(request, requirements):
                raise PermissionDenied()
    elif not tool.requires_active_owner:
        raise PermissionDenied()
    if tool.requires_active_owner and not IsActiveOwner().has_permission(request, view):
        raise PermissionDenied()
    if tool.features:
        requirements = _requirements(tool.permissions, tool.features)
        if not RequiresEntitlements().has_permission(request, requirements):
            raise PermissionDenied()
    if contract.finance_closed and not TrainerFinanceClosed().has_permission(request, view):
        raise PermissionDenied()
    if tool.requires_recent_mfa:
        RequiresRecentMFA().has_permission(request, view)


class _RequirementView:
    """Carries the codes the original view declared. It is not an HTTP view."""

    def __init__(self, permissions: tuple[str, ...], features: tuple[str, ...]) -> None:
        self.required_permissions = permissions
        self.required_features = features


def _requirements(permissions: tuple[str, ...], features: tuple[str, ...]) -> APIView:
    return cast(APIView, _RequirementView(permissions, features))


def _match_approval(
    payload: Mapping[str, object],
    load_approval: Callable[[], ApprovalSnapshot] | None,
) -> None:
    if load_approval is None:
        raise ExecutionRejected(
            "The approval record was not loaded.",
            code="approval_mismatch",
        )
    stored = load_approval()
    record_id = payload.get("record_id")
    amount = payload.get("amount")
    if (
        not isinstance(stored, ApprovalSnapshot)
        or not isinstance(record_id, str)
        or not isinstance(amount, str)
        or record_id != stored.record_id
        or amount != stored.amount
    ):
        raise ExecutionRejected(
            "The confirmed record and amount do not match the stored record.",
            code="approval_mismatch",
        )


def _load_step(
    *,
    gym: Gym,
    goal: Goal,
    step_id: UUID,
    tool_name: str,
    payload: Mapping[str, object],
) -> tuple[WorkflowStep, str, ExecutionResult | None]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        try:
            step = WorkflowStep.objects.get(gym_id=gym.id, id=step_id, goal_id=goal.id)
        except WorkflowStep.DoesNotExist as exc:
            raise ExecutionRejected(
                "The workflow step is not in this gym.",
                code="step_not_found",
            ) from exc
        if step.tool_name != tool_name:
            raise ExecutionRejected(
                "The workflow step does not name this tool.",
                code="step_tool",
            )
        identity = execution_identity(
            gym_id=gym.id,
            goal_id=goal.id,
            step_id=step.id,
            tool_name=tool_name,
            attempt_number=step.attempt_number,
            payload=payload,
        )
        stored = _claim_result(gym_id=gym.id, goal_id=goal.id, identity=identity)
        return step, identity, stored


def _claim_result(*, gym_id: UUID, goal_id: UUID, identity: str) -> ExecutionResult | None:
    row = (
        ToolExecution.objects.filter(gym_id=gym_id, execution_identity=identity)
        .only("effect_reference")
        .first()
    )
    if row is None:
        return None
    if row.effect_reference == "":
        raise ExecutionRejected(
            "The stored effect reference is empty.",
            code="missing_effect_reference",
        )
    current = Goal.objects.only("version").get(gym_id=gym_id, id=goal_id)
    return ExecutionResult("existing", current.version, row.effect_reference, identity)


def _run_same_transaction(
    *,
    call: ToolCall,
    effect: Callable[[ToolCall], str] | None,
    expected_version: int,
    identity: str,
) -> ExecutionResult:
    handler = effect
    assert handler is not None

    def _effect() -> str:
        if not connection.in_atomic_block:
            raise ExecutionRejected(
                "A same-transaction tool left its database transaction.",
                code="same_transaction",
            )
        reference = handler(call)
        if not isinstance(reference, str) or reference == "" or len(reference) > 64:
            raise ExecutionRejected(
                "The database effect needs a reference.",
                code="missing_effect_reference",
            )
        return reference

    result = commit_database_effect(
        gym=call.gym,
        goal_id=call.goal.id,
        expected_version=expected_version,
        step_id=call.step.id,
        tool_name=call.step.tool_name,
        attempt_number=call.attempt_number,
        payload=call.payload,
        effect=_effect,
    )
    return _recover(result, gym=call.gym, goal_id=call.goal.id, identity=identity)


def _run_finance(
    *,
    call: ToolCall,
    contract: ToolContract,
    finance_mutate: Callable[[ToolCall], IdempotentResponse] | None,
    expected_version: int,
    identity: str,
) -> ExecutionResult:
    handler = finance_mutate
    assert handler is not None
    if connection.in_atomic_block:
        raise ExecutionRejected(
            "A finance command cannot start inside the assistant transaction.",
            code="finance_inside_transaction",
        )
    decided = _logical_shortcut(call, identity)
    if decided is not None:
        return decided
    blocked = _finance_version(
        gym=call.gym,
        goal_id=call.goal.id,
        expected_version=expected_version,
        identity=identity,
    )
    if blocked is not None:
        if blocked.outcome == "stale_version":
            finished = _logical_shortcut(call, identity)
            if finished is not None:
                return finished
            try:
                recovered = _completed_finance_reference(
                    call=call,
                    contract=contract,
                    identity=identity,
                )
            except IdempotencyRequestInvalid:
                recovered = None
            if recovered is not None:
                return ExecutionResult(
                    "bookkeeping_pending",
                    blocked.version,
                    recovered,
                    identity,
                )
        return blocked
    owned = acquire_claim(
        gym=call.gym,
        goal=call.goal,
        step=call.step,
        execution_identity=identity,
    )
    if owned.kind == "completed":
        return _bookkeep_reference(call, owned.resource_id, identity)
    if owned.kind == "failed":
        raise ExecutionRejected(
            "The finance command is stored as a failure.",
            code="finance_failed",
        )
    if owned.kind == "expired":
        raise ExecutionRejected(
            "The logical claim has expired.",
            code="claim_expired",
        )
    if owned.kind != "owned" or owned.locked_until is None:
        return ExecutionResult("in_progress", None, None, identity)
    fence_token = owned.fence_token
    locked_until = owned.locked_until

    def _fail() -> None:
        fail_owned_claim(
            gym_id=call.gym.id,
            claim_id=owned.claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
        )

    def _release() -> None:
        release_owned_claim(
            gym_id=call.gym.id,
            claim_id=owned.claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
        )

    def _mutate() -> IdempotentResponse:
        require_current_owner(
            gym_id=call.gym.id,
            claim_id=owned.claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
        )
        response = handler(call)
        reference = _finance_reference(response.resource_id)
        complete_owned_claim(
            gym_id=call.gym.id,
            claim_id=owned.claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
            resource_id=reference,
        )
        return response

    try:
        finance = run_idempotent(
            gym_id=call.gym.id,
            actor_membership_id=call.actor.id,
            http_method=contract.http_method,
            route=contract.route,
            idempotency_key=identity,
            body=call.payload,
            mutate=_mutate,
            created_by_membership_id=call.actor.id,
            on_failed=_fail,
            on_released=_release,
        )
    except CacheableIdempotentFailure:
        raise
    except IdempotencyInProgress:
        _release()
        return ExecutionResult("in_progress", None, None, identity)
    except Exception:
        _release()
        raise
    if finance.status < 200 or finance.status > 299:
        _release()
        raise ExecutionRejected(
            "The finance command is stored as a failure.",
            code="finance_failed",
        )
    try:
        reference = _finance_reference(finance.resource_id)
    except ExecutionRejected:
        _fail()
        raise
    return _bookkeep_reference(call, reference, identity)


def _finance_reference(resource_id: object) -> str:
    """Return the stored record id, or reject before a claim can commit without one."""

    if resource_id is None:
        raise ExecutionRejected(
            "The finance result has no committed record.",
            code="missing_effect_reference",
        )
    reference = str(resource_id)
    if reference == "" or len(reference) > 64:
        raise ExecutionRejected(
            "The finance result has no committed record.",
            code="missing_effect_reference",
        )
    return reference


def _logical_shortcut(call: ToolCall, identity: str) -> ExecutionResult | None:
    """Finish a stored claim. An open lease returns control to acquisition."""

    decision = peek_claim(gym_id=call.gym.id, execution_identity=identity)
    if decision is None or decision.kind == "open":
        return None
    if decision.kind == "completed":
        return _bookkeep_reference(call, decision.resource_id, identity)
    if decision.kind == "failed":
        raise ExecutionRejected(
            "The finance command is stored as a failure.",
            code="finance_failed",
        )
    if decision.kind == "expired":
        raise ExecutionRejected(
            "The logical claim has expired.",
            code="claim_expired",
        )
    return ExecutionResult("in_progress", None, None, identity)


def _bookkeep_reference(call: ToolCall, reference: str, identity: str) -> ExecutionResult:
    booked = commit_bookkeeping(
        gym=call.gym,
        goal_id=call.goal.id,
        step_id=call.step.id,
        tool_name=call.step.tool_name,
        attempt_number=call.attempt_number,
        payload=call.payload,
        reference=reference,
    )
    return _recover(
        booked,
        gym=call.gym,
        goal_id=call.goal.id,
        identity=identity,
        recovered_reference=reference,
    )


def _finance_version(
    *,
    gym: Gym,
    goal_id: UUID,
    expected_version: int,
    identity: str,
) -> ExecutionResult | None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        _set_lock_timeout(lock_timeout_ms())
        try:
            locked = _lock_goal(gym_id=gym.id, goal_id=goal_id)
        except TimeoutError:
            _clear_lock_timeout()
            return ExecutionResult("in_progress", None, None, identity)
        except Goal.DoesNotExist as exc:
            _clear_lock_timeout()
            raise ExecutionRejected("The goal is not in this gym.", code="goal_not_found") from exc
        _clear_lock_timeout()
        if locked.version != expected_version:
            return ExecutionResult("stale_version", locked.version, None, identity)
        return None


def _completed_finance_reference(
    *,
    call: ToolCall,
    contract: ToolContract,
    identity: str,
) -> str | None:
    expected_hash = canonical_request_hash(
        gym_id=call.gym.id,
        actor_membership_id=call.actor.id,
        http_method=contract.http_method,
        route=contract.route,
        body=call.payload,
    )
    with transaction.atomic():
        set_gym_context(gym_id=call.gym.id)
        row = (
            IdempotencyRecord.objects.filter(
                gym_id=call.gym.id,
                actor_membership_id=call.actor.id,
                actor_member_id=None,
                http_method=contract.http_method,
                route=contract.route,
                idempotency_key=identity,
                state=IdempotencyRecord.State.COMPLETED,
            )
            .only("request_hash", "resource_id")
            .first()
        )
        if row is None or row.resource_id is None:
            return None
        if not hmac.compare_digest(row.request_hash, expected_hash):
            return None
        return str(row.resource_id)


def _recover(
    result: ExecutionResult,
    *,
    gym: Gym,
    goal_id: UUID,
    identity: str,
    recovered_reference: str | None = None,
) -> ExecutionResult:
    if result.outcome in {"committed", "existing"}:
        return result
    if result.outcome not in {"stale_version", "in_progress"}:
        return result
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = _claim_result(gym_id=gym.id, goal_id=goal_id, identity=identity)
    if stored is not None:
        return stored
    if recovered_reference is not None:
        return ExecutionResult(
            "bookkeeping_pending",
            result.version,
            recovered_reference,
            identity,
        )
    return result
