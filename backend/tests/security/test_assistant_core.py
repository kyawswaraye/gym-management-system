from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier, Event, Lock, Thread
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.assistant.execution import ExecutionRejected, ExecutionResult, commit_database_effect
from apps.assistant.models import Goal, Message, ToolExecution
from apps.assistant.records import open_staff_goal
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User
from apps.users.services import create_staff_session

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _jwt_settings() -> dict[str, object]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return {
        "REFRESH_TOKEN_PEPPER": "assistant-core-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"assistant-core": public_pem},
        "JWT_KEY_ID": "assistant-core",
    }


JWT_SETTINGS = _jwt_settings()


def _staff(code: str) -> tuple[Gym, GymMembership]:
    user = User.objects.create_user(f"{code}@example.test", "correct horse battery staple")
    gym = Gym.objects.create(code=code, name=code, status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    return gym, membership


def _goal(code: str) -> tuple[Gym, GymMembership, Goal]:
    gym, actor = _staff(code)
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Bookkeeping",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return gym, actor, goal


def test_rollback_leaves_no_claim_or_version_change() -> None:
    gym, _actor, goal = _goal("asst-rollback")
    step = goal.steps.get()

    def effect() -> str:
        Message.objects.create(
            gym=gym,
            conversation=goal.conversation,
            goal=goal,
            role=Message.Role.SYSTEM,
            body="rolled back",
        )
        return "rolled-back"

    with pytest.raises(RuntimeError), transaction.atomic():
        commit_database_effect(
            gym=gym,
            goal_id=goal.id,
            expected_version=1,
            step_id=step.id,
            tool_name="create_member",
            attempt_number=1,
            payload={"name": "Maung"},
            effect=effect,
        )
        raise RuntimeError("rollback")

    goal.refresh_from_db()
    assert goal.version == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    assert Message.objects.filter(goal=goal).count() == 0


def test_commit_writes_claim_effect_and_version_together() -> None:
    gym, _actor, goal = _goal("asst-commit")
    step = goal.steps.get()
    result = commit_database_effect(
        gym=gym,
        goal_id=goal.id,
        expected_version=1,
        step_id=step.id,
        tool_name="create_member",
        attempt_number=1,
        payload={"name": "Maung"},
        effect=lambda: "member-one",
        external_side_effects=("email",),
    )
    goal.refresh_from_db()
    claim = ToolExecution.objects.get(goal=goal)
    assert result.outcome == "committed"
    assert result.version == 2
    assert goal.version == 2
    assert claim.effect_reference == "member-one"
    assert claim.covers_external_side_effects is False
    assert claim.external_side_effects == ["email"]


def test_stale_goal_version_does_not_insert_a_claim() -> None:
    gym, _actor, goal = _goal("asst-stale")
    step = goal.steps.get()
    result = commit_database_effect(
        gym=gym,
        goal_id=goal.id,
        expected_version=0,
        step_id=step.id,
        tool_name="create_member",
        attempt_number=1,
        payload={"name": "Maung"},
        effect=lambda: "should-not-run",
    )
    goal.refresh_from_db()
    assert result.outcome == "stale_version"
    assert result.version == 1
    assert goal.version == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_unique_identity_conflict_does_not_call_the_effect_again() -> None:
    gym, _actor, goal = _goal("asst-unique")
    step = goal.steps.get()
    payload = {"name": "Maung"}
    first = commit_database_effect(
        gym=gym,
        goal_id=goal.id,
        expected_version=1,
        step_id=step.id,
        tool_name="create_member",
        attempt_number=1,
        payload=payload,
        effect=lambda: "member-one",
    )
    calls = {"count": 0}

    def effect() -> str:
        calls["count"] += 1
        return "member-two"

    second = commit_database_effect(
        gym=gym,
        goal_id=goal.id,
        expected_version=first.version or 0,
        step_id=step.id,
        tool_name="create_member",
        attempt_number=1,
        payload=payload,
        effect=effect,
    )
    assert first.outcome == "committed"
    assert second.outcome == "existing"
    assert second.effect_reference == "member-one"
    assert calls["count"] == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 1


def test_lock_timeout_is_transient_and_writes_nothing() -> None:
    gym, _actor, goal = _goal("asst-lock")
    step = goal.steps.get()
    started = Event()
    release = Event()

    def hold() -> None:
        connections.close_all()
        try:
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                Goal.objects.select_for_update().get(id=goal.id)
                started.set()
                assert release.wait(5)
        finally:
            connections.close_all()

    worker = Thread(target=hold)
    worker.start()
    assert started.wait(5)
    result = commit_database_effect(
        gym=gym,
        goal_id=goal.id,
        expected_version=1,
        step_id=step.id,
        tool_name="create_member",
        attempt_number=1,
        payload={"name": "Maung"},
        effect=lambda: "should-not-run",
        lock_timeout=200,
    )
    release.set()
    worker.join(5)
    assert result.outcome == "in_progress"
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_external_coverage_flag_is_rejected() -> None:
    gym, _actor, goal = _goal("asst-external")
    step = goal.steps.get()
    with pytest.raises(ExecutionRejected):
        commit_database_effect(
            gym=gym,
            goal_id=goal.id,
            expected_version=1,
            step_id=step.id,
            tool_name="create_member",
            attempt_number=1,
            payload={"name": "Maung"},
            effect=lambda: "member-one",
            covers_external_side_effects=True,
        )
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_empty_effect_reference_cannot_commit() -> None:
    gym, actor = _staff("asst-empty")
    _conversation, goal, step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Empty effect",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    with pytest.raises(IntegrityError, match="assistant_guard"), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ToolExecution.objects.create(
            gym=gym,
            goal=goal,
            step=step,
            tool_name="create_member",
            attempt_number=1,
            execution_identity="a" * 64,
            payload_hash="b" * 64,
            effect_reference="",
        )


