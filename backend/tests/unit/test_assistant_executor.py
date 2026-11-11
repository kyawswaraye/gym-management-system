from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from uuid import uuid4

import pytest
from django.http import Http404
from django.test import override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import (
    ApprovalSnapshot,
    ExecutionMode,
    ReadCall,
    ToolCall,
    ToolContract,
    _finance_command,
    execute_confirmed_tool,
    execute_read_tool,
)
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition, closed_production_names, production_registry
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.exceptions import MFARequired
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
from apps.trainers.selectors import TrainerMemberScope
from apps.users.models import RefreshSession, User


def _gym(code: str = "exec") -> Gym:
    return Gym(id=uuid4(), code=code, name=code, status=Gym.Status.ACTIVE)


def _actor(gym: Gym) -> GymMembership:
    return GymMembership(
        id=uuid4(),
        gym=gym,
        status=GymMembership.Status.ACTIVE,
        is_owner=True,
    )


def _goal(gym: Gym) -> Goal:
    return Goal(id=uuid4(), gym=gym, version=1)


def _request(
    gym: Gym,
    actor: GymMembership,
    codes: frozenset[str],
    *,
    verified_at: object | None = None,
    mfa_enabled: bool = True,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="staff@example.test", mfa_enabled=mfa_enabled)
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.refresh_session = RefreshSession(mfa_verified_at=verified_at)
    return request


