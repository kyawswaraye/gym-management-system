"""PostgreSQL races for the actor-independent logical claim."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone

from apps.assistant.execution import ExecutionRejected, commit_bookkeeping
from apps.assistant.logical import (
    acquire_claim,
    complete_owned_claim,
    release_owned_claim,
    require_current_owner,
)
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.classes.exceptions import OccurrenceStateConflict, ScheduleConflict
from apps.classes.models import ClassSchedule
from apps.classes.services import create_class_schedule, create_class_type
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
from tests.security.test_assistant_schedule import (
    _enable,
    _goal,
    _profile,
    _run,
    _schedule_payload,
)
from tests.security.test_tenant_gym_settings import (
    add_staff,
    jwt_settings,
    platform_token,
    provision,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _world(code: str):
    client, token = platform_token(f"{code}-platform@example.test")
    payload = provision(client, token, code=code, owner_email=f"{code}-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email=f"{code}-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = create_class_type(
        gym=gym,
        actor=owner,
        code="YOGA",
        name="Yoga",
        description="",
        default_duration_minutes=45,
        default_capacity=8,
        request_id="req_type",
        ip_address=None,
        user_agent="",
    )
    command = _schedule_payload(class_type.id, profile.id)
    return gym, owner, goal, command


@override_settings(**jwt_settings())
def test_reclaim_after_lease_expiry_is_the_only_ownership_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, goal, command = _world("FN01")
    calls = {"count": 0}
    real = create_class_schedule

    def fail_once(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        raise RuntimeError("stopped before the schedule")

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", fail_once)
    with pytest.raises(RuntimeError, match="stopped before the schedule"):
        _run(gym, owner, goal, command, 1)
    claim = LogicalClaim.objects.get(gym=gym)
    assert claim.state == LogicalClaim.State.IN_PROGRESS
    assert claim.fence_token == 1
    assert claim.locked_until is not None
    assert claim.locked_until <= timezone.now()
    assert ClassSchedule.objects.filter(gym=gym).count() == 0

    stolen = acquire_claim(
        gym=gym,
        goal=goal,
        step=goal.steps.get(tool_name="create_class_schedule"),
        execution_identity=claim.execution_identity,
    )
    claim.refresh_from_db()
    assert stolen.kind == "owned"
    assert stolen.fence_token == 2
    assert claim.fence_token == 2
    blocked = _run(gym, owner, goal, command, 1)
    assert blocked.outcome == "in_progress"
    assert calls["count"] == 1

    assert claim.locked_until is not None
    owned_until = claim.locked_until
    with pytest.raises(ExecutionRejected, match="fence") as stale:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            complete_owned_claim(
                gym_id=gym.id,
                claim_id=claim.id,
                fence_token=1,
                locked_until=owned_until,
                resource_id="not-the-owner",
            )
    assert stale.value.code == "stale_owner"
    release_owned_claim(
        gym_id=gym.id,
        claim_id=claim.id,
        fence_token=1,
        locked_until=owned_until,
    )
    claim.refresh_from_db()
    assert claim.fence_token == 2
    assert claim.state == LogicalClaim.State.IN_PROGRESS

    LogicalClaim.objects.filter(pk=claim.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", real)
    created = _run(gym, owner, goal, command, 1)
    claim.refresh_from_db()
    assert created.outcome == "committed"
    assert calls["count"] == 1
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert claim.state == LogicalClaim.State.COMPLETED
    assert claim.fence_token == 3


@override_settings(**jwt_settings())
def test_owner_who_locks_first_after_lease_expiry_keeps_the_fence() -> None:
    gym, _owner, goal, _command = _world("FN02")
    identity = "a" * 64
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = LogicalClaim.objects.create(
            gym=gym,
            goal=goal,
            step=goal.steps.get(),
            execution_identity=identity,
            state=LogicalClaim.State.IN_PROGRESS,
            fence_token=1,
            locked_until=timezone.now() - timedelta(seconds=5),
            expires_at=timezone.now() + timedelta(hours=1),
            resource_id="",
        )
    row.refresh_from_db()
    assert row.locked_until is not None
    owned_until = row.locked_until
    started = Event()
    pid_ready = Event()
    pids: dict[str, int] = {}

    def owner_thread() -> None:
        connections.close_all()
        try:
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                require_current_owner(
                    gym_id=gym.id,
                    claim_id=row.id,
                    fence_token=1,
                    locked_until=owned_until,
                )
                started.set()
                assert pid_ready.wait(5)
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids["reclaimer"]])
                        fetched = cursor.fetchone()
                        blockers = () if fetched is None else fetched[0]
                    if blockers:
                        blocked = True
                        break
                    time.sleep(0.05)
                assert blocked, "reclaim did not wait on the locked claim"
                complete_owned_claim(
                    gym_id=gym.id,
                    claim_id=row.id,
                    fence_token=1,
                    locked_until=owned_until,
                    resource_id="held-resource",
                )
        finally:
            connections.close_all()

    def reclaimer_thread() -> str:
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                fetched = cursor.fetchone()
                assert fetched is not None
                pids["reclaimer"] = int(fetched[0])
            pid_ready.set()
            assert started.wait(5)
            decision = acquire_claim(
                gym=gym,
                goal=goal,
                step=goal.steps.get(),
                execution_identity=identity,
            )
            return decision.kind
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(owner_thread)
        reclaim_future = pool.submit(reclaimer_thread)
        owner_future.result(timeout=15)
        kind = reclaim_future.result(timeout=15)
    row.refresh_from_db()
    assert kind == "completed"
    assert row.state == LogicalClaim.State.COMPLETED
    assert row.fence_token == 1
    assert row.resource_id == "held-resource"


@override_settings(**jwt_settings())
def test_crash_during_the_service_rolls_back_and_retry_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, goal, command = _world("FN03")
    real = create_class_schedule
    calls = {"count": 0}

    def crash(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        real(**kwargs)
        raise RuntimeError("crash during the schedule")

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", crash)
    with pytest.raises(RuntimeError, match="crash during"):
        _run(gym, owner, goal, command, 1)
    assert ClassSchedule.objects.filter(gym=gym).count() == 0
    claim = LogicalClaim.objects.get(gym=gym)
    assert claim.state == LogicalClaim.State.IN_PROGRESS
    assert claim.locked_until is not None
    assert claim.locked_until <= timezone.now()

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", real)
    created = _run(gym, owner, goal, command, 1)
    assert created.outcome == "committed"
    assert calls["count"] == 1
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    claim.refresh_from_db()
    assert claim.state == LogicalClaim.State.COMPLETED


@override_settings(**jwt_settings())
def test_bookkeeping_uses_the_locked_version_and_does_not_repeat_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, goal, command = _world("FN04")
    calls = {"count": 0}
    real = create_class_schedule

    def counting(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", counting)

    def fail_bookkeeping(**_kwargs: object) -> None:
        raise RuntimeError("bookkeeping failed")

    monkeypatch.setattr("apps.assistant.executor.commit_bookkeeping", fail_bookkeeping)
    with pytest.raises(RuntimeError, match="bookkeeping failed"):
        _run(gym, owner, goal, command, 1)
    assert calls["count"] == 1
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    claim = LogicalClaim.objects.get(gym=gym)
    assert claim.state == LogicalClaim.State.COMPLETED

    schedule_step = goal.steps.get(tool_name="create_class_schedule")
    schedule_step.tool_name = "renamed_tool"
    schedule_step.save(update_fields=["tool_name", "updated_at"])
    with pytest.raises(ExecutionRejected) as mismatch:
        commit_bookkeeping(
            gym=gym,
            goal_id=goal.id,
            step_id=schedule_step.id,
            tool_name="create_class_schedule",
            attempt_number=schedule_step.attempt_number,
            payload=command,
            reference=claim.resource_id,
        )
    assert mismatch.value.code == "step_tool"
    assert calls["count"] == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    schedule_step.tool_name = "create_class_schedule"
    schedule_step.save(update_fields=["tool_name", "updated_at"])

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Goal.objects.filter(pk=goal.pk).update(version=6)
    monkeypatch.setattr("apps.assistant.executor.commit_bookkeeping", commit_bookkeeping)
    recovered = _run(gym, owner, goal, command, 1)
    goal.refresh_from_db()
    assert recovered.outcome == "committed"
    assert recovered.version == 7
    assert calls["count"] == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 1
    assert ClassSchedule.objects.filter(gym=gym).count() == 1

    monkeypatch.setattr("apps.assistant.executor.commit_bookkeeping", fail_bookkeeping)
    gym_b, owner_b, goal_b, command_b = _world("FN05")
    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", counting)
    with pytest.raises(RuntimeError, match="bookkeeping failed"):
        _run(gym_b, owner_b, goal_b, command_b, 1)
    calls["count"] = 0
    monkeypatch.setattr("apps.assistant.executor.commit_bookkeeping", commit_bookkeeping)
    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", counting)
    barrier = Barrier(2, timeout=30)

    def recover(actor_id: object) -> str:
        connections.close_all()
        try:
            barrier.wait()
            actor = GymMembership.objects.select_related("user").get(id=actor_id)
            result = _run(gym_b, actor, goal_b, command_b, 1)
            return result.outcome
        finally:
            connections.close_all()

    manager = add_staff(gym_b, email="fn05-manager@example.test", role_key="MANAGER")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result()
            for future in (
                pool.submit(recover, owner_b.id),
                pool.submit(recover, manager.id),
            )
        )
    assert calls["count"] == 0
    assert outcomes == ["committed", "existing"]
    assert ToolExecution.objects.filter(goal=goal_b).count() == 1
    assert ClassSchedule.objects.filter(gym=gym_b).count() == 1


@override_settings(**jwt_settings())
def test_cacheable_failure_is_replayed_and_a_conflict_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, goal, command = _world("FN06")
    calls = {"count": 0}

    def cacheable(**_kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        raise OccurrenceStateConflict()

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", cacheable)
    with pytest.raises(OccurrenceStateConflict):
        _run(gym, owner, goal, command, 1)
    with pytest.raises(ExecutionRejected) as replayed:
        _run(gym, owner, goal, command, 1)
    claim = LogicalClaim.objects.get(gym=gym)
    finance = IdempotencyRecord.objects.get(gym=gym)
    assert replayed.value.code == "finance_failed"
    assert calls["count"] == 1
    assert claim.state == LogicalClaim.State.FAILED
    assert finance.state == IdempotencyRecord.State.FAILED
    assert ClassSchedule.objects.filter(gym=gym).count() == 0

    gym, owner, goal, command = _world("FN07")
    calls["count"] = 0
    real = create_class_schedule

    def conflict(**_kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        raise ScheduleConflict()

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", conflict)
    with pytest.raises(ScheduleConflict):
        _run(gym, owner, goal, command, 1)
    released = LogicalClaim.objects.get(gym=gym)
    assert released.state == LogicalClaim.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    finance = IdempotencyRecord.objects.get(gym=gym)
    assert finance.state == IdempotencyRecord.State.IN_PROGRESS
    assert finance.locked_until is not None
    assert finance.locked_until <= timezone.now()
    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", real)
    created = _run(gym, owner, goal, command, 1)
    assert created.outcome == "committed"
    assert calls["count"] == 1
    assert ClassSchedule.objects.filter(gym=gym).count() == 1


@override_settings(**jwt_settings())
def test_expired_in_progress_claim_is_reclaimed_and_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, owner, goal, command = _world("FN08")
    calls = {"count": 0}

    def stop(**_kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        raise RuntimeError("before the schedule")

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", stop)
    with pytest.raises(RuntimeError, match="before the schedule"):
        _run(gym, owner, goal, command, 1)
    claim = LogicalClaim.objects.get(gym=gym)
    assert claim.state == LogicalClaim.State.IN_PROGRESS
    assert claim.fence_token == 1
    assert claim.locked_until is not None
    assert claim.locked_until <= timezone.now()
    LogicalClaim.objects.filter(pk=claim.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(RuntimeError, match="before the schedule"):
        _run(gym, owner, goal, command, 1)
    claim.refresh_from_db()
    assert calls["count"] == 2
    assert claim.fence_token == 2
    assert claim.state == LogicalClaim.State.IN_PROGRESS
    assert ClassSchedule.objects.filter(gym=gym).count() == 0
    with pytest.raises(DatabaseError, match="assistant_guard"):
        claim.delete()


@override_settings(**jwt_settings())
def test_runtime_role_cannot_read_another_gyms_logical_claim() -> None:
    gym, _owner, goal, _command = _world("FN09")
    other_client, other_token = platform_token("fence10-platform@example.test")
    other_payload = provision(
        other_client,
        other_token,
        code="FEN10",
        owner_email="fence10-owner@example.test",
    )
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        LogicalClaim.objects.create(
            gym=gym,
            goal=goal,
            step=goal.steps.get(),
            execution_identity="c" * 64,
            state=LogicalClaim.State.IN_PROGRESS,
            fence_token=1,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=1),
            resource_id="",
        )
    with transaction.atomic(), connection.cursor() as cursor:
        set_gym_context(gym_id=gym.id)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_logical_claim")
        assert cursor.fetchone() == (1,)
    with transaction.atomic(), connection.cursor() as cursor:
        set_gym_context(gym_id=other.id)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_logical_claim")
        assert cursor.fetchone() == (0,)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_logical_claim")
        assert cursor.fetchone() == (0,)