def test_cross_gym_message_foreign_key_is_rejected() -> None:
    gym_a, _actor_a, goal_a = _goal("asst-fk-a")
    _gym_b, _actor_b, goal_b = _goal("asst-fk-b")
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        Message.objects.create(
            gym=gym_a,
            conversation=goal_b.conversation,
            role=Message.Role.SYSTEM,
            body="cross gym",
        )
    assert Message.objects.filter(goal=goal_a).count() == 0


def test_runtime_role_sees_only_the_current_gym() -> None:
    gym_a, actor_a = _staff("asst-rls-a")
    gym_b, actor_b = _staff("asst-rls-b")
    open_staff_goal(
        gym=gym_a,
        actor=actor_a,
        intent="A",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    open_staff_goal(
        gym=gym_b,
        actor=actor_b,
        intent="B",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    with transaction.atomic(), connection.cursor() as cursor:
        set_gym_context(gym_id=gym_a.id)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_goal")
        assert cursor.fetchone()[0] == 1
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_goal")
        assert cursor.fetchone()[0] == 0


def test_assistant_records_cannot_be_deleted_or_move_gym() -> None:
    _gym, _actor, goal = _goal("asst-guard")
    step = goal.steps.get()
    with pytest.raises(DatabaseError, match="assistant_guard"):
        step.delete()
    other = uuid4()
    with pytest.raises(DatabaseError, match="assistant_guard"), transaction.atomic():
        Goal.objects.filter(pk=goal.pk).update(gym_id=other)


def test_goal_version_cannot_decrease() -> None:
    _gym, _actor, goal = _goal("asst-version")
    with pytest.raises(DatabaseError, match="assistant_guard"), transaction.atomic():
        Goal.objects.filter(pk=goal.pk).update(version=0)


def test_effect_exception_rolls_the_claim_back() -> None:
    gym, _actor, goal = _goal("asst-effect")
    step = goal.steps.get()

    def effect() -> str:
        Message.objects.create(
            gym=gym,
            conversation=goal.conversation,
            goal=goal,
            role=Message.Role.SYSTEM,
            body="not committed",
        )
        raise RuntimeError("effect failed")

    with pytest.raises(RuntimeError, match="effect failed"):
        commit_database_effect(
            gym=gym,
            goal_id=goal.id,
            expected_version=1,
            step_id=step.id,
            tool_name="create_member",
            attempt_number=1,
            payload={"name": "Maung"},
            effect=effect,
        )
    goal.refresh_from_db()
    assert goal.version == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    assert Message.objects.filter(goal=goal).count() == 0


def test_two_connections_commit_one_database_effect() -> None:
    gym, _actor, goal = _goal("asst-race")
    step = goal.steps.get()
    barrier = Barrier(2, timeout=10)
    calls = {"count": 0}
    call_lock = Lock()

    def effect() -> str:
        with call_lock:
            calls["count"] += 1
        return "member-one"

    def run() -> ExecutionResult:
        connections.close_all()
        try:
            barrier.wait()
            return commit_database_effect(
                gym=gym,
                goal_id=goal.id,
                expected_version=1,
                step_id=step.id,
                tool_name="create_member",
                attempt_number=1,
                payload={"name": "Maung"},
                effect=effect,
                lock_timeout=5000,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run), pool.submit(run)]
        results = [future.result() for future in futures]
    outcomes = sorted(result.outcome for result in results)
    assert outcomes == ["committed", "stale_version"]
    assert calls["count"] == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 1


def test_uncommitted_claim_is_invisible_on_another_connection() -> None:
    gym, _actor, goal = _goal("asst-iso")
    step = goal.steps.get()
    started = Event()
    release = Event()

    def hold() -> None:
        connections.close_all()
        try:
            with transaction.atomic():
                try:
                    set_gym_context(gym_id=gym.id)
                    ToolExecution.objects.create(
                        gym=gym,
                        goal=goal,
                        step=step,
                        tool_name="create_member",
                        attempt_number=1,
                        execution_identity="c" * 64,
                        payload_hash="d" * 64,
                        effect_reference="held",
                    )
                    started.set()
                    assert release.wait(5)
                finally:
                    transaction.set_rollback(True)
        finally:
            connections.close_all()

    worker = Thread(target=hold)
    worker.start()
    assert started.wait(5)
    with transaction.atomic(), connection.cursor() as cursor:
        set_gym_context(gym_id=gym.id)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM assistant_tool_execution")
        visible = cursor.fetchone()[0]
    release.set()
    worker.join(5)
    assert visible == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 0


@override_settings(**JWT_SETTINGS)
def test_get_reads_state_without_continuing_the_goal() -> None:
    gym, actor, goal = _goal("asst-read")
    pair = create_staff_session(user=actor.user, membership=actor)
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {pair.access_token}"}
    listed = client.get("/api/v1/assistant/conversations/", **headers)
    detail = client.get(f"/api/v1/assistant/goals/{goal.id}/", **headers)
    rejected = client.get(
        "/api/v1/assistant/conversations/?gym_id=" + str(gym.id),
        **headers,
    )
    keyed = client.get(
        f"/api/v1/assistant/goals/{goal.id}/",
        HTTP_IDEMPOTENCY_KEY="same-key",
        **headers,
    )
    missing = client.post(f"/api/v1/assistant/goals/{goal.id}/", **headers)
    goal.refresh_from_db()
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == str(goal.conversation_id)
    assert detail.status_code == 200
    assert detail.json()["data"]["version"] == 1
    assert detail.json()["data"]["executions"] == []
    assert rejected.status_code == 400
    assert keyed.status_code == 400
    assert missing.status_code == 405
    assert goal.version == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0


@override_settings(**JWT_SETTINGS, ASSISTANT_ENABLED="false", GEMINI_API_KEY="", GEMINI_MODEL="")
def test_status_get_reports_unavailable_and_health_stays_up() -> None:
    _gym, actor, _goal_row = _goal("asst-status")
    pair = create_staff_session(user=actor.user, membership=actor)
    client = APIClient()
    status = client.get(
        "/api/v1/assistant/status/",
        HTTP_AUTHORIZATION=f"Bearer {pair.access_token}",
    )
    health = client.get("/api/v1/health/")
    assert status.status_code == 200
    assert status.json()["data"]["available"] is False
    assert "missing_key" in status.json()["data"]["reasons"]
    assert health.status_code == 200