def _tool(
    name: str,
    *,
    permissions: tuple[str, ...] = ("members.create",),
    features: tuple[str, ...] = (),
    operation: str = "write",
    confirmation_required: bool = False,
    approval_required: bool = False,
    requires_recent_mfa: bool = False,
    external_side_effects: tuple[str, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Probe",
        module="probe",
        operation=operation,
        permissions=permissions,
        features=features,
        confirmation_required=confirmation_required,
        approval_required=approval_required,
        requires_recent_mfa=requires_recent_mfa,
        external_side_effects=external_side_effects,
    )


def _contract(tool: ToolDefinition, mode: str = ExecutionMode.SAME_TRANSACTION) -> ToolContract:
    return ToolContract(
        definition=tool,
        mode=mode,
        http_method="POST" if mode == ExecutionMode.FINANCE_IDEMPOTENT else "",
        route="/api/v1/payments/" if mode == ExecutionMode.FINANCE_IDEMPOTENT else "",
    )


def _execute(
    request: Request,
    contract: ToolContract,
    goal: Goal,
    *,
    payload: dict[str, object],
    confirmed: bool = True,
    effect: object | None = None,
    finance_mutate: object | None = None,
    load_approval: object | None = None,
) -> None:
    execute_confirmed_tool(
        request=request,
        view=APIView(),
        contract=contract,
        goal=goal,
        step_id=uuid4(),
        expected_version=1,
        payload=payload,
        confirmed=confirmed,
        effect=effect,  # type: ignore[arg-type]
        finance_mutate=finance_mutate,  # type: ignore[arg-type]
        load_approval=load_approval,  # type: ignore[arg-type]
    )


def test_turn_endpoint_is_registered() -> None:
    match = resolve("/api/v1/assistant/turns/")
    assert match.view_name == "assistant-turns"
    assert production_registry().names() == closed_production_names()


def test_external_effects_are_rejected_before_the_handler() -> None:
    gym = _gym()
    called = {"count": 0}

    def effect(_call: ToolCall) -> str:
        called["count"] += 1
        return "member-one"

    with pytest.raises(ExecutionRejected) as caught:
        _execute(
            _request(gym, _actor(gym), frozenset({"members.create"})),
            _contract(_tool("probe_write", external_side_effects=("email",))),
            _goal(gym),
            payload={"name": "Maung"},
            effect=effect,
        )
    assert caught.value.code == "external_side_effects"
    with pytest.raises(ExecutionRejected) as finance_caught:
        _execute(
            _request(gym, _actor(gym), frozenset({"payments.create"})),
            _contract(
                _tool("probe_finance", permissions=("payments.create",)),
                ExecutionMode.EXTERNAL,
            ),
            _goal(gym),
            payload={"amount": "10.00"},
            finance_mutate=lambda _call: None,
        )
    assert finance_caught.value.code == "external_side_effects"
    assert called["count"] == 0
    assert production_registry().names() == closed_production_names()


def test_confirmation_reads_the_server_flag_only() -> None:
    gym = _gym()
    called = {"count": 0}
    request = _request(gym, _actor(gym), frozenset({"members.create"}))
    contract = _contract(_tool("probe_write", confirmation_required=True))
    with pytest.raises(ExecutionRejected) as caught:
        _execute(
            request,
            contract,
            _goal(gym),
            payload={"name": "Maung", "confirmed": True},
            confirmed=False,
            effect=lambda _call: called.__setitem__("count", 1),
        )
    assert caught.value.code == "confirmation_required"
    assert called["count"] == 0


def test_missing_fields_and_wrong_gym_stop_before_the_handler() -> None:
    gym = _gym()
    other = _gym("other")
    called = {"count": 0}
    request = _request(gym, _actor(gym), frozenset({"members.create"}))
    contract = ToolContract(
        definition=_tool("probe_write"),
        mode=ExecutionMode.SAME_TRANSACTION,
        prerequisite=Prerequisite(
            code="member",
            description="Member",
            fields=(FieldRequirement("first_name", "First name"),),
        ),
    )
    with pytest.raises(ExecutionRejected) as missing:
        _execute(
            request,
            contract,
            _goal(gym),
            payload={"first_name": ""},
            effect=lambda _call: called.__setitem__("count", 1),
        )
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as mismatched:
        _execute(
            request,
            _contract(_tool("probe_write")),
            _goal(gym),
            payload={"gym_id": str(other.id), "name": "Maung"},
            effect=lambda _call: called.__setitem__("count", 1),
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as other_goal:
        _execute(
            request,
            _contract(_tool("probe_write")),
            _goal(other),
            payload={"name": "Maung"},
            effect=lambda _call: called.__setitem__("count", 1),
        )
    assert other_goal.value.code == "gym_mismatch"
    assert called["count"] == 0


def test_approval_mismatch_does_not_call_the_handler() -> None:
    gym = _gym()
    loaded = {"count": 0}
    called = {"count": 0}

    def load() -> ApprovalSnapshot:
        loaded["count"] += 1
        return ApprovalSnapshot(record_id="pay-1", amount="10.00")

    request = _request(gym, _actor(gym), frozenset({"payments.approve"}))
    contract = _contract(
        _tool(
            "probe_approve",
            permissions=("payments.approve",),
            confirmation_required=True,
            approval_required=True,
        )
    )
    with pytest.raises(ExecutionRejected) as unconfirmed:
        _execute(
            request,
            contract,
            _goal(gym),
            payload={"record_id": "pay-1", "amount": "10.00", "confirmed": True},
            confirmed=False,
            effect=lambda _call: called.__setitem__("count", 1),
            load_approval=load,
        )
    assert unconfirmed.value.code == "confirmation_required"
    assert loaded["count"] == 0
    with pytest.raises(ExecutionRejected) as mismatched:
        _execute(
            request,
            contract,
            _goal(gym),
            payload={"record_id": "pay-1", "amount": "9.00"},
            effect=lambda _call: called.__setitem__("count", 1),
            load_approval=load,
        )
    assert mismatched.value.code == "approval_mismatch"
    assert loaded["count"] == 1
    assert called["count"] == 0


def test_permission_mfa_and_mode_denials_do_not_call_the_handler() -> None:
    gym = _gym()
    called = {"count": 0}
    actor = _actor(gym)

    def effect(_call: ToolCall) -> str:
        called["count"] += 1
        return "member-one"

    with pytest.raises(PermissionDenied):
        _execute(
            _request(gym, actor, frozenset()),
            _contract(_tool("probe_write")),
            _goal(gym),
            payload={"name": "Maung"},
            effect=effect,
        )
    with pytest.raises(PermissionDenied):
        _execute(
            _request(gym, actor, frozenset({"classes.view"})),
            ToolContract(
                definition=_tool(
                    "probe_book",
                    permissions=("classes.book", "classes.manage"),
                ),
                mode=ExecutionMode.SAME_TRANSACTION,
                permission_mode="any",
            ),
            _goal(gym),
            payload={"name": "Maung"},
            effect=effect,
        )
    stale = _request(
        gym,
        actor,
        frozenset({"staff.manage"}),
        verified_at=timezone.now() - timedelta(hours=2),
    )
    revoke = _contract(
        _tool("probe_revoke", permissions=("staff.manage",), requires_recent_mfa=True)
    )
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        _execute(
            stale,
            revoke,
            _goal(gym),
            payload={"reason": "Left"},
            effect=effect,
        )
    missing_session = _request(gym, actor, frozenset({"staff.manage"}))
    missing_session.refresh_session = None
    with override_settings(MFA_ENFORCED=True), pytest.raises(MFARequired):
        _execute(
            missing_session,
            revoke,
            _goal(gym),
            payload={"reason": "Left"},
            effect=effect,
        )
    with pytest.raises(ExecutionRejected) as route:
        _execute(
            _request(gym, actor, frozenset({"payments.create"})),
            ToolContract(
                definition=_tool("probe_finance", permissions=("payments.create",)),
                mode=ExecutionMode.FINANCE_IDEMPOTENT,
            ),
            _goal(gym),
            payload={"amount": "10.00"},
            finance_mutate=lambda _call: called.__setitem__("count", 1),
        )
    assert route.value.code == "finance_route"
    with pytest.raises(ExecutionRejected) as read:
        _execute(
            _request(gym, actor, frozenset({"members.view"})),
            _contract(_tool("probe_read", permissions=("members.view",), operation="read")),
            _goal(gym),
            payload={},
            effect=effect,
        )
    assert read.value.code == "operation"
    assert called["count"] == 0
    assert production_registry().names() == closed_production_names()


def _schedule_contract() -> ToolContract:
    return ToolContract(
        definition=_tool("update_class_schedule", permissions=("classes.manage",)),
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        http_method="PATCH",
        route="/api/v1/class-schedules/{schedule_id}/",
        route_fields=("schedule_id",),
    )


def test_static_finance_route_keeps_the_method_and_body() -> None:
    payload = {"amount": "10.00"}
    contract = _contract(
        _tool("probe_finance", permissions=("payments.create",)),
        ExecutionMode.FINANCE_IDEMPOTENT,
    )
    command = _finance_command(contract, payload)
    assert contract.route_fields == ()
    assert command.http_method == "POST"
    assert command.route == "/api/v1/payments/"
    assert command.body is payload


def test_confirmation_fields_stay_out_of_the_finance_body() -> None:
    gym = _gym()
    record_id = str(uuid4())
    payload = {
        "payment_route_id": record_id.upper(),
        "payment_id": record_id,
        "cash_session_id": None,
        "record_id": record_id,
        "amount": "10.0",
    }
    contract = ToolContract(
        definition=_tool(
            "probe_approve",
            permissions=("payments.approve",),
            confirmation_required=True,
            approval_required=True,
        ),
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        http_method="POST",
        route="/api/v1/payments/{payment_route_id}/approve/",
        route_fields=("payment_route_id",),
        confirmation_fields=("record_id", "amount"),
    )
    command = _finance_command(contract, payload)
    assert command.body == {"payment_id": record_id, "cash_session_id": None}
    goal_id = uuid4()
    step_id = uuid4()
    first_identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name="probe_approve",
        attempt_number=1,
        payload=payload,
    )
    second_identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal_id,
        step_id=step_id,
        tool_name="probe_approve",
        attempt_number=1,
        payload={**payload, "amount": "10.00"},
    )
    assert first_identity != second_identity
    called = {"count": 0}
    request = _request(gym, _actor(gym), frozenset({"payments.approve"}))
    with pytest.raises(ExecutionRejected) as mismatched:
        _execute(
            request,
            contract,
            _goal(gym),
            payload={**payload, "amount": "9.00"},
            finance_mutate=lambda _call: called.__setitem__("count", 1),
            load_approval=lambda: ApprovalSnapshot(record_id=record_id, amount="10.00"),
        )
    assert mismatched.value.code == "approval_mismatch"
    assert called["count"] == 0
    bare = _contract(
        _tool(
            "probe_approve",
            permissions=("payments.approve",),
            approval_required=True,
        ),
        ExecutionMode.FINANCE_IDEMPOTENT,
    )
    with pytest.raises(ExecutionRejected) as unsupported:
        _execute(
            request,
            bare,
            _goal(gym),
            payload=payload,
            finance_mutate=lambda _call: called.__setitem__("count", 1),
        )
    assert unsupported.value.code == "execution_mode"
    assert called["count"] == 0


