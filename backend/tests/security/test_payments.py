from __future__ import annotations

import importlib.util
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Membership, Payment, Receipt
from apps.members.services import gym_today, refresh_membership_payment_status
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership, StaffProfile
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
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

_KEY = "payment-command-key-0001"


def _name(membership: GymMembership, first: str, last: str) -> None:
    membership.user.first_name = first
    membership.user.last_name = last
    membership.user.save(update_fields=["first_name", "last_name"])


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code,
        defaults={"name": code, "description": code},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _gym(code: str) -> tuple[APIClient, Gym, GymMembership, dict[str, str]]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(
        client,
        token,
        code=code,
        owner_email=f"{code.lower()}-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user", "gym").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "finance")
    _enable(gym, "reports")
    _enable(gym, "members")
    _enable(gym, "memberships")
    return _client(), gym, owner, auth_headers(staff_token(owner))


def _member(api: APIClient, headers: dict[str, str], gym: Gym) -> str:
    response = api.post(
        "/api/v1/members/",
        {
            "first_name": "Ada",
            "last_name": "Member",
            "join_date": gym_today(gym).isoformat(),
        },
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.content
    return str(response.json()["data"]["id"])


def _membership(
    api: APIClient,
    headers: dict[str, str],
    gym: Gym,
    member_id: str,
    *,
    code: str = "PAYPLAN",
) -> str:
    plan = api.post(
        "/api/v1/membership-plans/",
        {
            "code": code,
            "name": "Pay Plan",
            "duration_value": 1,
            "duration_unit": "MONTH",
            "price": "100.00",
        },
        format="json",
        **headers,
    )
    assert plan.status_code == 201, plan.content
    sold = api.post(
        "/api/v1/memberships/",
        {
            "member_id": member_id,
            "plan_id": plan.json()["data"]["id"],
            "start_date": gym_today(gym).isoformat(),
            "discount_amount": "0.00",
        },
        format="json",
        **headers,
    )
    assert sold.status_code == 201, sold.content
    return str(sold.json()["data"]["id"])


def _draft(
    api: APIClient,
    headers: dict[str, str],
    *,
    gym: Gym,
    member_id: str,
    membership_id: str,
    method: str = "CASH",
    amount: str = "40.00",
    key: str | None = None,
) -> object:
    return api.post(
        "/api/v1/payments/",
        {
            "member_id": member_id,
            "membership_id": membership_id,
            "purpose": "MEMBERSHIP",
            "description": "October dues",
            "amount": amount,
            "currency": gym.currency,
            "method": method,
            "business_date": gym_today(gym).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"pay-create-{uuid4().hex}",
        **headers,
    )


@override_settings(**jwt_settings())
def test_cash_approval_posts_receipt_settlement_and_one_movement() -> None:
    api, gym, _owner, headers = _gym("PAYPOST")
    receptionist = add_staff(gym, email="paypost-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paypost-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "DRAFT"
    assert created.json()["data"]["cash_session_id"] is None

    denied = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-deny-{uuid4().hex}",
        **reception_headers,
    )
    assert denied.status_code == 403

    submitted = api.post(
        f"/api/v1/payments/{payment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-submit-{uuid4().hex}",
        **reception_headers,
    )
    assert submitted.status_code == 200, submitted.content
    assert submitted.json()["data"]["status"] == "PENDING_APPROVAL"

    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "50.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-open-{uuid4().hex}",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]

    self_approve = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-self-{uuid4().hex}",
        **reception_headers,
    )
    assert self_approve.status_code == 403

    approved = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_KEY,
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    body = approved.json()["data"]
    assert body["payment"]["status"] == "POSTED"
    assert body["receipt"]["status"] == "ISSUED"
    assert body["receipt"]["member_name"] == "Ada Member"
    assert body["cash_transaction"]["direction"] == "IN"
    assert body["cash_transaction"]["amount"] == "40.00"
    assert body["membership_settlement"]["payment_status"] == "PARTIAL"
    assert body["membership_settlement"]["paid_amount"] == "40.00"
    assert body["membership_settlement"]["balance_amount"] == "60.00"
    replay = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY=_KEY,
        **manager_headers,
    )
    assert replay.status_code == 200
    assert replay.content == approved.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            CashTransaction.objects.filter(
                gym_id=gym.id,
                source_type=CashTransaction.SourceType.PAYMENT,
                source_id=payment_id,
            ).count()
            == 1
        )
        issued = Receipt.objects.filter(gym_id=gym.id, payment_id=payment_id, status="ISSUED")
        assert issued.count() == 1
    actions = set(
        AuditLog.objects.filter(gym_id=gym.id, target_id=payment_id).values_list(
            "action", flat=True
        )
    )
    assert "PAYMENT_APPROVED_POSTED" in actions
    assert AuditLog.objects.filter(gym_id=gym.id, action="RECEIPT_ISSUED").exists()

    other = add_staff(gym, email="paypost-reception-2@example.test", role_key="RECEPTIONIST")
    second = _draft(
        api,
        auth_headers(staff_token(other)),
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="10.00",
    )
    assert second.status_code == 201, second.content
    second_id = second.json()["data"]["id"]
    submitter = add_staff(gym, email="paypost-reception-3@example.test", role_key="RECEPTIONIST")
    submitted_by_other = api.post(
        f"/api/v1/payments/{second_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-submit2-{uuid4().hex}",
        **auth_headers(staff_token(submitter)),
    )
    assert submitted_by_other.status_code == 200, submitted_by_other.content
    mismatched = api.post(
        f"/api/v1/payments/{second_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-till-{uuid4().hex}",
        **manager_headers,
    )
    assert mismatched.status_code == 400

    bank = _draft(
        api,
        headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method="BANK_TRANSFER",
        amount="60.00",
    )
    assert bank.status_code == 201, bank.content
    bank_id = bank.json()["data"]["id"]
    bank_submit = api.post(
        f"/api/v1/payments/{bank_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-bank-submit-{uuid4().hex}",
        **headers,
    )
    assert bank_submit.status_code == 200, bank_submit.content
    bank_approve = api.post(
        f"/api/v1/payments/{bank_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-bank-approve-{uuid4().hex}",
        **manager_headers,
    )
    assert bank_approve.status_code == 200, bank_approve.content
    assert bank_approve.json()["data"]["cash_transaction"] is None
    assert bank_approve.json()["data"]["membership_settlement"]["payment_status"] == "PAID"


