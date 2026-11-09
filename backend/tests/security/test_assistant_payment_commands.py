"""PostgreSQL security coverage for payment and receipt tools."""

from __future__ import annotations

import os
from datetime import date
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
from apps.assistant.payment_tools import (
    create_payment_command,
    execute_registered_tool,
    refund_payment_command,
    update_payment_command,
    void_payment_command,
    void_receipt_command,
)
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.members.models import Payment, Receipt
from apps.members.payment_serializers import PaymentWriteSerializer
from apps.members.payment_services import RefundExceedsBalance, approve_payment, command_route
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

_CREATE = "create_payment"
_UPDATE = "update_payment"
_SUBMIT = "submit_payment"
_CANCEL = "cancel_payment"
_VOID = "void_payment"
_REFUND = "refund_payment"
_RECEIPT = "void_receipt"
_CREATE_CODES = frozenset({"payments.create"})
_UPDATE_CODES = frozenset({"payments.update"})
_SUBMIT_CODES = frozenset({"payments.submit"})
_VOID_CODES = frozenset({"payments.void"})
_REFUND_CODES = frozenset({"payments.refund"})
_RECEIPT_CODES = frozenset({"receipts.void"})


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
    request.request_id = "req_payment_tool"
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


def _draft(gym: Gym, amount: str = "40.00") -> dict[str, object]:
    return {
        "purpose": "OTHER",
        "description": "  Day pass  ",
        "amount": amount,
        "currency": gym.currency,
        "method": "BANK_TRANSFER",
        "business_date": gym_today(gym).isoformat(),
    }


def _refund(payment_id: UUID, gym: Gym, amount: str) -> dict[str, object]:
    return {
        "payment_id": str(payment_id),
        "amount": amount,
        "reason": "Partial return",
        "method": "BANK_TRANSFER",
        "business_date": gym_today(gym).isoformat(),
    }


