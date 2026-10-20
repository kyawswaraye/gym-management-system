"""Draft-only PT sale publication."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import InternalError, ProgrammingError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, StaffProfile
from apps.trainers.exceptions import PTSaleStateConflict
from apps.trainers.models import PTSale
from apps.trainers.pt_sale_services import cancel_pt_sale, update_pt_sale
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


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _key(prefix: str) -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"{prefix}-{uuid4().hex}"}


def _assert_sale_actors(row: dict[str, object], *, actor_id: str, cancelled_by: str | None) -> None:
    assert row["created_by_membership_id"] == actor_id
    assert row["updated_by_membership_id"] == actor_id
    assert row["cancelled_by_membership_id"] == cancelled_by
    for name in (
        "submitted_by_membership_id",
        "approved_by_membership_id",
        "rejected_by_membership_id",
    ):
        assert row[name] is None


def _sale(member_id: str, trainer_id: str, day: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "member_id": member_id,
        "trainer_staff_profile_id": trainer_id,
        "service_date": day,
        "description": "  Private   session ",
        "gross_fee": "10.00",
        "currency": "THB",
    }
    payload.update(overrides)
    return payload


@override_settings(**jwt_settings())
def test_pt_sale_drafts_are_scoped_and_have_no_economic_effects() -> None:
    client, platform_access = platform_token("ptsale-platform@example.test")
    payload = provision(
        client, platform_access, code="PTS-A", owner_email="ptsale-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PTS-B", owner_email="ptsale-owner-b@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="ptsale-ada@example.test", role_key="TRAINER")
    other_trainer_membership = add_staff(gym, email="ptsale-bea@example.test", role_key="TRAINER")
    receptionist = add_staff(gym, email="ptsale-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="ptsale-accountant@example.test", role_key="ACCOUNTANT")
    ada = _profile(gym, trainer_membership, code="PTS-ADA", name="Ada Trainer")
    bea = _profile(gym, other_trainer_membership, code="PTS-BEA", name="Bea Trainer")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="33.33")
        StaffProfile.objects.filter(id=bea.id).update(default_commission_percent="50.00")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    today = gym_today(gym)
    today_text = today.isoformat()
    member_id = _member(api, owner_headers, first_name="Nila")
    other_member = _member(api, owner_headers, first_name="Omar")
    assigned = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=member_id,
        start=today_text,
        until=today_text,
    )
    assert assigned.status_code == 201, assigned.json()
    _assign(
        api,
        owner_headers,
        trainer_id=str(bea.id),
        member_id=other_member,
        start=today_text,
        until=today_text,
    )

    with pytest.raises(Resolver404):
        resolve("/api/v1/pt-sales/void/")
    incomplete_payment = api.post(
        "/api/v1/pt-sales/",
        {**_sale(member_id, str(ada.id), today_text), "payment": {"amount": "10.00"}},
        format="json",
        **owner_headers,
        **_key("pts-pay"),
    )
    assert incomplete_payment.status_code == 400
    stolen = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(bea.id), today_text),
        format="json",
        **trainer_headers,
        **_key("pts-stolen"),
    )
    assert stolen.status_code == 404
    outside = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), (today + timedelta(days=1)).isoformat()),
        format="json",
        **owner_headers,
        **_key("pts-after"),
    )
    assert outside.status_code == 422
    assert outside.json()["error"]["code"] == "ASSIGNMENT_NOT_EFFECTIVE"
    before = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), (today - timedelta(days=1)).isoformat()),
        format="json",
        **owner_headers,
        **_key("pts-before"),
    )
    assert before.status_code == 422
    assert before.json()["error"]["code"] == "ASSIGNMENT_NOT_EFFECTIVE"
    wrong_currency = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today_text, currency="USD"),
        format="json",
        **owner_headers,
        **_key("pts-usd"),
    )
    assert wrong_currency.status_code == 422
    assert wrong_currency.json()["error"]["code"] == "CURRENCY_MISMATCH"
    create_key = f"pts-create-{uuid4().hex}"
    created = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today_text),
        format="json",
        **owner_headers,
        HTTP_IDEMPOTENCY_KEY=create_key,
    )
    assert created.status_code == 201, created.json()
    body = created.json()["data"]
    assert body["description"] == "Private session"
    assert body["status"] == "DRAFT"
    assert body["gross_fee"] == "10.00"
    assert body["commission_percent_snapshot"] == "33.33"
    assert body["commission_amount"] == "3.33"
    assert body["gym_share"] == "6.67"
    assert body["paid_amount"] == "0.00"
    assert body["outstanding_amount"] == "3.33"
    assert body["payment_id"] is None
    assert body["currency"] == gym.currency
    _assert_sale_actors(body, actor_id=str(owner.id), cancelled_by=None)
    detail = api.get(f"/api/v1/pt-sales/{body['id']}/", **owner_headers)
    assert detail.status_code == 200
    _assert_sale_actors(detail.json()["data"], actor_id=str(owner.id), cancelled_by=None)
    replay = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today_text),
        format="json",
        **owner_headers,
        HTTP_IDEMPOTENCY_KEY=create_key,
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == body["id"]
    half = api.post(
        "/api/v1/pt-sales/",
        _sale(other_member, str(bea.id), today_text, gross_fee="0.01", description="Edge"),
        format="json",
        **owner_headers,
        **_key("pts-half"),
    )
    assert half.status_code == 201, half.json()
    assert half.json()["data"]["commission_amount"] == "0.01"
    assert half.json()["data"]["gym_share"] == "0.00"
    reception = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today_text, description="Front desk"),
        format="json",
        **reception_headers,
        **_key("pts-desk"),
    )
    assert reception.status_code == 201, reception.json()
    hidden = api.get(f"/api/v1/pt-sales/{body['id']}/", **reception_headers)
    assert hidden.status_code == 404
    desk_list = api.get("/api/v1/pt-sales/", **reception_headers)
    assert [row["id"] for row in desk_list.json()["data"]] == [reception.json()["data"]["id"]]
    trainer_list = api.get("/api/v1/pt-sales/", **trainer_headers)
    assert {row["id"] for row in trainer_list.json()["data"]} == {
        body["id"],
        reception.json()["data"]["id"],
    }
    assert half.json()["data"]["id"] not in {row["id"] for row in trainer_list.json()["data"]}
    foreign = api.get(f"/api/v1/pt-sales/{body['id']}/", **other_headers)
    assert foreign.status_code == 404
    accountant_get = api.get("/api/v1/pt-sales/", **accountant_headers)
    assert accountant_get.status_code == 200
    assert len(accountant_get.json()["data"]) == 3
    listed_owner = next(row for row in accountant_get.json()["data"] if row["id"] == body["id"])
    _assert_sale_actors(listed_owner, actor_id=str(owner.id), cancelled_by=None)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="40.00")
    kept = api.patch(
        f"/api/v1/pt-sales/{body['id']}/",
        {"description": "Renamed session"},
        format="json",
        **owner_headers,
        **_key("pts-rename"),
    )
    assert kept.status_code == 200, kept.json()
    assert kept.json()["data"]["commission_percent_snapshot"] == "33.33"
    _assert_sale_actors(kept.json()["data"], actor_id=str(owner.id), cancelled_by=None)
    moved = api.patch(
        f"/api/v1/pt-sales/{body['id']}/",
        {"trainer_staff_profile_id": str(bea.id), "member_id": other_member},
        format="json",
        **owner_headers,
        **_key("pts-move"),
    )
    assert moved.status_code == 200, moved.json()
    assert moved.json()["data"]["commission_percent_snapshot"] == "50.00"
    assert moved.json()["data"]["commission_amount"] == "5.00"
    trainer_after_move = api.get(f"/api/v1/pt-sales/{body['id']}/", **trainer_headers)
    assert trainer_after_move.status_code == 404

    with CaptureQueriesContext(connection) as captured:
        listed = api.get("/api/v1/pt-sales/?limit=1", **owner_headers)
    assert listed.status_code == 200
    assert listed.json()["meta"]["has_more"] is True
    sale_queries = [
        query
        for query in captured.captured_queries
        if "trainers_pt_sale" in query["sql"].lower() and "order by" in query["sql"].lower()
    ]
    assert len(sale_queries) == 1
    assert "limit" in sale_queries[0]["sql"].lower()
    followed = api.get(
        "/api/v1/pt-sales/",
        {"limit": 1, "after": listed.json()["meta"]["next_after"]},
        **owner_headers,
    )
    assert followed.status_code == 200
    assert followed.json()["data"]
    unknown = api.get(f"/api/v1/pt-sales/?gym_id={gym.id}", **owner_headers)
    assert unknown.status_code == 400

    cancel_key = f"pts-cancel-{uuid4().hex}"
    cancelled = api.post(
        f"/api/v1/pt-sales/{half.json()['data']['id']}/cancel/",
        {"reason": "  Booked   twice "},
        format="json",
        **owner_headers,
        HTTP_IDEMPOTENCY_KEY=cancel_key,
    )
    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert cancelled.json()["data"]["reason"] == "Booked twice"
    _assert_sale_actors(
        cancelled.json()["data"],
        actor_id=str(owner.id),
        cancelled_by=str(owner.id),
    )
    again = api.post(
        f"/api/v1/pt-sales/{half.json()['data']['id']}/cancel/",
        {"reason": "  Booked   twice "},
        format="json",
        **owner_headers,
        HTTP_IDEMPOTENCY_KEY=cancel_key,
    )
    assert again.status_code == 200
    assert again.json()["data"]["id"] == cancelled.json()["data"]["id"]
    mismatch = api.post(
        f"/api/v1/pt-sales/{half.json()['data']['id']}/cancel/",
        {"reason": "Different reason"},
        format="json",
        **owner_headers,
        HTTP_IDEMPOTENCY_KEY=cancel_key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    recancel = api.post(
        f"/api/v1/pt-sales/{half.json()['data']['id']}/cancel/",
        {"reason": "Again"},
        format="json",
        **owner_headers,
        **_key("pts-again"),
    )
    assert recancel.status_code == 409
    assert recancel.json()["error"]["code"] == "PT_SALE_STATE_CONFLICT"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        actions = set(
            AuditLog.objects.filter(target_id=body["id"]).values_list("action", flat=True)
        )
        assert {
            "PT_SALE_DRAFT_CREATED",
            "PT_SALE_DRAFT_UPDATED",
        } <= actions
        created_audit = AuditLog.objects.get(target_id=body["id"], action="PT_SALE_DRAFT_CREATED")
        assert created_audit.new_values["gross_fee"] == "10.00"
        assert created_audit.module == "trainers"
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                """
                SELECT
                  (SELECT count(*) FROM members_payment),
                  (SELECT count(*) FROM members_receipt),
                  (SELECT count(*) FROM cashbook_cash_transaction),
                  (SELECT count(*) FROM expenses_expense)
                """
            )
            assert cursor.fetchone() == (0, 0, 0, 0)
            cursor.execute("SELECT to_regclass('public.trainers_pt_commission')")
            assert cursor.fetchone() == ("trainers_pt_commission",)
            cursor.execute("SELECT count(*) FROM trainers_pt_commission")
            assert cursor.fetchone() == (0,)
        assert Payment.objects.count() == 0
        assert Receipt.objects.count() == 0
        assert CashTransaction.objects.count() == 0
        assert Expense.objects.count() == 0

    with pytest.raises((InternalError, ProgrammingError), match="pt_sale_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            PTSale.objects.filter(id=body["id"]).update(status="POSTED")
    with pytest.raises((InternalError, ProgrammingError), match="pt_sale_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            PTSale.objects.filter(id=body["id"]).delete()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'trainers_pt_sale', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_pt_sale', 'INSERT'),
              has_table_privilege('gym_runtime', 'trainers_pt_sale', 'UPDATE'),
              has_table_privilege('gym_runtime', 'trainers_pt_sale', 'DELETE'),
              has_function_privilege('public', 'app_guard_pt_sale()', 'EXECUTE'),
              has_function_privilege('gym_runtime', 'app_guard_pt_sale()', 'EXECUTE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False, False, True)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="pt").update(enabled=False)
    assert api.get("/api/v1/pt-sales/", **owner_headers).status_code == 403
    assert other.id != gym.id


@override_settings(**jwt_settings())
def test_pt_sale_update_and_cancel_leave_one_consistent_row() -> None:
    client, platform_access = platform_token("ptsale-race-platform@example.test")
    payload = provision(
        client, platform_access, code="PTS-R", owner_email="ptsale-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="ptsale-race-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer_membership, code="PTS-RACE", name="Race Trainer")
    api = _client()
    headers = auth_headers(staff_token(owner))
    today_text = gym_today(gym).isoformat()
    member_id = _member(api, headers, first_name="Race")
    assert (
        _assign(
            api,
            headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=today_text,
        ).status_code
        == 201
    )
    created = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today_text, description="Race"),
        format="json",
        **headers,
        **_key("pts-race-create"),
    )
    assert created.status_code == 201, created.json()
    sale_id = created.json()["data"]["id"]
    barrier = Barrier(2, timeout=20)

    def update_sale() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                gym_row = Gym.objects.get(id=gym.id)
                try:
                    update_pt_sale(
                        gym=gym_row,
                        actor=actor,
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

    def cancel_sale() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                gym_row = Gym.objects.get(id=gym.id)
                try:
                    cancel_pt_sale(
                        gym=gym_row,
                        actor=actor,
                        sale_id=sale_id,
                        reason="Stopped",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "conflict"
            return "cancelled"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda fn: fn(), (update_sale, cancel_sale)))
    assert "conflict" in outcomes or outcomes == ["cancelled", "updated"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = PTSale.objects.get(id=sale_id)
        assert row.payment_id is None
        if row.status == PTSale.Status.CANCELLED:
            assert row.reason == "Stopped"
            assert row.cancelled_at is not None
            assert row.cancelled_by_membership_id == owner.id
        else:
            assert row.status == PTSale.Status.DRAFT
            assert row.description == "Updated race"
            assert row.cancelled_at is None
            assert row.reason == ""


@override_settings(**jwt_settings())
def test_pt_sale_member_update_keeps_payment_draft_and_moves_member() -> None:
    client, platform_access = platform_token("ptsale-member-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="PTS-M",
        owner_email="ptsale-member-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    ada_membership = add_staff(gym, email="ptsale-member-ada@example.test", role_key="TRAINER")
    bea_membership = add_staff(gym, email="ptsale-member-bea@example.test", role_key="TRAINER")
    ada = _profile(gym, ada_membership, code="PTS-MADA", name="Ada Trainer")
    _profile(gym, bea_membership, code="PTS-MBEA", name="Bea Trainer")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    ada_headers = auth_headers(staff_token(ada_membership))
    bea_headers = auth_headers(staff_token(bea_membership))
    today = gym_today(gym).isoformat()
    nila = _member(api, owner_headers, first_name="Nila")
    omar = _member(api, owner_headers, first_name="Omar")
    una = _member(api, owner_headers, first_name="Una")
    for member_id in (nila, omar):
        assigned = _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=today,
            until=today,
        )
        assert assigned.status_code == 201, assigned.json()
    created = api.post(
        "/api/v1/pt-sales/",
        {
            **_sale(nila, str(ada.id), today, description="Sale note"),
            "payment": {
                "description": "Session fee",
                "amount": "12.50",
                "method": "CARD",
                "business_date": today,
                "reference_number": "PT-REF-1",
            },
        },
        format="json",
        **ada_headers,
        **_key("pts-member-create"),
    )
    assert created.status_code == 201, created.json()
    sale_id = created.json()["data"]["id"]
    payment_id = created.json()["data"]["payment"]["id"]
    moved = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"member_id": omar},
        format="json",
        **ada_headers,
        **_key("pts-member-move"),
    )
    assert moved.status_code == 200, moved.json()
    body = moved.json()["data"]
    payment = body["payment"]
    assert body["member_id"] == omar
    assert body["payment_id"] == payment_id
    assert payment["id"] == payment_id
    assert payment["member_id"] == omar
    assert payment["membership_id"] is None
    assert payment["purpose"] == "PT"
    assert payment["description"] == "Session fee"
    assert payment["amount"] == "12.50"
    assert payment["method"] == "CARD"
    assert payment["business_date"] == today
    assert payment["reference_number"] == "PT-REF-1"
    assert payment["currency"] == gym.currency
    hidden = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"member_id": nila},
        format="json",
        **bea_headers,
        **_key("pts-member-hidden"),
    )
    assert hidden.status_code == 404
    blocked = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"member_id": una},
        format="json",
        **ada_headers,
        **_key("pts-member-unassigned"),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "ASSIGNMENT_NOT_EFFECTIVE"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = Payment.objects.get(id=payment_id)
        sale = PTSale.objects.get(id=sale_id)
    assert str(sale.member_id) == omar
    assert str(stored.member_id) == omar
    assert stored.membership_id is None
    assert stored.purpose == "PT"
    assert stored.description == "Session fee"
    assert f"{stored.amount:.2f}" == "12.50"
    assert stored.method == "CARD"
    assert stored.business_date.isoformat() == today
    assert stored.reference_number == "PT-REF-1"
    assert stored.currency == gym.currency
    assert stored.pt_sale_id == sale.id
