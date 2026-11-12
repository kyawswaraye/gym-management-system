"""PostgreSQL security coverage for retail sale tools."""

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
from apps.assistant.retail_tools import (
    create_retail_sale_command,
    execute_registered_tool,
    return_retail_sale_command,
    void_retail_sale_command,
)
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock, create_category, create_product
from apps.members.models import Payment
from apps.members.services import gym_today
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailReturn, RetailSale, RetailSaleLine
from apps.sales.serializers import RetailSaleWriteSerializer
from apps.sales.services import approve_sale, command_route
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

_CREATE = "create_retail_sale"
_UPDATE = "update_retail_sale"
_SUBMIT = "submit_retail_sale"
_CANCEL = "cancel_retail_sale"
_VOID = "void_retail_sale"
_RETURN = "return_retail_sale"
_CREATE_CODES = frozenset({"retail.create"})
_UPDATE_CODES = frozenset({"retail.update"})
_SUBMIT_CODES = frozenset({"retail.submit"})
_VOID_CODES = frozenset({"retail.void"})


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
    request.request_id = "req_retail_tool"
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


def _sale(
    gym: Gym,
    product_id: UUID,
    quantity: str = "2.000",
    reference: str = "  POS-1  ",
) -> dict[str, object]:
    return {
        "business_date": gym_today(gym).isoformat(),
        "payment_method": "CARD",
        "reference_number": reference,
        "lines": [
            {"product_id": str(product_id), "quantity": quantity, "discount": "0.00"},
        ],
    }


def _stocked_product(gym: Gym, actor: GymMembership) -> UUID:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = create_category(
            gym=gym,
            actor=actor,
            fields={"code": "WATER", "name": "Water"},
            request_id="req_retail_tool",
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
            request_id="req_retail_tool",
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
            request_id="req_retail_tool",
            ip_address=None,
            user_agent="",
        )
    return product.id


def _approve(gym: Gym, actor: GymMembership, sale_id: UUID) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        approve_sale(
            gym=gym,
            actor=actor,
            sale_id=sale_id,
            cash_session_id=None,
            request_id="req_retail_tool",
            ip_address=None,
            user_agent="",
        )


def _movements(gym: Gym, sale_id: UUID, movement_type: str) -> int:
    return StockMovement.objects.filter(
        gym=gym,
        movement_type=movement_type,
        retail_sale_line__sale_id=sale_id,
    ).count()


