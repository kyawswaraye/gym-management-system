from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import (
    ApprovalSnapshot,
    ExecutionMode,
    ToolCall,
    ToolContract,
    execute_confirmed_tool,
)
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition, closed_production_names, production_registry
from apps.tenants.exceptions import MFARequired
from apps.tenants.models import Gym, GymMembership
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


def test_turn_endpoint_is_not_registered() -> None:
    with pytest.raises(Resolver404):
        resolve("/api/v1/assistant/turns/")
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