@override_settings(**jwt_settings())
def test_reject_and_cancel_leave_no_receipt_or_cash() -> None:
    api, gym, _owner, headers = _gym("PAYSTOP")
    manager = add_staff(gym, email="paystop-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    pending = _draft(api, headers, gym=gym, member_id=member_id, membership_id=membership_id)
    pending_id = pending.json()["data"]["id"]
    api.post(
        f"/api/v1/payments/{pending_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-stop-submit-{uuid4().hex}",
        **headers,
    )
    rejected = api.post(
        f"/api/v1/payments/{pending_id}/reject/",
        {"rejection_reason": "Duplicate"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-stop-reject-{uuid4().hex}",
        **auth_headers(staff_token(manager)),
    )
    assert rejected.status_code == 200, rejected.content
    draft = _draft(
        api,
        headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="5.00",
    )
    cancelled = api.post(
        f"/api/v1/payments/{draft.json()['data']['id']}/cancel/",
        {"reason": "Entered twice"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-stop-cancel-{uuid4().hex}",
        **headers,
    )
    assert cancelled.status_code == 200, cancelled.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Receipt.objects.filter(gym_id=gym.id).count() == 0
        assert CashTransaction.objects.filter(gym_id=gym.id, source_type="PAYMENT").count() == 0


@override_settings(**jwt_settings())
def test_daily_sales_uses_posted_rows_and_a_constant_query() -> None:
    api, gym, _owner, headers = _gym("PAYSALE")
    manager = add_staff(gym, email="paysale-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method="BANK_TRANSFER",
        amount="100.00",
    )
    payment_id = created.json()["data"]["id"]
    api.post(
        f"/api/v1/payments/{payment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-sale-submit-{uuid4().hex}",
        **headers,
    )
    approved = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-sale-approve-{uuid4().hex}",
        **auth_headers(staff_token(manager)),
    )
    assert approved.status_code == 200, approved.content
    today = gym_today(gym).isoformat()

    def read() -> object:
        with CaptureQueriesContext(connection) as captured:
            response = api.get(f"/api/v1/sales/daily/?date={today}", **headers)
        assert response.status_code == 200, response.content
        return response, len(captured)

    first, first_count = read()
    _second, second_count = read()
    assert first_count == second_count
    data = first.json()["data"]
    assert data["gross_sales"] == "100.00"
    assert data["net_total"] == "100.00"
    assert data["new_member_total"] == "100.00"
    assert data["pt_total"] == "0.00"
    assert data["product_quantity"] == "0.00"
    assert data["product_cogs"] == "0.00"
    assert data["product_gross_profit"] == "0.00"


@override_settings(**jwt_settings())
def test_receipt_download_escapes_markup() -> None:
    api, gym, _owner, headers = _gym("PAYHTML")
    manager = add_staff(gym, email="payhtml-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method="BANK_TRANSFER",
    )
    payment_id = created.json()["data"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Payment.objects.filter(id=payment_id).update(description="<script>alert(1)</script>")
    api.post(
        f"/api/v1/payments/{payment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-html-submit-{uuid4().hex}",
        **headers,
    )
    approved = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-html-approve-{uuid4().hex}",
        **auth_headers(staff_token(manager)),
    )
    assert approved.status_code == 200, approved.content
    receipt_id = approved.json()["data"]["receipt"]["id"]
    download = api.get(f"/api/v1/receipts/{receipt_id}/download/", **headers)
    assert download.status_code == 200
    assert b"<script>" not in download.content
    assert b"&lt;script&gt;" in download.content
    assert download["X-Content-Type-Options"] == "nosniff"


@override_settings(**jwt_settings())
def test_direct_sql_cannot_post_without_a_receipt() -> None:
    api, gym, owner, headers = _gym("PAYSQL")
    manager = add_staff(gym, email="paysql-manager@example.test", role_key="MANAGER")
    member_id = _member(api, headers, gym)
    moment = timezone.now()
    with pytest.raises(DatabaseError), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Payment.objects.create(
            gym=gym,
            member_id=member_id,
            entry_type=Payment.EntryType.CHARGE,
            purpose=Payment.Purpose.OTHER,
            description="Direct",
            amount=Decimal("1.00"),
            currency=gym.currency,
            method=Payment.Method.BANK_TRANSFER,
            business_date=gym_today(gym),
            received_at=moment,
            status=Payment.Status.POSTED,
            submitted_at=moment,
            submitted_by_membership_id=owner.id,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            posted_at=moment,
            posted_by_membership_id=manager.id,
            created_by_membership_id=owner.id,
            updated_by_membership_id=manager.id,
        )


@override_settings(**jwt_settings())
def test_voided_charge_and_reversal_net_to_zero() -> None:
    api, gym, owner, headers = _gym("PAYNET0")
    receptionist = add_staff(gym, email="paynet0-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paynet0-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)

    def post_full(suffix: str) -> object:
        created = _draft(
            api,
            headers,
            gym=gym,
            member_id=member_id,
            membership_id=membership_id,
            method="BANK_TRANSFER",
            amount="100.00",
            key=f"pay-net0-create-{suffix}",
        )
        assert created.status_code == 201, created.content
        payment_id = created.json()["data"]["id"]
        submitted = api.post(
            f"/api/v1/payments/{payment_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"pay-net0-submit-{suffix}",
            **headers,
        )
        assert submitted.status_code == 200, submitted.content
        return api.post(
            f"/api/v1/payments/{payment_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"pay-net0-approve-{suffix}",
            **manager_headers,
        )

    first = post_full("0001")
    assert first.status_code == 200, first.content
    assert first.json()["data"]["membership_settlement"]["paid_amount"] == "100.00"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.get(membership_id=membership_id, status="POSTED")
        moment = timezone.now()
        reversal = Payment.objects.create(
            gym=gym,
            member_id=original.member_id,
            membership_id=original.membership_id,
            original_payment=original,
            entry_type=Payment.EntryType.REVERSAL,
            purpose=original.purpose,
            description="Full reversal",
            amount=original.amount,
            currency=original.currency,
            method=original.method,
            business_date=original.business_date,
            received_at=moment,
            status=Payment.Status.POSTED,
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            submitted_with_reception_scope=True,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            posted_at=moment,
            posted_by_membership_id=manager.id,
            created_by_membership_id=owner.id,
            updated_by_membership_id=manager.id,
        )
        _receipt_for(reversal, number="LEDGER-NET0", sequence_value=1, actor_id=manager.id)
        Receipt.objects.filter(payment=original, status=Receipt.Status.ISSUED).update(
            status=Receipt.Status.VOID,
            voided_at=moment,
            voided_by_membership_id=manager.id,
            void_reason="reversal",
        )
        original.status = Payment.Status.VOID
        original.voided_at = moment
        original.voided_by_membership_id = manager.id
        original.void_reason = "reversal"
        original.reversal_payment = reversal
        original.save(
            update_fields=[
                "status",
                "voided_at",
                "voided_by_membership_id",
                "void_reason",
                "reversal_payment",
                "updated_at",
            ]
        )
        sold = Membership.objects.get(id=membership_id)
        refresh_membership_payment_status(sold)
        sold.refresh_from_db()
        assert sold.payment_status == "REFUNDED"
    second = post_full("0002")
    assert second.status_code == 200, second.content
    assert second.json()["data"]["membership_settlement"]["paid_amount"] == "100.00"
    assert second.json()["data"]["membership_settlement"]["payment_status"] == "PAID"
    third = post_full("0003")
    assert third.status_code == 409, third.content
    assert third.json()["error"]["code"] == "FINANCE_STATE_CONFLICT"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            Payment.objects.filter(
                membership_id=membership_id,
                entry_type=Payment.EntryType.CHARGE,
                status=Payment.Status.POSTED,
            ).count()
            == 1
        )
        assert (
            Payment.objects.filter(membership_id=membership_id, status="PENDING_APPROVAL").count()
            == 1
        )


@override_settings(**jwt_settings())
def test_legacy_reconciliation_is_deterministic_and_fails_closed() -> None:
    _api, gym_row, owner, _headers = _gym("PAYLEG")
    owner.user.first_name = "Legacy"
    owner.user.last_name = "Owner"
    owner.user.save(update_fields=["first_name", "last_name"])
    receptionist = add_staff(
        gym_row, email="payleg-reception@example.test", role_key="RECEPTIONIST"
    )
    manager = add_staff(gym_row, email="payleg-manager@example.test", role_key="MANAGER")
    with transaction.atomic():
        set_gym_context(gym_id=gym_row.id)
        StaffProfile.objects.create(
            gym=gym_row,
            gym_membership=manager,
            staff_code="DESK",
            display_name="Desk Lead",
            joined_date=gym_today(gym_row),
        )
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("members", "0015_payment_receipt_expand")])
        executor.loader.build_graph()
        state = executor.loader.project_state([("members", "0015_payment_receipt_expand")])
        PaymentHistory = state.apps.get_model("members", "Payment")
        ReceiptHistory = state.apps.get_model("members", "Receipt")
        moment = timezone.now() - timedelta(days=1)
        later = moment + timedelta(hours=2)
        gym = gym_row
        common = {
            "gym_id": gym.id,
            "entry_type": "CHARGE",
            "currency": gym.currency,
            "business_date": moment.date(),
            "created_by_membership_id": owner.id,
            "updated_by_membership_id": owner.id,
        }
        posted = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy posted",
            amount=Decimal("15.00"),
            method="CASH",
            received_at=moment,
            status="POSTED",
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            **common,
        )
        missing = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy missing receipt",
            amount=Decimal("8.00"),
            method="BANK_TRANSFER",
            received_at=later,
            status="POSTED",
            submitted_at=later,
            submitted_by_membership_id=receptionist.id,
            approved_at=later,
            approved_by_membership_id=manager.id,
            **common,
        )
        PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy draft",
            amount=Decimal("1.00"),
            method="CASH",
            received_at=moment,
            status="DRAFT",
            **common,
        )
        PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy pending",
            amount=Decimal("1.00"),
            method="CASH",
            received_at=moment,
            status="PENDING_APPROVAL",
            submitted_at=moment,
            submitted_by_membership_id=owner.id,
            **common,
        )
        PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy rejected",
            amount=Decimal("1.00"),
            method="CASH",
            received_at=moment,
            status="REJECTED",
            rejected_at=moment,
            rejected_by_membership_id=owner.id,
            rejection_reason="Duplicate",
            **common,
        )
        PaymentHistory.objects.create(
            purpose="OTHER",
            description="Legacy cancelled",
            amount=Decimal("1.00"),
            method="CASH",
            received_at=moment,
            status="CANCELLED",
            cancellation_reason="Entered twice",
            **common,
        )
        incomplete = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Incomplete void",
            amount=Decimal("4.00"),
            method="CARD",
            received_at=moment,
            status="VOID",
            voided_at=moment,
            voided_by_membership_id=owner.id,
            void_reason="Missing charge evidence",
            **common,
        )
        self_approved = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Self approved",
            amount=Decimal("3.00"),
            method="BANK_TRANSFER",
            received_at=moment,
            status="POSTED",
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            approved_at=moment,
            approved_by_membership_id=owner.id,
            **common,
        )
        voided = PaymentHistory.objects.create(
            **{
                **common,
                "purpose": "OTHER",
                "description": "Legacy void",
                "amount": Decimal("4.00"),
                "method": "CARD",
                "received_at": moment,
                "status": "VOID",
                "submitted_at": moment,
                "submitted_by_membership_id": receptionist.id,
                "approved_at": moment,
                "approved_by_membership_id": manager.id,
                "voided_at": moment + timedelta(hours=1),
                "void_reason": "Drawer",
                "updated_by_membership_id": manager.id,
            }
        )
        reversal = PaymentHistory.objects.create(
            gym_id=gym.id,
            original_payment_id=voided.id,
            entry_type="REVERSAL",
            purpose="OTHER",
            description="Refund: Void: Drawer",
            amount=Decimal("4.00"),
            currency=gym.currency,
            method="CARD",
            business_date=moment.date(),
            received_at=moment + timedelta(hours=1),
            status="POSTED",
            approved_at=moment + timedelta(hours=1),
            approved_by_membership_id=manager.id,
            created_by_membership_id=manager.id,
            updated_by_membership_id=manager.id,
        )
        refund = PaymentHistory.objects.create(
            gym_id=gym.id,
            original_payment_id=posted.id,
            entry_type="REFUND",
            purpose="OTHER",
            description="Refund: Customer request",
            amount=Decimal("5.00"),
            currency=gym.currency,
            method="CASH",
            business_date=later.date(),
            received_at=later,
            status="POSTED",
            approved_at=later,
            approved_by_membership_id=manager.id,
            created_by_membership_id=manager.id,
            updated_by_membership_id=manager.id,
        )
        email_only = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Email issuer",
            amount=Decimal("6.00"),
            method="BANK_TRANSFER",
            received_at=later + timedelta(minutes=30),
            status="POSTED",
            submitted_at=later,
            submitted_by_membership_id=manager.id,
            approved_at=later + timedelta(minutes=30),
            approved_by_membership_id=receptionist.id,
            **common,
        )
        ReceiptHistory.objects.create(
            gym_id=gym.id,
            payment_id=posted.id,
            receipt_number="RCPT-KEEP",
            issued_at=moment,
            member_number="Walk-in",
            member_name="Walk-in",
            description="Legacy posted",
            amount=Decimal("15.00"),
            currency=gym.currency,
            method="CASH",
            business_date=moment.date(),
            issuer_name="Legacy Owner",
            status="ISSUED",
            created_by_membership_id=owner.id,
        )
        ReceiptHistory.objects.create(
            gym_id=gym.id,
            payment_id=voided.id,
            receipt_number="RCPT-VOID",
            issued_at=moment + timedelta(hours=1),
            member_number="Walk-in",
            member_name="Walk-in",
            description="Legacy void",
            amount=Decimal("4.00"),
            currency=gym.currency,
            method="CARD",
            business_date=moment.date(),
            issuer_name="Legacy Owner",
            status="ISSUED",
            created_by_membership_id=owner.id,
        )
        ReceiptHistory.objects.create(
            gym_id=gym.id,
            payment_id=reversal.id,
            receipt_number=f"{moment.date():%Y%m%d}-{str(reversal.id).split('-')[0].upper()}",
            issued_at=later + timedelta(hours=2),
            member_number="Walk-in",
            member_name="Walk-in",
            description="Refund: Void: Drawer",
            amount=Decimal("4.00"),
            currency=gym.currency,
            method="CARD",
            business_date=moment.date(),
            issuer_name=manager.user.email,
            status="ISSUED",
            created_by_membership_id=manager.id,
        )
        ReceiptHistory.objects.create(
            gym_id=gym.id,
            payment_id=refund.id,
            receipt_number=f"{later.date():%Y%m%d}-{str(refund.id).split('-')[0].upper()}",
            issued_at=later + timedelta(hours=1),
            member_number="Walk-in",
            member_name="Walk-in",
            description="Refund: Customer request",
            amount=Decimal("5.00"),
            currency=gym.currency,
            method="CASH",
            business_date=later.date(),
            issuer_name=manager.user.email,
            status="ISSUED",
            created_by_membership_id=manager.id,
        )
        bad = PaymentHistory.objects.create(
            purpose="PT",
            description="Unsupported",
            amount=Decimal("9.00"),
            method="CASH",
            received_at=moment,
            status="DRAFT",
            **common,
        )
        contradictory = PaymentHistory.objects.create(
            purpose="OTHER",
            description="Contradictory",
            amount=Decimal("2.00"),
            method="CASH",
            received_at=moment,
            status="DRAFT",
            submitted_at=moment,
            **common,
        )
        with pytest.raises(
            RuntimeError,
            match=(
                r"charge_sod_violation=.*contradictory_lifecycle=.*"
                r"missing_lifecycle_evidence=.*unsupported_source="
            ),
        ):
            executor.migrate([("members", "0016_payment_receipt_legacy_reconcile")])
        executor.loader.build_graph()
        state = executor.loader.project_state([("members", "0015_payment_receipt_expand")])
        PaymentHistory = state.apps.get_model("members", "Payment")
        assert PaymentHistory.objects.get(id=posted.id).posted_at is None
        assert PaymentHistory.objects.get(id=voided.id).voided_by_membership_id is None
        bad.delete()
        contradictory.delete()
        incomplete.delete()
        self_approved.delete()
        executor.migrate([("members", "0017_payment_receipt_hardening")])
        executor.loader.build_graph()
        current = executor.loader.project_state([("members", "0017_payment_receipt_hardening")])
        PaymentNow = current.apps.get_model("members", "Payment")
        ReceiptNow = current.apps.get_model("members", "Receipt")
        restored = PaymentNow.objects.get(id=posted.id)
        assert restored.posted_at == restored.approved_at
        assert restored.legacy_unlinked_cash is True
        kept = ReceiptNow.objects.get(payment_id=posted.id)
        assert kept.receipt_number == "RCPT-KEEP"
        assert kept.sequence_key == f"LEGACY:{moment.year}"
        assert kept.sequence_value == 1
        created = ReceiptNow.objects.get(payment_id=missing.id)
        assert created.receipt_number == f"LEGACY-{later.year}-{3:06d}"
        assert created.member_name == "Walk-in"
        assert created.issuer_name == "Desk Lead"
        assert PaymentNow.objects.get(id=missing.id).legacy_unlinked_cash is False
        email_receipt = ReceiptNow.objects.get(payment_id=email_only.id)
        assert email_receipt.issuer_name == receptionist.user.email
        void_row = PaymentNow.objects.get(id=voided.id)
        assert void_row.voided_by_membership_id == manager.id
        assert void_row.reversal_payment_id == reversal.id
        reversal_row = PaymentNow.objects.get(id=reversal.id)
        assert reversal_row.original_payment_id == voided.id
        assert reversal_row.submitted_at is None
        assert reversal_row.posted_at == reversal_row.approved_at
        refund_row = PaymentNow.objects.get(id=refund.id)
        assert refund_row.submitted_at is None
        assert refund_row.posted_at == refund_row.approved_at
        assert refund_row.legacy_unlinked_cash is True
        refund_receipt = ReceiptNow.objects.get(payment_id=refund.id)
        assert refund_receipt.receipt_number.startswith(f"{later.date():%Y%m%d}-")
        assert refund_receipt.issuer_name == manager.user.email
        void_receipt = ReceiptNow.objects.get(payment_id=voided.id)
        assert void_receipt.receipt_number == "RCPT-VOID"
        assert void_receipt.status == "VOID"
        assert void_receipt.void_reason == "Drawer"
        untouched = PaymentNow.objects.filter(
            gym_id=gym.id,
            status__in=["DRAFT", "PENDING_APPROVAL", "REJECTED", "CANCELLED"],
        )
        assert untouched.count() == 4
        assert not ReceiptNow.objects.filter(payment_id__in=untouched.values("id")).exists()
        spec = importlib.util.spec_from_file_location(
            "legacy_reconcile",
            "apps/members/migrations/0016_payment_receipt_legacy_reconcile.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.reconcile_legacy_payments(current.apps, None)
        assert ReceiptNow.objects.filter(payment_id=posted.id).count() == 1
    finally:
        applied = set(
            MigrationRecorder.Migration.objects.filter(app="members").values_list("name", flat=True)
        )
        if "0017_payment_receipt_hardening" not in applied:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM members_receipt WHERE gym_id = %s", [gym_row.id])
                cursor.execute("DELETE FROM members_payment WHERE gym_id = %s", [gym_row.id])
        MigrationExecutor(connection).migrate([("members", "0017_payment_receipt_hardening")])


@override_settings(**jwt_settings())
def test_legacy_overpayment_counts_voided_charges() -> None:
    api, gym_row, owner, headers = _gym("PAYOVR")
    receptionist = add_staff(
        gym_row, email="payovr-reception@example.test", role_key="RECEPTIONIST"
    )
    manager = add_staff(gym_row, email="payovr-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    member_id = _member(api, headers, gym_row)
    membership_id = _membership(api, headers, gym_row, member_id)
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("members", "0015_payment_receipt_expand")])
        executor.loader.build_graph()
        state = executor.loader.project_state([("members", "0015_payment_receipt_expand")])
        PaymentHistory = state.apps.get_model("members", "Payment")
        moment = timezone.now() - timedelta(days=1)
        charge = {
            "gym_id": gym_row.id,
            "member_id": member_id,
            "membership_id": membership_id,
            "entry_type": "CHARGE",
            "purpose": "MEMBERSHIP",
            "currency": gym_row.currency,
            "method": "BANK_TRANSFER",
            "business_date": moment.date(),
            "received_at": moment,
            "submitted_at": moment,
            "submitted_by_membership_id": receptionist.id,
            "approved_at": moment,
            "approved_by_membership_id": manager.id,
            "created_by_membership_id": owner.id,
            "updated_by_membership_id": manager.id,
        }
        voided = PaymentHistory.objects.create(
            description="Voided charge",
            amount=Decimal("100.00"),
            status="VOID",
            voided_at=moment,
            void_reason="Drawer",
            **charge,
        )
        PaymentHistory.objects.create(
            gym_id=gym_row.id,
            member_id=member_id,
            membership_id=membership_id,
            original_payment_id=voided.id,
            entry_type="REVERSAL",
            purpose="MEMBERSHIP",
            description="Refund: Void: Drawer",
            amount=Decimal("100.00"),
            currency=gym_row.currency,
            method="BANK_TRANSFER",
            business_date=moment.date(),
            received_at=moment,
            status="POSTED",
            approved_at=moment,
            approved_by_membership_id=manager.id,
            created_by_membership_id=manager.id,
            updated_by_membership_id=manager.id,
        )
        PaymentHistory.objects.create(
            description="Replacement charge",
            amount=Decimal("100.00"),
            status="POSTED",
            **charge,
        )
        excess = PaymentHistory.objects.create(
            description="Over the obligation",
            amount=Decimal("40.00"),
            status="POSTED",
            **charge,
        )
        with pytest.raises(
            RuntimeError,
            match=r"^payment reconciliation failed: posted_exceeds_obligation=1$",
        ):
            executor.migrate([("members", "0016_payment_receipt_legacy_reconcile")])
        executor.loader.build_graph()
        rolled = executor.loader.project_state([("members", "0015_payment_receipt_expand")])
        rolled.apps.get_model("members", "Payment").objects.get(id=excess.id).delete()
        executor.migrate([("members", "0017_payment_receipt_hardening")])
        executor.loader.build_graph()
        current = executor.loader.project_state([("members", "0017_payment_receipt_hardening")])
        PaymentNow = current.apps.get_model("members", "Payment")
        restored = PaymentNow.objects.get(id=voided.id)
        assert restored.status == "VOID"
        assert restored.reversal_payment_id is not None
        posted = PaymentNow.objects.filter(
            gym_id=gym_row.id,
            membership_id=membership_id,
            entry_type="CHARGE",
            status="POSTED",
        )
        assert posted.count() == 1
        assert posted.get().amount == Decimal("100.00")
        assert not PaymentNow.objects.filter(id=excess.id).exists()
    finally:
        applied = set(
            MigrationRecorder.Migration.objects.filter(app="members").values_list("name", flat=True)
        )
        if "0017_payment_receipt_hardening" not in applied:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM members_receipt WHERE gym_id = %s", [gym_row.id])
                cursor.execute("DELETE FROM members_payment WHERE gym_id = %s", [gym_row.id])
        MigrationExecutor(connection).migrate([("members", "0017_payment_receipt_hardening")])