def test_uuid_route_fields_change_identity_and_finance_hash() -> None:
    gym = _gym()
    actor = _actor(gym)
    goal = _goal(gym)
    step_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    contract = _schedule_contract()
    first = {"schedule_id": str(first_id).upper(), "location": "Hall"}
    second = {"schedule_id": str(second_id), "location": "Hall"}
    first_command = _finance_command(contract, first)
    second_command = _finance_command(contract, second)
    assert first_command.http_method == "PATCH"
    assert first_command.route == f"/api/v1/class-schedules/{first_id}/"
    assert first_command.body == {"location": "Hall"}
    assert second_command.route == f"/api/v1/class-schedules/{second_id}/"
    assert first_command.body == second_command.body
    assert first_command.route != second_command.route
    first_identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=step_id,
        tool_name=contract.definition.name,
        attempt_number=1,
        payload=first,
    )
    second_identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=step_id,
        tool_name=contract.definition.name,
        attempt_number=1,
        payload=second,
    )
    assert first_identity != second_identity
    first_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=actor.id,
        http_method=first_command.http_method,
        route=first_command.route,
        body=first_command.body,
    )
    second_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=actor.id,
        http_method=second_command.http_method,
        route=second_command.route,
        body=second_command.body,
    )
    assert first_hash != second_hash


