"""PostgreSQL security coverage for schedule update and occurrence cancel."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connections, transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, LogicalClaim, ToolExecution, WorkflowStep
from apps.assistant.schedule_tools import (
    cancel_occurrence_command,
    execute_registered_tool,
    schedule_tool_definitions,
    update_schedule_command,
)
from apps.classes.booking_services import create_class_booking
from apps.classes.exceptions import OccurrenceStateConflict, ScheduleConflict
from apps.classes.models import ClassBooking, ClassOccurrence, ClassSchedule
from apps.classes.serializers import ClassOccurrenceCancelSerializer, ClassSchedulePatchSerializer
from apps.classes.services import (
    cancel_class_occurrence,
    create_class_schedule,
    create_class_type,
    update_class_schedule,
)
from apps.classes.views import schedule_command_body, schedule_route
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership, GymSettings, IdempotencyRecord
from apps.users.models import User
from tests.security.test_assistant_schedule import _enable, _goal, _profile
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

_UPDATE = "update_class_schedule"
_CANCEL = "cancel_class_occurrence"


def _step(goal: Goal, tool_name: str) -> WorkflowStep:
    found = WorkflowStep.objects.filter(goal=goal, tool_name=tool_name).first()
    if found is not None:
        return found
    with transaction.atomic():
        set_gym_context(gym_id=goal.gym_id)
        return WorkflowStep.objects.create(
            gym_id=goal.gym_id,
            workflow=goal.workflow,
            goal=goal,
            position=WorkflowStep.objects.filter(goal=goal).count() + 1,
            tool_name=tool_name,
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
    request.request_id = "req_schedule_command"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str] | None = None,
    confirmed: bool = True,
):
    return execute_registered_tool(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def _completed(gym: Gym) -> int:
    return IdempotencyRecord.objects.filter(
        gym=gym,
        state=IdempotencyRecord.State.COMPLETED,
    ).count()


def _schedule(
    gym: Gym,
    actor: GymMembership,
    class_type_id: UUID,
    trainer_id: UUID,
    *,
    weekdays: list[int] | None = None,
    local_start_time: time = time(18, 0),
    local_end_time: time = time(18, 45),
    effective_from: date = date(2026, 11, 16),
    effective_until: date = date(2026, 11, 16),
    location: str = "Studio",
    location_is_exclusive: bool = True,
    capacity: int = 8,
) -> ClassSchedule:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_class_schedule(
            gym=gym,
            actor=actor,
            class_type_id=class_type_id,
            trainer_staff_profile_id=trainer_id,
            weekdays=[0] if weekdays is None else weekdays,
            local_start_time=local_start_time,
            local_end_time=local_end_time,
            effective_from=effective_from,
            effective_until=effective_until,
            location=location,
            location_is_exclusive=location_is_exclusive,
            capacity=capacity,
            request_id="req_schedule_setup",
            ip_address=None,
            user_agent="",
        )


def _type(gym: Gym, actor: GymMembership, *, code: str = "YOGA"):
    return create_class_type(
        gym=gym,
        actor=actor,
        code=code,
        name="Yoga",
        description="",
        default_duration_minutes=45,
        default_capacity=8,
        request_id="req_type",
        ip_address=None,
        user_agent="",
    )


def _identity(goal: Goal, tool_name: str, payload: dict[str, object]) -> str:
    return execution_identity(
        gym_id=goal.gym_id,
        goal_id=goal.id,
        step_id=_step(goal, tool_name).id,
        tool_name=tool_name,
        attempt_number=_step(goal, tool_name).attempt_number,
        payload=payload,
    )


def _thread_result(
    gym_id: object,
    actor_id: object,
    goal_id: object,
    tool_name: str,
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
            result = _run(gym, actor, goal, tool_name, payload, 1)
        except Exception as exc:
            return ("error", type(exc).__name__)
        return ("ok", result.outcome)
    finally:
        connections.close_all()


@override_settings(**jwt_settings())
def test_permission_feature_gym_and_route_stop_before_finance() -> None:
    client, token = platform_token("upd1-platform@example.test")
    payload = provision(client, token, code="UPD1", owner_email="upd1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    update_payload = {"schedule_id": str(uuid4()), "location": "Hall"}
    cancel_payload = {"occurrence_id": str(uuid4()), "reason": "Rain"}
    for tool_name, command in ((_UPDATE, update_payload), (_CANCEL, cancel_payload)):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=frozenset())
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0

    bare_user = User.objects.create_user("upd1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="UPDB", name="Bare", status=Gym.Status.ACTIVE)
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
    for tool_name, command in ((_UPDATE, update_payload), (_CANCEL, cancel_payload)):
        with pytest.raises(PermissionDenied):
            _run(bare, bare_actor, bare_goal, tool_name, command, 1)
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0

    for tool_name, command in (
        (_UPDATE, {**update_payload, "gym_id": str(uuid4())}),
        (_CANCEL, {**cancel_payload, "gym_id": str(uuid4())}),
    ):
        with pytest.raises(Exception) as mismatched:
            _run(gym, owner, goal, tool_name, command, 1)
        assert getattr(mismatched.value, "code", "") == "gym_mismatch"
    for tool_name, command in (
        (_UPDATE, {**update_payload, "confirmed": True}),
        (_CANCEL, {**cancel_payload, "confirmed": True}),
    ):
        with pytest.raises(Exception) as unconfirmed:
            _run(gym, owner, goal, tool_name, command, 1, confirmed=False)
        assert getattr(unconfirmed.value, "code", "") == "confirmation_required"
    with pytest.raises(ExecutionRejected) as missing_schedule:
        _run(gym, owner, goal, _UPDATE, {"location": "Hall"}, 1)
    assert missing_schedule.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as bad_schedule:
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"schedule_id": "not-a-uuid", "location": "Hall"},
            1,
        )
    assert bad_schedule.value.code == "finance_route"
    with pytest.raises(ExecutionRejected) as bad_occurrence:
        _run(
            gym,
            owner,
            goal,
            _CANCEL,
            {"occurrence_id": "not-a-uuid", "reason": "Rain"},
            1,
        )
    assert bad_occurrence.value.code == "finance_route"
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert ClassSchedule.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_update_replay_hash_scope_conflict_and_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("upd2-platform@example.test")
    payload = provision(client, token, code="UPD2", owner_email="upd2-owner@example.test")
    other_payload = provision(client, token, code="UPD3", owner_email="upd3-owner@example.test")
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
    trainer = add_staff(gym, email="upd2-trainer@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym, email="upd2-other@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    other_profile = _profile(gym, other_trainer, code="BEA", name="Bea")
    foreign_trainer = add_staff(other, email="upd3-trainer@example.test", role_key="TRAINER")
    foreign_profile = _profile(other, foreign_trainer, code="CY", name="Cy")
    class_type = _type(gym, owner)
    foreign_type = _type(other, other_owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    other_schedule = _schedule(
        gym,
        owner,
        class_type.id,
        other_profile.id,
        location="Room",
        local_start_time=time(19, 0),
        local_end_time=time(19, 45),
    )
    foreign = _schedule(other, other_owner, foreign_type.id, foreign_profile.id, location="Away")
    command = {"schedule_id": str(schedule.id).upper(), "capacity": 5}
    with pytest.raises(Http404):
        _run(gym, trainer, goal, _UPDATE, {"schedule_id": str(other_schedule.id), "capacity": 4}, 1)
    with pytest.raises(Http404):
        _run(gym, owner, goal, _UPDATE, {"schedule_id": str(foreign.id), "capacity": 4}, 1)
    with pytest.raises(Http404):
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"schedule_id": str(schedule.id), "class_type_id": str(foreign_type.id)},
            1,
        )
    assert _completed(gym) == 0
    assert schedule.capacity == 8

    updated = _run(gym, owner, goal, _UPDATE, command, 1)
    updated_again = _run(gym, owner, goal, _UPDATE, command, updated.version)
    goal.refresh_from_db()
    schedule.refresh_from_db()
    prepared = update_schedule_command(command)
    serializer = ClassSchedulePatchSerializer(data={"capacity": 5})
    serializer.is_valid(raise_exception=True)
    view_body = schedule_command_body(dict(serializer.validated_data))
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=updated.execution_identity)
    expected_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="PATCH",
        route=schedule_route(schedule.id),
        body=view_body,
    )
    assert updated.outcome == "committed"
    assert updated.effect_reference == str(schedule.id)
    assert updated_again.outcome == "existing"
    assert updated_again.effect_reference == updated.effect_reference
    assert updated.execution_identity == _identity(goal, _UPDATE, prepared)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "PATCH"
    assert stored.route == schedule_route(schedule.id)
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == expected_hash
    assert schedule.capacity == 5
    assert ClassSchedule.objects.filter(gym=gym).count() == 2
    assert goal.version == 2
    claim = LogicalClaim.objects.get(gym=gym, execution_identity=updated.execution_identity)
    assert claim.state == LogicalClaim.State.COMPLETED
    assert claim.resource_id == updated.effect_reference

    conflict = {
        "schedule_id": str(other_schedule.id),
        "local_start_time": "18:00",
        "local_end_time": "18:45",
        "location": "Studio",
    }
    with pytest.raises(ScheduleConflict):
        _run(gym, owner, goal, _UPDATE, conflict, goal.version)
    goal.refresh_from_db()
    other_schedule.refresh_from_db()
    assert goal.version == 2
    assert other_schedule.location == "Room"
    conflict_key = _identity(goal, _UPDATE, update_schedule_command(conflict))
    released = IdempotencyRecord.objects.get(gym=gym, idempotency_key=conflict_key)
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()

    calls = {"count": 0}
    real = update_class_schedule

    def counting(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.update_class_schedule", counting)
    stale = _run(gym, owner, goal, _UPDATE, {"schedule_id": str(schedule.id), "capacity": 6}, 1)
    assert stale.outcome == "stale_version"
    assert stale.version == 2
    assert calls["count"] == 0
    schedule.refresh_from_db()
    assert schedule.capacity == 5


@override_settings(**jwt_settings())
def test_update_keeps_booked_and_cancelled_occurrence_snapshots() -> None:
    client, token = platform_token("upd4-platform@example.test")
    payload = provision(client, token, code="UPD4", owner_email="upd4-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="upd4-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        schedule = create_class_schedule(
            gym=gym,
            actor=owner,
            class_type_id=class_type.id,
            trainer_staff_profile_id=profile.id,
            weekdays=[0, 2],
            local_start_time=time(18, 0),
            local_end_time=time(18, 45),
            effective_from=date(2026, 11, 16),
            effective_until=date(2026, 11, 18),
            location="Studio",
            location_is_exclusive=True,
            capacity=8,
            request_id="req_schedule_setup",
            ip_address=None,
            user_agent="",
        )
        monday = ClassOccurrence.objects.get(class_schedule=schedule, local_date=date(2026, 11, 16))
        wednesday = ClassOccurrence.objects.get(
            class_schedule=schedule,
            local_date=date(2026, 11, 18),
        )
        member = Member.objects.create(
            gym=gym,
            member_number="M-1",
            first_name="Nwe",
            join_date=date.today(),
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
        booking = create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=monday.id,
            member_id=member.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="Front desk booking",
            request_id="req_booking_setup",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        cancel_class_occurrence(
            gym=gym,
            actor=owner,
            occurrence_id=wednesday.id,
            reason="Closed",
            request_id="req_cancel_setup",
            ip_address=None,
            user_agent="",
        )
    monday.refresh_from_db()
    wednesday.refresh_from_db()
    booked_before = (monday.id, monday.location, monday.capacity, monday.status, monday.starts_at)
    cancelled_before = (wednesday.id, wednesday.location, wednesday.capacity, wednesday.reason)
    updated = _run(
        gym,
        owner,
        goal,
        _UPDATE,
        {"schedule_id": str(schedule.id), "effective_until": "2026-11-23"},
        1,
    )
    schedule.refresh_from_db()
    monday.refresh_from_db()
    wednesday.refresh_from_db()
    booking.refresh_from_db()
    added = ClassOccurrence.objects.get(class_schedule=schedule, local_date=date(2026, 11, 23))
    assert updated.outcome == "committed"
    assert updated.effect_reference == str(schedule.id)
    assert schedule.location == "Studio"
    assert schedule.capacity == 8
    assert schedule.effective_until == date(2026, 11, 23)
    assert (monday.id, monday.location, monday.capacity, monday.status, monday.starts_at) == (
        booked_before
    )
    assert (wednesday.id, wednesday.location, wednesday.capacity, wednesday.reason) == (
        cancelled_before
    )
    assert wednesday.status == ClassOccurrence.Status.CANCELLED
    assert booking.class_occurrence_id == monday.id
    assert booking.status == ClassBooking.Status.BOOKED
    assert ClassBooking.objects.filter(gym=gym).count() == 1
    assert added.location == "Studio"
    assert added.capacity == 8
    assert added.status == ClassOccurrence.Status.SCHEDULED
    assert ClassOccurrence.objects.filter(class_schedule=schedule).count() == 3
    goal.refresh_from_db()
    with pytest.raises(ScheduleConflict):
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"schedule_id": str(schedule.id), "capacity": 3, "location": "Hall"},
            goal.version,
        )
    goal.refresh_from_db()
    schedule.refresh_from_db()
    monday.refresh_from_db()
    booking.refresh_from_db()
    assert goal.version == updated.version
    assert schedule.capacity == 8
    assert schedule.location == "Studio"
    assert monday.capacity == 8
    assert monday.location == "Studio"
    assert booking.status == ClassBooking.Status.BOOKED
    assert ClassBooking.objects.filter(gym=gym).count() == 1


@override_settings(**jwt_settings())
def test_update_bookkeeping_retries_without_a_second_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("upd5-platform@example.test")
    payload = provision(client, token, code="UPD5", owner_email="upd5-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="upd5-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    command = {"schedule_id": str(schedule.id), "capacity": 6}
    real = update_class_schedule

    def move_version(**kwargs: object) -> ClassSchedule:
        row = real(**kwargs)
        Goal.objects.filter(pk=goal.id, gym_id=gym.id).update(version=2)
        return row

    monkeypatch.setattr("apps.assistant.schedule_tools.update_class_schedule", move_version)
    pending = _run(gym, owner, goal, _UPDATE, command, 1)
    monkeypatch.undo()
    stale = _run(gym, owner, goal, _UPDATE, command, 1)
    booked = _run(gym, owner, goal, _UPDATE, command, 2)
    goal.refresh_from_db()
    schedule.refresh_from_db()
    assert pending.outcome == "committed"
    assert pending.version == 3
    assert stale.outcome == "existing"
    assert booked.outcome == "existing"
    assert booked.effect_reference == str(schedule.id)
    assert schedule.capacity == 6
    assert _completed(gym) == 1
    assert ToolExecution.objects.filter(goal=goal, tool_name=_UPDATE).count() == 1
    assert goal.version == 3


@override_settings(**jwt_settings())
def test_update_same_actor_concurrency_changes_one_schedule() -> None:
    client, token = platform_token("upd6-platform@example.test")
    payload = provision(client, token, code="UPD6", owner_email="upd6-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal, _UPDATE)
    trainer = add_staff(gym, email="upd6-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    command = {"schedule_id": str(schedule.id), "capacity": 7}
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, owner.id, goal.id, _UPDATE, command, barrier)
            for _index in range(2)
        ]
        results = sorted(future.result() for future in futures)
    schedule.refresh_from_db()
    assert [item[0] for item in results] == ["ok", "ok"]
    assert schedule.capacity == 7
    assert ClassSchedule.objects.filter(gym=gym).count() == 1
    assert _completed(gym) == 1


@override_settings(**jwt_settings())
def test_update_different_actor_concurrency_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("upd7-platform@example.test")
    payload = provision(client, token, code="UPD7", owner_email="upd7-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="upd7-manager@example.test", role_key="MANAGER")
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal, _UPDATE)
    trainer = add_staff(gym, email="upd7-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    command = {"schedule_id": str(schedule.id), "capacity": 4}
    calls = {"count": 0}
    real = update_class_schedule

    def counting(**kwargs: object) -> ClassSchedule:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.update_class_schedule", counting)
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, actor_id, goal.id, _UPDATE, command, barrier)
            for actor_id in (owner.id, manager.id)
        ]
        results = sorted(future.result() for future in futures)
    schedule.refresh_from_db()
    outcomes = sorted(item[1] for item in results)
    detail = {
        "results": results,
        "capacity": schedule.capacity,
        "service_calls": calls["count"],
        "tool_executions": ToolExecution.objects.filter(goal=goal, tool_name=_UPDATE).count(),
    }
    assert [item[0] for item in results] == ["ok", "ok"], detail
    assert "committed" in outcomes, detail
    assert set(outcomes) <= {"committed", "existing", "in_progress"}, detail
    assert schedule.capacity == 4, detail
    assert calls["count"] == 1, detail
    assert _completed(gym) == 1, detail
    assert detail["tool_executions"] == 1, detail


@override_settings(**jwt_settings())
def test_cancel_replay_scope_and_cached_state_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("can1-platform@example.test")
    payload = provision(client, token, code="CAN1", owner_email="can1-owner@example.test")
    other_payload = provision(client, token, code="CAN2", owner_email="can2-owner@example.test")
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
    trainer = add_staff(gym, email="can1-trainer@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym, email="can1-other@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    other_profile = _profile(gym, other_trainer, code="BEA", name="Bea")
    foreign_trainer = add_staff(other, email="can2-trainer@example.test", role_key="TRAINER")
    foreign_profile = _profile(other, foreign_trainer, code="CY", name="Cy")
    class_type = _type(gym, owner)
    foreign_type = _type(other, other_owner, code="FLOW")
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    hidden = _schedule(
        gym,
        owner,
        class_type.id,
        other_profile.id,
        location="Room",
        local_start_time=time(19, 0),
        local_end_time=time(19, 45),
    )
    foreign = _schedule(other, other_owner, foreign_type.id, foreign_profile.id, location="Away")
    occurrence = ClassOccurrence.objects.get(class_schedule=schedule)
    hidden_occurrence = ClassOccurrence.objects.get(class_schedule=hidden)
    foreign_occurrence = ClassOccurrence.objects.get(class_schedule=foreign)
    command = {"occurrence_id": str(occurrence.id).upper(), "reason": "  Rain  "}
    with pytest.raises(Http404):
        _run(
            gym,
            trainer,
            goal,
            _CANCEL,
            {"occurrence_id": str(hidden_occurrence.id), "reason": "Not mine"},
            1,
        )
    with pytest.raises(Http404):
        _run(
            gym,
            owner,
            goal,
            _CANCEL,
            {"occurrence_id": str(foreign_occurrence.id), "reason": "Away"},
            1,
        )
    assert _completed(gym) == 0
    assert occurrence.status == ClassOccurrence.Status.SCHEDULED

    cancelled = _run(gym, owner, goal, _CANCEL, command, 1)
    cancelled_again = _run(gym, owner, goal, _CANCEL, command, cancelled.version)
    goal.refresh_from_db()
    occurrence.refresh_from_db()
    prepared = cancel_occurrence_command(command)
    reason = ClassOccurrenceCancelSerializer(data={"reason": "  Rain  "})
    reason.is_valid(raise_exception=True)
    view_body = {"reason": str(reason.validated_data["reason"])}
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=cancelled.execution_identity)
    route = f"/api/v1/class-occurrences/{occurrence.id}/cancel/"
    expected_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=route,
        body=view_body,
    )
    assert cancelled.outcome == "committed"
    assert cancelled.effect_reference == str(occurrence.id)
    assert cancelled_again.outcome == "existing"
    assert cancelled_again.effect_reference == cancelled.effect_reference
    assert cancelled.execution_identity == _identity(goal, _CANCEL, prepared)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == route
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == expected_hash
    assert occurrence.status == ClassOccurrence.Status.CANCELLED
    assert occurrence.reason == "Rain"
    assert occurrence.cancelled_by_membership_id == owner.id
    claim = LogicalClaim.objects.get(gym=gym, execution_identity=cancelled.execution_identity)
    assert claim.state == LogicalClaim.State.COMPLETED
    assert claim.resource_id == cancelled.effect_reference
    assert ClassOccurrence.objects.filter(class_schedule=hidden, status="CANCELLED").count() == 0

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        repeated = ClassOccurrence.objects.get(class_schedule=hidden)
        cancel_class_occurrence(
            gym=gym,
            actor=owner,
            occurrence_id=repeated.id,
            reason="Already",
            request_id="req_cancel_setup",
            ip_address=None,
            user_agent="",
        )
    calls = {"count": 0}
    real = cancel_class_occurrence

    def counting(**kwargs: object) -> ClassOccurrence:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.cancel_class_occurrence", counting)
    failed_command = {"occurrence_id": str(repeated.id), "reason": "Again"}
    with pytest.raises(OccurrenceStateConflict):
        _run(gym, owner, goal, _CANCEL, failed_command, goal.version)
    with pytest.raises(ExecutionRejected) as replayed:
        _run(gym, owner, goal, _CANCEL, failed_command, goal.version)
    assert replayed.value.code == "finance_failed"
    assert calls["count"] == 1
    failed_key = _identity(goal, _CANCEL, cancel_occurrence_command(failed_command))
    failed = IdempotencyRecord.objects.get(gym=gym, idempotency_key=failed_key)
    assert failed.state == IdempotencyRecord.State.FAILED
    failed_claim = LogicalClaim.objects.get(gym=gym, execution_identity=failed_key)
    assert failed_claim.state == LogicalClaim.State.FAILED
    repeated.refresh_from_db()
    assert repeated.status == ClassOccurrence.Status.CANCELLED
    assert repeated.reason == "Already"


@override_settings(**jwt_settings())
def test_cancel_bookkeeping_retries_without_a_second_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("can3-platform@example.test")
    payload = provision(client, token, code="CAN3", owner_email="can3-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="can3-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    occurrence = ClassOccurrence.objects.get(class_schedule=schedule)
    command = {"occurrence_id": str(occurrence.id), "reason": "Storm"}
    real = cancel_class_occurrence

    def move_version(**kwargs: object) -> ClassOccurrence:
        row = real(**kwargs)
        Goal.objects.filter(pk=goal.id, gym_id=gym.id).update(version=2)
        return row

    monkeypatch.setattr("apps.assistant.schedule_tools.cancel_class_occurrence", move_version)
    pending = _run(gym, owner, goal, _CANCEL, command, 1)
    monkeypatch.undo()
    stale = _run(gym, owner, goal, _CANCEL, command, 1)
    booked = _run(gym, owner, goal, _CANCEL, command, 2)
    goal.refresh_from_db()
    occurrence.refresh_from_db()
    assert pending.outcome == "committed"
    assert pending.version == 3
    assert stale.outcome == "existing"
    assert booked.outcome == "existing"
    assert booked.effect_reference == str(occurrence.id)
    assert occurrence.status == ClassOccurrence.Status.CANCELLED
    assert _completed(gym) == 1
    assert ToolExecution.objects.filter(goal=goal, tool_name=_CANCEL).count() == 1
    assert goal.version == 3


@override_settings(**jwt_settings())
def test_cancel_same_actor_concurrency_cancels_once() -> None:
    client, token = platform_token("can4-platform@example.test")
    payload = provision(client, token, code="CAN4", owner_email="can4-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal, _CANCEL)
    trainer = add_staff(gym, email="can4-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    occurrence = ClassOccurrence.objects.get(class_schedule=schedule)
    command = {"occurrence_id": str(occurrence.id), "reason": "Rain"}
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, owner.id, goal.id, _CANCEL, command, barrier)
            for _index in range(2)
        ]
        results = sorted(future.result() for future in futures)
    occurrence.refresh_from_db()
    assert [item[0] for item in results] == ["ok", "ok"]
    assert occurrence.status == ClassOccurrence.Status.CANCELLED
    assert _completed(gym) == 1


@override_settings(**jwt_settings())
def test_cancel_different_actor_concurrency_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("can5-platform@example.test")
    payload = provision(client, token, code="CAN5", owner_email="can5-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="can5-manager@example.test", role_key="MANAGER")
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    _step(goal, _CANCEL)
    trainer = add_staff(gym, email="can5-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    schedule = _schedule(gym, owner, class_type.id, profile.id)
    occurrence = ClassOccurrence.objects.get(class_schedule=schedule)
    command = {"occurrence_id": str(occurrence.id), "reason": "Rain"}
    calls = {"count": 0}
    real = cancel_class_occurrence

    def counting(**kwargs: object) -> ClassOccurrence:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.schedule_tools.cancel_class_occurrence", counting)
    barrier = Barrier(2, timeout=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_thread_result, gym.id, actor_id, goal.id, _CANCEL, command, barrier)
            for actor_id in (owner.id, manager.id)
        ]
        results = sorted(future.result() for future in futures)
    occurrence.refresh_from_db()
    outcomes = sorted(item[1] for item in results)
    detail = {
        "results": results,
        "status": occurrence.status,
        "service_calls": calls["count"],
        "tool_executions": ToolExecution.objects.filter(goal=goal, tool_name=_CANCEL).count(),
    }
    assert [item[0] for item in results] == ["ok", "ok"], detail
    assert "committed" in outcomes, detail
    assert set(outcomes) <= {"committed", "existing", "in_progress"}, detail
    assert occurrence.status == ClassOccurrence.Status.CANCELLED, detail
    assert calls["count"] == 1, detail
    assert _completed(gym) == 1, detail
    assert detail["tool_executions"] == 1, detail
