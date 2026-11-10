"""PostgreSQL security coverage for expense tools."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

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
from apps.assistant.expense_tools import (
    create_category_command,
    create_expense_command,
    execute_registered_tool,
    void_expense_command,
)
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, WorkflowStep
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.expenses.exceptions import ExpensePostingConflict, ProtectedExpenseCategory
from apps.expenses.models import Expense
from apps.expenses.serializers import ExpenseCategoryWriteSerializer, ExpenseWriteSerializer
from apps.expenses.services import approve_expense, category_route, command_route
from apps.members.services import gym_today
from apps.tenants.models import ExpenseCategory, Gym, GymMembership, GymSettings, IdempotencyRecord
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

_CATEGORY = "create_expense_category"
_CATEGORY_UPDATE = "update_expense_category"
_CREATE = "create_expense"
_UPDATE = "update_expense"
_SUBMIT = "submit_expense"
_CANCEL = "cancel_expense"
_VOID = "void_expense"
_MANAGE = frozenset({"expense_categories.manage"})
_CREATE_CODES = frozenset({"expenses.create"})
_UPDATE_CODES = frozenset({"expenses.update"})
_SUBMIT_CODES = frozenset({"expenses.submit"})
_VOID_CODES = frozenset({"expenses.void"})


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
    request.request_id = "req_expense_tool"
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


def _category() -> dict[str, object]:
    return {"code": "SUPPLIES", "name": "  Supplies  "}


def _expense(gym: Gym, category_id: UUID, amount: str = "25.00") -> dict[str, object]:
    return {
        "category_id": str(category_id),
        "business_date": gym_today(gym).isoformat(),
        "description": "  Paper  ",
        "amount": amount,
        "currency": gym.currency,
        "payment_method": "BANK_TRANSFER",
    }


def _approve(gym: Gym, actor: GymMembership, expense_id: UUID) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        approve_expense(
            gym=gym,
            actor=actor,
            expense_id=expense_id,
            cash_session_id=None,
            request_id="req_expense_tool",
            ip_address=None,
            user_agent="",
        )


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_route_stop_before_finance() -> None:
    client, token = platform_token("exp1-platform@example.test")
    payload = provision(client, token, code="EXP1", owner_email="exp1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="exp1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    category = _category()
    for tool_name, command, codes in (
        (_CATEGORY, category, frozenset()),
        (_CATEGORY, category, frozenset({"expenses.view"})),
        (_CREATE, _expense(gym, UUID(int=1)), _MANAGE),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _CATEGORY, category, 1, codes=_MANAGE)
    bare_user = User.objects.create_user("exp1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="EXPB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _CATEGORY, category, 1, codes=_MANAGE)
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(
            gym,
            owner,
            goal,
            _CATEGORY,
            {**category, "gym_id": str(UUID(int=2))},
            1,
            codes=_MANAGE,
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as missing:
        _run(gym, owner, goal, _SUBMIT, {}, 1, codes=_SUBMIT_CODES)
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as bad_route:
        _run(
            gym,
            owner,
            goal,
            _CATEGORY_UPDATE,
            {"category_id": "not-a-uuid", "name": "Supplies"},
            1,
            codes=_MANAGE,
        )
    assert bad_route.value.code == "finance_route"
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CATEGORY, {"name": "   "}, 1, codes=_MANAGE)
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert ExpenseCategory.objects.filter(gym=gym, code="SUPPLIES").count() == 0


@override_settings(**jwt_settings())
def test_expense_draft_update_cancel_and_void_replay() -> None:
    client, token = platform_token("exp2-platform@example.test")
    payload = provision(client, token, code="EXP2", owner_email="exp2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    reserved = {"code": "STAFF_SALARY", "name": "Payroll"}
    with pytest.raises(ProtectedExpenseCategory):
        _run(gym, owner, goal, _CATEGORY, reserved, 1, codes=_MANAGE)
    with pytest.raises(ExecutionRejected) as reserved_failed:
        _run(gym, owner, goal, _CATEGORY, reserved, 1, codes=_MANAGE)
    assert reserved_failed.value.code == "finance_failed"
    seeded = ExpenseCategory.objects.get(gym=gym, code="STAFF_SALARY")
    assert seeded.system_key == "STAFF_SALARY"
    assert ExpenseCategory.objects.filter(gym=gym, code="STAFF_SALARY").count() == 1

    category_payload = _category()
    created = _run(gym, owner, goal, _CATEGORY, category_payload, 1, codes=_MANAGE)
    created_again = _run(
        gym, owner, goal, _CATEGORY, category_payload, created.version, codes=_MANAGE
    )
    category = ExpenseCategory.objects.get(id=created.effect_reference)
    category_serializer = ExpenseCategoryWriteSerializer(data=category_payload)
    category_serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(
        goal, _CATEGORY, create_category_command(category_payload)
    )
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == category_route(category_id=None)
    assert stored.resource_type == "ExpenseCategory"
    assert str(stored.resource_id) == str(category.id)
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=category_route(category_id=None),
        body=dict(category_serializer.validated_data),
    )
    assert category.code == "SUPPLIES"
    assert category.name == "Supplies"

    renamed = {"category_id": str(category.id).upper(), "name": "  Office supplies  "}
    updated_category = _run(
        gym, owner, goal, _CATEGORY_UPDATE, renamed, created.version, codes=_MANAGE
    )
    category.refresh_from_db()
    assert updated_category.outcome == "committed"
    assert category.name == "Office supplies"
    assert IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=updated_category.execution_identity
    ).route == category_route(category_id=category.id)

    mismatched = _expense(gym, category.id)
    mismatched["currency"] = "USD"
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, mismatched, updated_category.version, codes=_CREATE_CODES)
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, mismatched, updated_category.version, codes=_CREATE_CODES)
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _CREATE, create_expense_command(mismatched)),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    assert Expense.objects.filter(gym=gym).count() == 0

    draft = _expense(gym, category.id)
    expense_created = _run(
        gym, owner, goal, _CREATE, draft, updated_category.version, codes=_CREATE_CODES
    )
    expense_again = _run(
        gym, owner, goal, _CREATE, draft, expense_created.version, codes=_CREATE_CODES
    )
    expense = Expense.objects.get(id=expense_created.effect_reference)
    expense_serializer = ExpenseWriteSerializer(data=draft)
    expense_serializer.is_valid(raise_exception=True)
    expense_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=expense_created.execution_identity
    )
    assert expense_created.outcome == "committed"
    assert expense_again.outcome == "existing"
    assert expense.status == Expense.Status.DRAFT
    assert expense.description == "Paper"
    assert expense.amount == Decimal("25.00")
    assert expense_stored.resource_type == "Expense"
    assert expense_stored.route == command_route(expense_id=None, action="")
    assert expense_stored.actor_member_id is None
    assert expense_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=command_route(expense_id=None, action=""),
        body=dict(expense_serializer.validated_data),
    )

    description = {"expense_id": str(expense.id).upper(), "description": "  Toner  "}
    updated = _run(
        gym, owner, goal, _UPDATE, description, expense_created.version, codes=_UPDATE_CODES
    )
    expense.refresh_from_db()
    assert updated.outcome == "committed"
    assert expense.description == "Toner"

    early = {"expense_id": str(expense.id), "reason": "Too early"}
    with pytest.raises(ExpensePostingConflict):
        _run(gym, owner, goal, _VOID, early, updated.version, codes=_VOID_CODES)
    with pytest.raises(ExecutionRejected) as early_failed:
        _run(gym, owner, goal, _VOID, early, updated.version, codes=_VOID_CODES)
    assert early_failed.value.code == "finance_failed"
    expense.refresh_from_db()
    assert expense.status == Expense.Status.DRAFT
    assert Expense.objects.filter(gym=gym, original_expense_id=expense.id).count() == 0

    submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"expense_id": str(expense.id)},
        updated.version,
        codes=_SUBMIT_CODES,
    )
    expense.refresh_from_db()
    assert submitted.outcome == "committed"
    assert expense.status == Expense.Status.PENDING_APPROVAL

    other = _expense(gym, category.id, "10.00")
    other_created = _run(gym, owner, goal, _CREATE, other, submitted.version, codes=_CREATE_CODES)
    cancelled = _run(
        gym,
        owner,
        goal,
        _CANCEL,
        {"expense_id": str(other_created.effect_reference), "reason": "Duplicate"},
        other_created.version,
        codes=_UPDATE_CODES,
    )
    cancelled_expense = Expense.objects.get(id=cancelled.effect_reference)
    assert cancelled.outcome == "committed"
    assert cancelled_expense.status == Expense.Status.CANCELLED
    assert cancelled_expense.reason == "Duplicate"

    _approve(gym, owner, expense.id)
    expense.refresh_from_db()
    assert expense.status == Expense.Status.POSTED
    void_payload = {"expense_id": str(expense.id), "reason": "Wrong vendor"}
    voided = _run(gym, owner, goal, _VOID, void_payload, cancelled.version, codes=_VOID_CODES)
    voided_again = _run(gym, owner, goal, _VOID, void_payload, voided.version, codes=_VOID_CODES)
    expense.refresh_from_db()
    reversal = Expense.objects.get(id=voided.effect_reference)
    void_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=voided.execution_identity)
    assert voided.outcome == "committed"
    assert voided_again.outcome == "existing"
    assert voided.execution_identity == _identity(goal, _VOID, void_expense_command(void_payload))
    assert reversal.id != expense.id
    assert reversal.amount == Decimal("25.00")
    assert expense.status == Expense.Status.VOID
    assert void_stored.resource_type == "Expense"
    assert str(void_stored.resource_id) == str(reversal.id)
    assert void_stored.route == command_route(expense_id=expense.id, action="void")
    assert void_stored.actor_member_id is None
    assert Expense.objects.filter(gym=gym, original_expense_id=expense.id).count() == 1
