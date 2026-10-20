"""PT sale submission, approval, rejection, and commission accrual."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import InternalError, ProgrammingError
from django.test import override_settings

from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.models import Receipt
from apps.members.services import gym_today
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership, StaffProfile
from apps.trainers.exceptions import PTSaleStateConflict
from apps.trainers.models import PTCommission, PTSale
from apps.trainers.pt_sale_services import (
    approve_pt_sale,
    reject_pt_sale,
    submit_pt_sale,
    update_pt_sale,
)
from tests.security.test_pt_sales import _client, _key, _sale
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
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


def _payment(day: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "description": "Session fee",
        "amount": "10.00",
        "method": "CASH",
        "business_date": day,
    }
    payload.update(overrides)
    return payload


@override_settings(**jwt_settings())
def test_pt_sale_approval_posts_one_commission_and_optional_payment() -> None:
    client, platform_access = platform_token("ptapp-platform@example.test")
    payload = provision(
        client, platform_access, code="PTA-A", owner_email="ptapp-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    finance, _created = Feature.objects.get_or_create(
        code="finance", defaults={"name": "finance", "description": "finance"}
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=finance,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="ptapp-ada@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="ptapp-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="ptapp-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="ptapp-accountant@example.test", role_key="ACCOUNTANT")
    ada = _profile(gym, trainer_membership, code="PTA-ADA", name="Ada Trainer")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="33.33")
    api = _client()
    assert api.defaults["HTTP_X_GYM_BFF_SECRET"] == BFF_SECRET
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    today = gym_today(gym).isoformat()
    member_id = _member(api, owner_headers, first_name="Nila")
    assigned = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=member_id,
        start=today,
        until=today,
    )
    assert assigned.status_code == 201, assigned.json()
    public_pt = api.post(
        "/api/v1/payments/",
        {
            "member_id": member_id,
            "purpose": "PT",
            "description": "Outside",
            "amount": "10.00",
            "currency": gym.currency,
            "method": "CASH",
            "business_date": today,
        },
        format="json",
        **owner_headers,
        **_key("ptapp-public"),
    )
    assert public_pt.status_code == 400, public_pt.json()

    created = api.post(
        "/api/v1/pt-sales/",
        {
            **_sale(member_id, str(ada.id), today),
            "payment": _payment(today),
        },
        format="json",
        **reception_headers,
        **_key("ptapp-create"),
    )
    assert created.status_code == 201, created.json()
    sale = created.json()["data"]
    assert sale["payment"]["purpose"] == "PT"
    assert sale["payment"]["status"] == "DRAFT"
    assert sale["payment"]["member_id"] == member_id
    assert sale["payment"]["membership_id"] is None
    assert sale["payment"]["amount"] == "10.00"
    owned = api.patch(
        f"/api/v1/payments/{sale['payment']['id']}/",
        {"description": "Changed outside"},
        format="json",
        **owner_headers,
        **_key("ptapp-owned"),
    )
    assert owned.status_code == 409
    assert owned.json()["error"]["code"] == "PT_PAYMENT_OWNED"
    removed = api.patch(
        f"/api/v1/pt-sales/{sale['id']}/",
        {"payment": None},
        format="json",
        **reception_headers,
        **_key("ptapp-remove"),
    )
    assert removed.status_code == 200, removed.json()
    assert removed.json()["data"]["payment"] is None
    replaced = api.patch(
        f"/api/v1/pt-sales/{sale['id']}/",
        {"payment": _payment(today, method="CARD", amount="10.00")},
        format="json",
        **reception_headers,
        **_key("ptapp-card"),
    )
    assert replaced.status_code == 200, replaced.json()
    assert replaced.json()["data"]["payment"]["method"] == "CARD"
    submitted = api.post(
        f"/api/v1/pt-sales/{sale['id']}/submit/",
        {},
        format="json",
        **reception_headers,
        **_key("ptapp-submit"),
    )
    assert submitted.status_code == 200, submitted.json()
    assert submitted.json()["data"]["status"] == "PENDING_APPROVAL"
    assert submitted.json()["data"]["payment"]["status"] == "PENDING_APPROVAL"
    assert submitted.json()["data"]["submitted_by_membership_id"] == str(receptionist.id)
    frozen = api.patch(
        f"/api/v1/pt-sales/{sale['id']}/",
        {"description": "Too late"},
        format="json",
        **reception_headers,
        **_key("ptapp-frozen"),
    )
    assert frozen.status_code == 409
    assert frozen.json()["error"]["code"] == "PT_SALE_STATE_CONFLICT"
    owner_sale = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today, description="Owner draft"),
        format="json",
        **owner_headers,
        **_key("ptapp-owner-sale"),
    )
    assert owner_sale.status_code == 201, owner_sale.json()
    assert (
        api.post(
            f"/api/v1/pt-sales/{owner_sale.json()['data']['id']}/submit/",
            {},
            format="json",
            **owner_headers,
            **_key("ptapp-owner-submit"),
        ).status_code
        == 200
    )
    self_approve = api.post(
        f"/api/v1/pt-sales/{owner_sale.json()['data']['id']}/approve/",
        {},
        format="json",
        **owner_headers,
        **_key("ptapp-self"),
    )
    assert self_approve.status_code == 403
    reception_approve = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {},
        format="json",
        **reception_headers,
        **_key("ptapp-desk-approve"),
    )
    assert reception_approve.status_code == 403
    accountant_approve = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {},
        format="json",
        **accountant_headers,
        **_key("ptapp-books"),
    )
    assert accountant_approve.status_code == 403
    with_session = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {"cash_session_id": str(uuid4())},
        format="json",
        **manager_headers,
        **_key("ptapp-session"),
    )
    assert with_session.status_code == 400
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTSale.objects.get(id=sale["id"]).status == PTSale.Status.PENDING_APPROVAL
        assert PTCommission.objects.filter(pt_sale_id=sale["id"]).count() == 0
    approve_key = f"ptapp-approve-{uuid4().hex}"
    approved = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {},
        format="json",
        **manager_headers,
        HTTP_IDEMPOTENCY_KEY=approve_key,
    )
    assert approved.status_code == 200, approved.json()
    body = approved.json()["data"]
    assert body["sale"]["status"] == "POSTED"
    assert body["payment"]["status"] == "POSTED"
    assert body["payment"]["amount"] == "10.00"
    assert body["receipt"]["status"] == "ISSUED"
    assert body["cash_session"] is None
    assert body["cash_transaction"] is None
    assert body["commission"]["status"] == "ACCRUED"
    assert body["commission"]["commission_amount"] == "3.33"
    assert body["commission"]["gym_share"] == "6.67"
    assert body["commission"]["paid_amount"] == "0.00"
    assert body["commission"]["outstanding_amount"] == "3.33"
    replay = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {},
        format="json",
        **manager_headers,
        HTTP_IDEMPOTENCY_KEY=approve_key,
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["commission"]["id"] == body["commission"]["id"]
    again = api.post(
        f"/api/v1/pt-sales/{sale['id']}/approve/",
        {},
        format="json",
        **manager_headers,
        **_key("ptapp-again"),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "PT_SALE_STATE_CONFLICT"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="0.00")
    zero = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today, description="No commission"),
        format="json",
        **reception_headers,
        **_key("ptapp-zero"),
    )
    assert zero.status_code == 201, zero.json()
    assert (
        api.post(
            f"/api/v1/pt-sales/{zero.json()['data']['id']}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("ptapp-zero-submit"),
        ).status_code
        == 200
    )
    zero_approved = api.post(
        f"/api/v1/pt-sales/{zero.json()['data']['id']}/approve/",
        {"cash_session_id": str(uuid4())},
        format="json",
        **manager_headers,
        **_key("ptapp-zero-cash"),
    )
    assert zero_approved.status_code == 400
    zero_posted = api.post(
        f"/api/v1/pt-sales/{zero.json()['data']['id']}/approve/",
        {},
        format="json",
        **manager_headers,
        **_key("ptapp-zero-approve"),
    )
    assert zero_posted.status_code == 200, zero_posted.json()
    assert zero_posted.json()["data"]["payment"] is None
    assert zero_posted.json()["data"]["receipt"] is None
    assert zero_posted.json()["data"]["commission"]["commission_amount"] == "0.00"
    assert zero_posted.json()["data"]["commission"]["gym_share"] == "10.00"
    assert zero_posted.json()["data"]["commission"]["outstanding_amount"] == "0.00"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="100.00")
    full = api.post(
        "/api/v1/pt-sales/",
        {
            **_sale(member_id, str(ada.id), today, description="All commission", gross_fee="0.01"),
            "payment": _payment(today, amount="0.01"),
        },
        format="json",
        **reception_headers,
        **_key("ptapp-full"),
    )
    assert full.status_code == 201, full.json()
    full_id = full.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{full_id}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("ptapp-full-submit"),
        ).status_code
        == 200
    )
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {"register_code": "PTA", "business_date": today, "opening_cash": "20.00"},
        format="json",
        **reception_headers,
        **_key("ptapp-till"),
    )
    assert opened.status_code == 201, opened.json()
    session_id = opened.json()["data"]["session"]["id"]
    full_posted = api.post(
        f"/api/v1/pt-sales/{full_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        **manager_headers,
        **_key("ptapp-full-approve"),
    )
    assert full_posted.status_code == 200, full_posted.json()
    assert full_posted.json()["data"]["commission"]["commission_amount"] == "0.01"
    assert full_posted.json()["data"]["commission"]["gym_share"] == "0.00"
    assert full_posted.json()["data"]["cash_transaction"]["direction"] == "IN"
    assert full_posted.json()["data"]["cash_session"]["id"] == session_id
    assert Receipt.objects.filter(payment_id=full.json()["data"]["payment"]["id"]).count() == 1

    rejected_sale = api.post(
        "/api/v1/pt-sales/",
        {
            **_sale(member_id, str(ada.id), today, description="Reject me"),
            "payment": _payment(today, method="CARD"),
        },
        format="json",
        **reception_headers,
        **_key("ptapp-reject-create"),
    )
    assert rejected_sale.status_code == 201, rejected_sale.json()
    rejected_id = rejected_sale.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{rejected_id}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("ptapp-reject-submit"),
        ).status_code
        == 200
    )
    rejected = api.post(
        f"/api/v1/pt-sales/{rejected_id}/reject/",
        {"rejection_reason": "  Not   scheduled "},
        format="json",
        **manager_headers,
        **_key("ptapp-reject"),
    )
    assert rejected.status_code == 200, rejected.json()
    assert rejected.json()["data"]["status"] == "REJECTED"
    assert rejected.json()["data"]["rejection_reason"] == "Not scheduled"
    assert rejected.json()["data"]["payment"]["status"] == "REJECTED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTCommission.objects.filter(pt_sale_id=rejected_id).count() == 0
        assert Receipt.objects.filter(payment__pt_sale_id=rejected_id).count() == 0
        assert Expense.objects.count() == 0

    ended = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today, description="Ended assignment"),
        format="json",
        **reception_headers,
        **_key("ptapp-ended"),
    )
    assert ended.status_code == 201, ended.json()
    ended_id = ended.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{ended_id}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("ptapp-ended-submit"),
        ).status_code
        == 200
    )
    closed = api.post(
        f"/api/v1/trainer-assignments/{assigned.json()['data']['id']}/end/",
        {"effective_until": today, "reason": "Finished"},
        format="json",
        **owner_headers,
        **_key("ptapp-end"),
    )
    assert closed.status_code == 200, closed.json()
    blocked = api.post(
        f"/api/v1/pt-sales/{ended_id}/approve/",
        {},
        format="json",
        **manager_headers,
        **_key("ptapp-blocked"),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "ASSIGNMENT_NOT_EFFECTIVE"

    with pytest.raises((InternalError, ProgrammingError), match="pt_sale_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "UPDATE trainers_pt_sale SET gross_fee = 11 WHERE id = %s",
                    [sale["id"]],
                )
    with pytest.raises((InternalError, ProgrammingError), match="pt_commission_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "UPDATE trainers_pt_commission SET paid_amount = 1 WHERE pt_sale_id = %s",
                    [sale["id"]],
                )
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "DELETE FROM trainers_pt_commission WHERE pt_sale_id = %s",
                    [sale["id"]],
                )
    with pytest.raises((InternalError, ProgrammingError), match="pt_commission_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM trainers_pt_commission WHERE pt_sale_id = %s",
                    [sale["id"]],
                )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'trainers_pt_commission', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission', 'INSERT'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission', 'UPDATE'),
              has_table_privilege('gym_runtime', 'trainers_pt_commission', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False)
    cash_count = CashTransaction.objects.filter(
        source_id=full.json()["data"]["payment"]["id"]
    ).count()
    assert cash_count == 1


@override_settings(**jwt_settings())
def test_pt_sale_submit_update_and_approve_reject_leave_one_result() -> None:
    client, platform_access = platform_token("ptapp-race-platform@example.test")
    payload = provision(
        client, platform_access, code="PTA-R", owner_email="ptapp-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptapp-race-manager@example.test", role_key="MANAGER")
    trainer_membership = add_staff(gym, email="ptapp-race-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="PTA-RACE", name="Race Trainer")
    api = _client()
    headers = auth_headers(staff_token(owner))
    today = gym_today(gym).isoformat()
    member_id = _member(api, headers, first_name="Race")
    assert (
        _assign(api, headers, trainer_id=str(ada.id), member_id=member_id, start=today).status_code
        == 201
    )
    created = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today, description="Race"),
        format="json",
        **headers,
        **_key("ptapp-race"),
    )
    assert created.status_code == 201, created.json()
    sale_id = created.json()["data"]["id"]
    barrier = Barrier(2, timeout=20)

    def submit_sale() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    submit_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        sale_id=sale_id,
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "conflict"
            return "submitted"
        finally:
            connections.close_all()

    def update_sale() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    update_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        sale_id=sale_id,
                        fields={"description": "Updated race"},
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "conflict"
            return "updated"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda fn: fn(), (submit_sale, update_sale)))
    assert "conflict" in outcomes or outcomes == ["submitted", "updated"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = PTSale.objects.get(id=sale_id)
        assert row.status == PTSale.Status.PENDING_APPROVAL
        if "updated" in outcomes and "submitted" in outcomes:
            assert row.description == "Updated race"

    decision = Barrier(2, timeout=20)

    def approve_sale() -> str:
        connections.close_all()
        try:
            decision.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    approve_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=manager.id),
                        sale_id=sale_id,
                        cash_session_id=None,
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "conflict"
            return "approved"
        finally:
            connections.close_all()

    def reject_sale() -> str:
        connections.close_all()
        try:
            decision.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    reject_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=manager.id),
                        sale_id=sale_id,
                        rejection_reason="Stopped",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "conflict"
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        decided = sorted(pool.map(lambda fn: fn(), (approve_sale, reject_sale)))
    assert decided.count("conflict") == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        final = PTSale.objects.get(id=sale_id)
        commission_count = PTCommission.objects.filter(pt_sale_id=sale_id).count()
        if final.status == PTSale.Status.POSTED:
            assert commission_count == 1
            assert "approved" in decided
        else:
            assert final.status == PTSale.Status.REJECTED
            assert final.rejection_reason == "Stopped"
            assert commission_count == 0
            assert "rejected" in decided
