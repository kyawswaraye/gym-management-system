from __future__ import annotations

import os
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connections, transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.models import Goal, LogicalClaim, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.assistant.schedule_tools import (
    SCHEDULE_ROUTE,
    execute_registered_tool,
    schedule_finance_body,
    schedule_tool_definitions,
)
from apps.classes.exceptions import ScheduleConflict
from apps.classes.models import ClassSchedule
from apps.classes.serializers import ClassScheduleWriteSerializer
from apps.classes.services import create_class_schedule, create_class_type
from apps.classes.views import schedule_command_body
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymSettings,
    IdempotencyRecord,
    StaffProfile,
)
from apps.users.models import User
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


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code,
        defaults={"name": code, "description": code},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _goal(gym: Gym, actor: GymMembership) -> Goal:
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Class schedule",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return goal


def _step(goal: Goal) -> WorkflowStep:
    found = WorkflowStep.objects.filter(goal=goal, tool_name="create_class_schedule").first()
    if found is not None:
        return found
    with transaction.atomic():
        set_gym_context(gym_id=goal.gym_id)
        return WorkflowStep.objects.create(
            gym_id=goal.gym_id,
            workflow=goal.workflow,
            goal=goal,
            position=WorkflowStep.objects.filter(goal=goal).count() + 1,
            tool_name="create_class_schedule",
        )


