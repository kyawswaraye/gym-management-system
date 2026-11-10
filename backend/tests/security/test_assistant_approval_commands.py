"""PostgreSQL coverage for finance approval confirmation and replay."""

from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.expense_tools import (
    approve_expense_command,
    reject_expense_command,
)
from apps.assistant.expense_tools import (
    execute_registered_tool as run_expense,
)
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, WorkflowStep
from apps.assistant.payment_tools import (
    approve_payment_command,
    reject_payment_command,
)
from apps.assistant.payment_tools import (
    execute_registered_tool as run_payment,
)
from apps.assistant.pt_tools import (
    approve_pt_sale_command,
    reject_pt_sale_command,
)
from apps.assistant.pt_tools import (
    execute_registered_tool as run_pt,
)
from apps.assistant.retail_tools import (
    approve_retail_sale_command,
    reject_retail_sale_command,
)
from apps.assistant.retail_tools import (
    execute_registered_tool as run_retail,
)
from apps.assistant.salary_tools import (
    approve_salary_period_command,
    reject_salary_period_command,
)
from apps.assistant.salary_tools import (
    execute_registered_tool as run_salary,
)
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.expenses.models import Expense
from apps.expenses.services import command_route as expense_route
from apps.inventory.services import adjust_stock, create_category, create_product
from apps.members.models import Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.members.payment_services import command_route as payment_route
from apps.members.services import gym_today
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.salaries.services import command_route as salary_route
from apps.sales.models import RetailSale
from apps.sales.services import command_route as retail_route
from apps.tenants.models import ExpenseCategory, Gym, GymMembership, IdempotencyRecord
from apps.trainers.models import PTCommission, PTSale
from apps.trainers.pt_sale_services import command_route as pt_route
from tests.security.test_assistant_schedule import _enable, _goal, _profile
from tests.security.test_pt_sales import _client
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _member

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


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
    request.request_id = "req_approval_tool"
    return request


def _run(execute, gym, actor, goal, tool_name, payload, version, *, codes, confirmed=True):
    return execute(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=version,
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


def _claims(gym: Gym) -> int:
    return IdempotencyRecord.objects.filter(gym=gym).count()


def _reject_mismatch(execute, gym, actor, goal, tool_name, payload, version, *, codes) -> None:
    before = _claims(gym)
    with pytest.raises(ExecutionRejected) as caught:
        _run(execute, gym, actor, goal, tool_name, payload, version, codes=codes)
    assert caught.value.code == "approval_mismatch"
    assert _claims(gym) == before


def _stock(gym: Gym, actor: GymMembership) -> UUID:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=actor,
            fields={"code": "WATER", "name": "Water"},
            request_id="req_approval_tool",
            ip_address=None,
            user_agent="",
        )
        product = create_product(
            gym=gym,
            actor=actor,
            fields={
                "category_id": category.id,
                "sku": "WATER1",
                "name": "Water",
                "unit": "BTL",
                "sale_price": "10.00",
                "current_unit_cost": "4.00",
                "default_unit_cost": "4.00",
                "low_stock_threshold": "0.000",
            },
            request_id="req_approval_tool",
            ip_address=None,
            user_agent="",
        )
        adjust_stock(
            gym=gym,
            actor=actor,
            fields={
                "product_id": product.id,
                "quantity": "5.000",
                "movement_type": "OPENING",
                "business_date": gym_today(gym),
                "reason": "Opening count",
            },
            request_id="req_approval_tool",
            ip_address=None,
            user_agent="",
        )
    return product.id


