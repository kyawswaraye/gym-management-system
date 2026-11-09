from __future__ import annotations

import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant import executor as executor_module
from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import (
    ApprovalSnapshot,
    ExecutionMode,
    ToolCall,
    ToolContract,
    execute_confirmed_tool,
)
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, LogicalClaim, Message, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.assistant.registry import ToolDefinition, closed_production_names, production_registry
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.exceptions import IdempotencyRequestInvalid
from apps.common.finance.idempotency import (
    IdempotentResponse,
    canonical_request_hash,
    run_idempotent,
)
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
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

PAYMENTS_ROUTE = "/api/v1/payments/"


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
    membership.user = user
    return gym, membership


def _ready(code: str, tool_name: str) -> tuple[Gym, GymMembership, Goal, WorkflowStep]:
    gym, actor = _staff(code)
    _conversation, goal, step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Executor probe",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step.tool_name = tool_name
        step.save(update_fields=["tool_name", "updated_at"])
    return gym, actor, goal, step


def _request(gym: Gym, actor: GymMembership, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    return request


def _write_contract(
    name: str,
    permissions: tuple[str, ...],
    *,
    features: tuple[str, ...] = (),
    permission_mode: str = "all",
    confirmation_required: bool = False,
    approval_required: bool = False,
    finance_closed: bool = False,
) -> ToolContract:
    return ToolContract(
        definition=ToolDefinition(
            name=name,
            description="Probe",
            module="probe",
            operation="write",
            permissions=permissions,
            features=features,
            confirmation_required=confirmation_required,
            approval_required=approval_required,
        ),
        mode=ExecutionMode.SAME_TRANSACTION,
        permission_mode=permission_mode,
        finance_closed=finance_closed,
    )


def _finance_contract(
    name: str,
    permissions: tuple[str, ...],
    *,
    route: str = PAYMENTS_ROUTE,
    finance_closed: bool = False,
) -> ToolContract:
    return ToolContract(
        definition=ToolDefinition(
            name=name,
            description="Probe",
            module="probe",
            operation="write",
            permissions=permissions,
        ),
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        finance_closed=finance_closed,
        http_method="POST",
        route=route,
    )


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    step: WorkflowStep,
    contract: ToolContract,
    payload: dict[str, object],
    *,
    expected_version: int = 1,
    effect: object | None = None,
    finance_mutate: object | None = None,
    load_approval: object | None = None,
    confirmed: bool = True,
) -> ExecutionResult:
    return execute_confirmed_tool(
        request=_request(gym, actor, frozenset(contract.definition.permissions)),
        view=APIView(),
        contract=contract,
        goal=goal,
        step_id=step.id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
        effect=effect,  # type: ignore[arg-type]
        finance_mutate=finance_mutate,  # type: ignore[arg-type]
        load_approval=load_approval,  # type: ignore[arg-type]
    )


def _message(call: ToolCall, body: str) -> str:
    row = Message.objects.create(
        gym=call.gym,
        conversation_id=call.goal.conversation_id,
        goal=call.goal,
        role=Message.Role.SYSTEM,
        body=body,
    )
    return str(row.id)


def test_same_transaction_commits_once_and_a_stale_retry_returns_the_claim() -> None:
    gym, actor, goal, step = _ready("ex-same", "probe_write")
    calls = {"count": 0}
    payload = {"name": "Maung", "gym_id": str(gym.id)}

    def effect(call: ToolCall) -> str:
        calls["count"] += 1
        assert call.gym.id == gym.id
        assert connection.in_atomic_block
        return _message(call, "created")

    contract = _write_contract("probe_write", ("members.create",), confirmation_required=True)
    first = _run(gym, actor, goal, step, contract, payload, effect=effect)
    replay = _run(gym, actor, goal, step, contract, payload, expected_version=1, effect=effect)
    moved = _run(
        gym,
        actor,
        goal,
        step,
        contract,
        {"name": "Other", "gym_id": str(gym.id)},
        expected_version=1,
        effect=effect,
    )
    goal.refresh_from_db()
    assert first.outcome == "committed"
    assert first.version == 2
    assert replay.outcome == "existing"
    assert replay.effect_reference == first.effect_reference
    assert moved.outcome == "stale_version"
    assert moved.version == 2
    assert calls["count"] == 1
    assert Message.objects.filter(goal=goal).count() == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 1
    assert LogicalClaim.objects.filter(goal=goal).count() == 0
    assert goal.version == 2
    assert production_registry().names() == closed_production_names()


