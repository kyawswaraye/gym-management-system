"""PostgreSQL security coverage for cash-session tools."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.cash_tools import (
    adjust_cash_command,
    execute_registered_tool,
    open_cash_command,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, WorkflowStep
from apps.cashbook.exceptions import CashSessionUnavailable
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.serializers import (
    AdjustCashSessionSerializer,
    OpenCashSessionSerializer,
    ReconcileCashSessionSerializer,
    money_text,
)
from apps.cashbook.services import command_route, open_cash_session, reconcile_cash_session
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.members.services import gym_today
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

_OPEN = "open_cash_session"
_ADJUST = "adjust_cash_session"
_RECONCILE = "reconcile_cash_session"
_OPEN_CODES = frozenset({"cashbook.open"})
_ADJUST_CODES = frozenset({"cashbook.adjust"})
_RECONCILE_CODES = frozenset({"cashbook.reconcile"})


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


def _request(gym: Gym, actor: GymMembership, *, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_cash_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str],
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


def _identity(goal: Goal, tool_name: str, payload: dict[str, object]) -> str:
    return execution_identity(
        gym_id=goal.gym_id,
        goal_id=goal.id,
        step_id=_step(goal, tool_name).id,
        tool_name=tool_name,
        attempt_number=_step(goal, tool_name).attempt_number,
        payload=payload,
    )


def _open_payload(gym: Gym, opening: str = "100.00") -> dict[str, object]:
    return {
        "register_code": "MAIN",
        "business_date": gym_today(gym).isoformat(),
        "opening_cash": opening,
    }


def _adjust_payload(session: CashSession) -> dict[str, object]:
    return {
        "session_id": str(session.id).upper(),
        "direction": "IN",
        "amount": "5.00",
        "currency": session.currency,
        "business_date": session.business_date.isoformat(),
        "description": "  Float  ",
        "reason": "  Counted  ",
    }


def _reconcile_payload(session: CashSession, closing: str) -> dict[str, object]:
    return {"session_id": str(session.id), "actual_closing": closing}


def _view_open(payload: dict[str, object]) -> dict[str, object]:
    serializer = OpenCashSessionSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "register_code": fields["register_code"],
        "business_date": fields["business_date"].isoformat(),
        "opening_cash": money_text(fields["opening_cash"]),
    }


def _view_adjust(payload: dict[str, object], session_id: str) -> dict[str, object]:
    serializer = AdjustCashSessionSerializer(
        data={key: value for key, value in payload.items() if key != "session_id"}
    )
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "cash_session_id": session_id,
        "direction": fields["direction"],
        "amount": money_text(fields["amount"]),
        "currency": fields["currency"],
        "business_date": fields["business_date"].isoformat(),
        "description": fields["description"],
        "reason": fields["reason"],
    }


def _view_reconcile(payload: dict[str, object], session_id: str) -> dict[str, object]:
    serializer = ReconcileCashSessionSerializer(
        data={key: value for key, value in payload.items() if key != "session_id"}
    )
    serializer.is_valid(raise_exception=True)
    fields = serializer.validated_data
    return {
        "cash_session_id": session_id,
        "actual_closing": money_text(fields["actual_closing"]),
        "difference_reason": str(fields.get("difference_reason", "")),
    }


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_date_stop_before_finance() -> None:
    client, token = platform_token("cash1-platform@example.test")
    payload = provision(client, token, code="CASH1", owner_email="cash1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="cash1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    manager = add_staff(gym, email="cash1-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="cash1-desk@example.test", role_key="RECEPTIONIST")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    opened = _open_payload(gym)
    owner.is_owner = False
    for tool_name, command, codes in (
        (_OPEN, opened, frozenset()),
        (_OPEN, opened, frozenset({"cashbook.view"})),
        (
            _ADJUST,
            _adjust_payload(
                CashSession(id=uuid4(), currency="THB", business_date=date(2026, 11, 9))
            ),
            _OPEN_CODES,
        ),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    owner.is_owner = True
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _OPEN, opened, 1, codes=_OPEN_CODES)
    bare_user = User.objects.create_user("cash1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="CASHB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _OPEN, _open_payload(bare), 1, codes=_OPEN_CODES)
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(
            gym,
            owner,
            goal,
            _OPEN,
            {**opened, "gym_id": str(uuid4())},
            1,
            codes=_OPEN_CODES,
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _OPEN,
            {**opened, "business_date": "2020-01-01"},
            1,
            codes=_OPEN_CODES,
        )
    with pytest.raises(ExecutionRejected) as bad_route:
        _run(
            gym,
            owner,
            goal,
            _ADJUST,
            {
                **_adjust_payload(
                    CashSession(
                        id=uuid4(),
                        currency=gym.currency,
                        business_date=gym_today(gym),
                    )
                ),
                "session_id": "x",
            },
            1,
            codes=_ADJUST_CODES,
        )
    assert bad_route.value.code == "finance_route"
    created = _run(gym, owner, goal, _OPEN, opened, 1, codes=_OPEN_CODES)
    session = CashSession.objects.get(id=created.effect_reference)
    before = IdempotencyRecord.objects.filter(gym=gym).count()
    with pytest.raises(PermissionDenied):
        _run(
            gym,
            manager,
            goal,
            _ADJUST,
            _adjust_payload(session),
            created.version,
            codes=_ADJUST_CODES,
        )
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _ADJUST,
            {**_adjust_payload(session), "currency": "USD"},
            created.version,
            codes=_ADJUST_CODES,
        )
    with pytest.raises(Http404):
        _run(
            gym,
            receptionist,
            goal,
            _RECONCILE,
            _reconcile_payload(session, "100.00"),
            created.version,
            codes=_RECONCILE_CODES,
        )
    assert IdempotencyRecord.objects.filter(gym=gym).count() == before
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert CashSession.objects.filter(gym=gym).count() == 1


@override_settings(**jwt_settings())
def test_cash_session_replay_adjustment_and_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("cash2-platform@example.test")
    payload = provision(client, token, code="CASH2", owner_email="cash2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    opened_payload = _open_payload(gym)
    created = _run(gym, owner, goal, _OPEN, opened_payload, 1, codes=_OPEN_CODES)
    created_again = _run(
        gym,
        owner,
        goal,
        _OPEN,
        {"business_date": opened_payload["business_date"], "opening_cash": "100.00"},
        created.version,
        codes=_OPEN_CODES,
    )
    session = CashSession.objects.get(id=created.effect_reference)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(goal, _OPEN, open_cash_command(opened_payload))
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/cash-sessions/open/"
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.resource_type == "CashSession"
    assert str(stored.resource_id) == str(session.id)
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/cash-sessions/open/",
        body=_view_open(opened_payload),
    )
    assert session.register_code == "MAIN"
    assert session.opening_cash == Decimal("100.00")
    assert session.status == CashSession.Status.OPEN
    assert CashSession.objects.filter(gym=gym).count() == 1

    calls = {"open": 0}
    real_open = open_cash_session

    def counting_open(**kwargs: object) -> CashSession:
        calls["open"] += 1
        return real_open(**kwargs)

    monkeypatch.setattr("apps.assistant.cash_tools.open_cash_session", counting_open)
    conflict = _open_payload(gym, "50.00")
    with pytest.raises(CashSessionUnavailable):
        _run(gym, owner, goal, _OPEN, conflict, created.version, codes=_OPEN_CODES)
    with pytest.raises(ExecutionRejected) as failed:
        _run(gym, owner, goal, _OPEN, conflict, created.version, codes=_OPEN_CODES)
    assert failed.value.code == "finance_failed"
    assert calls["open"] == 1
    assert CashSession.objects.filter(gym=gym).count() == 1
    failed_row = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _OPEN, open_cash_command(conflict)),
    )
    assert failed_row.state == IdempotencyRecord.State.FAILED

    adjusted = _run(
        gym,
        owner,
        goal,
        _ADJUST,
        _adjust_payload(session),
        created.version,
        codes=_ADJUST_CODES,
    )
    adjusted_again = _run(
        gym,
        owner,
        goal,
        _ADJUST,
        _adjust_payload(session),
        adjusted.version,
        codes=_ADJUST_CODES,
    )
    session.refresh_from_db()
    movement = CashTransaction.objects.get(gym=gym)
    adjust_stored = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=adjusted.execution_identity,
    )
    adjust_command = adjust_cash_command(_adjust_payload(session))
    assert adjusted.outcome == "committed"
    assert adjusted_again.outcome == "existing"
    assert adjusted.effect_reference == str(session.id)
    assert adjusted.execution_identity == _identity(goal, _ADJUST, adjust_command)
    assert movement.direction == "IN"
    assert movement.amount == Decimal("5.00")
    assert movement.reason == "Counted"
    assert session.expected_closing == Decimal("105.00")
    assert CashTransaction.objects.filter(gym=gym).count() == 1
    assert adjust_stored.route == command_route(session_id=session.id, action="adjustments")
    assert adjust_stored.resource_type == "CashSession"
    assert str(adjust_stored.resource_id) == str(session.id)
    assert adjust_stored.actor_member_id is None
    assert adjust_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=adjust_stored.route,
        body=_view_adjust(_adjust_payload(session), str(session.id)),
    )

    closing = money_text(session.expected_closing)
    reconciled = _run(
        gym,
        owner,
        goal,
        _RECONCILE,
        _reconcile_payload(session, closing),
        adjusted.version,
        codes=_RECONCILE_CODES,
    )
    reconciled_again = _run(
        gym,
        owner,
        goal,
        _RECONCILE,
        _reconcile_payload(session, closing),
        reconciled.version,
        codes=_RECONCILE_CODES,
    )
    session.refresh_from_db()
    reconcile_stored = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=reconciled.execution_identity,
    )
    assert reconciled.outcome == "committed"
    assert reconciled_again.outcome == "existing"
    assert session.status == CashSession.Status.CLOSED
    assert session.actual_closing == Decimal("105.00")
    assert session.difference == Decimal("0.00")
    assert session.difference_reason == ""
    assert reconcile_stored.http_method == "POST"
    assert reconcile_stored.route == command_route(session_id=session.id, action="reconcile")
    assert reconcile_stored.resource_type == "CashSession"
    assert reconcile_stored.actor_member_id is None
    assert reconcile_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=reconcile_stored.route,
        body=_view_reconcile(_reconcile_payload(session, closing), str(session.id)),
    )

    calls["reconcile"] = 0
    real_reconcile = reconcile_cash_session

    def counting_reconcile(**kwargs: object) -> CashSession:
        calls["reconcile"] += 1
        return real_reconcile(**kwargs)

    monkeypatch.setattr("apps.assistant.cash_tools.reconcile_cash_session", counting_reconcile)
    closed_again = _reconcile_payload(session, "90.00")
    with pytest.raises(CashSessionUnavailable):
        _run(gym, owner, goal, _RECONCILE, closed_again, reconciled.version, codes=_RECONCILE_CODES)
    with pytest.raises(ExecutionRejected) as closed_failed:
        _run(gym, owner, goal, _RECONCILE, closed_again, reconciled.version, codes=_RECONCILE_CODES)
    assert closed_failed.value.code == "finance_failed"
    assert calls["reconcile"] == 1
    assert session.status == CashSession.Status.CLOSED
    assert CashSession.objects.filter(gym=gym).count() == 1
