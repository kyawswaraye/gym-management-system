"""Actor-independent claim for one assistant execution identity.

The lease is a retry delay. Ownership changes only when a reclaim commits a
new fence token. The business transaction locks the row and checks that token
before the service runs. Completion of the claim is in that same transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.assistant.execution import ExecutionRejected, _sqlstate
from apps.assistant.models import Goal, LogicalClaim, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym

_UNIQUE_VIOLATION = "23505"


@dataclass(frozen=True)
class ClaimDecision:
    """What the caller may do with one logical claim."""

    kind: str
    claim_id: UUID
    fence_token: int
    locked_until: datetime | None
    resource_id: str


def peek_claim(*, gym_id: UUID, execution_identity: str) -> ClaimDecision | None:
    """Read the claim without taking or extending its lease."""

    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        row = LogicalClaim.objects.filter(
            gym_id=gym_id,
            execution_identity=execution_identity,
        ).first()
        if row is None:
            return None
        return _decision(row, _classify(row, timezone.now()))


def acquire_claim(
    *,
    gym: Gym,
    goal: Goal,
    step: WorkflowStep,
    execution_identity: str,
) -> ClaimDecision:
    """Insert the claim or reclaim a finished lease.

    A completed or failed row is returned as-is. A live lease returns
    ``busy``. An in-progress row past ``expires_at`` is reclaimed when its
    lease is also over. The reclaim clock is read after the row lock.
    """

    with transaction.atomic(durable=True):
        set_gym_context(gym_id=gym.id)
        inserted: LogicalClaim | None
        try:
            with transaction.atomic():
                now = timezone.now()
                inserted = LogicalClaim.objects.create(
                    gym=gym,
                    goal=goal,
                    step=step,
                    execution_identity=execution_identity,
                    state=LogicalClaim.State.IN_PROGRESS,
                    fence_token=1,
                    locked_until=_lease_until(now),
                    expires_at=_ttl_until(now),
                    resource_id="",
                )
        except IntegrityError as exc:
            if _sqlstate(exc) != _UNIQUE_VIOLATION:
                raise
            inserted = None
        if inserted is not None:
            inserted.refresh_from_db()
            return _decision(inserted, "owned")
        existing = (
            LogicalClaim.objects.select_for_update()
            .filter(gym_id=gym.id, execution_identity=execution_identity)
            .first()
        )
        if existing is None:
            return ClaimDecision("busy", UUID(int=0), 0, None, "")
        now = timezone.now()
        kind = _classify(existing, now)
        if kind not in {"open", "expired"}:
            return _decision(existing, kind)
        if kind == "expired" and existing.locked_until is not None and existing.locked_until > now:
            return _decision(existing, "busy")
        updated = _transfer_lease(existing, now, renew_expiry=kind == "expired")
        if updated != 1:
            existing.refresh_from_db()
            kind = _classify(existing, timezone.now())
            if kind in {"open", "expired"}:
                kind = "busy"
            return _decision(existing, kind)
        existing.refresh_from_db()
        return _decision(existing, "owned")


def require_current_owner(
    *,
    gym_id: UUID,
    claim_id: UUID,
    fence_token: int,
    locked_until: datetime,
) -> None:
    """Lock the claim and reject a stale fence before the service runs.

    The lease clock is not part of this check. The row lock is what stops a
    concurrent reclaim until this transaction ends.
    """

    if not connection.in_atomic_block:
        raise ExecutionRejected(
            "The logical claim fence requires the business transaction.",
            code="stale_owner",
        )
    row = LogicalClaim.objects.select_for_update().filter(pk=claim_id, gym_id=gym_id).first()
    if row is None or not _owns(row, fence_token, locked_until):
        raise ExecutionRejected(
            "The logical claim fence is no longer owned by this attempt.",
            code="stale_owner",
        )


def complete_owned_claim(
    *,
    gym_id: UUID,
    claim_id: UUID,
    fence_token: int,
    locked_until: datetime,
    resource_id: str,
) -> None:
    """Mark the owned claim completed in the caller's open transaction."""

    if resource_id == "" or len(resource_id) > 64:
        raise ExecutionRejected(
            "The finance result has no committed record.",
            code="missing_effect_reference",
        )
    updated = _owned_update(
        gym_id=gym_id,
        claim_id=claim_id,
        fence_token=fence_token,
        locked_until=locked_until,
        values={
            "state": LogicalClaim.State.COMPLETED,
            "resource_id": resource_id,
            "locked_until": None,
            "updated_at": timezone.now(),
        },
    )
    if updated != 1:
        raise ExecutionRejected(
            "The logical claim fence is no longer owned by this attempt.",
            code="stale_owner",
        )


