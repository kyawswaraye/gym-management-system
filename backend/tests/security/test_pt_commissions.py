"""Commission list, detail, partial payout, and internal reversal."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.cashbook.models import CashSession, CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.expenses.services import post_pt_commission_expense, void_pt_commission_expense
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
    StaffProfile,
)
from apps.trainers.exceptions import CommissionOverpay, CommissionPayoutConflict
from apps.trainers.models import PTCommission, PTCommissionPayment
from apps.trainers.pt_commission_services import (
    record_pt_commission_payment,
    reverse_pt_commission_payment,
)
from apps.users.models import User
from tests.security.test_pt_sales import _key, _sale
from tests.security.test_tenant_gym_settings import (
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _member, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code, defaults={"name": code, "description": code}
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


def _open_till(
    api: APIClient, headers: dict[str, str], gym: Gym, *, register_code: str = "FRONT"
) -> str:
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": register_code,
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "50.00",
        },
        format="json",
        **_key("till"),
        **headers,
    )
    assert opened.status_code == 201, opened.content
    return str(opened.json()["data"]["session"]["id"])


def _post_sale(
    api: APIClient,
    owner_headers: dict[str, str],
    manager_headers: dict[str, str],
    *,
    member_id: str,
    trainer_id: str,
    day: str,
    creator_headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    headers = owner_headers if creator_headers is None else creator_headers
    created = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, trainer_id, day, description="Commission session"),
        format="json",
        **headers,
        **_key("sale"),
    )
    assert created.status_code == 201, created.content
    sale_id = str(created.json()["data"]["id"])
    submitted = api.post(
        f"/api/v1/pt-sales/{sale_id}/submit/",
        format="json",
        **headers,
        **_key("submit"),
    )
    assert submitted.status_code == 200, submitted.content
    approved = api.post(
        f"/api/v1/pt-sales/{sale_id}/approve/",
        {},
        format="json",
        **manager_headers,
        **_key("approve"),
    )
    assert approved.status_code == 200, approved.content
    body = approved.json()["data"]
    return str(body["sale"]["id"]), str(body["commission"]["id"])


def _pay(
    api: APIClient,
    headers: dict[str, str],
    commission_id: str,
    body: dict[str, object],
    *,
    key: str | None = None,
) -> object:
    return api.post(
        f"/api/v1/pt-commissions/{commission_id}/record-payment/",
        body,
        format="json",
        **headers,
        **({"HTTP_IDEMPOTENCY_KEY": key} if key else _key("pay")),
    )


@override_settings(**jwt_settings())
def test_commission_payouts_visibility_reversal_and_guards() -> None:
    api, platform_access = platform_token("ptcom-platform@example.test")
    first = provision(api, platform_access, code="PTC-A", owner_email="ptcom-owner@example.test")
    second = provision(api, platform_access, code="PTC-B", owner_email="ptcom-other@example.test")
    gym = Gym.objects.get(id=first["gym"]["id"])
    other = Gym.objects.get(id=second["gym"]["id"])
    _enable(gym, "finance")
    owner = GymMembership.objects.select_related("user").get(
        id=first["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=second["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptcom-manager@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptcom-payer@example.test", role_key="MANAGER")
    accountant = add_staff(gym, email="ptcom-accountant@example.test", role_key="ACCOUNTANT")
    reception = add_staff(gym, email="ptcom-reception@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="ptcom-ada@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym, email="ptcom-bea@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTC-ADA", name="Ada Coach")
    bea = _profile(gym, other_trainer, code="PTC-BEA", name="Bea Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id__in=[ada.id, bea.id]).update(
            default_commission_percent="33.33"
        )
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    payer_headers = auth_headers(staff_token(payer))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    reception_headers = auth_headers(staff_token(reception))
    other_headers = auth_headers(staff_token(other_owner))
    day = gym_today(gym).isoformat()
    member_id = _member(api, owner_headers, first_name="Nila")
    other_member = _member(api, owner_headers, first_name="Omar")
    assert (
        _assign(
            api, owner_headers, trainer_id=str(ada.id), member_id=member_id, start=day
        ).status_code
        == 201
    )
    assert (
        _assign(
            api, owner_headers, trainer_id=str(bea.id), member_id=other_member, start=day
        ).status_code
        == 201
    )
    sale_id, commission_id = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        creator_headers=reception_headers,
    )
    _hidden_sale, hidden_id = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=other_member,
        trainer_id=str(bea.id),
        day=day,
    )
    reception_session_id = _open_till(api, reception_headers, gym, register_code="FRONT")
    session_id = _open_till(api, payer_headers, gym, register_code="PAY")
    assert session_id != reception_session_id
    blocked_void = api.post(
        f"/api/v1/pt-sales/{sale_id}/void/",
        {"reason": "Not published"},
        format="json",
        **owner_headers,
        **_key("sale-void"),
    )
    assert blocked_void.status_code == 400

    cash_body = {
        "amount": "1.00",
        "business_date": day,
        "payment_method": "CASH",
        "cash_session_id": session_id,
        "reference_number": "  EXT-19  ",
        "policy_note": "  October   policy ",
    }
    partial = _pay(api, payer_headers, commission_id, cash_body)
    assert partial.status_code == 200, partial.content
    paid = partial.json()["data"]
    assert paid["commission_before"]["outstanding_amount"] == "3.33"
    assert paid["commission_before"]["status"] == "ACCRUED"
    assert paid["commission_after"]["paid_amount"] == "1.00"
    assert paid["commission_after"]["outstanding_amount"] == "2.33"
    assert paid["commission_after"]["status"] == "PARTIALLY_PAID"
    assert paid["commission_after"]["paid_at"] is None
    assert paid["commission_after"]["currency"] == "THB"
    assert paid["payment"]["amount"] == "1.00"
    assert paid["payment"]["reference_number"] == "EXT-19"
    assert paid["payment"]["policy_note"] == "October policy"
    assert paid["payment"]["status"] == "POSTED"
    assert paid["cash_transaction"]["direction"] == "OUT"
    assert paid["cash_session"]["id"] == session_id
    expense_id = paid["expense"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        expense = Expense.objects.select_related("category").get(id=expense_id)
        assert expense.category.system_key == "PT_COMMISSION"
        assert expense.status == Expense.Status.POSTED
        assert expense.amount == Decimal("1.00")
        assert expense.payment_method == "CASH"
        assert expense.created_by_membership_id == manager.id
        assert expense.submitted_by_membership_id == manager.id
        assert expense.approved_by_membership_id == payer.id
        assert expense.created_with_reception_scope is False
        assert expense.submitted_with_reception_scope is False
        paid_session = CashSession.objects.get(id=session_id)
        assert paid_session.opened_by_membership_id == payer.id
        reception_session = CashSession.objects.get(id=reception_session_id)
        assert reception_session.opened_by_membership_id == reception.id
    creator_pay = _pay(
        api,
        manager_headers,
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert creator_pay.status_code == 403
    accountant_pay = _pay(
        api,
        accountant_headers,
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert accountant_pay.status_code == 403
    manual_void = api.post(
        f"/api/v1/expenses/{expense_id}/void/",
        {"reason": "Manual correction", "business_date": day},
        format="json",
        **accountant_headers,
        **_key("manual-void"),
    )
    assert manual_void.status_code == 422
    assert manual_void.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"

    noncash = {
        "amount": "1.00",
        "business_date": day,
        "payment_method": "BANK_TRANSFER",
        "cash_session_id": session_id,
    }
    rejected_session = _pay(api, payer_headers, commission_id, noncash)
    assert rejected_session.status_code == 400
    assert "cash_session_id" in rejected_session.json()["error"]["fields"]
    bank = _pay(
        api,
        payer_headers,
        commission_id,
        {"amount": "2.33", "business_date": day, "payment_method": "BANK_TRANSFER"},
    )
    assert bank.status_code == 200, bank.content
    settled = bank.json()["data"]
    assert settled["commission_after"]["status"] == "PAID"
    assert settled["commission_after"]["paid_amount"] == "3.33"
    assert settled["commission_after"]["outstanding_amount"] == "0.00"
    assert settled["commission_after"]["paid_at"] is not None
    assert settled["cash_session"] is None
    assert settled["cash_transaction"] is None
    assert settled["payment"]["reference_number"] == ""
    assert settled["payment"]["policy_note"] == ""

    overpay = _pay(
        api,
        payer_headers,
        commission_id,
        {"amount": "0.01", "business_date": day, "payment_method": "OTHER"},
    )
    assert overpay.status_code == 422
    assert overpay.json()["error"]["code"] == "COMMISSION_OVERPAY"
    zero = _pay(
        api,
        payer_headers,
        commission_id,
        {"amount": "0.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert zero.status_code == 400
    assert "amount" in zero.json()["error"]["fields"]

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="0.00")
    _zero_sale, zero_commission = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
    )
    nothing_due = _pay(
        api,
        payer_headers,
        zero_commission,
        {"amount": "0.01", "business_date": day, "payment_method": "OTHER"},
    )
    assert nothing_due.status_code == 422
    assert nothing_due.json()["error"]["code"] == "COMMISSION_OVERPAY"

    listed = api.get("/api/v1/pt-commissions/?limit=1", **accountant_headers)
    assert listed.status_code == 200, listed.content
    assert listed.json()["meta"]["has_more"] is True
    cursor = listed.json()["meta"]["next_after"]
    page = api.get(
        "/api/v1/pt-commissions/",
        {"limit": 25, "after": cursor},
        **accountant_headers,
    )
    assert page.status_code == 200, page.content
    first_ids = [row["id"] for row in listed.json()["data"]]
    rest_ids = [row["id"] for row in page.json()["data"]]
    assert first_ids != rest_ids
    assert set(first_ids).isdisjoint(rest_ids)
    own = api.get("/api/v1/pt-commissions/?limit=25", **trainer_headers)
    assert own.status_code == 200, own.content
    assert {row["trainer_staff_profile_id"] for row in own.json()["data"]} == {str(ada.id)}
    assert api.get(f"/api/v1/pt-commissions/{hidden_id}/", **trainer_headers).status_code == 404
    filtered = api.get(
        f"/api/v1/pt-commissions/?trainer_staff_profile_id={bea.id}&limit=25",
        **trainer_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []
    dated = api.get(
        f"/api/v1/pt-commissions/?service_date_from={day}&service_date_to={day}&status=PAID",
        **accountant_headers,
    )
    assert dated.status_code == 200
    assert commission_id in {row["id"] for row in dated.json()["data"]}
    detail = api.get(f"/api/v1/pt-commissions/{commission_id}/", **accountant_headers)
    assert detail.status_code == 200, detail.content
    children = detail.json()["data"]["payments"]
    assert [row["amount"] for row in children] == ["1.00", "2.33"]
    assert api.get("/api/v1/pt-commissions/", **reception_headers).status_code == 403
    assert api.get(f"/api/v1/pt-commissions/{commission_id}/", **other_headers).status_code == 404
    assert api.get("/api/v1/pt-commissions/", **other_headers).json()["data"] == []
    platform = api.get(
        "/api/v1/pt-commissions/",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
    )
    assert platform.status_code == 401
    trainer_pay = _pay(
        api,
        trainer_headers,
        commission_id,
        {"amount": "0.01", "business_date": day, "payment_method": "OTHER"},
    )
    assert trainer_pay.status_code == 403
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not RolePermission.objects.filter(
            gym_id=gym.id,
            role__key="ACCOUNTANT",
            permission__code="pt.commissions.pay",
        ).exists()
        assert RolePermission.objects.filter(
            gym_id=gym.id,
            role__key="MANAGER",
            role__source_template__key="MANAGER",
            permission__code="pt.commissions.pay",
        ).exists()
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
    composed_trainer = _pay(
        api,
        auth_headers(staff_token(trainer)),
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert composed_trainer.status_code == 403
    with pytest.raises((InternalError, ProgrammingError), match="role_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            custom = Role.objects.create(
                gym=gym,
                key="PTPAY",
                name="PT Pay",
                created_by_membership_id=owner.id,
            )
            RolePermission.objects.create(
                gym=gym,
                role=custom,
                permission=Permission.objects.get(code="pt.commissions.pay"),
                created_by_membership_id=owner.id,
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        custom_only_role = Role.objects.create(
            gym=gym,
            key="FLOORPAY",
            name="Floor Pay",
            created_by_membership_id=owner.id,
        )
        custom_user = User.objects.create_user("ptcom-custom@example.test", PASSWORD)
        custom_membership = GymMembership.objects.create(
            gym=gym,
            user=custom_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(
            gym=gym, gym_membership=custom_membership, role=custom_only_role
        )
    custom_membership.user = custom_user
    custom_only = _pay(
        api,
        auth_headers(staff_token(custom_membership)),
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert custom_only.status_code == 403
    hidden_expenses = api.get("/api/v1/expenses/?limit=100", **reception_headers)
    assert hidden_expenses.status_code == 200, hidden_expenses.content
    assert expense_id not in {row["id"] for row in hidden_expenses.json()["data"]}
    visible_expense = api.get(f"/api/v1/expenses/{expense_id}/", **accountant_headers)
    assert visible_expense.status_code == 200, visible_expense.content

    replay_key = f"replay-{uuid4().hex}"
    replay_body = {"amount": "1.00", "business_date": day, "payment_method": "OTHER"}
    first_pay = _pay(api, payer_headers, hidden_id, replay_body, key=replay_key)
    second_pay = _pay(api, payer_headers, hidden_id, replay_body, key=replay_key)
    assert first_pay.status_code == 200, first_pay.content
    assert second_pay.status_code == 200, second_pay.content
    assert first_pay.json()["data"]["payment"]["id"] == second_pay.json()["data"]["payment"]["id"]
    mismatch = _pay(
        api,
        payer_headers,
        hidden_id,
        {"amount": "1.50", "business_date": day, "payment_method": "OTHER"},
        key=replay_key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            PTCommissionPayment.objects.filter(pt_commission_id=hidden_id, status="POSTED").count()
            == 1
        )

    bank_payment = PTCommissionPayment.objects.get(id=settled["payment"]["id"])
    with pytest.raises(RuntimeError, match="active transaction"):
        reverse_pt_commission_payment(
            gym=gym,
            actor=accountant,
            payment=bank_payment,
            cash_session_id=None,
            reason="Outside",
            business_date=gym_today(gym),
            request_id="req_outside",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        reverse_pt_commission_payment(
            gym=gym,
            actor=accountant,
            payment=bank_payment,
            cash_session_id=None,
            reason="  Sale   correction ",
            business_date=gym_today(gym),
            request_id="req_reverse",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        parent = PTCommission.objects.get(id=commission_id)
        voided = PTCommissionPayment.objects.select_related(
            "reversal_expense", "reversal_cash_transaction"
        ).get(id=bank_payment.id)
        original = Expense.objects.get(id=voided.expense_id)
        assert voided.status == PTCommissionPayment.Status.VOID
        assert voided.void_reason == "Sale correction"
        assert voided.voided_by_membership_id == accountant.id
        assert voided.reversal_expense_id is not None
        assert voided.reversal_cash_transaction_id is None
        assert original.status == Expense.Status.VOID
        assert original.reversal_expense_id == voided.reversal_expense_id
        assert parent.paid_amount == Decimal("1.00")
        assert parent.outstanding_amount == Decimal("2.33")
        assert parent.status == PTCommission.Status.PARTIALLY_PAID
        assert parent.paid_at is None
    with pytest.raises(CommissionPayoutConflict):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            reverse_pt_commission_payment(
                gym=gym,
                actor=accountant,
                payment=voided,
                cash_session_id=None,
                reason="Again",
                business_date=gym_today(gym),
                request_id="req_again",
                ip_address=None,
                user_agent="",
            )

    cash_payment = PTCommissionPayment.objects.get(id=paid["payment"]["id"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        reverse_pt_commission_payment(
            gym=gym,
            actor=accountant,
            payment=cash_payment,
            cash_session_id=session_id,
            reason="Cash correction",
            business_date=gym_today(gym),
            request_id="req_cash_reverse",
            ip_address=None,
            user_agent="",
        )
        corrected = PTCommissionPayment.objects.get(id=cash_payment.id)
        inbound = CashTransaction.objects.get(id=corrected.reversal_cash_transaction_id)
        assert corrected.status == PTCommissionPayment.Status.VOID
        assert inbound.direction == CashTransaction.Direction.IN
        assert inbound.reversal_of_id == corrected.cash_transaction_id
        restored = PTCommission.objects.get(id=commission_id)
        assert restored.paid_amount == Decimal("0.00")
        assert restored.outstanding_amount == Decimal("3.33")
        assert restored.status == PTCommission.Status.ACCRUED
    owner_pay = _pay(
        api,
        owner_headers,
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "CARD"},
    )
    assert owner_pay.status_code == 200, owner_pay.content
    assert owner_pay.json()["data"]["commission_after"]["status"] == "PARTIALLY_PAID"
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE tenants_gym_membership_role DISABLE TRIGGER membership_role_owner_guard"
        )
    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            GymMembershipRole.objects.create(
                gym=gym,
                gym_membership=owner,
                role=Role.objects.get(gym=gym, key="RECEPTIONIST"),
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE tenants_gym_membership_role ENABLE TRIGGER membership_role_owner_guard"
            )
    owner_reception = _pay(
        api,
        owner_headers,
        commission_id,
        {"amount": "1.00", "business_date": day, "payment_method": "OTHER"},
    )
    assert owner_reception.status_code == 403

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="33.33")
    _fresh_sale, fresh = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        before_children = PTCommissionPayment.objects.filter(gym_id=gym.id).count()
        before_expenses = Expense.objects.filter(gym_id=gym.id).count()
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            record_pt_commission_payment(
                gym=gym,
                actor=payer,
                commission_id=fresh,
                amount=Decimal("1.00"),
                business_date=gym_today(gym),
                payment_method="OTHER",
                cash_session_id=None,
                reference_number="",
                policy_note="",
                request_id="req_rollback",
                ip_address=None,
                user_agent="",
            )
            raise RuntimeError("rollback")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTCommissionPayment.objects.filter(gym_id=gym.id).count() == before_children
        assert Expense.objects.filter(gym_id=gym.id).count() == before_expenses
        rolled = PTCommission.objects.get(id=fresh)
        assert rolled.status == PTCommission.Status.ACCRUED
        assert rolled.paid_amount == Decimal("0.00")

    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM trainers_pt_commission WHERE id = %s",
                [commission_id],
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'trainers_pt_commission'
                  AND column_name IN ('commission_expense_id', 'reversal_commission_id')
                """
            )
            assert cursor.fetchall() == []
    with pytest.raises((InternalError, ProgrammingError), match="pt_commission_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "UPDATE trainers_pt_commission SET gross_fee_snapshot = 11 WHERE id = %s",
                    [commission_id],
                )
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "DELETE FROM trainers_pt_commission_payment WHERE pt_commission_id = %s",
                    [commission_id],
                )
    with pytest.raises((InternalError, ProgrammingError), match="pt_commission_payment_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM trainers_pt_commission_payment WHERE pt_commission_id = %s",
                    [commission_id],
                )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'trainers_pt_commission_payment', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission_payment', 'INSERT'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission_payment', 'UPDATE'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission_payment', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False)