def test_same_transaction_rollback_leaves_no_claim() -> None:
    gym, actor, goal, step = _ready("ex-rollback", "probe_write")

    def effect(call: ToolCall) -> str:
        _message(call, "rolled back")
        raise RuntimeError("effect failed")

    with pytest.raises(RuntimeError, match="effect failed"):
        _run(
            gym,
            actor,
            goal,
            step,
            _write_contract("probe_write", ("members.create",)),
            {"name": "Maung"},
            effect=effect,
        )
    goal.refresh_from_db()
    assert goal.version == 1
    assert Message.objects.filter(goal=goal).count() == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_same_transaction_refuses_the_finance_runner_and_rolls_back() -> None:
    gym, actor, goal, step = _ready("ex-boundary", "probe_write")
    calls = {"count": 0}

    def effect(call: ToolCall) -> str:
        calls["count"] += 1
        _message(call, "should roll back")
        run_idempotent(
            gym_id=call.gym.id,
            actor_membership_id=call.actor.id,
            http_method="POST",
            route=PAYMENTS_ROUTE,
            idempotency_key="a" * 64,
            body={"amount": "10.00"},
            mutate=lambda: IdempotentResponse(status=201, body=b"{}"),
        )
        return "should-not-return"

    with pytest.raises(RuntimeError, match="own transaction"):
        _run(
            gym,
            actor,
            goal,
            step,
            _write_contract("probe_write", ("members.create",)),
            {"name": "Maung"},
            effect=effect,
        )
    goal.refresh_from_db()
    assert calls["count"] == 1
    assert goal.version == 1
    assert Message.objects.filter(goal=goal).count() == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0


def test_matching_approval_calls_the_effect_once() -> None:
    gym, actor, goal, step = _ready("ex-approve", "probe_approve")
    calls = {"count": 0}
    payload = {"record_id": "pay-1", "amount": "10.00"}

    def effect(call: ToolCall) -> str:
        calls["count"] += 1
        assert call.payload["amount"] == "10.00"
        return _message(call, "approved")

    result = _run(
        gym,
        actor,
        goal,
        step,
        _write_contract(
            "probe_approve",
            ("payments.approve",),
            confirmation_required=True,
            approval_required=True,
        ),
        payload,
        effect=effect,
        load_approval=lambda: ApprovalSnapshot(record_id="pay-1", amount="10.00"),
    )
    assert result.outcome == "committed"
    assert calls["count"] == 1
    assert Message.objects.filter(goal=goal).count() == 1


def test_finance_retry_returns_the_original_result_without_a_second_effect() -> None:
    gym, actor, goal, step = _ready("ex-finance", "probe_finance")
    calls = {"count": 0}
    payload = {"amount": "10.00", "description": "Desk receipt"}

    def finance_mutate(call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        assert not ToolExecution.objects.filter(goal=call.goal).exists()
        return IdempotentResponse(
            status=201,
            body=b'{"ok":true}',
            resource_type="probe",
            resource_id=UUID(_message(call, "posted")),
        )

    contract = _finance_contract("probe_finance", ("payments.create",), finance_closed=True)
    first = _run(gym, actor, goal, step, contract, payload, finance_mutate=finance_mutate)
    second = _run(gym, actor, goal, step, contract, payload, finance_mutate=finance_mutate)
    goal.refresh_from_db()
    assert first.outcome == "committed"
    assert second.outcome == "existing"
    assert second.effect_reference == first.effect_reference
    assert calls["count"] == 1
    assert Message.objects.filter(goal=goal).count() == 1
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 1
    record = IdempotencyRecord.objects.get(gym=gym)
    assert record.state == IdempotencyRecord.State.COMPLETED
    assert record.idempotency_key == execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=step.id,
        tool_name="probe_finance",
        attempt_number=step.attempt_number,
        payload=payload,
    )
    assert record.route == PAYMENTS_ROUTE
    assert goal.version == 2