def fail_owned_claim(
    *,
    gym_id: UUID,
    claim_id: UUID,
    fence_token: int,
    locked_until: datetime,
) -> None:
    """Store a cacheable failure while this attempt still owns the fence."""

    def _apply() -> None:
        _owned_update(
            gym_id=gym_id,
            claim_id=claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
            values={
                "state": LogicalClaim.State.FAILED,
                "resource_id": "",
                "locked_until": None,
                "updated_at": timezone.now(),
            },
        )

    _in_gym(gym_id, _apply)


def release_owned_claim(
    *,
    gym_id: UUID,
    claim_id: UUID,
    fence_token: int,
    locked_until: datetime,
) -> None:
    """Drop this attempt's lease. A stale fence updates nothing."""

    def _apply() -> None:
        _owned_update(
            gym_id=gym_id,
            claim_id=claim_id,
            fence_token=fence_token,
            locked_until=locked_until,
            values={
                "locked_until": timezone.now() - timedelta(seconds=1),
                "updated_at": timezone.now(),
            },
        )

    _in_gym(gym_id, _apply)


def _in_gym(gym_id: UUID, operation: Callable[[], None]) -> None:
    if connection.in_atomic_block:
        set_gym_context(gym_id=gym_id)
        operation()
        return
    with transaction.atomic(durable=True):
        set_gym_context(gym_id=gym_id)
        operation()


def _owned_update(
    *,
    gym_id: UUID,
    claim_id: UUID,
    fence_token: int,
    locked_until: datetime,
    values: dict[str, object],
) -> int:
    if not connection.in_atomic_block:
        raise ExecutionRejected(
            "The logical claim fence requires the business transaction.",
            code="stale_owner",
        )
    return LogicalClaim.objects.filter(
        pk=claim_id,
        gym_id=gym_id,
        state=LogicalClaim.State.IN_PROGRESS,
        fence_token=fence_token,
        locked_until=locked_until,
    ).update(**values)


def _lease_until(now: datetime) -> datetime:
    return now + timedelta(seconds=settings.FINANCE_IDEMPOTENCY_CLAIM_SECONDS)


def _ttl_until(now: datetime) -> datetime:
    return now + timedelta(seconds=settings.FINANCE_IDEMPOTENCY_TTL_SECONDS)


def _transfer_lease(existing: LogicalClaim, now: datetime, *, renew_expiry: bool) -> int:
    """Move the fence after the row is locked. Both clocks use this ``now``."""

    values: dict[str, object] = {
        "fence_token": existing.fence_token + 1,
        "locked_until": _lease_until(now),
        "updated_at": now,
    }
    query = LogicalClaim.objects.filter(
        pk=existing.pk,
        gym_id=existing.gym_id,
        state=LogicalClaim.State.IN_PROGRESS,
        fence_token=existing.fence_token,
        locked_until=existing.locked_until,
    )
    if renew_expiry:
        values["expires_at"] = _ttl_until(now)
        query = query.filter(expires_at__lte=now)
        if existing.locked_until is None:
            query = query.filter(locked_until__isnull=True)
        else:
            query = query.filter(locked_until__lte=now)
    else:
        query = query.filter(expires_at__gt=now)
    return query.update(**values)


def _classify(row: LogicalClaim, now: datetime) -> str:
    if row.state == LogicalClaim.State.COMPLETED:
        return "completed"
    if row.state == LogicalClaim.State.FAILED:
        return "failed"
    if row.expires_at <= now:
        return "expired"
    if row.locked_until is not None and row.locked_until > now:
        return "busy"
    return "open"


def _owns(row: LogicalClaim, fence_token: int, locked_until: datetime) -> bool:
    return (
        row.state == LogicalClaim.State.IN_PROGRESS
        and row.fence_token == fence_token
        and row.locked_until == locked_until
    )


def _decision(row: LogicalClaim, kind: str) -> ClaimDecision:
    return ClaimDecision(
        kind=kind,
        claim_id=row.id,
        fence_token=row.fence_token,
        locked_until=row.locked_until,
        resource_id=row.resource_id,
    )