@override_settings(**jwt_settings())
def test_simultaneous_partial_payouts_cannot_exceed_outstanding() -> None:
    api, platform_access = platform_token("ptcom-race-platform@example.test")
    payload = provision(
        api, platform_access, code="PTC-R", owner_email="ptcom-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptcom-race-manager@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptcom-race-payer@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="ptcom-race-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTC-RACE", name="Race Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="33.33")
    headers = auth_headers(staff_token(owner))
    day = gym_today(gym).isoformat()
    member_id = _member(api, headers, first_name="Race")
    assert (
        _assign(api, headers, trainer_id=str(ada.id), member_id=member_id, start=day).status_code
        == 201
    )
    _race_sale, commission_id = _post_sale(
        api,
        headers,
        auth_headers(staff_token(manager)),
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
    )
    barrier = Barrier(2, timeout=20)

    def attempt() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    record_pt_commission_payment(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=payer.id),
                        commission_id=commission_id,
                        amount=Decimal("2.00"),
                        business_date=gym_today(gym),
                        payment_method="OTHER",
                        cash_session_id=None,
                        reference_number="",
                        policy_note="",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except CommissionOverpay:
                    return "overpay"
            return "paid"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _item: attempt(), (1, 2)))
    assert results == ["overpay", "paid"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        parent = PTCommission.objects.get(id=commission_id)
        assert parent.paid_amount == Decimal("2.00")
        assert parent.outstanding_amount == Decimal("1.33")
        assert parent.status == PTCommission.Status.PARTIALLY_PAID
        assert (
            PTCommissionPayment.objects.filter(
                pt_commission_id=commission_id, status="POSTED"
            ).count()
            == 1
        )


@override_settings(**jwt_settings())
def test_commission_payout_aggregates_are_checked_at_commit() -> None:
    api, platform_access = platform_token("ptcom-agg-platform@example.test")
    payload = provision(
        api, platform_access, code="PTC-G", owner_email="ptcom-agg-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    approver = add_staff(gym, email="ptcom-agg-approver@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptcom-agg-payer@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="ptcom-agg-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTC-AGG", name="Aggregate Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="33.33")
    headers = auth_headers(staff_token(owner))
    day = gym_today(gym)
    member_id = _member(api, headers, first_name="Gale")
    assert (
        _assign(
            api, headers, trainer_id=str(ada.id), member_id=member_id, start=day.isoformat()
        ).status_code
        == 201
    )
    _sale_id, commission_id = _post_sale(
        api,
        headers,
        auth_headers(staff_token(approver)),
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day.isoformat(),
    )

    def raw_child() -> None:
        expense, _movement = post_pt_commission_expense(
            gym=gym,
            approver=payer,
            created_by_membership_id=approver.id,
            submitted_by_membership_id=approver.id,
            business_date=day,
            description="Unmatched payout",
            amount=Decimal("1.00"),
            currency=gym.currency,
            payment_method="OTHER",
            cash_session_id=None,
            policy_note="",
            request_id="req_raw_child",
            ip_address=None,
            user_agent="",
        )
        PTCommissionPayment.objects.create(
            gym=gym,
            pt_commission_id=commission_id,
            amount=Decimal("1.00"),
            business_date=day,
            payment_method="OTHER",
            status=PTCommissionPayment.Status.POSTED,
            expense=expense,
            created_by_membership_id=payer.id,
        )

    with pytest.raises(DatabaseError, match="payout aggregates"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            raw_child()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        untouched = PTCommission.objects.get(id=commission_id)
        assert untouched.paid_amount == Decimal("0.00")
        assert untouched.status == PTCommission.Status.ACCRUED
        assert PTCommissionPayment.objects.filter(pt_commission_id=commission_id).count() == 0

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        _before, paid, payment, _expense, _movement = record_pt_commission_payment(
            gym=gym,
            actor=payer,
            commission_id=commission_id,
            amount=Decimal("1.00"),
            business_date=day,
            payment_method="OTHER",
            cash_session_id=None,
            reference_number="",
            policy_note="",
            request_id="req_agg_pay",
            ip_address=None,
            user_agent="",
        )
        assert paid.paid_amount == Decimal("1.00")
        assert paid.outstanding_amount == Decimal("2.33")
        assert paid.status == PTCommission.Status.PARTIALLY_PAID
        assert paid.paid_at is None

    with pytest.raises(DatabaseError, match="payout aggregates"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            locked = PTCommissionPayment.objects.select_for_update(of=("self",)).get(id=payment.id)
            original = Expense.objects.select_for_update().get(id=locked.expense_id)
            _voided, reversal, movement = void_pt_commission_expense(
                gym=gym,
                actor=payer,
                original=original,
                reason="Raw void",
                business_date=day,
                cash_session_id=None,
                request_id="req_raw_void",
                ip_address=None,
                user_agent="",
            )
            locked.status = PTCommissionPayment.Status.VOID
            locked.voided_at = timezone.now()
            locked.voided_by_membership_id = payer.id
            locked.void_reason = "Raw void"
            locked.reversal_expense = reversal
            locked.reversal_cash_transaction = movement
            locked.save()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        still_posted = PTCommissionPayment.objects.get(id=payment.id)
        parent = PTCommission.objects.get(id=commission_id)
        assert still_posted.status == PTCommissionPayment.Status.POSTED
        assert parent.paid_amount == Decimal("1.00")
        assert parent.status == PTCommission.Status.PARTIALLY_PAID

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        reverse_pt_commission_payment(
            gym=gym,
            actor=payer,
            payment=PTCommissionPayment.objects.get(id=payment.id),
            cash_session_id=None,
            reason="Matched reversal",
            business_date=day,
            request_id="req_agg_reverse",
            ip_address=None,
            user_agent="",
        )
        restored = PTCommission.objects.get(id=commission_id)
        assert restored.paid_amount == Decimal("0.00")
        assert restored.outstanding_amount == Decimal("3.33")
        assert restored.status == PTCommission.Status.ACCRUED
        assert restored.paid_at is None
        assert (
            PTCommissionPayment.objects.get(id=payment.id).status == PTCommissionPayment.Status.VOID
        )


@override_settings(**jwt_settings())
def test_commission_payout_date_stays_inside_the_sale_window() -> None:
    api, platform_access = platform_token("ptcom-date-platform@example.test")
    payload = provision(
        api, platform_access, code="PTC-D", owner_email="ptcom-date-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable(gym, "finance")
    _enable(gym, "pt")
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptcom-date-manager@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptcom-date-payer@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="ptcom-date-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTC-DATE", name="Date Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="50.00")
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    payer_headers = auth_headers(staff_token(payer))
    today = gym_today(gym)
    service_day = today - timedelta(days=3)
    member_id = _member(api, owner_headers, first_name="Nila")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=service_day.isoformat(),
        ).status_code
        == 201
    )
    _sale_id, commission_id = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=service_day.isoformat(),
    )
    session_id = _open_till(api, payer_headers, gym, register_code="DATE")
    early = service_day - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    def pay(day: date, method: str, *, session: str | None = None) -> object:
        body: dict[str, object] = {
            "amount": "1.00",
            "business_date": day.isoformat(),
            "payment_method": method,
        }
        if session is not None:
            body["cash_session_id"] = session
        return _pay(api, payer_headers, commission_id, body)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        before_payments = PTCommissionPayment.objects.filter(gym_id=gym.id).count()
        before_expenses = Expense.objects.filter(gym_id=gym.id).count()
    with pytest.raises(ValidationError, match="sale service date"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            record_pt_commission_payment(
                gym=gym,
                actor=payer,
                commission_id=commission_id,
                amount=Decimal("1.00"),
                business_date=early,
                payment_method="OTHER",
                cash_session_id=None,
                reference_number="",
                policy_note="",
                request_id="req_early_payout",
                ip_address=None,
                user_agent="",
            )
    for day, method, session in (
        (early, "CARD", None),
        (tomorrow, "OTHER", None),
        (tomorrow, "CASH", session_id),
        (early, "CASH", session_id),
    ):
        rejected = pay(day, method, session=session)
        assert rejected.status_code == 400, rejected.content
        assert "sale service date" in rejected.content.decode()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTCommissionPayment.objects.filter(gym_id=gym.id).count() == before_payments
        assert Expense.objects.filter(gym_id=gym.id).count() == before_expenses
    for day, method, session in (
        (service_day, "OTHER", None),
        (today, "BANK_TRANSFER", None),
        (today, "CASH", session_id),
    ):
        accepted = pay(day, method, session=session)
        assert accepted.status_code == 200, accepted.content
    with pytest.raises((InternalError, ProgrammingError), match="outside the sale window"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_pt_commission_payment (
                      id, gym_id, created_at, updated_at, pt_commission_id, amount,
                      business_date, payment_method, reference_number, policy_note,
                      status, void_reason, expense_id, created_by_membership_id
                    ) VALUES (
                      gen_random_uuid(), %s, statement_timestamp(), statement_timestamp(),
                      %s, 1.00, %s, 'OTHER', '', '', 'POSTED', '', gen_random_uuid(), %s
                    )
                    """,
                    [str(gym.id), commission_id, "1999-01-01", str(payer.id)],
                )
    with pytest.raises((InternalError, ProgrammingError), match="outside the sale window"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_pt_commission_payment (
                      id, gym_id, created_at, updated_at, pt_commission_id, amount,
                      business_date, payment_method, reference_number, policy_note,
                      status, void_reason, expense_id, created_by_membership_id
                    ) VALUES (
                      gen_random_uuid(), %s, statement_timestamp(), statement_timestamp(),
                      %s, 1.00, %s, 'CASH', '', '', 'POSTED', '', gen_random_uuid(), %s
                    )
                    """,
                    [str(gym.id), commission_id, "2999-01-01", str(payer.id)],
                )
