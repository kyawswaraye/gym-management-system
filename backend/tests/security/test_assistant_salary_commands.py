"""PostgreSQL security coverage for salary period tools."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, WorkflowStep
from apps.assistant.salary_tools import (
    create_salary_period_command,
    execute_registered_tool,
    replace_salary_lines_command,
    submit_salary_period_command,
    void_salary_period_command,
)
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.expenses.models import Expense
from apps.members.services import gym_today
from apps.salaries.exceptions import SalaryPostingConflict
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.salaries.serializers import SalaryPeriodWriteSerializer, VoidSalarySerializer
from apps.salaries.services import approve_period, command_route
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

_CREATE = "create_salary_period"
_UPDATE = "update_salary_period"
_LINES = "replace_salary_lines"
_SUBMIT = "submit_salary_period"
_CANCEL = "cancel_salary_period"
_VOID = "void_salary_period"
_CREATE_CODES = frozenset({"salaries.create"})
_UPDATE_CODES = frozenset({"salaries.update"})
_SUBMIT_CODES = frozenset({"salaries.submit"})
_VOID_CODES = frozenset({"salaries.void"})


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
    request.request_id = "req_salary_tool"
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


def _period(gym: Gym, description: str) -> dict[str, object]:
    today = gym_today(gym)
    return {
        "period_start": (today - timedelta(days=6)).isoformat(),
        "period_end": today.isoformat(),
        "business_date": today.isoformat(),
        "description": description,
    }


def _line(gym: Gym, staff_id: UUID, amount: str = "1500.00") -> dict[str, object]:
    return {
        "staff_profile_id": str(staff_id),
        "amount": amount,
        "currency": gym.currency,
        "payment_method": "BANK_TRANSFER",
        "description": "  November pay  ",
    }


def _approve(gym: Gym, actor: GymMembership, period_id: UUID) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        approve_period(
            gym=gym,
            actor=actor,
            period_id=period_id,
            cash_sessions=[],
            request_id="req_salary_tool",
            ip_address=None,
            user_agent="",
        )


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_route_stop_before_finance() -> None:
    client, token = platform_token("sal1-platform@example.test")
    payload = provision(client, token, code="SAL1", owner_email="sal1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="sal1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    draft = _period(gym, "  November payroll  ")
    line_payload = {
        "salary_period_id": str(uuid4()),
        "lines": [_line(gym, uuid4())],
    }
    owner.is_owner = False
    for tool_name, command, codes in (
        (_CREATE, draft, frozenset()),
        (_CREATE, draft, frozenset({"salaries.view"})),
        (_SUBMIT, {"salary_period_id": str(uuid4())}, _CREATE_CODES),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    owner.is_owner = True
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    bare_user = User.objects.create_user("sal1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="SALB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _CREATE, _period(bare, "Bare"), 1, codes=_CREATE_CODES)
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(gym, owner, goal, _CREATE, {**draft, "gym_id": str(uuid4())}, 1, codes=_CREATE_CODES)
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as missing:
        _run(gym, owner, goal, _SUBMIT, {}, 1, codes=_SUBMIT_CODES)
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as bad_route:
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"salary_period_id": "not-a-uuid", "description": "Desk payroll"},
            1,
            codes=_UPDATE_CODES,
        )
    assert bad_route.value.code == "finance_route"
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _LINES,
            {**line_payload, "lines": [{**_line(gym, uuid4()), "amount": "1500.123456789"}]},
            1,
            codes=_UPDATE_CODES,
        )
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert SalaryPeriod.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_salary_draft_lines_cancel_and_void_replay() -> None:
    client, token = platform_token("sal2-platform@example.test")
    payload = provision(client, token, code="SAL2", owner_email="sal2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    payee = add_staff(gym, email="sal2-payee@example.test", role_key="RECEPTIONIST")
    profile = _profile(gym, payee, code="PAY", name="Payee")
    _enable(gym, "finance")
    goal = _goal(gym, owner)

    draft = _period(gym, "  November payroll  ")
    created = _run(gym, owner, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    created_again = _run(gym, owner, goal, _CREATE, draft, created.version, codes=_CREATE_CODES)
    period = SalaryPeriod.objects.get(id=created.effect_reference)
    serializer = SalaryPeriodWriteSerializer(data=draft)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(
        goal, _CREATE, create_salary_period_command(draft)
    )
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/salary-periods/"
    assert stored.resource_type == "SalaryPeriod"
    assert str(stored.resource_id) == str(period.id)
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/salary-periods/",
        body=dict(serializer.validated_data),
    )
    assert period.status == SalaryPeriod.Status.DRAFT
    assert period.description == "November payroll"
    assert StaffSalaryLine.objects.filter(salary_period=period).count() == 0

    updated = _run(
        gym,
        owner,
        goal,
        _UPDATE,
        {"salary_period_id": str(period.id).upper(), "description": "  Desk payroll  "},
        created.version,
        codes=_UPDATE_CODES,
    )
    period.refresh_from_db()
    update_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=updated.execution_identity
    )
    assert updated.outcome == "committed"
    assert period.description == "Desk payroll"
    assert update_stored.http_method == "PATCH"
    assert update_stored.route == command_route(period_id=period.id, action="")
    assert update_stored.actor_member_id is None

    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _SUBMIT,
            {"salary_period_id": str(period.id)},
            updated.version,
            codes=_SUBMIT_CODES,
        )
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _SUBMIT,
            {"salary_period_id": str(period.id)},
            updated.version,
            codes=_SUBMIT_CODES,
        )
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(
            goal,
            _SUBMIT,
            submit_salary_period_command({"salary_period_id": str(period.id)}),
        ),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    period.refresh_from_db()
    assert period.status == SalaryPeriod.Status.DRAFT

    early = {"salary_period_id": str(period.id), "reason": "Too early"}
    with pytest.raises(SalaryPostingConflict):
        _run(gym, owner, goal, _VOID, early, updated.version, codes=_VOID_CODES)
    with pytest.raises(ExecutionRejected) as early_failed:
        _run(gym, owner, goal, _VOID, early, updated.version, codes=_VOID_CODES)
    assert early_failed.value.code == "finance_failed"
    period.refresh_from_db()
    assert period.status == SalaryPeriod.Status.DRAFT

    mismatched = {
        "salary_period_id": str(period.id),
        "lines": [{**_line(gym, profile.id), "currency": "USD"}],
    }
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _LINES, mismatched, updated.version, codes=_UPDATE_CODES)
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _LINES, mismatched, updated.version, codes=_UPDATE_CODES)
    currency_released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _LINES, replace_salary_lines_command(mismatched)),
    )
    assert currency_released.state == IdempotencyRecord.State.IN_PROGRESS
    assert StaffSalaryLine.objects.filter(salary_period=period).count() == 0

    lines_payload = {"salary_period_id": str(period.id), "lines": [_line(gym, profile.id)]}
    replaced = _run(gym, owner, goal, _LINES, lines_payload, updated.version, codes=_UPDATE_CODES)
    replaced_again = _run(
        gym, owner, goal, _LINES, lines_payload, replaced.version, codes=_UPDATE_CODES
    )
    line = StaffSalaryLine.objects.get(salary_period=period)
    lines_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=replaced.execution_identity
    )
    assert replaced.outcome == "committed"
    assert replaced_again.outcome == "existing"
    assert replaced.effect_reference == str(period.id)
    assert line.amount == Decimal("1500.00")
    assert line.currency == gym.currency
    assert line.description == "November pay"
    assert line.staff_profile_id == profile.id
    assert StaffSalaryLine.objects.filter(salary_period=period).count() == 1
    assert lines_stored.http_method == "PUT"
    assert lines_stored.route == command_route(period_id=period.id, action="lines")
    assert lines_stored.actor_member_id is None
    assert lines_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="PUT",
        route=lines_stored.route,
        body={
            key: value
            for key, value in replace_salary_lines_command(lines_payload).items()
            if key != "period_route_id"
        },
    )

    submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"salary_period_id": str(period.id)},
        replaced.version,
        codes=_SUBMIT_CODES,
    )
    period.refresh_from_db()
    assert submitted.outcome == "committed"
    assert period.status == SalaryPeriod.Status.PENDING_APPROVAL

    cancelled_draft = _period(gym, "  Cancel payroll  ")
    cancelled_created = _run(
        gym, owner, goal, _CREATE, cancelled_draft, submitted.version, codes=_CREATE_CODES
    )
    cancelled = _run(
        gym,
        owner,
        goal,
        _CANCEL,
        {"salary_period_id": str(cancelled_created.effect_reference), "reason": "Duplicate"},
        cancelled_created.version,
        codes=_UPDATE_CODES,
    )
    cancelled_period = SalaryPeriod.objects.get(id=cancelled.effect_reference)
    assert cancelled.outcome == "committed"
    assert cancelled_period.status == SalaryPeriod.Status.CANCELLED
    assert cancelled_period.reason == "Duplicate"

    void_draft = _period(gym, "  Void payroll  ")
    void_created = _run(
        gym, owner, goal, _CREATE, void_draft, cancelled.version, codes=_CREATE_CODES
    )
    void_lines = {
        "salary_period_id": str(void_created.effect_reference),
        "lines": [_line(gym, profile.id, "800.00")],
    }
    void_replaced = _run(
        gym, owner, goal, _LINES, void_lines, void_created.version, codes=_UPDATE_CODES
    )
    void_submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"salary_period_id": str(void_created.effect_reference)},
        void_replaced.version,
        codes=_SUBMIT_CODES,
    )
    void_period_id = UUID(str(void_created.effect_reference))
    _approve(gym, owner, void_period_id)
    posted = SalaryPeriod.objects.get(id=void_period_id)
    posted_line = StaffSalaryLine.objects.get(salary_period=posted)
    charge = Expense.objects.get(id=posted_line.expense_id)
    assert posted.status == SalaryPeriod.Status.POSTED
    assert charge.amount == Decimal("800.00")
    assert Expense.objects.filter(gym=gym, original_expense_id=charge.id).count() == 0
    void_payload = {"salary_period_id": str(posted.id), "reason": "Wrong period"}
    voided = _run(gym, owner, goal, _VOID, void_payload, void_submitted.version, codes=_VOID_CODES)
    voided_again = _run(gym, owner, goal, _VOID, void_payload, voided.version, codes=_VOID_CODES)
    posted.refresh_from_db()
    charge.refresh_from_db()
    reversal = Expense.objects.get(original_expense_id=charge.id)
    void_serializer = VoidSalarySerializer(data={"reason": "Wrong period"})
    void_serializer.is_valid(raise_exception=True)
    void_view = {
        "salary_period_id": str(posted.id),
        "reason": void_serializer.validated_data["reason"],
        "business_date": void_serializer.validated_data.get("business_date"),
        "cash_sessions": list(void_serializer.validated_data.get("cash_sessions", [])),
    }
    void_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=voided.execution_identity)
    assert voided.outcome == "committed"
    assert voided_again.outcome == "existing"
    assert voided.execution_identity == _identity(
        goal, _VOID, void_salary_period_command(void_payload)
    )
    assert voided.effect_reference == str(posted.id)
    assert posted.status == SalaryPeriod.Status.VOID
    assert charge.status == Expense.Status.VOID
    assert reversal.amount == Decimal("800.00")
    assert Expense.objects.filter(gym=gym, original_expense_id=charge.id).count() == 1
    assert void_stored.resource_type == "SalaryPeriod"
    assert str(void_stored.resource_id) == str(posted.id)
    assert void_stored.route == command_route(period_id=posted.id, action="void")
    assert void_stored.http_method == "POST"
    assert void_stored.actor_member_id is None
    assert void_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=void_stored.route,
        body=void_view,
    )