@override_settings(**jwt_settings())
def test_permission_feature_trainer_and_route_stop_before_finance() -> None:
    client, token = platform_token("rtl1-platform@example.test")
    payload = provision(client, token, code="RTL1", owner_email="rtl1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="rtl1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    draft = _sale(gym, uuid4())
    owner.is_owner = False
    for tool_name, command, codes in (
        (_CREATE, draft, frozenset()),
        (_CREATE, draft, frozenset({"retail.view"})),
        (_SUBMIT, {"retail_sale_id": str(uuid4())}, _CREATE_CODES),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    owner.is_owner = True
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    bare_user = User.objects.create_user("rtl1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="RTLB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _CREATE, _sale(bare, uuid4()), 1, codes=_CREATE_CODES)
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
            {"retail_sale_id": "not-a-uuid", "reference_number": "POS-2"},
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
            {
                "business_date": gym_today(gym).isoformat(),
                "payment_method": "CARD",
                "lines": [
                    {
                        "product_id": str(uuid4()),
                        "quantity": "1.0000",
                        "discount": "0.00",
                    }
                ],
            },
            1,
            codes=_CREATE_CODES,
        )
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert RetailSale.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_retail_draft_void_and_return_replay() -> None:
    client, token = platform_token("rtl2-platform@example.test")
    payload = provision(client, token, code="RTL2", owner_email="rtl2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    goal = _goal(gym, owner)
    missing_product = _sale(gym, uuid4())
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, missing_product, 1, codes=_CREATE_CODES)
    with pytest.raises(ValidationError):
        _run(gym, owner, goal, _CREATE, missing_product, 1, codes=_CREATE_CODES)
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _CREATE, create_retail_sale_command(missing_product)),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    assert RetailSale.objects.filter(gym=gym).count() == 0

    product_id = _stocked_product(gym, owner)
    draft = _sale(gym, product_id, "1.000")
    created = _run(gym, owner, goal, _CREATE, draft, 1, codes=_CREATE_CODES)
    created_again = _run(gym, owner, goal, _CREATE, draft, created.version, codes=_CREATE_CODES)
    sale = RetailSale.objects.get(id=created.effect_reference)
    serializer = RetailSaleWriteSerializer(data=draft)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(goal, _CREATE, create_retail_sale_command(draft))
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/retail-sales/"
    assert stored.resource_type == "RetailSale"
    assert str(stored.resource_id) == str(sale.id)
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/retail-sales/",
        body=dict(serializer.validated_data),
    )
    assert sale.status == RetailSale.Status.DRAFT
    assert sale.reference_number == "POS-1"
    assert sale.total_amount == Decimal("10.00")
    assert RetailSale.objects.filter(gym=gym).count() == 1

    updated = _run(
        gym,
        owner,
        goal,
        _UPDATE,
        {"retail_sale_id": str(sale.id).upper(), "reference_number": "  POS-2  "},
        created.version,
        codes=_UPDATE_CODES,
    )
    sale.refresh_from_db()
    update_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=updated.execution_identity
    )
    assert updated.outcome == "committed"
    assert sale.reference_number == "POS-2"
    assert update_stored.http_method == "PATCH"
    assert update_stored.route == command_route(sale_id=sale.id, action="")
    assert update_stored.actor_member_id is None

    submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"retail_sale_id": str(sale.id)},
        updated.version,
        codes=_SUBMIT_CODES,
    )
    sale.refresh_from_db()
    assert submitted.outcome == "committed"
    assert sale.status == RetailSale.Status.PENDING_APPROVAL

    cancelled_draft = _sale(gym, product_id, "1.000", "  POS-3  ")
    cancelled_created = _run(
        gym, owner, goal, _CREATE, cancelled_draft, submitted.version, codes=_CREATE_CODES
    )
    cancelled = _run(
        gym,
        owner,
        goal,
        _CANCEL,
        {"retail_sale_id": str(cancelled_created.effect_reference), "reason": "Duplicate"},
        cancelled_created.version,
        codes=_UPDATE_CODES,
    )
    cancelled_sale = RetailSale.objects.get(id=cancelled.effect_reference)
    assert cancelled.outcome == "committed"
    assert cancelled_sale.status == RetailSale.Status.CANCELLED
    assert cancelled_sale.reason == "Duplicate"

    void_draft = _sale(gym, product_id, "1.000", "  POS-4  ")
    void_created = _run(
        gym, owner, goal, _CREATE, void_draft, cancelled.version, codes=_CREATE_CODES
    )
    void_submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"retail_sale_id": str(void_created.effect_reference)},
        void_created.version,
        codes=_SUBMIT_CODES,
    )
    void_sale_id = UUID(str(void_created.effect_reference))
    _approve(gym, owner, void_sale_id)
    posted = RetailSale.objects.get(id=void_sale_id)
    charge = Payment.objects.get(id=posted.payment_id)
    assert posted.status == RetailSale.Status.POSTED
    assert _movements(gym, posted.id, StockMovement.Type.SALE) == 1
    assert (
        Payment.objects.filter(
            gym=gym,
            retail_sale_id=posted.id,
            purpose=Payment.Purpose.PRODUCT,
            entry_type=Payment.EntryType.CHARGE,
        ).count()
        == 1
    )
    void_payload = {"retail_sale_id": str(posted.id), "reason": "Wrong item"}
    voided = _run(gym, owner, goal, _VOID, void_payload, void_submitted.version, codes=_VOID_CODES)
    voided_again = _run(gym, owner, goal, _VOID, void_payload, voided.version, codes=_VOID_CODES)
    posted.refresh_from_db()
    charge.refresh_from_db()
    void_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=voided.execution_identity)
    assert voided.outcome == "committed"
    assert voided_again.outcome == "existing"
    assert voided.execution_identity == _identity(
        goal, _VOID, void_retail_sale_command(void_payload)
    )
    assert voided.effect_reference == str(posted.id)
    assert posted.status == RetailSale.Status.VOID
    assert charge.status == Payment.Status.VOID
    assert _movements(gym, posted.id, StockMovement.Type.SALE_RETURN) == 1
    assert (
        Payment.objects.filter(
            gym=gym,
            original_payment_id=charge.id,
            entry_type=Payment.EntryType.REVERSAL,
        ).count()
        == 1
    )
    assert void_stored.resource_type == "RetailSale"
    assert str(void_stored.resource_id) == str(posted.id)
    assert void_stored.route == command_route(sale_id=posted.id, action="void")
    assert void_stored.actor_member_id is None

    return_draft = _sale(gym, product_id, "2.000", "  POS-5  ")
    return_created = _run(
        gym, owner, goal, _CREATE, return_draft, voided.version, codes=_CREATE_CODES
    )
    return_submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"retail_sale_id": str(return_created.effect_reference)},
        return_created.version,
        codes=_SUBMIT_CODES,
    )
    return_sale_id = UUID(str(return_created.effect_reference))
    _approve(gym, owner, return_sale_id)
    returned_sale = RetailSale.objects.get(id=return_sale_id)
    return_charge = Payment.objects.get(id=returned_sale.payment_id)
    line = RetailSaleLine.objects.get(gym=gym, sale_id=returned_sale.id)
    assert _movements(gym, returned_sale.id, StockMovement.Type.SALE) == 1
    assert return_charge.purpose == Payment.Purpose.PRODUCT
    assert return_charge.amount == Decimal("20.00")
    return_payload = {
        "retail_sale_id": str(returned_sale.id),
        "reason": "Damaged",
        "lines": [{"line_id": str(line.id), "quantity": "1.000"}],
    }
    returned = _run(
        gym,
        owner,
        goal,
        _RETURN,
        return_payload,
        return_submitted.version,
        codes=_VOID_CODES,
    )
    returned_again = _run(
        gym, owner, goal, _RETURN, return_payload, returned.version, codes=_VOID_CODES
    )
    retail_return = RetailReturn.objects.get(id=returned.effect_reference)
    refund = Payment.objects.get(id=retail_return.payment_id)
    return_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=returned.execution_identity
    )
    returned_sale.refresh_from_db()
    assert returned.outcome == "committed"
    assert returned_again.outcome == "existing"
    assert returned.execution_identity == _identity(
        goal, _RETURN, return_retail_sale_command(return_payload)
    )
    assert returned_sale.status == RetailSale.Status.POSTED
    assert retail_return.sale_id == returned_sale.id
    assert retail_return.refund_amount == Decimal("10.00")
    assert refund.entry_type == Payment.EntryType.REFUND
    assert refund.amount == Decimal("10.00")
    assert refund.original_payment_id == return_charge.id
    assert _movements(gym, returned_sale.id, StockMovement.Type.SALE_RETURN) == 1
    assert (
        Payment.objects.filter(
            gym=gym,
            original_payment_id=return_charge.id,
            entry_type=Payment.EntryType.REFUND,
        ).count()
        == 1
    )
    assert return_stored.resource_type == "RetailReturn"
    assert str(return_stored.resource_id) == str(retail_return.id)
    assert return_stored.route == command_route(sale_id=returned_sale.id, action="return")
    assert return_stored.actor_member_id is None
    assert return_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=return_stored.route,
        body={
            key: value
            for key, value in return_retail_sale_command(return_payload).items()
            if key != "sale_route_id"
        },
    )

    excess = {
        "retail_sale_id": str(returned_sale.id),
        "reason": "Too many",
        "lines": [{"line_id": str(line.id), "quantity": "2.000"}],
    }
    with pytest.raises(RetailPostingConflict):
        _run(gym, owner, goal, _RETURN, excess, returned.version, codes=_VOID_CODES)
    with pytest.raises(ExecutionRejected) as failed:
        _run(gym, owner, goal, _RETURN, excess, returned.version, codes=_VOID_CODES)
    assert failed.value.code == "finance_failed"
    failed_row = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _RETURN, return_retail_sale_command(excess)),
    )
    assert failed_row.state == IdempotencyRecord.State.FAILED
    assert RetailReturn.objects.filter(gym=gym, sale_id=returned_sale.id).count() == 1
    assert _movements(gym, returned_sale.id, StockMovement.Type.SALE_RETURN) == 1
    assert (
        Payment.objects.filter(
            gym=gym,
            original_payment_id=return_charge.id,
            entry_type=Payment.EntryType.REFUND,
        ).count()
        == 1
    )
