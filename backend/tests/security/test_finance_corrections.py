"""Published payment, receipt, and manual-expense corrections."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.models import CashSession, CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.expenses.selectors import expense_net_totals
from apps.expenses.services import post_staff_salary_expense
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.tenants.models import (
    ExpenseCategory,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from apps.users.models import User
from tests.security.test_expenses import _category
from tests.security.test_expenses import _draft as _expense_draft
from tests.security.test_expenses import _gym as _expense_gym
from tests.security.test_payments import _draft, _gym, _member, _membership, _name, _receipt_for
from tests.security.test_tenant_gym_settings import (
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _post_membership_payment(
    *,
    gym_code: str,
    method: str,
    amount: str,
    creator: str,
) -> tuple[APIClient, object, dict[str, str], dict[str, str], str, str, str]:
    api, gym, _owner, owner_headers = _gym(gym_code)
    receptionist = add_staff(
        gym, email=f"{gym_code.lower()}-frontdesk@example.test", role_key="RECEPTIONIST"
    )
    manager = add_staff(gym, email=f"{gym_code.lower()}-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    creator_headers = (
        owner_headers if creator == "owner" else auth_headers(staff_token(receptionist))
    )
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, owner_headers, gym)
    membership_id = _membership(api, owner_headers, gym, member_id, code=f"{gym_code[:6]}PLAN")
    created = _draft(
        api,
        creator_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method=method,
        amount=amount,
    )
    assert created.status_code == 201, created.content
    payment_id = str(created.json()["data"]["id"])
    assert (
        api.post(
            f"/api/v1/payments/{payment_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"corr-submit-{uuid4().hex[:12]}",
            **creator_headers,
        ).status_code
        == 200
    )
    session_id = None
    if method == "CASH":
        opened = api.post(
            "/api/v1/cash-sessions/open/",
            {
                "register_code": "FRONT",
                "business_date": gym_today(gym).isoformat(),
                "opening_cash": "50.00",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"corr-open-{uuid4().hex[:12]}",
            **auth_headers(staff_token(receptionist)),
        )
        assert opened.status_code == 201, opened.content
        session_id = str(opened.json()["data"]["session"]["id"])
    approved = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {} if session_id is None else {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"corr-approve-{uuid4().hex[:12]}",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    return api, gym, owner_headers, manager_headers, payment_id, member_id, session_id or ""


def _open_session(api: APIClient, headers: dict[str, str], gym: object, code: str) -> str:
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": code,
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "20.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"corr-open2-{uuid4().hex[:12]}",
        **headers,
    )
    assert opened.status_code == 201, opened.content
    return str(opened.json()["data"]["session"]["id"])


@override_settings(**jwt_settings())
def test_payment_void_refund_receipt_and_role_policy() -> None:
    api, gym, owner_headers, manager_headers, payment_id, member_id, session_id = (
        _post_membership_payment(
            gym_code="CORRPY",
            method="CASH",
            amount="40.00",
            creator="reception",
        )
    )
    receptionist = GymMembership.objects.get(gym=gym, user__email="corrpy-frontdesk@example.test")
    reception_headers = auth_headers(staff_token(receptionist))
    accountant = add_staff(gym, email="corrpy-accountant@example.test", role_key="ACCOUNTANT")
    _name(accountant, "Ada", "Books")
    accountant_headers = auth_headers(staff_token(accountant))
    _other_api, other_gym, _other_owner, other_headers = _gym("CORRB")

    denied_reception = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Front desk", "cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-reception1",
        **reception_headers,
    )
    assert denied_reception.status_code == 403
    denied_platform = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Platform", "cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-platform01",
        HTTP_AUTHORIZATION=f"Bearer {platform_token('corrpy-platform-2@example.test')[1]}",
    )
    assert denied_platform.status_code == 401
    foreign = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Other gym"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-foreign001",
        **other_headers,
    )
    assert foreign.status_code == 404

    closed = api.get(f"/api/v1/cash-sessions/{session_id}/", **owner_headers)
    assert closed.status_code == 200, closed.content
    expected = closed.json()["data"]["expected_closing"]
    reconciled = api.post(
        f"/api/v1/cash-sessions/{session_id}/reconcile/",
        {"actual_closing": expected, "difference_reason": ""},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-close-session01",
        **owner_headers,
    )
    assert reconciled.status_code == 200, reconciled.content
    stale = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Closed till", "cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-closed0001",
        **manager_headers,
    )
    assert stale.status_code == 409
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Payment.objects.get(id=payment_id).status == "POSTED"
        assert not AuditLog.objects.filter(gym_id=gym.id, action="PAYMENT_VOIDED").exists()

    destination = _open_session(api, reception_headers, gym, "FRONT")
    other_session = _open_session(_other_api, other_headers, other_gym, "FRONT")
    cross = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Wrong till", "cash_session_id": other_session},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-crossgym01",
        **manager_headers,
    )
    assert cross.status_code == 409
    missing_session = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "No till"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-nosession1",
        **manager_headers,
    )
    assert missing_session.status_code == 400
    yesterday = (gym_today(gym) - timedelta(days=1)).isoformat()
    backdated = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Too early", "business_date": yesterday, "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-backdate01",
        **manager_headers,
    )
    assert backdated.status_code == 400
    assigned = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Duplicate", "status": "VOID", "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-mass000001",
        **manager_headers,
    )
    assert assigned.status_code == 400

    voided = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Duplicate charge", "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-posted0001",
        **accountant_headers,
    )
    assert voided.status_code == 200, voided.content
    body = voided.json()["data"]
    assert body["original_payment"]["status"] == "VOID"
    assert body["correction_payment"]["entry_type"] == "REVERSAL"
    assert body["correction_payment"]["amount"] == "40.00"
    assert body["correction_payment"]["status"] == "POSTED"
    assert body["original_receipt"]["status"] == "VOID"
    assert body["correction_receipt"]["status"] == "ISSUED"
    assert (
        body["correction_receipt"]["receipt_number"] != body["original_receipt"]["receipt_number"]
    )
    assert body["cash_transactions"][0]["direction"] == "OUT"
    assert body["cash_transactions"][0]["amount"] == "40.00"
    assert body["refundable_amount"] == "0.00"
    assert body["membership_settlement"]["paid_amount"] == "0.00"
    replay = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Duplicate charge", "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-posted0001",
        **accountant_headers,
    )
    assert replay.status_code == 200
    assert replay.content == voided.content
    mismatch = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Different reason", "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-posted0001",
        **accountant_headers,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.get(id=payment_id)
        reversal = Payment.objects.get(id=body["correction_payment"]["id"])
        assert original.reversal_payment_id == reversal.id
        assert reversal.original_payment_id == original.id
        assert reversal.legacy_unlinked_cash is False
        movement = CashTransaction.objects.get(id=body["cash_transactions"][0]["id"])
        assert str(movement.cash_session_id) == destination
        assert movement.reversal_of_id == original.cash_transaction_id
        original_session = CashSession.objects.get(id=session_id)
        assert original_session.status == "CLOSED"
        assert CashTransaction.objects.filter(gym_id=gym.id, source_id=reversal.id).count() == 1
        assert AuditLog.objects.filter(gym_id=gym.id, action="PAYMENT_VOIDED").count() == 1
        assert AuditLog.objects.filter(gym_id=gym.id, action="PAYMENT_REVERSAL_POSTED").count() == 1

    sales = api.get(
        "/api/v1/sales/daily/",
        {"date": gym_today(gym).isoformat()},
        **accountant_headers,
    )
    assert sales.status_code == 200, sales.content
    assert sales.json()["data"]["gross_sales"] == "40.00"
    assert sales.json()["data"]["reversals"] == "40.00"
    assert sales.json()["data"]["net_total"] == "0.00"
    summary = api.get(f"/api/v1/members/{member_id}/summary/", **owner_headers)
    assert summary.status_code == 200, summary.content
    assert summary.json()["data"]["payment_summary"]["net_paid"] == "0.00"


@override_settings(**jwt_settings())
def test_partial_refunds_race_and_creator_correction() -> None:
    self_api, _self_gym, self_owner, _self_manager, self_payment, _self_member, _self_session = (
        _post_membership_payment(
            gym_code="CORRSELF",
            method="BANK_TRANSFER",
            amount="10.00",
            creator="owner",
        )
    )
    self_void = self_api.post(
        f"/api/v1/payments/{self_payment}/void/",
        {"reason": "Creator correction"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-self-void00001",
        **self_owner,
    )
    assert self_void.status_code == 200, self_void.content
    assert self_void.json()["data"]["original_payment"]["status"] == "VOID"
    api, gym, owner_headers, manager_headers, payment_id, member_id, _session = (
        _post_membership_payment(
            gym_code="CORRFN",
            method="BANK_TRANSFER",
            amount="40.00",
            creator="owner",
        )
    )
    cash_rejected = api.post(
        f"/api/v1/payments/{payment_id}/refund/",
        {
            "amount": "10.00",
            "reason": "Bank refund",
            "method": "BANK_TRANSFER",
            "business_date": gym_today(gym).isoformat(),
            "cash_session_id": str(uuid4()),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-refund-cash0001",
        **manager_headers,
    )
    assert cash_rejected.status_code == 400

    def _refund(amount: str, key: str, headers: dict[str, str] | None = None) -> int:
        response = api.post(
            f"/api/v1/payments/{payment_id}/refund/",
            {
                "amount": amount,
                "reason": "Member request",
                "method": "BANK_TRANSFER",
                "business_date": gym_today(gym).isoformat(),
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            **(manager_headers if headers is None else headers),
        )
        return int(response.status_code)

    first = _refund("15.00", "corr-refund-part0001", owner_headers)
    assert first == 200
    second = _refund("10.00", "corr-refund-part0002")
    assert second == 200
    third = _refund("20.00", "corr-refund-over00001")
    assert third == 409
    blocked_void = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "After refunds"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-afterref01",
        **manager_headers,
    )
    assert blocked_void.status_code == 409
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.get(id=payment_id)
        assert original.status == "POSTED"
        refunds = Payment.objects.filter(
            gym_id=gym.id, original_payment_id=original.id, entry_type="REFUND", status="POSTED"
        )
        assert refunds.count() == 2
        assert sum(row.amount for row in refunds) == Decimal("25.00")
        assert Receipt.objects.filter(payment=original, status="ISSUED").count() == 1

    race_api, race_gym, _owner_headers, race_manager, race_payment, _member, _none = (
        _post_membership_payment(
            gym_code="CORRRACE",
            method="BANK_TRANSFER",
            amount="40.00",
            creator="reception",
        )
    )

    def _race_refund(key: str) -> int:
        return int(
            race_api.post(
                f"/api/v1/payments/{race_payment}/refund/",
                {
                    "amount": "30.00",
                    "reason": "Race",
                    "method": "BANK_TRANSFER",
                    "business_date": gym_today(race_gym).isoformat(),
                },
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
                **race_manager,
            ).status_code
        )

    def _race_void() -> int:
        return int(
            race_api.post(
                f"/api/v1/payments/{race_payment}/void/",
                {"reason": "Race void"},
                format="json",
                HTTP_IDEMPOTENCY_KEY="corr-race-void000001",
                **race_manager,
            ).status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        refund_code = pool.submit(_race_refund, "corr-race-refund0001")
        void_code = pool.submit(_race_void)
        codes = {refund_code.result(), void_code.result()}
    assert codes == {200, 409}
    with transaction.atomic():
        set_gym_context(gym_id=race_gym.id)
        charge = Payment.objects.get(id=race_payment)
        posted = list(
            Payment.objects.filter(
                gym_id=race_gym.id,
                original_payment_id=charge.id,
                status="POSTED",
                entry_type__in=["REFUND", "REVERSAL"],
            )
        )
        assert sum(row.amount for row in posted) <= Decimal(charge.amount)
        assert charge.status in {"POSTED", "VOID"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        over_a = pool.submit(_race_refund, "corr-race-over000001")
        over_b = pool.submit(_race_refund, "corr-race-over000002")
        over_codes = sorted([over_a.result(), over_b.result()])
    assert over_codes == [409, 409]
    with transaction.atomic():
        set_gym_context(gym_id=race_gym.id)
        charge = Payment.objects.get(id=race_payment)
        used = sum(
            row.amount
            for row in Payment.objects.filter(
                gym_id=race_gym.id,
                original_payment_id=charge.id,
                status="POSTED",
                entry_type__in=["REFUND", "REVERSAL"],
            )
        )
        assert used <= Decimal(charge.amount)

    sales = api.get(
        "/api/v1/sales/daily/",
        {"date": gym_today(gym).isoformat()},
        **manager_headers,
    )
    assert sales.json()["data"]["refunds"] == "25.00"
    assert sales.json()["data"]["net_total"] == "15.00"
    summary = api.get(f"/api/v1/members/{member_id}/summary/", **owner_headers)
    assert summary.json()["data"]["payment_summary"]["total_refunded"] == "25.00"
    assert summary.json()["data"]["payment_summary"]["net_paid"] == "15.00"


@override_settings(**jwt_settings())
def test_receipt_void_has_no_economic_effect_and_permission_is_live() -> None:
    api, gym, _owner_headers, manager_headers, payment_id, _member_id, _session = (
        _post_membership_payment(
            gym_code="CORRCPT",
            method="BANK_TRANSFER",
            amount="40.00",
            creator="reception",
        )
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        receipt = Receipt.objects.get(payment_id=payment_id, status="ISSUED")
        receipt_id = receipt.id
        before_cash = CashTransaction.objects.filter(gym_id=gym.id).count()
    receptionist = GymMembership.objects.get(gym=gym, user__email="corrcpt-frontdesk@example.test")
    assert (
        api.post(
            f"/api/v1/receipts/{receipt_id}/void/",
            {"reason": "Front desk copy"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-rcpt-reception1",
            **auth_headers(staff_token(receptionist)),
        ).status_code
        == 403
    )
    voided = api.post(
        f"/api/v1/receipts/{receipt_id}/void/",
        {"reason": "Reprinted copy"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-rcpt-void000001",
        **manager_headers,
    )
    assert voided.status_code == 200, voided.content
    assert voided.json()["data"]["status"] == "VOID"
    assert voided.json()["data"]["receipt_number"] == receipt.receipt_number
    again = api.post(
        f"/api/v1/receipts/{receipt_id}/void/",
        {"reason": "Reprinted copy"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-rcpt-void000002",
        **manager_headers,
    )
    assert again.status_code == 409
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.get(id=payment_id)
        assert payment.status == "POSTED"
        assert payment.voided_at is None
        assert CashTransaction.objects.filter(gym_id=gym.id).count() == before_cash
        assert AuditLog.objects.filter(gym_id=gym.id, action="RECEIPT_VOIDED").count() == 1
    sales = api.get(
        "/api/v1/sales/daily/",
        {"date": gym_today(gym).isoformat()},
        **manager_headers,
    )
    assert sales.json()["data"]["gross_sales"] == "40.00"
    assert sales.json()["data"]["net_total"] == "40.00"

    manager = GymMembership.objects.get(gym=gym, user__email="corrcpt-manager@example.test")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        RolePermission.objects.filter(
            gym_id=gym.id,
            role__key="MANAGER",
            permission__code="payments.void",
        ).delete()
    revoked = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Revoked"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-revoked001",
        **auth_headers(staff_token(manager)),
    )
    assert revoked.status_code == 403

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        custom = Role.objects.create(gym=gym, key="VOID_CLERK", name="Void clerk")
        RolePermission.objects.create(
            gym=gym,
            role=custom,
            permission=Permission.objects.get(code="payments.void"),
        )
        user = User.objects.create_user("corr-clerk@example.test", PASSWORD)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=gym_today(gym),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(gym=gym, gym_membership=membership, role=custom)
    clerk_void = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Custom role correction"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-void-clerk00001",
        **auth_headers(staff_token(membership)),
    )
    assert clerk_void.status_code == 200, clerk_void.content
    assert clerk_void.json()["data"]["original_payment"]["status"] == "VOID"


@override_settings(**jwt_settings())
def test_legacy_cash_void_does_not_invent_a_movement() -> None:
    api, gym, owner, _headers = _gym("CORRLGCY")
    manager = add_staff(gym, email="corrlgcy-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    receptionist = add_staff(gym, email="corrlgcy-front@example.test", role_key="RECEPTIONIST")
    moment = timezone.now()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.create(
            gym=gym,
            purpose=Payment.Purpose.OTHER,
            description="Legacy cash",
            amount=Decimal("18.00"),
            currency=gym.currency,
            method=Payment.Method.CASH,
            business_date=gym_today(gym),
            received_at=moment,
            status=Payment.Status.POSTED,
            legacy_unlinked_cash=True,
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            created_with_reception_scope=True,
            submitted_with_reception_scope=True,
            approved_at=moment,
            approved_by_membership_id=owner.id,
            posted_at=moment,
            posted_by_membership_id=owner.id,
            created_by_membership_id=receptionist.id,
            updated_by_membership_id=owner.id,
        )
        _receipt_for(payment, number="LEGACY-1", sequence_value=1, actor_id=owner.id)
        payment_id = payment.id
    opened = _open_session(api, auth_headers(staff_token(receptionist)), gym, "FRONT")
    rejected = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Invent a till", "cash_session_id": opened},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-legacy-session1",
        **auth_headers(staff_token(manager)),
    )
    assert rejected.status_code == 400
    voided = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Legacy duplicate"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-legacy-void0001",
        **auth_headers(staff_token(manager)),
    )
    assert voided.status_code == 200, voided.content
    assert voided.json()["data"]["cash_transactions"] == []
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.get(id=payment_id)
        reversal = Payment.objects.get(original_payment_id=original.id)
        assert original.status == "VOID"
        assert reversal.legacy_unlinked_cash is True
        assert reversal.cash_transaction_id is None
        assert CashTransaction.objects.filter(gym_id=gym.id, source_id=reversal.id).count() == 0
        assert CashTransaction.objects.filter(gym_id=gym.id, source_id=original.id).count() == 0


@override_settings(**jwt_settings())
def test_expense_void_cash_noncash_and_protected_categories() -> None:
    api, gym, owner, headers = _expense_gym("CORREXP")
    receptionist = add_staff(gym, email="correxp-front@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="correxp-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    internet = _category(gym, "INTERNET")
    bank = _expense_draft(
        api, headers, gym=gym, category_id=str(internet.id), method="BANK_TRANSFER", amount="25.00"
    )
    assert bank.status_code == 201, bank.content
    bank_id = bank.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/expenses/{bank_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-exp-submit0001",
            **headers,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/expenses/{bank_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-exp-approve001",
            **manager_headers,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/expenses/{bank_id}/void/",
            {"reason": "Front desk"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-exp-front00001",
            **reception_headers,
        ).status_code
        == 403
    )
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "30.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-open000001",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    rejected_session = api.post(
        f"/api/v1/expenses/{bank_id}/void/",
        {"reason": "Not cash", "cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-session0001",
        **manager_headers,
    )
    assert rejected_session.status_code == 400
    voided = api.post(
        f"/api/v1/expenses/{bank_id}/void/",
        {"reason": "Entered twice"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-void0000001",
        **headers,
    )
    assert voided.status_code == 200, voided.content
    payload = voided.json()["data"]
    assert payload["original_expense"]["status"] == "VOID"
    assert payload["reversal_expense"]["status"] == "POSTED"
    assert payload["reversal_expense"]["amount"] == "25.00"
    assert payload["cash_transaction"] is None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Expense.objects.get(id=bank_id)
        reversal = Expense.objects.get(id=payload["reversal_expense"]["id"])
        assert original.reversal_expense_id == reversal.id
        assert reversal.original_expense_id == original.id
        totals = expense_net_totals(gym_id=gym.id, business_date=gym_today(gym))
        assert totals["gross_amount"] == Decimal("25.00")
        assert totals["reversal_amount"] == Decimal("25.00")
        assert totals["net_amount"] == Decimal("0.00")

    cash = _expense_draft(
        api,
        reception_headers,
        gym=gym,
        category_id=str(internet.id),
        method="CASH",
        amount="12.00",
    )
    cash_id = cash.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/expenses/{cash_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-exp-submit0002",
            **reception_headers,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/expenses/{cash_id}/approve/",
            {"cash_session_id": session_id},
            format="json",
            HTTP_IDEMPOTENCY_KEY="corr-exp-approve002",
            **manager_headers,
        ).status_code
        == 200
    )
    detail = api.get(f"/api/v1/cash-sessions/{session_id}/", **headers)
    reconciled = api.post(
        f"/api/v1/cash-sessions/{session_id}/reconcile/",
        {
            "actual_closing": detail.json()["data"]["expected_closing"],
            "difference_reason": "",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-close00001",
        **headers,
    )
    assert reconciled.status_code == 200, reconciled.content
    destination = _open_session(api, reception_headers, gym, "SIDE")
    cash_void = api.post(
        f"/api/v1/expenses/{cash_id}/void/",
        {"reason": "Wrong vendor", "cash_session_id": destination},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-voidcash001",
        **manager_headers,
    )
    assert cash_void.status_code == 200, cash_void.content
    movement = cash_void.json()["data"]["cash_transaction"]
    assert movement["direction"] == "IN"
    assert movement["amount"] == "12.00"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = CashTransaction.objects.get(id=movement["id"])
        original_cash = Expense.objects.get(id=cash_id)
        assert stored.reversal_of_id == original_cash.cash_transaction_id
        assert CashSession.objects.get(id=session_id).status == "CLOSED"
        assert AuditLog.objects.filter(gym_id=gym.id, action="EXPENSE_VOIDED").count() == 2

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        salary_category = ExpenseCategory.objects.get(gym_id=gym.id, system_key="STAFF_SALARY")
        salary, _movement = post_staff_salary_expense(
            gym=gym,
            approver=manager,
            category=salary_category,
            created_by_membership_id=owner.id,
            submitted_by_membership_id=owner.id,
            business_date=gym_today(gym),
            description="October salary",
            amount=Decimal("80.00"),
            currency=gym.currency,
            payment_method="BANK_TRANSFER",
            cash_session_id=None,
            policy_note="Payroll",
            request_id="req_salary",
            ip_address=None,
            user_agent="test",
        )
        salary_id = salary.id
    protected = api.post(
        f"/api/v1/expenses/{salary_id}/void/",
        {"reason": "Direct void"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="corr-exp-salary0001",
        **manager_headers,
    )
    assert protected.status_code == 422
    assert protected.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Expense.objects.get(id=salary_id).status == "POSTED"
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "UPDATE expenses_expense SET amount = %s WHERE id = %s",
                    [Decimal("1.00"), bank_id],
                )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Expense.objects.get(id=bank_id).amount == Decimal("25.00")
