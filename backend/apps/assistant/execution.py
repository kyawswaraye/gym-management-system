from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone

from apps.assistant.configuration import lock_timeout_ms
from apps.assistant.identity import execution_identity, payload_hash
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym

_LOCK_NOT_AVAILABLE = "55P03"
_UNIQUE_VIOLATION = "23505"
_IDENTITY_CONSTRAINT = "assistant_execution_identity_uniq"


class ExecutionRejected(Exception):
    """The caller asked the assistant to cover something it does not."""

    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutionResult:
    outcome: str
    version: int | None
    effect_reference: str | None
    execution_identity: str | None


def _sqlstate(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ("sqlstate", "pgcode"):
            code = getattr(current, attribute, None)
            if isinstance(code, str):
                return code
        current = current.__cause__ or current.__context__
    return None


def _constraint_name(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        diag = getattr(current, "diag", None)
        name = getattr(diag, "constraint_name", None)
        if isinstance(name, str):
            return name
        current = current.__cause__ or current.__context__
    text = str(exc)
    if _IDENTITY_CONSTRAINT in text:
        return _IDENTITY_CONSTRAINT
    return None


def _set_lock_timeout(milliseconds: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [f"{milliseconds}ms"])


def _clear_lock_timeout() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('lock_timeout', '0', true)")


def _lock_goal(*, gym_id: UUID, goal_id: UUID) -> Goal:
    try:
        with transaction.atomic():
            return Goal.objects.select_for_update().get(gym_id=gym_id, id=goal_id)
    except OperationalError as exc:
        if _sqlstate(exc) == _LOCK_NOT_AVAILABLE:
            raise TimeoutError("goal lock") from exc
        raise


def _insert_claim(
    *,
    gym: Gym,
    goal: Goal,
    step: WorkflowStep,
    tool_name: str,
    attempt_number: int,
    identity: str,
    payload_digest: str,
    external_side_effects: tuple[str, ...],
) -> ToolExecution:
    try:
        with transaction.atomic():
            return ToolExecution.objects.create(
                gym=gym,
                goal=goal,
                step=step,
                tool_name=tool_name,
                attempt_number=attempt_number,
                execution_identity=identity,
                payload_hash=payload_digest,
                effect_reference="",
                external_side_effects=list(external_side_effects),
                covers_external_side_effects=False,
            )
    except IntegrityError as exc:
        if _sqlstate(exc) == _UNIQUE_VIOLATION and _constraint_name(exc) == _IDENTITY_CONSTRAINT:
            raise FileExistsError(identity) from exc
        raise
    except OperationalError as exc:
        if _sqlstate(exc) == _LOCK_NOT_AVAILABLE:
            raise TimeoutError("claim lock") from exc
        raise


def commit_database_effect(
    *,
    gym: Gym,
    goal_id: UUID,
    expected_version: int,
    step_id: UUID,
    tool_name: str,
    attempt_number: int,
    payload: Mapping[str, object],
    effect: Callable[[], str],
    external_side_effects: tuple[str, ...] = (),
    covers_external_side_effects: bool = False,
    lock_timeout: int | None = None,
) -> ExecutionResult:
    """Commit at most one database effect for this execution identity.

    The goal row lock runs first. The unique identity constraint is the safety
    net. The claim, the effect, and the goal version commit together or not
    at all. A timeout while the original transaction is unresolved returns
    ``in_progress`` and writes nothing. External side effects stay uncovered.
    """

    if covers_external_side_effects:
        raise ExecutionRejected(
            "External side effects stay outside the database claim.",
            code="external_side_effects",
        )
    timeout = lock_timeout if lock_timeout is not None else lock_timeout_ms()
    identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name=tool_name,
        attempt_number=attempt_number,
        payload=payload,
    )
    digest = payload_hash(payload)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        _set_lock_timeout(timeout)
        try:
            goal = _lock_goal(gym_id=gym.id, goal_id=goal_id)
        except TimeoutError:
            _clear_lock_timeout()
            return ExecutionResult("in_progress", None, None, identity)
        if goal.version != expected_version:
            _clear_lock_timeout()
            return ExecutionResult("stale_version", goal.version, None, identity)
        step = WorkflowStep.objects.get(gym_id=gym.id, id=step_id, goal_id=goal.id)
        try:
            claim = _insert_claim(
                gym=gym,
                goal=goal,
                step=step,
                tool_name=tool_name,
                attempt_number=attempt_number,
                identity=identity,
                payload_digest=digest,
                external_side_effects=external_side_effects,
            )
        except TimeoutError:
            _clear_lock_timeout()
            return ExecutionResult("in_progress", goal.version, None, identity)
        except FileExistsError:
            _clear_lock_timeout()
            existing = ToolExecution.objects.get(gym_id=gym.id, execution_identity=identity)
            return ExecutionResult(
                "existing",
                goal.version,
                existing.effect_reference,
                identity,
            )
        _clear_lock_timeout()
        reference = effect()
        if not isinstance(reference, str) or reference == "" or len(reference) > 64:
            raise ExecutionRejected(
                "The database effect needs a reference.",
                code="missing_effect_reference",
            )
        claim.effect_reference = reference
        claim.save(update_fields=["effect_reference", "updated_at"])
        next_version = goal.version + 1
        updated = Goal.objects.filter(pk=goal.pk, version=goal.version).update(
            version=next_version,
            updated_at=timezone.now(),
        )
        if updated != 1:
            raise ExecutionRejected(
                "The goal version changed inside the lock.",
                code="version_conflict",
            )
        return ExecutionResult("committed", next_version, reference, identity)


def commit_bookkeeping(
    *,
    gym: Gym,
    goal_id: UUID,
    step_id: UUID,
    tool_name: str,
    attempt_number: int,
    payload: Mapping[str, object],
    reference: str,
    lock_timeout: int | None = None,
) -> ExecutionResult:
    """Record an already committed resource id and advance the locked version.

    The caller version is not consulted. This path does not run a business
    service. The unique execution identity still rejects a second insert.
    """

    if reference == "" or len(reference) > 64:
        raise ExecutionRejected(
            "The database effect needs a reference.",
            code="missing_effect_reference",
        )
    timeout = lock_timeout if lock_timeout is not None else lock_timeout_ms()
    identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name=tool_name,
        attempt_number=attempt_number,
        payload=payload,
    )
    digest = payload_hash(payload)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        _set_lock_timeout(timeout)
        try:
            goal = _lock_goal(gym_id=gym.id, goal_id=goal_id)
        except TimeoutError:
            _clear_lock_timeout()
            return ExecutionResult("in_progress", None, None, identity)
        except Goal.DoesNotExist as exc:
            _clear_lock_timeout()
            raise ExecutionRejected("The goal is not in this gym.", code="goal_not_found") from exc
        try:
            step = WorkflowStep.objects.get(gym_id=gym.id, id=step_id, goal_id=goal.id)
        except WorkflowStep.DoesNotExist as exc:
            _clear_lock_timeout()
            raise ExecutionRejected(
                "The workflow step is not in this gym.",
                code="step_not_found",
            ) from exc
        if step.tool_name != tool_name or step.attempt_number != attempt_number:
            _clear_lock_timeout()
            raise ExecutionRejected(
                "The workflow step does not name this tool.",
                code="step_tool",
            )
        try:
            claim = _insert_claim(
                gym=gym,
                goal=goal,
                step=step,
                tool_name=tool_name,
                attempt_number=attempt_number,
                identity=identity,
                payload_digest=digest,
                external_side_effects=(),
            )
        except TimeoutError:
            _clear_lock_timeout()
            return ExecutionResult("in_progress", goal.version, None, identity)
        except FileExistsError:
            _clear_lock_timeout()
            existing = ToolExecution.objects.get(gym_id=gym.id, execution_identity=identity)
            return ExecutionResult(
                "existing",
                goal.version,
                existing.effect_reference,
                identity,
            )
        _clear_lock_timeout()
        claim.effect_reference = reference
        claim.save(update_fields=["effect_reference", "updated_at"])
        next_version = goal.version + 1
        updated = Goal.objects.filter(pk=goal.pk, version=goal.version).update(
            version=next_version,
            updated_at=timezone.now(),
        )
        if updated != 1:
            raise ExecutionRejected(
                "The goal version changed inside the lock.",
                code="version_conflict",
            )
        return ExecutionResult("committed", next_version, reference, identity)