@override_settings(**jwt_settings())
def test_approval_mismatch_does_not_claim_and_replay_posts_once() -> None:
    client, token = platform_token("apv1-platform@example.test")
    payload = provision(client, token, code="APV1", owner_email="apv1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    version = 1
    today = gym_today(gym).isoformat()

    payment_draft = {
        "purpose": "OTHER",
        "description": "Approval day pass",
        "amount": "40.00",
        "currency": gym.currency,
        "method": "BANK_TRANSFER",
        "business_date": today,
    }
    created = _run(
        run_payment,
        gym,
        owner,
        goal,
        "create_payment",
        payment_draft,
        version,
        codes=frozenset({"payments.create"}),
    )
    version = created.version
    submitted = _run(
        run_payment,
        gym,
        owner,
        goal,
        "submit_payment",
        {"payment_id": str(created.effect_reference)},
        version,
        codes=frozenset({"payments.submit"}),
    )
    version = submitted.version
    payment = Payment.objects.get(id=created.effect_reference)
    amount = money_text(payment.amount)
    approve_payload = {
        "payment_id": str(payment.id),
        "record_id": str(payment.id),
        "amount": amount,
        "confirmed": True,
    }
    before = _claims(gym)
    with pytest.raises(ExecutionRejected) as unconfirmed:
        _run(
            run_payment,
            gym,
            owner,
            goal,
            "approve_payment",
            approve_payload,
            version,
            codes=frozenset({"payments.approve"}),
            confirmed=False,
        )
    assert unconfirmed.value.code == "confirmation_required"
    assert _claims(gym) == before
    _reject_mismatch(
        run_payment,
        gym,
        owner,
        goal,
        "approve_payment",
        {**approve_payload, "record_id": str(uuid4())},
        version,
        codes=frozenset({"payments.approve"}),
    )
    _reject_mismatch(
        run_payment,
        gym,
        owner,
        goal,
        "approve_payment",
        {**approve_payload, "amount": "40.0"},
        version,
        codes=frozenset({"payments.approve"}),
    )
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING_APPROVAL
    approved = _run(
        run_payment,
        gym,
        owner,
        goal,
        "approve_payment",
        approve_payload,
        version,
        codes=frozenset({"payments.approve"}),
    )
    replayed = _run(
        run_payment,
        gym,
        owner,
        goal,
        "approve_payment",
        approve_payload,
        approved.version,
        codes=frozenset({"payments.approve"}),
    )
    payment.refresh_from_db()
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=approved.execution_identity)
    assert approved.outcome == "committed"
    assert replayed.outcome == "existing"
    assert approved.execution_identity == _identity(
        goal, "approve_payment", approve_payment_command(approve_payload)
    )
    assert payment.status == Payment.Status.POSTED
    assert Receipt.objects.filter(gym=gym, payment_id=payment.id).count() == 1
    assert stored.http_method == "POST"
    assert stored.route == payment_route(payment_id=payment.id, action="approve")
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=stored.route,
        body={"payment_id": str(payment.id), "cash_session_id": None},
    )
    version = replayed.version

    reject_draft = {**payment_draft, "description": "Reject day pass", "amount": "12.00"}
    reject_created = _run(
        run_payment,
        gym,
        owner,
        goal,
        "create_payment",
        reject_draft,
        version,
        codes=frozenset({"payments.create"}),
    )
    reject_submitted = _run(
        run_payment,
        gym,
        owner,
        goal,
        "submit_payment",
        {"payment_id": str(reject_created.effect_reference)},
        reject_created.version,
        codes=frozenset({"payments.submit"}),
    )
    rejected_payment = Payment.objects.get(id=reject_created.effect_reference)
    reject_payload = {
        "payment_id": str(rejected_payment.id),
        "rejection_reason": "Duplicate",
        "record_id": str(rejected_payment.id),
        "amount": money_text(rejected_payment.amount),
    }
    rejected = _run(
        run_payment,
        gym,
        owner,
        goal,
        "reject_payment",
        reject_payload,
        reject_submitted.version,
        codes=frozenset({"payments.approve"}),
    )
    rejected_again = _run(
        run_payment,
        gym,
        owner,
        goal,
        "reject_payment",
        reject_payload,
        rejected.version,
        codes=frozenset({"payments.approve"}),
    )
    rejected_payment.refresh_from_db()
    assert rejected.outcome == "committed"
    assert rejected_again.outcome == "existing"
    assert rejected.execution_identity == _identity(
        goal, "reject_payment", reject_payment_command(reject_payload)
    )
    assert rejected_payment.status == Payment.Status.REJECTED
    assert Receipt.objects.filter(gym=gym, payment_id=rejected_payment.id).count() == 0
    version = rejected_again.version

    category = _run(
        run_expense,
        gym,
        owner,
        goal,
        "create_expense_category",
        {"code": "SUPPLIES", "name": "Supplies"},
        version,
        codes=frozenset({"expense_categories.manage"}),
    )
    version = category.version
    expense_draft = {
        "category_id": str(category.effect_reference),
        "business_date": today,
        "description": "Approval paper",
        "amount": "25.00",
        "currency": gym.currency,
        "payment_method": "BANK_TRANSFER",
    }
    expense_created = _run(
        run_expense,
        gym,
        owner,
        goal,
        "create_expense",
        expense_draft,
        version,
        codes=frozenset({"expenses.create"}),
    )
    expense_submitted = _run(
        run_expense,
        gym,
        owner,
        goal,
        "submit_expense",
        {"expense_id": str(expense_created.effect_reference)},
        expense_created.version,
        codes=frozenset({"expenses.submit"}),
    )
    expense = Expense.objects.get(id=expense_created.effect_reference)
    expense_payload = {
        "expense_id": str(expense.id),
        "record_id": str(expense.id),
        "amount": money_text(expense.amount),
    }
    _reject_mismatch(
        run_expense,
        gym,
        owner,
        goal,
        "approve_expense",
        {**expense_payload, "record_id": str(uuid4())},
        expense_submitted.version,
        codes=frozenset({"expenses.approve"}),
    )
    _reject_mismatch(
        run_expense,
        gym,
        owner,
        goal,
        "approve_expense",
        {**expense_payload, "amount": "25.0"},
        expense_submitted.version,
        codes=frozenset({"expenses.approve"}),
    )
    expense.refresh_from_db()
    assert expense.status == Expense.Status.PENDING_APPROVAL
    expense_approved = _run(
        run_expense,
        gym,
        owner,
        goal,
        "approve_expense",
        expense_payload,
        expense_submitted.version,
        codes=frozenset({"expenses.approve"}),
    )
    expense_again = _run(
        run_expense,
        gym,
        owner,
        goal,
        "approve_expense",
        expense_payload,
        expense_approved.version,
        codes=frozenset({"expenses.approve"}),
    )
    expense.refresh_from_db()
    expense_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=expense_approved.execution_identity
    )
    assert expense_again.outcome == "existing"
    assert expense_approved.execution_identity == _identity(
        goal, "approve_expense", approve_expense_command(expense_payload)
    )
    assert expense.status == Expense.Status.POSTED
    assert Expense.objects.filter(gym=gym, id=expense.id, status=Expense.Status.POSTED).count() == 1
    assert expense_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=expense_route(expense_id=expense.id, action="approve"),
        body={"expense_id": str(expense.id), "cash_session_id": None},
    )
    reject_expense_draft = {**expense_draft, "description": "Reject paper", "amount": "8.00"}
    reject_expense_created = _run(
        run_expense,
        gym,
        owner,
        goal,
        "create_expense",
        reject_expense_draft,
        expense_again.version,
        codes=frozenset({"expenses.create"}),
    )
    reject_expense_submitted = _run(
        run_expense,
        gym,
        owner,
        goal,
        "submit_expense",
        {"expense_id": str(reject_expense_created.effect_reference)},
        reject_expense_created.version,
        codes=frozenset({"expenses.submit"}),
    )
    other_expense = Expense.objects.get(id=reject_expense_created.effect_reference)
    expense_reject_payload = {
        "expense_id": str(other_expense.id),
        "rejection_reason": "Duplicate",
        "record_id": str(other_expense.id),
        "amount": money_text(other_expense.amount),
    }
    expense_rejected = _run(
        run_expense,
        gym,
        owner,
        goal,
        "reject_expense",
        expense_reject_payload,
        reject_expense_submitted.version,
        codes=frozenset({"expenses.approve"}),
    )
    expense_rejected_again = _run(
        run_expense,
        gym,
        owner,
        goal,
        "reject_expense",
        expense_reject_payload,
        expense_rejected.version,
        codes=frozenset({"expenses.approve"}),
    )
    other_expense.refresh_from_db()
    assert expense_rejected_again.outcome == "existing"
    assert expense_rejected.execution_identity == _identity(
        goal, "reject_expense", reject_expense_command(expense_reject_payload)
    )
    assert other_expense.status == Expense.Status.REJECTED
    version = expense_rejected_again.version

    product_id = _stock(gym, owner)
    sale_draft = {
        "business_date": today,
        "payment_method": "CARD",
        "reference_number": "POS-APPROVE",
        "lines": [{"product_id": str(product_id), "quantity": "1.000", "discount": "0.00"}],
    }
    sale_created = _run(
        run_retail,
        gym,
        owner,
        goal,
        "create_retail_sale",
        sale_draft,
        version,
        codes=frozenset({"retail.create"}),
    )
    sale_submitted = _run(
        run_retail,
        gym,
        owner,
        goal,
        "submit_retail_sale",
        {"retail_sale_id": str(sale_created.effect_reference)},
        sale_created.version,
        codes=frozenset({"retail.submit"}),
    )
    sale = RetailSale.objects.get(id=sale_created.effect_reference)
    sale_amount = money_text(sale.total_amount)
    sale_payload = {
        "retail_sale_id": str(sale.id),
        "record_id": str(sale.id),
        "amount": sale_amount,
    }
    _reject_mismatch(
        run_retail,
        gym,
        owner,
        goal,
        "approve_retail_sale",
        {**sale_payload, "amount": "0.01"},
        sale_submitted.version,
        codes=frozenset({"retail.approve"}),
    )
    sale.refresh_from_db()
    assert sale.status == RetailSale.Status.PENDING_APPROVAL
    sale_approved = _run(
        run_retail,
        gym,
        owner,
        goal,
        "approve_retail_sale",
        sale_payload,
        sale_submitted.version,
        codes=frozenset({"retail.approve"}),
    )
    sale_again = _run(
        run_retail,
        gym,
        owner,
        goal,
        "approve_retail_sale",
        sale_payload,
        sale_approved.version,
        codes=frozenset({"retail.approve"}),
    )
    sale.refresh_from_db()
    sale_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=sale_approved.execution_identity
    )
    assert sale_again.outcome == "existing"
    assert sale_approved.execution_identity == _identity(
        goal, "approve_retail_sale", approve_retail_sale_command(sale_payload)
    )
    assert sale.status == RetailSale.Status.POSTED
    assert Payment.objects.filter(gym=gym, retail_sale_id=sale.id).count() == 1
    assert sale_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=retail_route(sale_id=sale.id, action="approve"),
        body={"retail_sale_id": str(sale.id), "cash_session_id": None},
    )
    reject_sale_draft = {**sale_draft, "reference_number": "POS-REJECT"}
    reject_sale_created = _run(
        run_retail,
        gym,
        owner,
        goal,
        "create_retail_sale",
        reject_sale_draft,
        sale_again.version,
        codes=frozenset({"retail.create"}),
    )
    reject_sale_submitted = _run(
        run_retail,
        gym,
        owner,
        goal,
        "submit_retail_sale",
        {"retail_sale_id": str(reject_sale_created.effect_reference)},
        reject_sale_created.version,
        codes=frozenset({"retail.submit"}),
    )
    other_sale = RetailSale.objects.get(id=reject_sale_created.effect_reference)
    sale_reject_payload = {
        "retail_sale_id": str(other_sale.id),
        "rejection_reason": "Duplicate",
        "record_id": str(other_sale.id),
        "amount": money_text(other_sale.total_amount),
    }
    sale_rejected = _run(
        run_retail,
        gym,
        owner,
        goal,
        "reject_retail_sale",
        sale_reject_payload,
        reject_sale_submitted.version,
        codes=frozenset({"retail.approve"}),
    )
    sale_rejected_again = _run(
        run_retail,
        gym,
        owner,
        goal,
        "reject_retail_sale",
        sale_reject_payload,
        sale_rejected.version,
        codes=frozenset({"retail.approve"}),
    )
    other_sale.refresh_from_db()
    assert sale_rejected_again.outcome == "existing"
    assert sale_rejected.execution_identity == _identity(
        goal, "reject_retail_sale", reject_retail_sale_command(sale_reject_payload)
    )
    assert other_sale.status == RetailSale.Status.REJECTED
    assert Payment.objects.filter(gym=gym, retail_sale_id=other_sale.id).count() == 0
    version = sale_rejected_again.version

    first_payee = add_staff(gym, email="apv1-payee@example.test", role_key="RECEPTIONIST")
    second_payee = add_staff(gym, email="apv1-other@example.test", role_key="RECEPTIONIST")
    first_profile = _profile(gym, first_payee, code="PAY1", name="Payee")
    second_profile = _profile(gym, second_payee, code="PAY2", name="Other")
    period_created = _run(
        run_salary,
        gym,
        owner,
        goal,
        "create_salary_period",
        {
            "period_start": (gym_today(gym) - timedelta(days=6)).isoformat(),
            "period_end": today,
            "business_date": today,
            "description": "Approval payroll",
        },
        version,
        codes=frozenset({"salaries.create"}),
    )
    lines = {
        "salary_period_id": str(period_created.effect_reference),
        "lines": [
            {
                "staff_profile_id": str(first_profile.id),
                "amount": "100.00",
                "currency": gym.currency,
                "payment_method": "BANK_TRANSFER",
                "description": "First line",
            },
            {
                "staff_profile_id": str(second_profile.id),
                "amount": "25.50",
                "currency": gym.currency,
                "payment_method": "BANK_TRANSFER",
                "description": "Second line",
            },
        ],
    }
    replaced = _run(
        run_salary,
        gym,
        owner,
        goal,
        "replace_salary_lines",
        lines,
        period_created.version,
        codes=frozenset({"salaries.update"}),
    )
    period_submitted = _run(
        run_salary,
        gym,
        owner,
        goal,
        "submit_salary_period",
        {"salary_period_id": str(period_created.effect_reference)},
        replaced.version,
        codes=frozenset({"salaries.submit"}),
    )
    period = SalaryPeriod.objects.get(id=period_created.effect_reference)
    total = sum(
        (line.amount for line in StaffSalaryLine.objects.filter(salary_period=period)),
        Decimal("0.00"),
    )
    assert money_text(total) == "125.50"
    period_payload = {
        "salary_period_id": str(period.id),
        "record_id": str(period.id),
        "amount": money_text(total),
    }
    _reject_mismatch(
        run_salary,
        gym,
        owner,
        goal,
        "approve_salary_period",
        {**period_payload, "amount": "100.00"},
        period_submitted.version,
        codes=frozenset({"salaries.approve"}),
    )
    period.refresh_from_db()
    assert period.status == SalaryPeriod.Status.PENDING_APPROVAL
    period_approved = _run(
        run_salary,
        gym,
        owner,
        goal,
        "approve_salary_period",
        period_payload,
        period_submitted.version,
        codes=frozenset({"salaries.approve"}),
    )
    period_again = _run(
        run_salary,
        gym,
        owner,
        goal,
        "approve_salary_period",
        period_payload,
        period_approved.version,
        codes=frozenset({"salaries.approve"}),
    )
    period.refresh_from_db()
    assert period_again.outcome == "existing"
    assert period_approved.execution_identity == _identity(
        goal, "approve_salary_period", approve_salary_period_command(period_payload)
    )
    assert period.status == SalaryPeriod.Status.POSTED
    assert StaffSalaryLine.objects.filter(salary_period=period, expense__isnull=False).count() == 2
    assert (
        IdempotencyRecord.objects.filter(
            gym=gym, idempotency_key=period_approved.execution_identity
        ).count()
        == 1
    )
    assert IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=period_approved.execution_identity
    ).request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=salary_route(period_id=period.id, action="approve"),
        body={"salary_period_id": str(period.id), "cash_sessions": []},
    )
    reject_period_created = _run(
        run_salary,
        gym,
        owner,
        goal,
        "create_salary_period",
        {
            "period_start": (gym_today(gym) - timedelta(days=6)).isoformat(),
            "period_end": today,
            "business_date": today,
            "description": "Reject payroll",
        },
        period_again.version,
        codes=frozenset({"salaries.create"}),
    )
    reject_lines = {
        "salary_period_id": str(reject_period_created.effect_reference),
        "lines": [lines["lines"][0]],
    }
    reject_replaced = _run(
        run_salary,
        gym,
        owner,
        goal,
        "replace_salary_lines",
        reject_lines,
        reject_period_created.version,
        codes=frozenset({"salaries.update"}),
    )
    reject_period_submitted = _run(
        run_salary,
        gym,
        owner,
        goal,
        "submit_salary_period",
        {"salary_period_id": str(reject_period_created.effect_reference)},
        reject_replaced.version,
        codes=frozenset({"salaries.submit"}),
    )
    other_period = SalaryPeriod.objects.get(id=reject_period_created.effect_reference)
    other_total = sum(
        (line.amount for line in StaffSalaryLine.objects.filter(salary_period=other_period)),
        Decimal("0.00"),
    )
    period_reject_payload = {
        "salary_period_id": str(other_period.id),
        "rejection_reason": "Duplicate",
        "record_id": str(other_period.id),
        "amount": money_text(other_total),
    }
    period_rejected = _run(
        run_salary,
        gym,
        owner,
        goal,
        "reject_salary_period",
        period_reject_payload,
        reject_period_submitted.version,
        codes=frozenset({"salaries.approve"}),
    )
    period_rejected_again = _run(
        run_salary,
        gym,
        owner,
        goal,
        "reject_salary_period",
        period_reject_payload,
        period_rejected.version,
        codes=frozenset({"salaries.approve"}),
    )
    other_period.refresh_from_db()
    assert period_rejected_again.outcome == "existing"
    assert period_rejected.execution_identity == _identity(
        goal, "reject_salary_period", reject_salary_period_command(period_reject_payload)
    )
    assert other_period.status == SalaryPeriod.Status.REJECTED
    version = period_rejected_again.version

    trainer_membership = add_staff(gym, email="apv1-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="APVADA", name="Ada")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ada.default_commission_percent = Decimal("10.00")
        ada.save(update_fields=["default_commission_percent", "updated_at"])
    api = _client()
    headers = auth_headers(staff_token(owner))
    member_id = _member(api, headers, first_name="Nila")
    assigned = _assign(
        api,
        headers,
        trainer_id=str(ada.id),
        member_id=member_id,
        start=(gym_today(gym) - timedelta(days=1)).isoformat(),
    )
    assert assigned.status_code == 201, assigned.json()
    pt_draft = {
        "member_id": member_id,
        "trainer_staff_profile_id": str(ada.id),
        "service_date": today,
        "description": "Approval session",
        "gross_fee": "100.00",
        "currency": gym.currency,
    }
    pt_created = _run(
        run_pt,
        gym,
        owner,
        goal,
        "create_pt_sale",
        pt_draft,
        version,
        codes=frozenset({"pt.sales.create"}),
    )
    pt_submitted = _run(
        run_pt,
        gym,
        owner,
        goal,
        "submit_pt_sale",
        {"pt_sale_id": str(pt_created.effect_reference)},
        pt_created.version,
        codes=frozenset({"pt.sales.submit"}),
    )
    pt_sale = PTSale.objects.get(id=pt_created.effect_reference)
    pt_payload = {
        "pt_sale_id": str(pt_sale.id),
        "record_id": str(pt_sale.id),
        "amount": money_text(pt_sale.gross_fee),
    }
    _reject_mismatch(
        run_pt,
        gym,
        owner,
        goal,
        "approve_pt_sale",
        {**pt_payload, "record_id": str(uuid4())},
        pt_submitted.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    _reject_mismatch(
        run_pt,
        gym,
        owner,
        goal,
        "approve_pt_sale",
        {**pt_payload, "amount": "100.0"},
        pt_submitted.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    pt_sale.refresh_from_db()
    assert pt_sale.status == PTSale.Status.PENDING_APPROVAL
    assert PTCommission.objects.filter(pt_sale=pt_sale).count() == 0
    pt_approved = _run(
        run_pt,
        gym,
        owner,
        goal,
        "approve_pt_sale",
        pt_payload,
        pt_submitted.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    pt_again = _run(
        run_pt,
        gym,
        owner,
        goal,
        "approve_pt_sale",
        pt_payload,
        pt_approved.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    pt_sale.refresh_from_db()
    pt_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=pt_approved.execution_identity
    )
    assert pt_again.outcome == "existing"
    assert pt_approved.execution_identity == _identity(
        goal, "approve_pt_sale", approve_pt_sale_command(pt_payload)
    )
    assert pt_sale.status == PTSale.Status.POSTED
    assert PTCommission.objects.filter(pt_sale=pt_sale).count() == 1
    assert pt_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=pt_route(sale_id=pt_sale.id, action="approve"),
        body={"cash_session_id": None},
    )
    reject_pt_draft = {**pt_draft, "description": "Reject session", "gross_fee": "80.00"}
    reject_pt_created = _run(
        run_pt,
        gym,
        owner,
        goal,
        "create_pt_sale",
        reject_pt_draft,
        pt_again.version,
        codes=frozenset({"pt.sales.create"}),
    )
    reject_pt_submitted = _run(
        run_pt,
        gym,
        owner,
        goal,
        "submit_pt_sale",
        {"pt_sale_id": str(reject_pt_created.effect_reference)},
        reject_pt_created.version,
        codes=frozenset({"pt.sales.submit"}),
    )
    other_pt = PTSale.objects.get(id=reject_pt_created.effect_reference)
    pt_reject_payload = {
        "pt_sale_id": str(other_pt.id),
        "rejection_reason": "Duplicate",
        "record_id": str(other_pt.id),
        "amount": money_text(other_pt.gross_fee),
    }
    pt_rejected = _run(
        run_pt,
        gym,
        owner,
        goal,
        "reject_pt_sale",
        pt_reject_payload,
        reject_pt_submitted.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    pt_rejected_again = _run(
        run_pt,
        gym,
        owner,
        goal,
        "reject_pt_sale",
        pt_reject_payload,
        pt_rejected.version,
        codes=frozenset({"pt.sales.approve"}),
    )
    other_pt.refresh_from_db()
    assert pt_rejected_again.outcome == "existing"
    assert pt_rejected.execution_identity == _identity(
        goal, "reject_pt_sale", reject_pt_sale_command(pt_reject_payload)
    )
    assert other_pt.status == PTSale.Status.REJECTED
    assert PTCommission.objects.filter(pt_sale=other_pt).count() == 0
    assert ExpenseCategory.objects.filter(gym=gym, code="SUPPLIES").count() == 1