def test_finance_bookkeeping_failure_recovers_without_a_second_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym, actor, goal, step = _ready("ex-recover", "probe_finance")
    calls = {"count": 0}
    payload = {"amount": "10.00"}

    def finance_mutate(call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(
            status=201,
            body=b'{"ok":true}',
            resource_type="probe",
            resource_id=UUID(_message(call, "posted")),
        )

    def fail_bookkeeping(**_kwargs: object) -> ExecutionResult:
        raise RuntimeError("bookkeeping failed")

    monkeypatch.setattr(executor_module, "commit_bookkeeping", fail_bookkeeping)
    with pytest.raises(RuntimeError, match="bookkeeping failed"):
        _run(
            gym,
            actor,
            goal,
            step,
            _finance_contract("probe_finance", ("payments.create",)),
            payload,
            finance_mutate=finance_mutate,
        )
    monkeypatch.undo()
    recovered = _run(
        gym,
        actor,
        goal,
        step,
        _finance_contract("probe_finance", ("payments.create",)),
        payload,
        finance_mutate=finance_mutate,
    )
    goal.refresh_from_db()
    assert calls["count"] == 1
    assert recovered.outcome == "committed"
    assert recovered.effect_reference == str(Message.objects.get(goal=goal).id)
    assert Message.objects.filter(goal=goal).count() == 1
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 1
    assert goal.version == 2


def test_stale_version_does_not_start_a_finance_command() -> None:
    gym, actor, goal, step = _ready("ex-stale", "probe_finance")
    calls = {"count": 0}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Goal.objects.filter(pk=goal.pk).update(version=3, updated_at=timezone.now())
    goal.refresh_from_db()

    def finance_mutate(_call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(status=201, body=b"{}")

    result = _run(
        gym,
        actor,
        goal,
        step,
        _finance_contract("probe_finance", ("payments.create",)),
        {"amount": "10.00"},
        finance_mutate=finance_mutate,
    )
    assert result.outcome == "stale_version"
    assert result.version == 3
    assert calls["count"] == 0
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_version_conflict_after_finance_keeps_the_original_result() -> None:
    gym, actor, goal, step = _ready("ex-pending", "probe_finance")
    calls = {"count": 0}
    payload = {"amount": "10.00"}

    def finance_mutate(call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        Goal.objects.filter(pk=call.goal.pk, gym_id=call.gym.id).update(version=2)
        return IdempotentResponse(
            status=201,
            body=b'{"ok":true}',
            resource_type="probe",
            resource_id=UUID(_message(call, "posted")),
        )

    contract = _finance_contract("probe_finance", ("payments.create",))
    pending = _run(gym, actor, goal, step, contract, payload, finance_mutate=finance_mutate)
    stale_retry = _run(
        gym,
        actor,
        goal,
        step,
        contract,
        payload,
        expected_version=1,
        finance_mutate=finance_mutate,
    )
    booked = _run(
        gym,
        actor,
        goal,
        step,
        contract,
        payload,
        expected_version=2,
        finance_mutate=finance_mutate,
    )
    goal.refresh_from_db()
    assert pending.outcome == "committed"
    assert pending.version == 3
    assert stale_retry.outcome == "existing"
    assert stale_retry.effect_reference == pending.effect_reference
    assert booked.outcome == "existing"
    assert booked.effect_reference == pending.effect_reference
    assert calls["count"] == 1
    assert Message.objects.filter(goal=goal).count() == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 1
    assert goal.version == 3


def test_finance_inside_a_transaction_does_not_mutate() -> None:
    gym, actor, goal, step = _ready("ex-atomic", "probe_finance")
    calls = {"count": 0}

    def finance_mutate(_call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(status=201, body=b"{}")

    with transaction.atomic():
        with pytest.raises(ExecutionRejected) as caught:
            _run(
                gym,
                actor,
                goal,
                step,
                _finance_contract("probe_finance", ("payments.create",)),
                {"amount": "10.00"},
                finance_mutate=finance_mutate,
            )
        assert caught.value.code == "finance_inside_transaction"
    assert calls["count"] == 0
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0


def test_invalid_finance_route_and_missing_record_do_not_repeat() -> None:
    gym, actor, goal, step = _ready("ex-route", "probe_finance")
    calls = {"count": 0}

    def finance_mutate(_call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(status=201, body=b"{}")

    with pytest.raises(IdempotencyRequestInvalid):
        _run(
            gym,
            actor,
            goal,
            step,
            _finance_contract("probe_finance", ("payments.create",), route="not-a-route"),
            {"amount": "10.00"},
            finance_mutate=finance_mutate,
        )
    assert calls["count"] == 0
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0

    gym, actor, goal, step = _ready("ex-noref", "probe_finance")
    with pytest.raises(ExecutionRejected) as first:
        _run(
            gym,
            actor,
            goal,
            step,
            _finance_contract("probe_finance", ("payments.create",)),
            {"amount": "10.00"},
            finance_mutate=finance_mutate,
        )
    with pytest.raises(ExecutionRejected) as second:
        _run(
            gym,
            actor,
            goal,
            step,
            _finance_contract("probe_finance", ("payments.create",)),
            {"amount": "10.00"},
            finance_mutate=finance_mutate,
        )
    assert first.value.code == "missing_effect_reference"
    assert second.value.code == "missing_effect_reference"
    assert calls["count"] == 2
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    assert (
        IdempotencyRecord.objects.filter(
            gym=gym,
            state=IdempotencyRecord.State.COMPLETED,
        ).count()
        == 0
    )
    claim = LogicalClaim.objects.get(gym=gym, goal=goal)
    assert claim.state == LogicalClaim.State.IN_PROGRESS
    assert claim.locked_until is not None
    assert claim.locked_until <= timezone.now()


def test_missing_resource_id_rolls_back_before_another_actor_can_reclaim() -> None:
    gym, actor, goal, step = _ready("ex-rollback-id", "probe_finance")
    other_user = User.objects.create_user(
        "ex-rollback-other@example.test",
        "correct horse battery staple",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        other = GymMembership.objects.create(
            gym=gym,
            user=other_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    other.user = other_user
    calls = {"count": 0}
    payload = {"amount": "10.00"}

    def finance_mutate(call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        _message(call, "must roll back")
        return IdempotentResponse(status=201, body=b"{}")

    contract = _finance_contract("probe_finance", ("payments.create",))
    with pytest.raises(ExecutionRejected) as first:
        _run(gym, actor, goal, step, contract, payload, finance_mutate=finance_mutate)
    with pytest.raises(ExecutionRejected) as second:
        _run(gym, other, goal, step, contract, payload, finance_mutate=finance_mutate)
    assert first.value.code == "missing_effect_reference"
    assert second.value.code == "missing_effect_reference"
    assert calls["count"] == 2
    assert Message.objects.filter(goal=goal).count() == 0
    assert (
        IdempotencyRecord.objects.filter(
            gym=gym,
            state=IdempotencyRecord.State.COMPLETED,
        ).count()
        == 0
    )
    assert LogicalClaim.objects.filter(gym=gym, state=LogicalClaim.State.COMPLETED).count() == 0


def test_in_progress_finance_lease_does_not_mint_another_key() -> None:
    gym, actor, goal, step = _ready("ex-lease", "probe_finance")
    calls = {"count": 0}
    payload = {"amount": "10.00"}
    identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=step.id,
        tool_name="probe_finance",
        attempt_number=step.attempt_number,
        payload=payload,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=PAYMENTS_ROUTE,
            idempotency_key=identity,
            request_hash=canonical_request_hash(
                gym_id=gym.id,
                actor_membership_id=actor.id,
                http_method="POST",
                route=PAYMENTS_ROUTE,
                body=payload,
            ),
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=1),
            created_by_membership_id=actor.id,
        )

    def finance_mutate(_call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(status=201, body=b"{}")

    result = _run(
        gym,
        actor,
        goal,
        step,
        _finance_contract("probe_finance", ("payments.create",)),
        payload,
        finance_mutate=finance_mutate,
    )
    assert result.outcome == "in_progress"
    assert calls["count"] == 0
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 1
    assert ToolExecution.objects.filter(goal=goal).count() == 0


def test_missing_feature_and_either_booking_permission() -> None:
    gym, actor, goal, step = _ready("ex-feature", "probe_write")
    calls = {"count": 0}

    def effect(call: ToolCall) -> str:
        calls["count"] += 1
        return _message(call, "created")

    with pytest.raises(PermissionDenied):
        _run(
            gym,
            actor,
            goal,
            step,
            _write_contract("probe_write", ("members.create",), features=("members",)),
            {"name": "Maung"},
            effect=effect,
        )
    assert calls["count"] == 0

    gym, actor, goal, step = _ready("ex-either", "probe_book")
    contract = _write_contract(
        "probe_book",
        ("classes.book", "classes.manage"),
        permission_mode="any",
    )
    denied = _request(gym, actor, frozenset({"classes.view"}))
    with pytest.raises(PermissionDenied):
        execute_confirmed_tool(
            request=denied,
            view=APIView(),
            contract=contract,
            goal=goal,
            step_id=step.id,
            expected_version=1,
            payload={"name": "Denied"},
            confirmed=True,
            effect=effect,
        )
    booked = _request(gym, actor, frozenset({"classes.book"}))
    managed = _request(gym, actor, frozenset({"classes.manage"}))
    first = execute_confirmed_tool(
        request=booked,
        view=APIView(),
        contract=contract,
        goal=goal,
        step_id=step.id,
        expected_version=1,
        payload={"name": "Booked"},
        confirmed=True,
        effect=effect,
    )
    second = execute_confirmed_tool(
        request=managed,
        view=APIView(),
        contract=contract,
        goal=goal,
        step_id=step.id,
        expected_version=first.version or 1,
        payload={"name": "Managed"},
        confirmed=True,
        effect=effect,
    )
    assert first.outcome == "committed"
    assert second.outcome == "committed"
    assert calls["count"] == 2


@override_settings(**jwt_settings())
def test_trainer_only_finance_closure_stops_before_mutate() -> None:
    client, platform_access = platform_token("ex-scope-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="EX-SCOPE",
        owner_email="ex-scope-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym, email="ex-scope-ada@example.test", role_key="TRAINER")
    _conversation, goal, step = open_staff_goal(
        gym=gym,
        actor=owner,
        intent="Finance closure",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    step.tool_name = "probe_finance"
    step.save(update_fields=["tool_name", "updated_at"])
    calls = {"count": 0}
    contract = _finance_contract(
        "probe_finance",
        ("payments.create",),
        finance_closed=True,
    )

    def finance_mutate(call: ToolCall) -> IdempotentResponse:
        calls["count"] += 1
        return IdempotentResponse(
            status=201,
            body=b'{"ok":true}',
            resource_type="probe",
            resource_id=UUID(_message(call, "posted")),
        )

    with pytest.raises(Http404):
        execute_confirmed_tool(
            request=_request(gym, trainer, frozenset({"payments.create"})),
            view=APIView(),
            contract=contract,
            goal=goal,
            step_id=step.id,
            expected_version=1,
            payload={"amount": "10.00"},
            confirmed=True,
            finance_mutate=finance_mutate,
        )
    owner_result = execute_confirmed_tool(
        request=_request(gym, owner, frozenset({"payments.create"})),
        view=APIView(),
        contract=contract,
        goal=goal,
        step_id=step.id,
        expected_version=1,
        payload={"amount": "10.00"},
        confirmed=True,
        finance_mutate=finance_mutate,
    )
    assert calls["count"] == 1
    assert owner_result.outcome == "committed"
    assert Message.objects.filter(goal=goal).count() == 1
    assert production_registry().names() == closed_production_names()