def test_occurrence_cancel_route_keeps_only_the_reason() -> None:
    occurrence_id = uuid4()
    contract = ToolContract(
        definition=_tool("cancel_class_occurrence", permissions=("classes.manage",)),
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        http_method="POST",
        route="/api/v1/class-occurrences/{occurrence_id}/cancel/",
        route_fields=("occurrence_id",),
    )
    command = _finance_command(
        contract,
        {"occurrence_id": str(occurrence_id), "reason": "Rain"},
    )
    assert command.http_method == "POST"
    assert command.route == f"/api/v1/class-occurrences/{occurrence_id}/cancel/"
    assert command.body == {"reason": "Rain"}


def test_invalid_route_resource_stops_before_finance(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("finance started")

    monkeypatch.setattr("apps.assistant.executor.acquire_claim", fail)
    monkeypatch.setattr("apps.assistant.executor.run_idempotent", fail)
    request = _request(gym, _actor(gym), frozenset({"classes.manage"}))
    contract = _schedule_contract()
    payloads: tuple[dict[str, object], ...] = (
        {"location": "Hall"},
        {"schedule_id": "not-a-uuid", "location": "Hall"},
    )
    for payload in payloads:
        with pytest.raises(ExecutionRejected) as caught:
            _execute(request, contract, _goal(gym), payload=payload, finance_mutate=fail)
        assert caught.value.code == "finance_route"
    unresolved = ToolContract(
        definition=contract.definition,
        mode=ExecutionMode.FINANCE_IDEMPOTENT,
        http_method="PATCH",
        route="/api/v1/class-schedules/",
        route_fields=("schedule_id",),
    )
    with pytest.raises(ExecutionRejected) as missing_token:
        _execute(
            request,
            unresolved,
            _goal(gym),
            payload={"schedule_id": str(uuid4()), "location": "Hall"},
            finance_mutate=fail,
        )
    assert missing_token.value.code == "finance_route"


def test_route_field_gym_mismatch_stops_before_a_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    other = _gym("other")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("finance started")

    monkeypatch.setattr("apps.assistant.executor.acquire_claim", fail)
    monkeypatch.setattr("apps.assistant.executor.run_idempotent", fail)
    with pytest.raises(ExecutionRejected) as caught:
        _execute(
            _request(gym, _actor(gym), frozenset({"classes.manage"})),
            _schedule_contract(),
            _goal(gym),
            payload={"gym_id": str(other.id), "schedule_id": str(uuid4()), "location": "Hall"},
            finance_mutate=fail,
        )
    assert caught.value.code == "gym_mismatch"


def _read_contract(
    *,
    permissions: tuple[str, ...] = ("payments.view",),
    finance_closed: bool = False,
) -> ToolContract:
    return ToolContract(
        definition=_tool("probe_read", permissions=permissions, operation="read"),
        mode=ExecutionMode.READ,
        finance_closed=finance_closed,
    )


def _install_read_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen = {"atomic": False, "gym_id": None, "writes": 0}

    class _Atomic:
        def __enter__(self) -> _Atomic:
            seen["atomic"] = True
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def context(*, gym_id: object) -> None:
        assert seen["atomic"] is True
        seen["gym_id"] = gym_id

    def forbidden(*_args: object, **_kwargs: object) -> None:
        seen["writes"] = int(seen["writes"]) + 1
        raise AssertionError("a read wrote")

    monkeypatch.setattr("apps.assistant.executor.transaction.atomic", lambda: _Atomic())
    monkeypatch.setattr("apps.assistant.executor.set_gym_context", context)
    monkeypatch.setattr(ToolExecution.objects, "create", forbidden)
    monkeypatch.setattr(IdempotencyRecord.objects, "create", forbidden)
    monkeypatch.setattr(LogicalClaim.objects, "create", forbidden)
    monkeypatch.setattr("apps.assistant.executor.acquire_claim", forbidden)
    monkeypatch.setattr("apps.assistant.executor.run_idempotent", forbidden)
    monkeypatch.setattr("apps.assistant.executor.commit_database_effect", forbidden)
    return seen


def _read(
    request: Request,
    contract: ToolContract,
    payload: dict[str, object],
    read: object,
) -> Mapping[str, object]:
    return execute_read_tool(
        request=request,
        view=APIView(),
        contract=contract,
        payload=payload,
        read=read,  # type: ignore[arg-type]
    )


def test_permitted_read_returns_the_selector_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym = _gym()
    goal = _goal(gym)
    seen = _install_read_context(monkeypatch)
    called = {"count": 0}

    def read(call: ReadCall) -> dict[str, object]:
        called["count"] += 1
        assert call.gym.id == gym.id
        assert call.actor.gym_id == gym.id
        return {"id": "pay-1", "amount": "10.00"}

    result = _read(
        _request(gym, _actor(gym), frozenset({"payments.view"})),
        _read_contract(),
        {"limit": 25},
        read,
    )
    assert result == {"id": "pay-1", "amount": "10.00"}
    assert called["count"] == 1
    assert seen["gym_id"] == gym.id
    assert seen["writes"] == 0
    assert goal.version == 1


def test_read_permission_denial_does_not_call_the_selector() -> None:
    gym = _gym()
    called = {"count": 0}
    with pytest.raises(PermissionDenied):
        _read(
            _request(gym, _actor(gym), frozenset()),
            _read_contract(),
            {},
            lambda _call: called.__setitem__("count", 1),
        )
    assert called["count"] == 0


def test_trainer_only_finance_read_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    called = {"count": 0}
    monkeypatch.setattr(
        "apps.trainers.selectors.trainer_member_scope",
        lambda **_kwargs: TrainerMemberScope(restricted=True, profile_ids=()),
    )
    with pytest.raises(Http404):
        _read(
            _request(gym, _actor(gym), frozenset({"payments.view"})),
            _read_contract(finance_closed=True),
            {},
            lambda _call: called.__setitem__("count", 1),
        )
    assert called["count"] == 0


def test_read_payload_gym_mismatch_stops_before_the_selector() -> None:
    gym = _gym()
    other = _gym("other")
    called = {"count": 0}
    with pytest.raises(ExecutionRejected) as caught:
        _read(
            _request(gym, _actor(gym), frozenset({"payments.view"})),
            _read_contract(),
            {"gym_id": str(other.id)},
            lambda _call: called.__setitem__("count", 1),
        )
    assert caught.value.code == "gym_mismatch"
    assert called["count"] == 0


def test_read_hides_a_record_from_another_gym(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    other = _gym("other")
    payment_id = uuid4()
    seen = _install_read_context(monkeypatch)

    def read(call: ReadCall) -> dict[str, object]:
        if call.gym.id != other.id:
            raise Http404
        return {"id": str(payment_id)}

    with pytest.raises(ExecutionRejected) as caught:
        _read(
            _request(gym, _actor(gym), frozenset({"payments.view"})),
            _read_contract(),
            {"payment_id": str(payment_id)},
            read,
        )
    assert caught.value.code == "not_found"
    assert other.code not in str(caught.value)
    assert seen["writes"] == 0


def test_read_limit_above_25_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    called = {"count": 0}
    _install_read_context(monkeypatch)
    with pytest.raises(ExecutionRejected) as caught:
        _read(
            _request(gym, _actor(gym), frozenset({"payments.view"})),
            _read_contract(),
            {"limit": 26},
            lambda _call: called.__setitem__("count", 1),
        )
    assert caught.value.code == "invalid_limit"
    assert called["count"] == 0


def test_write_executor_still_rejects_a_read_operation() -> None:
    gym = _gym()
    called = {"count": 0}
    contract = ToolContract(
        definition=_tool("probe_read", permissions=("payments.view",), operation="read"),
        mode=ExecutionMode.SAME_TRANSACTION,
    )
    with pytest.raises(ExecutionRejected) as caught:
        _execute(
            _request(gym, _actor(gym), frozenset({"payments.view"})),
            contract,
            _goal(gym),
            payload={},
            effect=lambda _call: called.__setitem__("count", 1),
        )
    assert caught.value.code == "operation"
    assert called["count"] == 0
    assert production_registry().names() == closed_production_names()