def _request(
    gym: Gym,
    actor: GymMembership,
    *,
    codes: frozenset[str] | None = None,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    if codes is None:
        codes = frozenset(schedule_tool_definitions()[0].permissions)
    request.permission_codes = codes
    request.request_id = "req_schedule_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str] | None = None,
    confirmed: bool = True,
):
    return execute_registered_tool(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name="create_class_schedule",
        goal=goal,
        step_id=_step(goal).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def _profile(gym: Gym, membership: GymMembership, *, code: str, name: str) -> StaffProfile:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return StaffProfile.objects.create(
            gym=gym,
            gym_membership=membership,
            staff_code=code,
            display_name=name,
            phone="",
            joined_date=date.today(),
            status=StaffProfile.Status.ACTIVE,
            employment_title="",
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )


def _schedule_payload(
    class_type_id: object,
    trainer_id: object,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "class_type_id": str(class_type_id),
        "trainer_staff_profile_id": str(trainer_id),
        "weekdays": [0],
        "local_start_time": "18:00",
        "local_end_time": "18:45",
        "effective_from": "2026-11-16",
        "effective_until": "2026-11-16",
        "location": "Studio",
        "location_is_exclusive": True,
        "capacity": 8,
    }
    payload.update(overrides)
    return payload


def _completed(gym: Gym) -> int:
    return IdempotencyRecord.objects.filter(
        gym=gym,
        state=IdempotencyRecord.State.COMPLETED,
    ).count()


@override_settings(**jwt_settings())
def test_permission_feature_and_gym_stop_before_a_finance_claim() -> None:
    client, token = platform_token("sch1-platform@example.test")
    payload = provision(client, token, code="SCH1", owner_email="sch1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="sch1-trainer@example.test", role_key="TRAINER")
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
    owner.is_owner = False
    with pytest.raises(PermissionDenied):
        _run(gym, owner, goal, command, 1, codes=frozenset())
    owner.is_owner = True
    assert _completed(gym) == 0
    assert ClassSchedule.objects.filter(gym=gym).count() == 0

    bare_user = User.objects.create_user("sch-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="SCHB", name="Bare", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=bare.id)
        bare_actor = GymMembership.objects.create(
            gym=bare,
            user=bare_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=bare,
            created_by_membership_id=bare_actor.id,
            updated_by_membership_id=bare_actor.id,
        )
    bare_actor.user = bare_user
    bare_goal = _goal(bare, bare_actor)
    with pytest.raises(PermissionDenied):
        _run(bare, bare_actor, bare_goal, command, 1)
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0

    with pytest.raises(Exception) as mismatched:
        _run(gym, owner, goal, {**command, "gym_id": str(uuid4())}, 1)
    assert getattr(mismatched.value, "code", "") == "gym_mismatch"
    with pytest.raises(Exception) as unconfirmed:
        _run(gym, owner, goal, {**command, "confirmed": True}, 1, confirmed=False)
    assert getattr(unconfirmed.value, "code", "") == "confirmation_required"
    assert _completed(gym) == 0
    assert ClassSchedule.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_replay_conflict_hash_trainer_scope_and_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("sch2-platform@example.test")
    payload = provision(client, token, code="SCH2", owner_email="sch2-owner@example.test")
    other_payload = provision(client, token, code="SCH3", owner_email="sch3-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    _enable(other, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="sch2-trainer@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym, email="sch2-other@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    other_profile = _profile(gym, other_trainer, code="BEA", name="Bea")
    foreign_trainer = add_staff(other, email="sch3-trainer@example.test", role_key="TRAINER")
    foreign_profile = _profile(other, foreign_trainer, code="CY", name="Cy")
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
    foreign_type = create_class_type(
        gym=other,
        actor=other_owner,
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
    with pytest.raises(Http404):
        _run(gym, trainer, goal, _schedule_payload(class_type.id, other_profile.id), 1)
    with pytest.raises(Http404):
        _run(gym, owner, goal, _schedule_payload(foreign_type.id, profile.id), 1)
    with pytest.raises(Http404):
        _run(gym, owner, goal, _schedule_payload(class_type.id, foreign_profile.id), 1)
    assert ClassSchedule.objects.filter(gym=gym).count() == 0
    assert _completed(gym) == 0

    created = _run(gym, owner, goal, command, 1)
    created_again = _run(gym, owner, goal, command, 1)
    goal.refresh_from_db()
    row = ClassSchedule.objects.get(id=created.effect_reference)
    serializer = ClassScheduleWriteSerializer(data=command)
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    expected_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=SCHEDULE_ROUTE,
        body=view_body,
    )
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created_again.effect_reference == created.effect_reference
    assert len(created.execution_identity or "") == 64
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == SCHEDULE_ROUTE
    assert stored.request_hash == expected_hash
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=SCHEDULE_ROUTE,
        body=schedule_finance_body(command),
    )
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert row.gym_id == gym.id
    assert goal.version == 2
    stored_claim = LogicalClaim.objects.get(
        gym=gym,
        execution_identity=created.execution_identity,
    )
    assert stored_claim.state == LogicalClaim.State.COMPLETED
    assert stored_claim.resource_id == created.effect_reference

    conflict = _schedule_payload(class_type.id, profile.id, capacity=9)
    with pytest.raises(ScheduleConflict):
        _run(gym, owner, goal, conflict, goal.version)
    goal.refresh_from_db()
    assert goal.version == 2
    with pytest.raises(ScheduleConflict):
        _run(gym, owner, goal, conflict, goal.version)
    goal.refresh_from_db()
    assert goal.version == 2
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    conflict_key = _run_identity(gym, owner, goal, conflict)
    released = IdempotencyRecord.objects.get(gym=gym, idempotency_key=conflict_key)
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()

    calls = {"count": 0}
    real = create_class_schedule

    def counting(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", counting)
    stale = _run(
        gym,
        owner,
        goal,
        _schedule_payload(class_type.id, profile.id, location="Room B", capacity=4),
        1,
    )
    assert stale.outcome == "stale_version"
    assert stale.version == 2
    assert calls["count"] == 0
    assert ClassSchedule.objects.filter(gym=gym).count() == 1


def _run_identity(gym: Gym, actor: GymMembership, goal: Goal, payload: dict[str, object]) -> str:
    from apps.assistant.identity import execution_identity

    return execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=_step(goal).id,
        tool_name="create_class_schedule",
        attempt_number=_step(goal).attempt_number,
        payload=schedule_finance_body(payload),
    )


@override_settings(**jwt_settings())
def test_bookkeeping_pending_retries_without_a_second_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("sch4-platform@example.test")
    payload = provision(client, token, code="SCH4", owner_email="sch4-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="sch4-trainer@example.test", role_key="TRAINER")
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
    real = create_class_schedule

    def move_version(**kwargs: object) -> ClassSchedule:
        row = real(**kwargs)
        Goal.objects.filter(pk=goal.id, gym_id=gym.id).update(version=2)
        return row

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", move_version)
    pending = _run(gym, owner, goal, command, 1)
    monkeypatch.undo()
    stale = _run(gym, owner, goal, command, 1)
    booked = _run(gym, owner, goal, command, 2)
    goal.refresh_from_db()
    assert pending.outcome == "committed"
    assert pending.version == 3
    assert stale.outcome == "existing"
    assert booked.outcome == "existing"
    assert booked.effect_reference == pending.effect_reference
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert _completed(gym) == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 1
    assert goal.version == 3


def _thread_result(
    gym_id: object,
    actor_id: object,
    goal_id: object,
    payload: dict[str, object],
    barrier: Barrier,
) -> tuple[str, str]:
    connections.close_all()
    try:
        barrier.wait()
        gym = Gym.objects.get(id=gym_id)
        actor = GymMembership.objects.select_related("user").get(id=actor_id)
        goal = Goal.objects.get(id=goal_id)
        try:
            result = _run(gym, actor, goal, payload, 1)
        except Exception as exc:
            return ("error", type(exc).__name__)
        return ("ok", result.outcome)
    finally:
        connections.close_all()


@override_settings(**jwt_settings())
def test_same_actor_concurrency_creates_one_schedule() -> None:
    from concurrent.futures import ThreadPoolExecutor

    client, token = platform_token("sch5-platform@example.test")
    payload = provision(client, token, code="SCH5", owner_email="sch5-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal)
    trainer = add_staff(gym, email="sch5-trainer@example.test", role_key="TRAINER")
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
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, owner.id, goal.id, command, barrier)
            for _index in range(2)
        ]
        results = sorted(future.result() for future in futures)
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert _completed(gym) == 1
    assert [item[0] for item in results] == ["ok", "ok"]


@override_settings(**jwt_settings())
def test_different_actor_concurrency_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    client, token = platform_token("sch6-platform@example.test")
    payload = provision(client, token, code="SCH6", owner_email="sch6-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="sch6-manager@example.test", role_key="MANAGER")
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal)
    trainer = add_staff(gym, email="sch6-trainer@example.test", role_key="TRAINER")
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
    calls = {"count": 0}
    real = create_class_schedule

    def counting(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.create_class_schedule", counting)
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, actor_id, goal.id, command, barrier)
            for actor_id in (owner.id, manager.id)
        ]
        results = sorted(future.result() for future in futures)
    schedules = ClassSchedule.objects.filter(gym=gym).count()
    outcomes = sorted(item[1] for item in results)
    detail = {
        "results": results,
        "schedules": schedules,
        "service_calls": calls["count"],
        "tool_executions": ToolExecution.objects.filter(goal=goal).count(),
    }
    assert [item[0] for item in results] == ["ok", "ok"], detail
    assert "committed" in outcomes, detail
    assert set(outcomes) <= {"committed", "existing", "in_progress"}, detail
    assert schedules == 1, detail
    assert calls["count"] == 1, detail
    assert _completed(gym) == 1, detail
    assert detail["tool_executions"] == 1, detail
