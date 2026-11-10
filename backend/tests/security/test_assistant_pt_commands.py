"""PostgreSQL security coverage for PT sale, package, and booking tools."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.db.models import Sum
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
from apps.assistant.pt_tools import (
    create_pt_sale_command,
    execute_registered_tool,
    record_pt_commission_payment_command,
    void_pt_sale_command,
)
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.expenses.models import Expense
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership, GymSettings, IdempotencyRecord
from apps.trainers.exceptions import SaleGrantRequired
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServicePackage,
    ServiceSessionLedger,
)
from apps.trainers.pt_commission_services import command_route as commission_command_route
from apps.trainers.pt_sale_services import approve_pt_sale
from apps.trainers.pt_sale_services import command_route as sale_command_route
from apps.trainers.serializers import PTSaleWriteSerializer
from apps.trainers.services import create_service_package
from apps.users.models import User
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

_CREATE = "create_pt_sale"
_UPDATE = "update_pt_sale"
_SUBMIT = "submit_pt_sale"
_CANCEL = "cancel_pt_sale"
_VOID = "void_pt_sale"
_PAY = "record_pt_commission_payment"
_GRANT = "grant_member_package"
_LEDGER = "adjust_package_ledger"
_BOOK = "create_pt_booking"
_ATTEND = "attend_pt_booking"
_NO_SHOW = "mark_pt_booking_no_show"
_BOOK_CANCEL = "cancel_pt_booking"
_SALE_CODES = frozenset({"pt.sales.create", "pt.sales.update", "pt.sales.submit", "pt.sales.void"})
_PAY_CODES = frozenset({"pt.commissions.pay"})
_PACKAGE_CODES = frozenset({"pt.sessions.adjust"})
_SCHEDULE_CODES = frozenset({"pt.schedule.manage"})


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
    request.request_id = "req_pt_tool"
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
):
    return execute_registered_tool(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=True,
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


def _sale(member_id: str, trainer_id: UUID, day: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "member_id": member_id,
        "trainer_staff_profile_id": str(trainer_id),
        "service_date": day,
        "description": "Private session",
        "gross_fee": "100.00",
        "currency": "THB",
    }
    payload.update(extra)
    return payload


def _approve(gym: Gym, actor: GymMembership, sale_id: UUID) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        approve_pt_sale(
            gym=gym,
            actor=actor,
            sale_id=sale_id,
            cash_session_id=None,
            request_id="req_pt_tool",
            ip_address=None,
            user_agent="",
        )


def _catalog(gym: Gym, actor: GymMembership) -> ServicePackage:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_service_package(
            gym=gym,
            actor=actor,
            package_type=ServicePackage.PackageType.PT,
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id="req_pt_tool",
            ip_address=None,
            user_agent="",
        )


@override_settings(**jwt_settings())
def test_permission_feature_and_route_stop_before_finance() -> None:
    client, token = platform_token("pt1-platform@example.test")
    payload = provision(client, token, code="PT1", owner_email="pt1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pt1-trainer@example.test", role_key="TRAINER")
    _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    draft = _sale(str(uuid4()), uuid4(), "2026-11-10")
    for tool_name, command, codes in (
        (_CREATE, draft, frozenset()),
        (_CREATE, draft, frozenset({"pt.sales.view"})),
        (_SUBMIT, {"pt_sale_id": str(uuid4())}, frozenset({"pt.sales.create"})),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    with pytest.raises(ValidationError):
        _run(
            gym,
            trainer_membership,
            goal,
            _CREATE,
            {**draft, "gross_fee": "100.001"},
            1,
            codes=frozenset({"pt.sales.create"}),
        )
    bare_user = User.objects.create_user("pt1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="PTB", name="Bare", status=Gym.Status.ACTIVE)
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
        _run(bare, bare_actor, bare_goal, _CREATE, draft, 1, codes=frozenset({"pt.sales.create"}))
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(
            gym,
            owner,
            goal,
            _CREATE,
            {**draft, "gym_id": str(uuid4())},
            1,
            codes=frozenset({"pt.sales.create"}),
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as missing:
        _run(gym, owner, goal, _SUBMIT, {}, 1, codes=frozenset({"pt.sales.submit"}))
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as routed:
        _run(
            gym,
            owner,
            goal,
            _UPDATE,
            {"pt_sale_id": "not-a-uuid", "description": "Desk session"},
            1,
            codes=frozenset({"pt.sales.update"}),
        )
    assert routed.value.code == "finance_route"
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert PTSale.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_sale_package_booking_and_commission_replay() -> None:
    client, token = platform_token("pt2-platform@example.test")
    payload = provision(client, token, code="PT2", owner_email="pt2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pt2-ada@example.test", role_key="TRAINER")
    other_trainer_membership = add_staff(gym, email="pt2-bea@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="PT2ADA", name="Ada")
    _profile(gym, other_trainer_membership, code="PT2BEA", name="Bea")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ada.default_commission_percent = Decimal("10.00")
        ada.save(update_fields=["default_commission_percent", "updated_at"])
    _enable(gym, "pt")
    api = _client()
    headers = auth_headers(staff_token(owner))
    today = gym_today(gym)
    yesterday = today - timedelta(days=1)
    member_id = _member(api, headers, first_name="Nila")
    assigned = _assign(
        api,
        headers,
        trainer_id=str(ada.id),
        member_id=member_id,
        start=yesterday.isoformat(),
    )
    assert assigned.status_code == 201, assigned.json()
    catalog = _catalog(gym, owner)
    goal = _goal(gym, owner)
    draft = _sale(
        member_id,
        ada.id,
        today.isoformat(),
        currency=gym.currency,
        service_package_id=str(catalog.id),
    )

    created = _run(gym, owner, goal, _CREATE, draft, 1, codes=_SALE_CODES)
    created_again = _run(gym, owner, goal, _CREATE, draft, created.version, codes=_SALE_CODES)
    sale = PTSale.objects.get(id=created.effect_reference)
    serializer = PTSaleWriteSerializer(data=draft)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created.execution_identity == _identity(goal, _CREATE, create_pt_sale_command(draft))
    assert PTSale.objects.filter(gym=gym).count() == 1
    assert sale.status == PTSale.Status.DRAFT
    assert sale.gross_fee == Decimal("100.00")
    assert sale.service_package_id == catalog.id
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/pt-sales/"
    assert stored.resource_type == "PTSale"
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/pt-sales/",
        body=dict(serializer.validated_data),
    )
    with pytest.raises(Http404):
        _run(
            gym,
            other_trainer_membership,
            goal,
            _UPDATE,
            {"pt_sale_id": str(sale.id), "description": "Taken"},
            created.version,
            codes=_SALE_CODES,
        )
    sale.refresh_from_db()
    assert sale.description == "Private session"

    updated = _run(
        gym,
        owner,
        goal,
        _UPDATE,
        {"pt_sale_id": str(sale.id).upper(), "description": "  Desk session  "},
        created.version,
        codes=_SALE_CODES,
    )
    sale.refresh_from_db()
    assert updated.outcome == "committed"
    assert sale.description == "Desk session"
    update_stored = IdempotencyRecord.objects.get(
        gym=gym, idempotency_key=updated.execution_identity
    )
    assert update_stored.http_method == "PATCH"
    assert update_stored.route == sale_command_route(sale_id=sale.id, action="")

    submitted = _run(
        gym,
        owner,
        goal,
        _SUBMIT,
        {"pt_sale_id": str(sale.id)},
        updated.version,
        codes=_SALE_CODES,
    )
    sale.refresh_from_db()
    assert submitted.outcome == "committed"
    assert sale.status == PTSale.Status.PENDING_APPROVAL

    cancel_draft = _sale(member_id, ada.id, today.isoformat(), description="Cancel session")
    cancel_draft["currency"] = gym.currency
    cancelled_created = _run(
        gym, owner, goal, _CREATE, cancel_draft, submitted.version, codes=_SALE_CODES
    )
    cancelled = _run(
        gym,
        owner,
        goal,
        _CANCEL,
        {"pt_sale_id": str(cancelled_created.effect_reference), "reason": "Duplicate"},
        cancelled_created.version,
        codes=_SALE_CODES,
    )
    cancelled_sale = PTSale.objects.get(id=cancelled.effect_reference)
    assert cancelled_sale.status == PTSale.Status.CANCELLED
    assert cancelled_sale.reason == "Duplicate"

    _approve(gym, owner, sale.id)
    sale.refresh_from_db()
    commission = PTCommission.objects.get(pt_sale=sale)
    granted = MemberServicePackage.objects.get(pt_sale=sale)
    assert sale.status == PTSale.Status.POSTED
    assert commission.commission_amount == Decimal("10.00")
    assert commission.status == PTCommission.Status.ACCRUED
    assert granted.granted_sessions == 3
    assert granted.source == MemberServicePackage.Source.PT_SALE

    payout = {
        "commission_id": str(commission.id),
        "amount": "10.00",
        "business_date": today.isoformat(),
        "payment_method": "BANK_TRANSFER",
    }
    paid = _run(gym, owner, goal, _PAY, payout, cancelled.version, codes=_PAY_CODES)
    paid_again = _run(gym, owner, goal, _PAY, payout, paid.version, codes=_PAY_CODES)
    payment = PTCommissionPayment.objects.get(id=paid.effect_reference)
    pay_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=paid.execution_identity)
    commission.refresh_from_db()
    assert paid.outcome == "committed"
    assert paid_again.outcome == "existing"
    assert payment.amount == Decimal("10.00")
    assert payment.cash_session_id is None
    assert PTCommissionPayment.objects.filter(pt_commission=commission).count() == 1
    assert commission.status == PTCommission.Status.PAID
    assert Expense.objects.filter(gym=gym, id=payment.expense_id, amount=Decimal("10.00")).exists()
    assert pay_stored.route == commission_command_route(commission_id=commission.id)
    assert pay_stored.resource_type == "PTCommissionPayment"
    assert pay_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=pay_stored.route,
        body={
            key: value
            for key, value in record_pt_commission_payment_command(payout).items()
            if key != "commission_id"
        },
    )

    void_payload = {
        "pt_sale_id": str(sale.id),
        "reason": "Wrong period",
        "business_date": today.isoformat(),
        "payout_reversals": [
            {"commission_payment_id": str(payment.id), "cash_session_id": None},
        ],
    }
    voided = _run(gym, owner, goal, _VOID, void_payload, paid.version, codes=_SALE_CODES)
    voided_again = _run(gym, owner, goal, _VOID, void_payload, voided.version, codes=_SALE_CODES)
    sale.refresh_from_db()
    commission.refresh_from_db()
    payment.refresh_from_db()
    granted.refresh_from_db()
    void_stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=voided.execution_identity)
    assert voided.outcome == "committed"
    assert voided_again.outcome == "existing"
    assert voided.effect_reference == str(sale.id)
    assert sale.status == PTSale.Status.VOID
    assert commission.status == PTCommission.Status.VOID
    assert payment.status == PTCommissionPayment.Status.VOID
    assert granted.status == MemberServicePackage.Status.VOID
    original_expense = Expense.objects.get(id=payment.expense_id)
    assert original_expense.status == Expense.Status.VOID
    assert original_expense.reversal_expense_id is not None
    assert void_stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=sale_command_route(sale_id=sale.id, action="void"),
        body={
            key: value
            for key, value in void_pt_sale_command(void_payload).items()
            if key != "pt_sale_id"
        },
    )

    paid_grant = {
        "member_id": member_id,
        "service_package_id": str(catalog.id),
        "business_date": yesterday.isoformat(),
        "complimentary": False,
        "reason": "Paid grant",
    }
    with pytest.raises(SaleGrantRequired):
        _run(gym, owner, goal, _GRANT, paid_grant, voided.version, codes=_PACKAGE_CODES)
    with pytest.raises(ExecutionRejected) as grant_failed:
        _run(gym, owner, goal, _GRANT, paid_grant, voided.version, codes=_PACKAGE_CODES)
    assert grant_failed.value.code == "finance_failed"

    courtesy = {**paid_grant, "complimentary": True, "reason": "Courtesy sessions"}
    granted_tool = _run(gym, owner, goal, _GRANT, courtesy, voided.version, codes=_PACKAGE_CODES)
    granted_again = _run(
        gym, owner, goal, _GRANT, courtesy, granted_tool.version, codes=_PACKAGE_CODES
    )
    package = MemberServicePackage.objects.get(id=granted_tool.effect_reference)
    assert granted_tool.outcome == "committed"
    assert granted_again.outcome == "existing"
    assert package.source == MemberServicePackage.Source.ADMIN_GRANT
    assert package.complimentary is True
    assert package.granted_sessions == 3
    assert ServiceSessionLedger.objects.filter(member_service_package=package).count() == 1

    start = timezone.now() - timedelta(hours=5)
    attended_payload = {
        "trainer_staff_profile_id": str(ada.id),
        "member_id": member_id,
        "member_service_package_id": str(package.id),
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
    }
    no_show_payload = {
        **attended_payload,
        "starts_at": (start + timedelta(hours=2)).isoformat(),
        "ends_at": (start + timedelta(hours=3)).isoformat(),
    }
    future_start = timezone.now() + timedelta(hours=4)
    cancel_payload = {
        **attended_payload,
        "starts_at": future_start.isoformat(),
        "ends_at": (future_start + timedelta(hours=1)).isoformat(),
    }
    booked = _run(
        gym, owner, goal, _BOOK, attended_payload, granted_again.version, codes=_SCHEDULE_CODES
    )
    booked_again = _run(
        gym, owner, goal, _BOOK, attended_payload, booked.version, codes=_SCHEDULE_CODES
    )
    assert booked.outcome == "committed"
    assert booked_again.outcome == "existing"
    assert PTBooking.objects.filter(gym=gym, member_service_package=package).count() == 1
    attended = _run(
        gym,
        owner,
        goal,
        _ATTEND,
        {"pt_booking_id": str(booked.effect_reference)},
        booked.version,
        codes=_SCHEDULE_CODES,
    )
    attended_again = _run(
        gym,
        owner,
        goal,
        _ATTEND,
        {"pt_booking_id": str(booked.effect_reference)},
        attended.version,
        codes=_SCHEDULE_CODES,
    )
    assert attended.outcome == "committed"
    assert attended_again.outcome == "existing"
    assert PTBooking.objects.get(id=booked.effect_reference).status == PTBooking.Status.ATTENDED
    no_show_booking = _run(
        gym, owner, goal, _BOOK, no_show_payload, attended.version, codes=_SCHEDULE_CODES
    )
    no_show = _run(
        gym,
        owner,
        goal,
        _NO_SHOW,
        {"pt_booking_id": str(no_show_booking.effect_reference)},
        no_show_booking.version,
        codes=_SCHEDULE_CODES,
    )
    assert PTBooking.objects.get(id=no_show.effect_reference).status == PTBooking.Status.NO_SHOW
    cancel_booking = _run(
        gym, owner, goal, _BOOK, cancel_payload, no_show.version, codes=_SCHEDULE_CODES
    )
    cancelled_booking = _run(
        gym,
        owner,
        goal,
        _BOOK_CANCEL,
        {"pt_booking_id": str(cancel_booking.effect_reference), "reason": "Member moved"},
        cancel_booking.version,
        codes=_SCHEDULE_CODES,
    )
    assert (
        PTBooking.objects.get(id=cancelled_booking.effect_reference).status
        == PTBooking.Status.CANCELLED
    )
    consumed = ServiceSessionLedger.objects.filter(
        member_service_package=package,
        reason__in=[
            ServiceSessionLedger.Reason.ATTENDED,
            ServiceSessionLedger.Reason.POLICY_NO_SHOW,
        ],
    )
    assert consumed.count() == 2
    assert list(consumed.order_by("reason").values_list("session_delta", flat=True)) == [-1, -1]
    attended_entry = ServiceSessionLedger.objects.get(
        member_service_package=package,
        pt_booking_id=booked.effect_reference,
        reason=ServiceSessionLedger.Reason.ATTENDED,
    )
    corrected = _run(
        gym,
        owner,
        goal,
        _LEDGER,
        {
            "member_service_package_id": str(package.id),
            "reverses_entry_id": str(attended_entry.id),
            "reason": "Restore attendance",
        },
        cancelled_booking.version,
        codes=_PACKAGE_CODES,
    )
    corrected_again = _run(
        gym,
        owner,
        goal,
        _LEDGER,
        {
            "member_service_package_id": str(package.id),
            "reverses_entry_id": str(attended_entry.id),
            "reason": "Restore attendance",
        },
        corrected.version,
        codes=_PACKAGE_CODES,
    )
    assert corrected.outcome == "committed"
    assert corrected_again.outcome == "existing"
    assert (
        ServiceSessionLedger.objects.filter(
            member_service_package=package,
            reason=ServiceSessionLedger.Reason.CORRECTION,
        ).count()
        == 1
    )
    remaining = ServiceSessionLedger.objects.filter(member_service_package=package).aggregate(
        total=Sum("session_delta")
    )["total"]
    assert remaining == 2