def _other_payment(api: APIClient, headers: dict[str, str], gym: Gym, amount: str) -> str:
    created = api.post(
        "/api/v1/payments/",
        {
            "purpose": "OTHER",
            "description": "Walk-in",
            "amount": amount,
            "currency": gym.currency,
            "method": "BANK_TRANSFER",
            "business_date": gym_today(gym).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-other-{uuid4().hex}",
        **headers,
    )
    assert created.status_code == 201, created.content
    return str(created.json()["data"]["id"])


def _receipt_for(payment: Payment, *, number: str, sequence_value: int, actor_id: object) -> None:
    Receipt.objects.create(
        gym_id=payment.gym_id,
        payment=payment,
        receipt_number=number,
        sequence_key=f"LEDGER:{payment.business_date.year}",
        sequence_value=sequence_value,
        sequence_year=payment.business_date.year,
        issued_at=payment.received_at,
        gym_code="GYM",
        gym_name="Gym",
        gym_address="",
        member_number="Walk-in",
        member_name="Walk-in",
        description=payment.description,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        business_date=payment.business_date,
        issuer_name="Ledger",
        status=Receipt.Status.ISSUED,
        created_by_membership_id=actor_id,
    )


@override_settings(**jwt_settings())
def test_daily_sales_keeps_void_gross_and_dates_returns() -> None:
    api, gym, owner, headers = _gym("PAYNET")
    receptionist = add_staff(gym, email="paynet-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paynet-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    manager_headers = auth_headers(staff_token(manager))
    today = gym_today(gym)
    yesterday = today - timedelta(days=1)
    charge_id = _other_payment(api, headers, gym, "100.00")
    void_id = _other_payment(api, headers, gym, "40.00")
    for payment_id in (charge_id, void_id):
        assert (
            api.post(
                f"/api/v1/payments/{payment_id}/submit/",
                {},
                format="json",
                HTTP_IDEMPOTENCY_KEY=f"pay-net-submit-{uuid4().hex}",
                **headers,
            ).status_code
            == 200
        )
        approved = api.post(
            f"/api/v1/payments/{payment_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"pay-net-approve-{uuid4().hex}",
            **manager_headers,
        )
        assert approved.status_code == 200, approved.content
    moment = timezone.now()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.get(id=charge_id)
        refund = Payment.objects.create(
            gym=gym,
            original_payment=original,
            entry_type=Payment.EntryType.REFUND,
            purpose=original.purpose,
            description="Refund",
            amount=Decimal("25.00"),
            currency=gym.currency,
            method=Payment.Method.BANK_TRANSFER,
            business_date=today,
            received_at=moment,
            status=Payment.Status.POSTED,
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            posted_at=moment,
            posted_by_membership_id=manager.id,
            created_by_membership_id=owner.id,
            updated_by_membership_id=manager.id,
            submitted_with_reception_scope=True,
        )
        _receipt_for(refund, number="LEDGER-REFUND", sequence_value=1, actor_id=manager.id)
        voided = Payment.objects.get(id=void_id)
        reversal = Payment.objects.create(
            gym=gym,
            original_payment=voided,
            entry_type=Payment.EntryType.REVERSAL,
            purpose=voided.purpose,
            description="Reversal",
            amount=voided.amount,
            currency=gym.currency,
            method=Payment.Method.BANK_TRANSFER,
            business_date=yesterday,
            received_at=moment,
            status=Payment.Status.POSTED,
            submitted_at=moment,
            submitted_by_membership_id=receptionist.id,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            posted_at=moment,
            posted_by_membership_id=manager.id,
            created_by_membership_id=owner.id,
            updated_by_membership_id=manager.id,
            submitted_with_reception_scope=True,
        )
        _receipt_for(reversal, number="LEDGER-REVERSAL", sequence_value=2, actor_id=manager.id)
        Receipt.objects.filter(payment=voided, status=Receipt.Status.ISSUED).update(
            status=Receipt.Status.VOID,
            voided_at=moment,
            voided_by_membership_id=manager.id,
            void_reason="reversal",
        )
        voided.status = Payment.Status.VOID
        voided.voided_at = moment
        voided.voided_by_membership_id = manager.id
        voided.void_reason = "reversal"
        voided.reversal_payment = reversal
        voided.save(
            update_fields=[
                "status",
                "voided_at",
                "voided_by_membership_id",
                "void_reason",
                "reversal_payment",
                "updated_at",
            ]
        )

    def read(day: object) -> tuple[dict[str, object], int]:
        with CaptureQueriesContext(connection) as captured:
            response = api.get(f"/api/v1/sales/daily/?date={day}", **headers)
        assert response.status_code == 200, response.content
        data = response.json()["data"]
        assert isinstance(data, dict)
        return data, len(captured)

    same_day, same_count = read(today.isoformat())
    _again, again_count = read(today.isoformat())
    prior_day, _prior_count = read(yesterday.isoformat())
    assert same_count == again_count
    assert same_day["gross_sales"] == "140.00"
    assert same_day["refunds"] == "25.00"
    assert same_day["reversals"] == "0.00"
    assert same_day["net_total"] == "115.00"
    assert same_day["other_total"] == "115.00"
    assert prior_day["gross_sales"] == "0.00"
    assert prior_day["reversals"] == "40.00"
    assert prior_day["net_total"] == "-40.00"
    assert prior_day["other_total"] == "-40.00"
    assert prior_day["pt_total"] == "0.00"
    assert prior_day["product_cogs"] == "0.00"
    nets = Decimal(str(same_day["net_total"])) + Decimal(str(prior_day["net_total"]))
    assert nets == Decimal("75.00")


@override_settings(**jwt_settings())
def test_receipt_issuer_uses_staff_name_then_email() -> None:
    api, gym, _owner, headers = _gym("PAYISS")
    named = add_staff(gym, email="payiss-named@example.test", role_key="MANAGER")
    plain = add_staff(gym, email="payiss-plain@example.test", role_key="MANAGER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.create(
            gym=gym,
            gym_membership=named,
            staff_code="DESK",
            display_name="Desk Lead",
            joined_date=gym_today(gym),
        )
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    for actor, expected in (
        (named, "Desk Lead"),
        (plain, plain.user.email),
    ):
        created = _draft(
            api,
            headers,
            gym=gym,
            member_id=member_id,
            membership_id=membership_id,
            method="BANK_TRANSFER",
            amount="10.00",
        )
        payment_id = created.json()["data"]["id"]
        assert (
            api.post(
                f"/api/v1/payments/{payment_id}/submit/",
                {},
                format="json",
                HTTP_IDEMPOTENCY_KEY=f"pay-iss-submit-{uuid4().hex}",
                **headers,
            ).status_code
            == 200
        )
        approved = api.post(
            f"/api/v1/payments/{payment_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"pay-iss-approve-{uuid4().hex}",
            **auth_headers(staff_token(actor)),
        )
        assert approved.status_code == 200, approved.content
        assert approved.json()["data"]["receipt"]["issuer_name"] == expected
