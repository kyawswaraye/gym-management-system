"""Posted PT sale void, including payout, payment, and package effects."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, connections, transaction
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.models import Payment
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    StaffProfile,
)
from apps.trainers.exceptions import (
    CommissionOverpay,
    CommissionPayoutConflict,
    PayoutReversalIncomplete,
    PTSaleStateConflict,
)
from apps.trainers.models import (
    MemberServicePackage,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServiceSessionLedger,
)
from apps.trainers.pt_commission_services import record_pt_commission_payment
from apps.trainers.pt_sale_void_services import void_pt_sale
from tests.security.test_pt_sale_approval import _payment
from tests.security.test_pt_sales import _client, _key, _sale
from tests.security.test_service_packages import _body
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
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
    pytest.mark.django_db(transaction=True),
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


def _open(api: APIClient, headers: dict[str, str], *, day: str, register: str) -> str:
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {"register_code": register, "business_date": day, "opening_cash": "200.00"},
        format="json",
        **headers,
        **_key(f"open-{register}"),
    )
    assert opened.status_code == 201, opened.json()
    return str(opened.json()["data"]["session"]["id"])


def _approve(
    api: APIClient,
    creator_headers: dict[str, str],
    manager_headers: dict[str, str],
    *,
    member_id: str,
    trainer_id: str,
    day: str,
    body: dict[str, object],
    approve_body: dict[str, object] | None = None,
) -> dict[str, object]:
    created = api.post(
        "/api/v1/pt-sales/",
        body,
        format="json",
        **creator_headers,
        **_key("create"),
    )
    assert created.status_code == 201, created.json()
    sale_id = created.json()["data"]["id"]
    submitted = api.post(
        f"/api/v1/pt-sales/{sale_id}/submit/",
        {},
        format="json",
        **creator_headers,
        **_key("submit"),
    )
    assert submitted.status_code == 200, submitted.json()
    approved = api.post(
        f"/api/v1/pt-sales/{sale_id}/approve/",
        {} if approve_body is None else approve_body,
        format="json",
        **manager_headers,
        **_key("approve"),
    )
    assert approved.status_code == 200, approved.json()
    return approved.json()["data"]


def _void(
    api: APIClient,
    headers: dict[str, str],
    sale_id: str,
    body: dict[str, object],
    *,
    key: str | None = None,
) -> object:
    return api.post(
        f"/api/v1/pt-sales/{sale_id}/void/",
        body,
        format="json",
        **headers,
        **({"HTTP_IDEMPOTENCY_KEY": key} if key else _key("void")),
    )


@override_settings(**jwt_settings())
def test_void_reverses_optional_payment_and_mixed_payouts() -> None:
    client, platform_access = platform_token("ptvoid-platform@example.test")
    payload = provision(
        client, platform_access, code="PTV-A", owner_email="ptvoid-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PTV-B", owner_email="ptvoid-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable(gym, "pt")
    _enable(gym, "finance")
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptvoid-manager@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptvoid-payer@example.test", role_key="MANAGER")
    accountant = add_staff(gym, email="ptvoid-accountant@example.test", role_key="ACCOUNTANT")
    reception = add_staff(gym, email="ptvoid-reception@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="ptvoid-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTV-ADA", name="Ada Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="40.00")
    api = _client()
    assert api.defaults["HTTP_X_GYM_BFF_SECRET"] == BFF_SECRET
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    payer_headers = auth_headers(staff_token(payer))
    accountant_headers = auth_headers(staff_token(accountant))
    reception_headers = auth_headers(staff_token(reception))
    trainer_headers = auth_headers(staff_token(trainer))
    other_headers = auth_headers(staff_token(other_owner))
    day = gym_today(gym).isoformat()
    member_id = _member(api, owner_headers, first_name="Nila")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=day,
            until=day,
        ).status_code
        == 201
    )
    bare = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        body=_sale(member_id, str(ada.id), day, description="No money"),
    )
    bare_void = _void(
        api,
        accountant_headers,
        str(bare["sale"]["id"]),
        {
            "reason": "  Never   delivered  ",
            "business_date": day,
            "cash_session_id": None,
            "payout_reversals": [],
        },
    )
    assert bare_void.status_code == 200, bare_void.json()
    assert bare_void.json()["data"]["sale"]["status"] == "VOID"
    assert bare_void.json()["data"]["sale"]["reason"] == "Never delivered"
    assert bare_void.json()["data"]["sale"]["voided_by_membership_id"] == str(accountant.id)
    assert bare_void.json()["data"]["sale"]["reversal_pt_sale_id"] is None
    assert bare_void.json()["data"]["commission"]["status"] == "VOID"
    assert bare_void.json()["data"]["commission"]["paid_amount"] == "0.00"
    assert bare_void.json()["data"]["commission"]["outstanding_amount"] == "0.00"
    assert bare_void.json()["data"]["payment"] is None
    assert bare_void.json()["data"]["member_service_package"] is None
    assert PTSale.objects.filter(gym_id=gym.id, reversal_pt_sale_id__isnull=False).count() == 0

    card = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        body={
            **_sale(member_id, str(ada.id), day, description="Card fee", gross_fee="100.00"),
            "payment": _payment(day, method="CARD", amount="25.00"),
        },
    )
    before = api.get(f"/api/v1/sales/daily/?date={day}", **owner_headers)
    assert before.status_code == 200, before.json()
    assert before.json()["data"]["pt_total"] == "25.00"
    assert before.json()["data"]["class_total"] == "0.00"
    assert before.json()["data"]["product_total"] == "0.00"
    commission_id = str(card["commission"]["id"])
    owner_session = _open(api, owner_headers, day=day, register="OWN")
    cash_pay = api.post(
        f"/api/v1/pt-commissions/{commission_id}/record-payment/",
        {
            "amount": "15.00",
            "business_date": day,
            "payment_method": "CASH",
            "cash_session_id": owner_session,
        },
        format="json",
        **payer_headers,
        **_key("pay-cash"),
    )
    assert cash_pay.status_code == 200, cash_pay.json()
    card_pay = api.post(
        f"/api/v1/pt-commissions/{commission_id}/record-payment/",
        {
            "amount": "10.00",
            "business_date": day,
            "payment_method": "CARD",
            "cash_session_id": None,
        },
        format="json",
        **payer_headers,
        **_key("pay-card"),
    )
    assert card_pay.status_code == 200, card_pay.json()
    cash_child = cash_pay.json()["data"]["payment"]["id"]
    card_child = card_pay.json()["data"]["payment"]["id"]
    expense_id = cash_pay.json()["data"]["expense"]["id"]
    payment_id = card["payment"]["id"]
    blocked_payment = api.post(
        f"/api/v1/payments/{payment_id}/void/",
        {"reason": "Public route", "business_date": day},
        format="json",
        **owner_headers,
        **_key("public-pay"),
    )
    assert blocked_payment.status_code == 409
    assert blocked_payment.json()["error"]["code"] == "PT_PAYMENT_OWNED"
    blocked_expense = api.post(
        f"/api/v1/expenses/{expense_id}/void/",
        {"reason": "Public expense", "business_date": day},
        format="json",
        **owner_headers,
        **_key("public-exp"),
    )
    assert blocked_expense.status_code == 422
    assert blocked_expense.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"
    incomplete = _void(
        api,
        owner_headers,
        str(card["sale"]["id"]),
        {
            "reason": "Missing payout",
            "business_date": day,
            "payout_reversals": [
                {"commission_payment_id": cash_child, "cash_session_id": owner_session}
            ],
        },
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "PAYOUT_REVERSAL_INCOMPLETE"
    mapped_card = _void(
        api,
        owner_headers,
        str(card["sale"]["id"]),
        {
            "reason": "Card payout has no till",
            "business_date": day,
            "payout_reversals": [
                {"commission_payment_id": cash_child, "cash_session_id": owner_session},
                {"commission_payment_id": card_child, "cash_session_id": owner_session},
            ],
        },
    )
    assert mapped_card.status_code == 422
    assert mapped_card.json()["error"]["code"] == "PAYOUT_REVERSAL_INCOMPLETE"
    extra = _void(
        api,
        owner_headers,
        str(card["sale"]["id"]),
        {
            "reason": "Extra payout",
            "business_date": day,
            "payout_reversals": [
                {"commission_payment_id": cash_child, "cash_session_id": owner_session},
                {"commission_payment_id": card_child, "cash_session_id": None},
                {"commission_payment_id": str(uuid4()), "cash_session_id": None},
            ],
        },
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "PAYOUT_REVERSAL_INCOMPLETE"
    assert (
        _void(
            api,
            reception_headers,
            str(card["sale"]["id"]),
            {"reason": "Desk", "business_date": day, "payout_reversals": []},
        ).status_code
        == 403
    )
    assert (
        _void(
            api,
            trainer_headers,
            str(card["sale"]["id"]),
            {"reason": "Trainer", "business_date": day, "payout_reversals": []},
        ).status_code
        == 403
    )
    assert (
        _void(
            api,
            other_headers,
            str(card["sale"]["id"]),
            {"reason": "Other gym", "business_date": day, "payout_reversals": []},
        ).status_code
        == 404
    )
    platform = api.post(
        f"/api/v1/pt-sales/{card['sale']['id']}/void/",
        {"reason": "Platform", "business_date": day, "payout_reversals": []},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
        **_key("platform-void"),
    )
    assert platform.status_code == 401
    void_body = {
        "reason": "Customer cancelled",
        "business_date": day,
        "cash_session_id": None,
        "payout_reversals": [
            {"commission_payment_id": card_child, "cash_session_id": None},
            {"commission_payment_id": cash_child, "cash_session_id": owner_session},
        ],
    }
    void_key = "ptvoid-card-replay01"
    voided = _void(api, owner_headers, str(card["sale"]["id"]), void_body, key=void_key)
    assert voided.status_code == 200, voided.json()
    data = voided.json()["data"]
    assert data["sale"]["status"] == "VOID"
    assert data["commission"]["status"] == "VOID"
    assert data["commission"]["paid_amount"] == "0.00"
    assert data["commission"]["outstanding_amount"] == "0.00"
    assert {row["status"] for row in data["payouts"]} == {"VOID"}
    assert data["payment"]["original"]["status"] == "VOID"
    assert data["payment"]["correction"]["entry_type"] == "REVERSAL"
    assert data["payment"]["original_receipt"]["status"] == "VOID"
    assert data["payment"]["correction_receipt"]["status"] == "ISSUED"
    assert data["payment"]["cash_transaction"] is None
    replay = _void(api, owner_headers, str(card["sale"]["id"]), void_body, key=void_key)
    assert replay.status_code == 200
    assert replay.json()["data"]["sale"]["id"] == data["sale"]["id"]
    mismatch = _void(
        api,
        owner_headers,
        str(card["sale"]["id"]),
        {**void_body, "reason": "Different reason"},
        key=void_key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    again = _void(
        api,
        owner_headers,
        str(card["sale"]["id"]),
        void_body,
        key="ptvoid-card-second01",
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "PT_SALE_STATE_CONFLICT"
    after = api.get(f"/api/v1/sales/daily/?date={day}", **owner_headers)
    assert after.json()["data"]["pt_total"] == "0.00"
    assert after.json()["data"]["class_total"] == "0.00"
    assert after.json()["data"]["other_total"] == "0.00"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Payment.objects.filter(gym_id=gym.id, pt_sale_id=card["sale"]["id"]).count() == 1
        assert (
            PTCommissionPayment.objects.filter(
                pt_commission_id=commission_id, status="POSTED"
            ).count()
            == 0
        )
        assert Expense.objects.get(id=expense_id).status == "VOID"

    desk_session = _open(api, reception_headers, day=day, register="DESK")
    cash_sale = _approve(
        api,
        reception_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        body={
            **_sale(member_id, str(ada.id), day, description="Cash fee", gross_fee="40.00"),
            "payment": _payment(day, method="CASH", amount="40.00"),
        },
        approve_body={"cash_session_id": desk_session},
    )
    posted_day = api.get(f"/api/v1/sales/daily/?date={day}", **owner_headers)
    assert posted_day.json()["data"]["pt_total"] == "40.00"
    cash_void = _void(
        api,
        owner_headers,
        str(cash_sale["sale"]["id"]),
        {
            "reason": "Cash reversed",
            "business_date": day,
            "cash_session_id": desk_session,
            "payout_reversals": [],
        },
    )
    assert cash_void.status_code == 200, cash_void.json()
    movement = cash_void.json()["data"]["payment"]["cash_transaction"]
    assert movement["direction"] == "OUT"
    assert movement["reversal_of_id"] == cash_sale["cash_transaction"]["id"]
    cleared = api.get(f"/api/v1/sales/daily/?date={day}", **owner_headers)
    assert cleared.json()["data"]["pt_total"] == "0.00"
    assert cleared.json()["data"]["class_total"] == "0.00"


@override_settings(**jwt_settings())
def test_void_refunds_a_full_grant_and_rolls_back_when_consumed() -> None:
    client, platform_access = platform_token("ptvoid-pkg-platform@example.test")
    payload = provision(
        client, platform_access, code="PTV-P", owner_email="ptvoid-pkg-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable(gym, "pt")
    _enable(gym, "finance")
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptvoid-pkg-manager@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="ptvoid-pkg-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTV-PKG", name="Ada Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="10.00")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    day = gym_today(gym).isoformat()
    member_id = _member(api, owner_headers, first_name="Nila")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=day,
            until=day,
        ).status_code
        == 201
    )
    catalog = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_VOID", name="Void pack", session_count=4, price="80.00"),
        format="json",
        **owner_headers,
        **_key("pkg"),
    )
    assert catalog.status_code == 201, catalog.json()
    package_id = catalog.json()["data"]["id"]
    granted = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        body={
            **_sale(member_id, str(ada.id), day, description="Packaged"),
            "service_package_id": package_id,
        },
    )
    sale_id = str(granted["sale"]["id"])
    voided = _void(
        api,
        owner_headers,
        sale_id,
        {
            "reason": "Unused package",
            "business_date": day,
            "payout_reversals": [],
        },
    )
    assert voided.status_code == 200, voided.json()
    assert voided.json()["data"]["member_service_package"]["status"] == "VOID"
    assert voided.json()["data"]["member_service_package"]["remaining_sessions"] == 0
    assert voided.json()["data"]["ledger_entry"]["reason"] == "REFUND"
    assert voided.json()["data"]["ledger_entry"]["session_delta"] == -4
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        package = MemberServicePackage.objects.get(pt_sale_id=sale_id)
        assert package.status == "VOID"
        assert list(
            ServiceSessionLedger.objects.filter(member_service_package_id=package.id)
            .order_by("created_at", "id")
            .values_list("reason", "session_delta")
        ) == [("GRANT", 4), ("REFUND", -4)]

    consumed_sale = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day,
        body={
            **_sale(member_id, str(ada.id), day, description="Consumed pack", gross_fee="20.00"),
            "service_package_id": package_id,
            "payment": _payment(day, method="CARD", amount="20.00"),
        },
    )
    consumed_id = consumed_sale["sale"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        grant = MemberServicePackage.objects.get(pt_sale_id=consumed_id)
    now = timezone.now()
    zone = ZoneInfo(gym.timezone)
    local_now = now.astimezone(zone)
    starts = now - timedelta(hours=2)
    if starts.astimezone(zone).date() != local_now.date():
        starts = datetime.combine(local_now.date(), time(0, 1), tzinfo=zone)
    ends = starts + timedelta(minutes=30)
    if ends > now:
        ends = starts + timedelta(minutes=1)
    booked = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(ada.id),
            "member_id": member_id,
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "member_service_package_id": str(grant.id),
        },
        format="json",
        **owner_headers,
        **_key("consume-book"),
    )
    assert booked.status_code == 201, booked.json()
    booking_id = booked.json()["data"]["id"]
    attended = api.post(
        f"/api/v1/pt-bookings/{booking_id}/attend/",
        {},
        format="json",
        **owner_headers,
        **_key("consume-attend"),
    )
    assert attended.status_code == 200, attended.json()
    blocked = _void(
        api,
        owner_headers,
        consumed_id,
        {"reason": "Too late", "business_date": day, "payout_reversals": []},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PACKAGE_CONSUMED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = PTSale.objects.get(id=consumed_id)
        package = MemberServicePackage.objects.get(pt_sale_id=sale.id)
        attended_row = ServiceSessionLedger.objects.get(
            member_service_package_id=package.id,
            reason=ServiceSessionLedger.Reason.ATTENDED,
        )
        assert sale.status == "POSTED"
        assert Payment.objects.get(id=sale.payment_id).status == "POSTED"
        assert PTCommission.objects.get(pt_sale_id=sale.id).status == "ACCRUED"
        assert package.status == "ACTIVE"
        assert attended_row.session_delta == -1
        assert str(attended_row.pt_booking_id) == booking_id
        assert list(
            ServiceSessionLedger.objects.filter(member_service_package_id=package.id)
            .order_by("created_at", "id")
            .values_list("reason", "session_delta")
        ) == [("GRANT", 4), ("ATTENDED", -1)]
        assert (
            ServiceSessionLedger.objects.filter(
                member_service_package__pt_sale_id=sale.id, reason="REFUND"
            ).count()
            == 0
        )
        grant_id = (
            ServiceSessionLedger.objects.filter(member_service_package_id=grant.id, reason="GRANT")
            .values_list("id", flat=True)
            .get()
        )
    with pytest.raises((InternalError, ProgrammingError), match="consumption evidence"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                      created_by_membership_id, member_service_package_id
                    ) VALUES (
                      gen_random_uuid(), %s, now(), now(), -1, 'ATTENDED', now(), %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), str(grant.id)],
                )
    with pytest.raises((InternalError, ProgrammingError), match="refund evidence"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                      created_by_membership_id, member_service_package_id, reverses_entry_id
                    ) VALUES (
                      gen_random_uuid(), %s, now(), now(), -1, 'REFUND', now(), %s, %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), str(grant.id), str(grant_id)],
                )


@override_settings(**jwt_settings())
def test_void_serializes_with_payout_and_rejects_raw_partial_transitions() -> None:
    client, platform_access = platform_token("ptvoid-race-platform@example.test")
    payload = provision(
        client, platform_access, code="PTV-R", owner_email="ptvoid-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable(gym, "pt")
    _enable(gym, "finance")
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="ptvoid-race-manager@example.test", role_key="MANAGER")
    payer = add_staff(gym, email="ptvoid-race-payer@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="ptvoid-race-ada@example.test", role_key="TRAINER")
    ada = _profile(gym, trainer, code="PTV-RACE", name="Ada Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id=ada.id).update(default_commission_percent="50.00")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    day = gym_today(gym)
    member_id = _member(api, owner_headers, first_name="Nila")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=day.isoformat(),
            until=day.isoformat(),
        ).status_code
        == 201
    )
    posted = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day.isoformat(),
        body=_sale(member_id, str(ada.id), day.isoformat(), description="Race", gross_fee="80.00"),
    )
    sale_id = posted["sale"]["id"]
    commission_id = posted["commission"]["id"]
    barrier = Barrier(2, timeout=20)

    def pay() -> str:
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
                        amount=Decimal("10.00"),
                        business_date=day,
                        payment_method="OTHER",
                        cash_session_id=None,
                        reference_number="",
                        policy_note="",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except (CommissionOverpay, CommissionPayoutConflict, PTSaleStateConflict):
                    return "payout-blocked"
            return "paid"
        finally:
            connections.close_all()

    def void_sale() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    void_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        sale_id=sale_id,
                        reason="Race void",
                        business_date=day,
                        cash_session_id=None,
                        payout_reversals=[],
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except (PTSaleStateConflict, PayoutReversalIncomplete):
                    return "void-blocked"
            return "voided"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda item: item(), (pay, void_sale)))
    assert results in (["paid", "void-blocked"], ["payout-blocked", "voided"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sale = PTSale.objects.get(id=sale_id)
        posted_children = PTCommissionPayment.objects.filter(
            pt_commission__pt_sale_id=sale.id, status="POSTED"
        ).count()
        if sale.status == "VOID":
            assert posted_children == 0
            assert PTCommission.objects.get(pt_sale_id=sale.id).status == "VOID"
        else:
            assert sale.status == "POSTED"
            assert posted_children == 1

    second = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day.isoformat(),
        body=_sale(
            member_id, str(ada.id), day.isoformat(), description="Duplicate", gross_fee="30.00"
        ),
    )
    second_id = second["sale"]["id"]
    duplicate = Barrier(2, timeout=20)

    def once() -> str:
        connections.close_all()
        try:
            duplicate.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    void_pt_sale(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        sale_id=second_id,
                        reason="One void",
                        business_date=day,
                        cash_session_id=None,
                        payout_reversals=[],
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except PTSaleStateConflict:
                    return "blocked"
            return "voided"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = sorted(pool.map(lambda _item: once(), (1, 2)))
    assert raced == ["blocked", "voided"]

    raw = _approve(
        api,
        owner_headers,
        manager_headers,
        member_id=member_id,
        trainer_id=str(ada.id),
        day=day.isoformat(),
        body=_sale(member_id, str(ada.id), day.isoformat(), description="Raw", gross_fee="12.00"),
    )
    with pytest.raises((InternalError, ProgrammingError), match="void evidence"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    UPDATE trainers_pt_sale
                    SET status = 'VOID'
                    WHERE id = %s
                    """,
                    [str(raw["sale"]["id"])],
                )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                """
                SELECT
                  has_table_privilege('gym_runtime', 'trainers_pt_sale', 'UPDATE'),
                  has_table_privilege('gym_runtime', 'trainers_pt_sale', 'DELETE'),
                  has_table_privilege('gym_runtime', 'trainers_pt_commission', 'UPDATE'),
                  has_table_privilege('gym_runtime', 'trainers_service_session_ledger', 'UPDATE')
                """
            )
            assert cursor.fetchone() == (True, False, True, False)
            cursor.execute(
                "SELECT status FROM trainers_pt_sale WHERE id = %s",
                [str(raw["sale"]["id"])],
            )
            assert cursor.fetchone() == ("POSTED",)
    with pytest.raises(DatabaseError, match=r"closed sales are immutable|void evidence"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    UPDATE trainers_pt_sale
                    SET description = 'Changed'
                    WHERE id = %s
                    """,
                    [str(second_id)],
                )