def _approve(gym: Gym, actor: GymMembership, payment_id: UUID) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        approve_payment(
            gym=gym,
            actor=actor,
            payment_id=payment_id,
            cash_session_id=None,
            request_id="req_payment_tool",
            ip_address=None,
            user_agent="",
        )


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_route_stop_before_finance() -> None:
    client, token = platform_token("pay1-platform@example.test")
    payload = provision(client, token, code="PAY1", owner_email="pay1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pay1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    draft = _draft(gym)
    for tool_name, command, codes in (
        (_CREATE, draft, frozenset()),
        (_CREATE, draft, frozenset({"payments.view"})),
        (_SUBMIT, {"payment_id": str(uuid4())}, _CREATE_CODES),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    bare_user = User.objects.create_user("pay1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="PAYB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _CREATE, _draft(bare), 1, codes=_CREATE_CODES)
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
            {"payment_id": "not-a-uuid", "description": "Desk fee"},
            1,
            codes=_UPDATE_CODES,
        )
    assert bad_route.value.code == "finance_route"
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _CREATE,
            {**draft, "purpose": "PRODUCT"},
            1,
            codes=_CREATE_CODES,
        )
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert Payment.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_payment_draft_refund_void_and_receipt_replay() -> None:
    client, token = platform_token("pay2-platform@example.test")
    payload = provision(client, token, code="PAY2", owner_email="pay2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    mismatched = {**_draft(gym), "currency": "USD"}
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, mismatched, 1, codes=_CREATE_CODES)
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, mismatched, 1, codes=_CREATE_CODES)
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _CREATE, create_payment_command(mismatched)),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    assert Payment.objects.filter(gym=gym).count() == 0

    draft = _draft(gym)
    created = _run(gym, owner, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    created_again = _run(gym, owner, goal, _CREATE, draft, created.version, codes=_CREATE_CODES)
    payment = Payment.objects.get(id=created.effect_reference)
    serializer = PaymentWriteSerializer(data=draft)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(goal, _CREATE, create_payment_command(draft))
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/payments/"
    assert stored.resource_type == "Payment"
    assert str(stored.resource_id) == str(payment.id)
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/payments/",
        body=dict(serializer.validated_data),
    )
    assert payment.status == Payment.Status.DRAFT
    assert payment.description == "Day pass"
    assert payment.amount == Decimal("40.00")
    assert Payment.objects.filter(gym=gym).count() == 1

    update_payload = {"payment_id": str(payment.id).upper(), "description": "  Desk fee  "}
    updated = _run(gym, owner, goal, _UPDATE, update_payload, created.version, codes=_UPDATE_CODES)
    updated_again = _run(
        gym, owner, goal, _UPDATE, update_payload, updated.version, codes=_UPDATE_CODES
    )
    payment.refresh_from_db()
    update_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=updated.execution_identity
    )
    assert updated.outcome == "committed"
    assert updated_again.outcome == "existing"
    assert updated.execution_identity == _identity(
        goal, _UPDATE, update_payment_command(update_payload)
    )
    assert update_stored.http_method == "PATCH"
    assert update_stored.route == command_route(payment_id=payment.id, action="")
    assert payment.description == "Desk fee"

    submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"payment_id": str(payment.id)},
        updated.version,
        codes=_SUBMIT_CODES,
    )
    payment.refresh_from_db()
    assert submitted.outcome == "committed"
    assert payment.status == Payment.Status.PENDING_APPROVAL

    cancelled_draft = _draft(gym, "10.00")
    cancelled_created = _run(
        gym, owner, goal, _CREATE, cancelled_draft, submitted.version, codes=_CREATE_CODES
    )
    cancelled = _run(
        gym,
        owner,
        goal,
        _CANCEL,
        {"payment_id": str(cancelled_created.effect_reference), "reason": "Duplicate"},
        cancelled_created.version,
        codes=_UPDATE_CODES,
    )
    cancelled_payment = Payment.objects.get(id=cancelled.effect_reference)
    assert cancelled.outcome == "committed"
    assert cancelled_payment.status == Payment.Status.CANCELLED
    assert cancelled_payment.cancellation_reason == "Duplicate"

    _approve(gym, owner, payment.id)
    payment.refresh_from_db()
    assert payment.status == Payment.Status.POSTED
    original_receipt = Receipt.objects.get(payment_id=payment.id, status=Receipt.Status.ISSUED)

    refund_payload = _refund(payment.id, gym, "15.00")
    refunded = _run(
        gym, owner, goal, _REFUND, refund_payload, cancelled.version, codes=_REFUND_CODES
    )
    refunded_again = _run(
        gym, owner, goal, _REFUND, refund_payload, refunded.version, codes=_REFUND_CODES
    )
    refund = Payment.objects.get(id=refunded.effect_reference)
    refund_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=refunded.execution_identity
    )
    payment.refresh_from_db()
    assert refunded.outcome == "committed"
    assert refunded_again.outcome == "existing"
    assert refunded.effect_reference == str(refund.id)
    assert refund.id != payment.id
    assert refund.entry_type == Payment.EntryType.REFUND
    assert refund.amount == Decimal("15.00")
    assert payment.status == Payment.Status.POSTED
    assert refund_stored.resource_type == "Payment"
    assert str(refund_stored.resource_id) == str(refund.id)
    assert refund_stored.actor_member_id is None
    assert refund_stored.route == command_route(payment_id=payment.id, action="refund")
    assert refund_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=refund_stored.route,
        body={
            key: value
            for key, value in refund_payment_command(refund_payload).items()
            if key != "payment_route_id"
        },
    )
    assert Payment.objects.filter(gym=gym, original_payment_id=payment.id).count() == 1

    excess = _refund(payment.id, gym, "30.00")
    with pytest.raises(RefundExceedsBalance):
        _run(gym, owner, goal, _REFUND, excess, refunded.version, codes=_REFUND_CODES)
    with pytest.raises(ExecutionRejected) as failed:
        _run(gym, owner, goal, _REFUND, excess, refunded.version, codes=_REFUND_CODES)
    assert failed.value.code == "finance_failed"
    failed_row = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _REFUND, refund_payment_command(excess)),
    )
    assert failed_row.state == IdempotencyRecord.State.FAILED
    assert Payment.objects.filter(gym=gym, original_payment_id=payment.id).count() == 1

    receipt_payload = {"receipt_id": str(original_receipt.id).upper(), "reason": "Reprint"}
    voided_receipt = _run(
        gym, owner, goal, _RECEIPT, receipt_payload, refunded.version, codes=_RECEIPT_CODES
    )
    voided_receipt_again = _run(
        gym,
        owner,
        goal,
        _RECEIPT,
        receipt_payload,
        voided_receipt.version,
        codes=_RECEIPT_CODES,
    )
    original_receipt.refresh_from_db()
    receipt_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=voided_receipt.execution_identity
    )
    assert voided_receipt.outcome == "committed"
    assert voided_receipt_again.outcome == "existing"
    assert voided_receipt.effect_reference == str(original_receipt.id)
    assert original_receipt.status == Receipt.Status.VOID
    assert receipt_stored.resource_type == "Receipt"
    assert receipt_stored.route == f"/api/v1/receipts/{original_receipt.id}/void/"
    assert receipt_stored.actor_member_id is None
    assert receipt_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=receipt_stored.route,
        body={
            key: value
            for key, value in void_receipt_command(receipt_payload).items()
            if key != "receipt_route_id"
        },
    )
    payment.refresh_from_db()
    assert payment.status == Payment.Status.POSTED

    void_draft = _draft(gym, "20.00")
    void_created = _run(
        gym, owner, goal, _CREATE, void_draft, voided_receipt.version, codes=_CREATE_CODES
    )
    void_submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"payment_id": str(void_created.effect_reference)},
        void_created.version,
        codes=_SUBMIT_CODES,
    )
    void_payment_id = UUID(str(void_created.effect_reference))
    _approve(gym, owner, void_payment_id)
    void_payload = {"payment_id": str(void_payment_id), "reason": "Wrong charge"}
    voided = _run(gym, owner, goal, _VOID, void_payload, void_submitted.version, codes=_VOID_CODES)
    voided_again = _run(gym, owner, goal, _VOID, void_payload, voided.version, codes=_VOID_CODES)
    original = Payment.objects.get(id=void_payment_id)
    correction = Payment.objects.get(id=voided.effect_reference)
    void_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=voided.execution_identity)
    assert voided.outcome == "committed"
    assert voided_again.outcome == "existing"
    assert voided.execution_identity == _identity(goal, _VOID, void_payment_command(void_payload))
    assert correction.id != original.id
    assert correction.entry_type == Payment.EntryType.REVERSAL
    assert correction.amount == Decimal("20.00")
    assert original.status == Payment.Status.VOID
    assert void_stored.resource_type == "Payment"
    assert str(void_stored.resource_id) == str(correction.id)
    assert void_stored.route == command_route(payment_id=original.id, action="void")
    assert void_stored.actor_member_id is None
