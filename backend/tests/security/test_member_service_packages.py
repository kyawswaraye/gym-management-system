"""Member package grants, sale linkage, and the session ledger."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.test import override_settings

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Payment, Receipt
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Role,
)
from apps.trainers.exceptions import LedgerBounds, LedgerEntryIneligible
from apps.trainers.models import (
    MemberServicePackage,
    PTCommission,
    ServicePackage,
    ServiceSessionLedger,
)
from apps.trainers.package_services import adjust_ledger
from apps.trainers.pt_sale_services import approve_pt_sale
from apps.trainers.validity import inclusive_until
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
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _enable_finance(gym: Gym) -> None:
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


def _package(api, headers, **overrides: object):
    return api.post(
        "/api/v1/service-packages/",
        _body(**overrides),
        format="json",
        **headers,
    )


@override_settings(**jwt_settings())
def test_sale_package_grant_is_independent_of_gross_and_payment() -> None:
    client, platform_access = platform_token("pkg-sale-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-S", owner_email="pkg-sale-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable_finance(gym)
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pkg-sale-ada@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="pkg-sale-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="pkg-sale-reception@example.test", role_key="RECEPTIONIST")
    ada = _profile(gym, trainer_membership, code="PKG-ADA", name="Ada Trainer")
    api = _client()
    assert api.defaults["HTTP_X_GYM_BFF_SECRET"] == BFF_SECRET
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
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
    catalog = _package(api, owner_headers, code="PT_10", price="1500.00", session_count=10)
    assert catalog.status_code == 201, catalog.json()
    package_id = catalog.json()["data"]["id"]
    bare = api.post(
        "/api/v1/pt-sales/",
        _sale(member_id, str(ada.id), today, currency=gym.currency),
        format="json",
        **reception_headers,
        **_key("pkg-bare"),
    )
    assert bare.status_code == 201, bare.json()
    assert bare.json()["data"]["service_package_id"] is None
    assert bare.json()["data"]["package_price_snapshot"] is None
    selected = api.post(
        "/api/v1/pt-sales/",
        _sale(
            member_id,
            str(ada.id),
            today,
            currency=gym.currency,
            service_package_id=package_id,
            gross_fee="10.00",
            payment={
                "description": "Session fee",
                "amount": "10.00",
                "method": "BANK_TRANSFER",
                "business_date": today,
            },
        ),
        format="json",
        **reception_headers,
        **_key("pkg-select"),
    )
    assert selected.status_code == 201, selected.json()
    sale = selected.json()["data"]
    sale_id = sale["id"]
    assert sale["gross_fee"] == "10.00"
    assert sale["payment"]["amount"] == "10.00"
    assert sale["service_package_id"] == package_id
    assert sale["package_price_snapshot"] == "1500.00"
    assert sale["member_service_package_id"] is None
    cleared = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"service_package_id": None},
        format="json",
        **reception_headers,
        **_key("pkg-clear"),
    )
    assert cleared.status_code == 200, cleared.json()
    assert cleared.json()["data"]["service_package_id"] is None
    assert cleared.json()["data"]["package_price_snapshot"] is None
    restored = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"service_package_id": package_id},
        format="json",
        **reception_headers,
        **_key("pkg-restore"),
    )
    assert restored.status_code == 200, restored.json()
    kept = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"description": "Kept package"},
        format="json",
        **reception_headers,
        **_key("pkg-keep"),
    )
    assert kept.status_code == 200, kept.json()
    assert kept.json()["data"]["service_package_id"] == package_id
    assert kept.json()["data"]["package_price_snapshot"] == "1500.00"
    missing = api.post(
        "/api/v1/pt-sales/",
        _sale(
            member_id,
            str(ada.id),
            today,
            currency=gym.currency,
            service_package_id=str(uuid4()),
        ),
        format="json",
        **reception_headers,
        **_key("pkg-missing"),
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "PACKAGE_NOT_ELIGIBLE"
    submitted = api.post(
        f"/api/v1/pt-sales/{sale_id}/submit/",
        {},
        format="json",
        **reception_headers,
        **_key("pkg-submit"),
    )
    assert submitted.status_code == 200, submitted.json()
    frozen = api.patch(
        f"/api/v1/pt-sales/{sale_id}/",
        {"service_package_id": None},
        format="json",
        **reception_headers,
        **_key("pkg-frozen"),
    )
    assert frozen.status_code == 409
    rejected_sale = api.post(
        "/api/v1/pt-sales/",
        _sale(
            member_id,
            str(ada.id),
            today,
            currency=gym.currency,
            service_package_id=package_id,
        ),
        format="json",
        **reception_headers,
        **_key("pkg-reject-sale"),
    )
    assert rejected_sale.status_code == 201, rejected_sale.json()
    rejected_id = rejected_sale.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{rejected_id}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("pkg-reject-submit"),
        ).status_code
        == 200
    )
    rejected = api.post(
        f"/api/v1/pt-sales/{rejected_id}/reject/",
        {"rejection_reason": "No grant"},
        format="json",
        **manager_headers,
        **_key("pkg-reject"),
    )
    assert rejected.status_code == 200, rejected.json()
    bare_submit = api.post(
        f"/api/v1/pt-sales/{bare.json()['data']['id']}/submit/",
        {},
        format="json",
        **reception_headers,
        **_key("pkg-bare-submit"),
    )
    assert bare_submit.status_code == 200, bare_submit.json()
    bare_approved = api.post(
        f"/api/v1/pt-sales/{bare.json()['data']['id']}/approve/",
        {},
        format="json",
        **manager_headers,
        **_key("pkg-bare-approve"),
    )
    assert bare_approved.status_code == 200, bare_approved.json()
    assert bare_approved.json()["data"]["member_service_package"] is None
    approve_key = _key("pkg-approve")
    approved = api.post(
        f"/api/v1/pt-sales/{sale_id}/approve/",
        {},
        format="json",
        **manager_headers,
        **approve_key,
    )
    assert approved.status_code == 200, approved.json()
    granted = approved.json()["data"]["member_service_package"]
    assert granted["source"] == "PT_SALE"
    assert granted["complimentary"] is False
    assert granted["granted_sessions"] == 10
    assert granted["used_sessions"] == 0
    assert granted["remaining_sessions"] == 10
    assert granted["amount_snapshot"] == "1500.00"
    assert granted["payment_id"] == approved.json()["data"]["payment"]["id"]
    assert approved.json()["data"]["payment"]["amount"] == "10.00"
    assert approved.json()["data"]["sale"]["member_service_package_id"] == granted["id"]
    replay = api.post(
        f"/api/v1/pt-sales/{sale_id}/approve/",
        {},
        format="json",
        **manager_headers,
        **approve_key,
    )
    assert replay.status_code == 200, replay.json()
    assert replay.json()["data"]["member_service_package"]["id"] == granted["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert MemberServicePackage.objects.filter(pt_sale_id=sale_id).count() == 1
        assert MemberServicePackage.objects.filter(pt_sale_id=rejected_id).count() == 0
        assert (
            MemberServicePackage.objects.filter(pt_sale_id=bare.json()["data"]["id"]).count() == 0
        )
        assert (
            ServiceSessionLedger.objects.filter(member_service_package_id=granted["id"]).count()
            == 1
        )
        entry = ServiceSessionLedger.objects.get(member_service_package_id=granted["id"])
        assert entry.reason == "GRANT"
        assert entry.session_delta == 10
    cancelled = api.post(
        "/api/v1/pt-sales/",
        _sale(
            member_id,
            str(ada.id),
            today,
            currency=gym.currency,
            service_package_id=package_id,
        ),
        format="json",
        **reception_headers,
        **_key("pkg-cancel-sale"),
    )
    assert cancelled.status_code == 201, cancelled.json()
    cancelled_id = cancelled.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{cancelled_id}/cancel/",
            {"reason": "Dropped"},
            format="json",
            **reception_headers,
            **_key("pkg-cancel"),
        ).status_code
        == 200
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert MemberServicePackage.objects.filter(pt_sale_id=cancelled_id).count() == 0


@override_settings(**jwt_settings())
def test_approval_rollback_removes_grant_payment_and_commission() -> None:
    client, platform_access = platform_token("pkg-roll-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-R", owner_email="pkg-roll-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    _enable_finance(gym)
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pkg-roll-ada@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="pkg-roll-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="pkg-roll-reception@example.test", role_key="RECEPTIONIST")
    ada = _profile(gym, trainer_membership, code="PKR-ADA", name="Ada Trainer")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    reception_headers = auth_headers(staff_token(receptionist))
    today = gym_today(gym).isoformat()
    member_id = _member(api, owner_headers, first_name="Ria")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=today,
            until=today,
        ).status_code
        == 201
    )
    catalog = _package(api, owner_headers, code="PT_8", price="900.00", session_count=8)
    assert catalog.status_code == 201, catalog.json()
    created = api.post(
        "/api/v1/pt-sales/",
        _sale(
            member_id,
            str(ada.id),
            today,
            currency=gym.currency,
            gross_fee="25.00",
            service_package_id=catalog.json()["data"]["id"],
            payment={
                "description": "Transfer",
                "amount": "25.00",
                "method": "BANK_TRANSFER",
                "business_date": today,
            },
        ),
        format="json",
        **reception_headers,
        **_key("pkg-roll-create"),
    )
    assert created.status_code == 201, created.json()
    sale_id = created.json()["data"]["id"]
    payment_id = created.json()["data"]["payment"]["id"]
    assert (
        api.post(
            f"/api/v1/pt-sales/{sale_id}/submit/",
            {},
            format="json",
            **reception_headers,
            **_key("pkg-roll-submit"),
        ).status_code
        == 200
    )
    with pytest.raises(RuntimeError, match="rollback marker"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            approve_pt_sale(
                gym=Gym.objects.get(id=gym.id),
                actor=GymMembership.objects.select_related("user").get(id=manager.id),
                sale_id=sale_id,
                cash_session_id=None,
                request_id="req_pkg_rollback",
                ip_address=None,
                user_agent="",
            )
            raise RuntimeError("rollback marker")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert MemberServicePackage.objects.count() == 0
        assert ServiceSessionLedger.objects.count() == 0
        assert PTCommission.objects.count() == 0
        assert Receipt.objects.count() == 0
        payment = Payment.objects.get(id=payment_id)
        assert payment.status == "PENDING_APPROVAL"


@override_settings(**jwt_settings())
def test_admin_grant_scope_class_package_and_ledger_correction() -> None:
    client, platform_access = platform_token("pkg-grant-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-G", owner_email="pkg-grant-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PKG-O", owner_email="pkg-grant-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pkg-grant-ada@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="pkg-grant-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="pkg-grant-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="pkg-grant-accountant@example.test", role_key="ACCOUNTANT")
    ada = _profile(gym, trainer_membership, code="PGG-ADA", name="Ada Trainer")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    other_headers = auth_headers(staff_token(other_owner))
    today = gym_today(gym)
    member_id = _member(api, owner_headers, first_name="Nila")
    other_member = _member(api, owner_headers, first_name="Outside")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=member_id,
            start=today.isoformat(),
            until=today.isoformat(),
        ).status_code
        == 201
    )
    catalog = _package(
        api,
        owner_headers,
        code="PT_2",
        price="200.00",
        session_count=2,
        validity_value=1,
        validity_unit="DAY",
    )
    assert catalog.status_code == 201, catalog.json()
    package_id = catalog.json()["data"]["id"]
    blocked_class = api.post(
        "/api/v1/service-packages/",
        _body(type="CLASS", code="CLS_X"),
        format="json",
        **owner_headers,
    )
    assert blocked_class.status_code == 400
    assert "class_type_id" in blocked_class.json()["error"]["fields"]
    with pytest.raises((InternalError, ProgrammingError), match="active class type"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ServicePackage.objects.create(
                gym=gym,
                type=ServicePackage.PackageType.CLASS,
                code="CLS_4",
                name="Class four",
                normalized_name="CLASS FOUR",
                session_count=4,
                validity_value=1,
                validity_unit=ServicePackage.ValidityUnit.MONTH,
                price="80.00",
                currency=gym.currency,
                is_active=True,
                created_by_membership_id=owner.id,
                updated_by_membership_id=owner.id,
            )
    with pytest.raises((InternalError, ProgrammingError), match="active class type"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_service_package (
                      id, gym_id, created_at, updated_at, type, code, name, normalized_name,
                      session_count, validity_value, validity_unit, price, currency, is_active,
                      created_by_membership_id, updated_by_membership_id
                    ) VALUES (
                      gen_random_uuid(), %s, now(), now(), 'CLASS', 'CLS_SQL', 'Class sql',
                      'CLASS SQL', 4, 1, 'MONTH', 80.00, %s, true, %s, %s
                    )
                    """,
                    [str(gym.id), gym.currency, str(owner.id), str(owner.id)],
                )
    paid_pt = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": today.isoformat(),
            "complimentary": False,
            "reason": "Should be a sale",
        },
        format="json",
        **owner_headers,
        **_key("pkg-paid-pt"),
    )
    assert paid_pt.status_code == 422
    assert paid_pt.json()["error"]["code"] == "SALE_GRANT_REQUIRED"
    assert (
        api.post(
            "/api/v1/member-service-packages/",
            {
                "member_id": member_id,
                "service_package_id": package_id,
                "business_date": today.isoformat(),
                "complimentary": True,
                "reason": "Welcome",
            },
            format="json",
            **reception_headers,
            **_key("pkg-reception"),
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/member-service-packages/",
            {
                "member_id": member_id,
                "service_package_id": package_id,
                "business_date": today.isoformat(),
                "complimentary": True,
                "reason": "Welcome",
            },
            format="json",
            **trainer_headers,
            **_key("pkg-trainer"),
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/member-service-packages/",
            {
                "member_id": member_id,
                "service_package_id": package_id,
                "business_date": today.isoformat(),
                "complimentary": True,
                "reason": "Welcome",
            },
            format="json",
            **accountant_headers,
            **_key("pkg-accountant"),
        ).status_code
        == 403
    )
    grant_key = _key("pkg-grant")
    granted = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "  Welcome   sessions ",
        },
        format="json",
        **manager_headers,
        **grant_key,
    )
    assert granted.status_code == 201, granted.json()
    grant = granted.json()["data"]["member_service_package"]
    ledger_entry = granted.json()["data"]["ledger_entry"]
    assert grant["source"] == "ADMIN_GRANT"
    assert grant["complimentary"] is True
    assert grant["payment_id"] is None
    assert grant["pt_sale_id"] is None
    assert grant["remaining_sessions"] == 2
    assert grant["valid_from"] == today.isoformat()
    assert grant["valid_until"] == today.isoformat()
    assert ledger_entry["reason"] == "GRANT"
    assert ledger_entry["session_delta"] == 2
    replay = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "  Welcome   sessions ",
        },
        format="json",
        **manager_headers,
        **grant_key,
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["member_service_package"]["id"] == grant["id"]
    visible = api.get("/api/v1/member-service-packages/", **trainer_headers)
    assert visible.status_code == 200, visible.json()
    visible_ids = {row["id"] for row in visible.json()["data"]}
    assert grant["id"] in visible_ids
    outside = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": other_member,
            "service_package_id": package_id,
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Unassigned",
        },
        format="json",
        **owner_headers,
        **_key("pkg-unassigned"),
    )
    assert outside.status_code == 201, outside.json()
    assert outside.json()["data"]["member_service_package"]["id"] not in {
        row["id"]
        for row in api.get("/api/v1/member-service-packages/", **trainer_headers).json()["data"]
    }
    hidden = api.get(
        f"/api/v1/member-service-packages/{outside.json()['data']['member_service_package']['id']}/",
        **trainer_headers,
    )
    assert hidden.status_code == 404
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Payment.objects.count() == 0
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer_membership,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
    composed = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Composed trainer",
        },
        format="json",
        **auth_headers(staff_token(trainer_membership)),
        **_key("pkg-composed"),
    )
    assert composed.status_code == 403
    foreign = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": str(uuid4()),
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Missing package",
        },
        format="json",
        **owner_headers,
        **_key("pkg-foreign-package"),
    )
    assert foreign.status_code == 404
    other_catalog = _package(api, other_headers, code="PT_2", price="200.00")
    assert other_catalog.status_code == 201, other_catalog.json()
    cross = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": other_catalog.json()["data"]["id"],
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Other gym",
        },
        format="json",
        **owner_headers,
        **_key("pkg-cross"),
    )
    assert cross.status_code == 404
    accountant_list = api.get(
        "/api/v1/member-service-packages/?package_type=PT", **accountant_headers
    )
    assert accountant_list.status_code == 200
    assert grant["id"] in {row["id"] for row in accountant_list.json()["data"]}
    reception_list = api.get(
        f"/api/v1/member-service-packages/?member_id={member_id}",
        **reception_headers,
    )
    assert reception_list.status_code == 200
    assert {row["member_id"] for row in reception_list.json()["data"]} == {member_id}
    past = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": (today - timedelta(days=2)).isoformat(),
            "complimentary": True,
            "reason": "Already over",
        },
        format="json",
        **owner_headers,
        **_key("pkg-expired"),
    )
    assert past.status_code == 201, past.json()
    assert past.json()["data"]["member_service_package"]["status"] == "EXPIRED"
    active = api.get(
        f"/api/v1/member-service-packages/?status=ACTIVE&member_id={member_id}",
        **owner_headers,
    )
    assert grant["id"] in {row["id"] for row in active.json()["data"]}
    assert past.json()["data"]["member_service_package"]["id"] not in {
        row["id"] for row in active.json()["data"]
    }
    expired = api.get("/api/v1/member-service-packages/?status=EXPIRED", **owner_headers)
    assert past.json()["data"]["member_service_package"]["id"] in {
        row["id"] for row in expired.json()["data"]
    }
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        cursor = connection.cursor()
        cursor.execute(
            "SELECT app_inclusive_until(%s, %s, %s), app_inclusive_until(%s, %s, %s)",
            [date(2024, 2, 29), 1, "YEAR", date(2026, 1, 31), 1, "MONTH"],
        )
        assert cursor.fetchone() == (date(2025, 2, 27), date(2026, 2, 27))
        cursor.close()
        assert inclusive_until(date(2024, 2, 29), 1, "YEAR") == date(2025, 2, 27)
    ledger = api.get(
        f"/api/v1/member-service-packages/{grant['id']}/ledger/",
        **owner_headers,
    )
    assert ledger.status_code == 200, ledger.json()
    assert {row["reason"] for row in ledger.json()["data"]} == {"GRANT"}
    grant_entry_id = ledger.json()["data"][0]["id"]
    correct_key = _key("pkg-correct")
    corrected = api.post(
        f"/api/v1/member-service-packages/{grant['id']}/ledger/adjust/",
        {"reverses_entry_id": grant_entry_id, "reason": "Wrong mark"},
        format="json",
        **manager_headers,
        **correct_key,
    )
    assert corrected.status_code == 409
    assert corrected.json()["error"]["code"] == "LEDGER_ENTRY_INELIGIBLE"
    replayed = api.post(
        f"/api/v1/member-service-packages/{grant['id']}/ledger/adjust/",
        {"reverses_entry_id": grant_entry_id, "reason": "Wrong mark"},
        format="json",
        **manager_headers,
        **correct_key,
    )
    assert replayed.status_code == 409
    assert replayed.json()["error"]["code"] == "LEDGER_ENTRY_INELIGIBLE"
    original = api.post(
        f"/api/v1/member-service-packages/{grant['id']}/ledger/adjust/",
        {"reverses_entry_id": grant_entry_id, "reason": "Undo grant"},
        format="json",
        **manager_headers,
        **_key("pkg-grant-adjust"),
    )
    assert original.status_code == 409
    assert original.json()["error"]["code"] == "LEDGER_ENTRY_INELIGIBLE"
    assert (
        api.post(
            f"/api/v1/member-service-packages/{grant['id']}/ledger/adjust/",
            {"reverses_entry_id": grant_entry_id, "reason": "Trainer"},
            format="json",
            **trainer_headers,
            **_key("pkg-trainer-adjust"),
        ).status_code
        == 403
    )

    one = _package(
        api,
        owner_headers,
        code="PT_1",
        name="One session",
        price="50.00",
        session_count=1,
        validity_value=7,
        validity_unit="DAY",
    )
    assert one.status_code == 201, one.json()
    single = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": one.json()["data"]["id"],
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Last session",
        },
        format="json",
        **owner_headers,
        **_key("pkg-last"),
    )
    assert single.status_code == 201, single.json()
    single_id = single.json()["data"]["member_service_package"]["id"]
    single_grant_id = single.json()["data"]["ledger_entry"]["id"]
    barrier = Barrier(2, timeout=20)

    def correct_grant() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    adjust_ledger(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        package_id=single_id,
                        reverses_entry_id=single_grant_id,
                        reason="Concurrent correction",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except (LedgerBounds, LedgerEntryIneligible):
                    return "blocked"
            return "corrected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _item: correct_grant(), (1, 2)))
    assert results == ["blocked", "blocked"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = MemberServicePackage.objects.get(id=single_id)
        assert row.status == "ACTIVE"
        assert list(
            ServiceSessionLedger.objects.filter(member_service_package_id=single_id).values_list(
                "reason",
                "session_delta",
            )
        ) == [("GRANT", 1)]


@override_settings(**jwt_settings())
def test_ledger_constraints_reject_forged_and_mutating_sql() -> None:
    client, platform_access = platform_token("pkg-sql-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-Q", owner_email="pkg-sql-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PKG-Z", owner_email="pkg-sql-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    today = gym_today(gym)
    member_id = _member(api, owner_headers, first_name="Nila")
    catalog = _package(api, owner_headers, code="PT_3", session_count=3, price="30.00")
    assert catalog.status_code == 201, catalog.json()
    granted = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": catalog.json()["data"]["id"],
            "business_date": today.isoformat(),
            "complimentary": True,
            "reason": "Constraint",
        },
        format="json",
        **owner_headers,
        **_key("pkg-sql-grant"),
    )
    assert granted.status_code == 201, granted.json()
    package_row = granted.json()["data"]["member_service_package"]["id"]
    grant_entry = granted.json()["data"]["ledger_entry"]["id"]
    other_catalog = _package(api, other_headers, code="PT_3", session_count=3, price="30.00")
    assert other_catalog.status_code == 201, other_catalog.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM trainers_member_service_package WHERE id = %s",
                [package_row],
            )
            assert cursor.fetchone() == (1,)
            cursor.execute(
                """
                SELECT
                  has_table_privilege('gym_runtime', 'trainers_service_session_ledger', 'SELECT'),
                  has_table_privilege('gym_runtime', 'trainers_service_session_ledger', 'INSERT'),
                  has_table_privilege('gym_runtime', 'trainers_service_session_ledger', 'UPDATE'),
                  has_table_privilege('gym_runtime', 'trainers_service_session_ledger', 'DELETE'),
                  has_table_privilege('gym_runtime', 'trainers_member_service_package', 'UPDATE'),
                  has_table_privilege('gym_runtime', 'trainers_member_service_package', 'DELETE')
                """
            )
            assert cursor.fetchone() == (True, True, False, False, True, False)
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM trainers_member_service_package WHERE id = %s",
                [package_row],
            )
            assert cursor.fetchone() == (0,)
    with pytest.raises((InternalError, ProgrammingError), match="append-only"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE trainers_service_session_ledger SET session_delta = 1 WHERE id = %s",
                    [grant_entry],
                )
    with pytest.raises((InternalError, ProgrammingError), match="append-only"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM trainers_service_session_ledger WHERE id = %s",
                    [grant_entry],
                )
    with pytest.raises(DatabaseError, match="permission denied"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "UPDATE trainers_service_session_ledger SET session_delta = 1 WHERE id = %s",
                    [grant_entry],
                )
    with pytest.raises(DatabaseError, match=r"not active|same_gym|foreign key"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trainers_member_service_package (
                      id, gym_id, created_at, updated_at, source, package_type, package_code,
                      package_name, granted_sessions, amount_snapshot, currency, validity_value,
                      validity_unit, valid_from, valid_until, complimentary, status,
                      created_by_membership_id, updated_by_membership_id, member_id,
                      service_package_id
                    )
                    VALUES (
                      gen_random_uuid(), %s, now(), now(), 'ADMIN_GRANT', 'PT', 'PT_3',
                      'Ten sessions', 3, 30.00, %s, 3, 'MONTH',
                      %s, app_inclusive_until(%s, 3, 'MONTH'),
                      true, 'ACTIVE', %s, %s, %s, %s
                    )
                    """,
                    [
                        str(gym.id),
                        gym.currency,
                        today,
                        today,
                        str(owner.id),
                        str(owner.id),
                        member_id,
                        other_catalog.json()["data"]["id"],
                    ],
                )
    with pytest.raises(DatabaseError, match=r"one grant|duplicate key|sess_ledger_one_grant"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                      created_by_membership_id, member_service_package_id
                    ) VALUES (
                      gen_random_uuid(), %s, now(), now(), 3, 'GRANT', now(), %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), package_row],
                )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_functiondef('app_guard_session_ledger()'::regprocedure)")
        ledger = str(cursor.fetchone()[0])
        cursor.execute(
            "SELECT pg_get_functiondef('app_assert_booking_ledger_effect()'::regprocedure)"
        )
        effect = str(cursor.fetchone()[0])
    assert "consumption evidence is incomplete" in ledger
    assert "consumption booking is not eligible" in ledger
    assert "NEW.reason = 'EXPIRY'" in ledger
    assert "IN ('ATTENDED', 'POLICY_NO_SHOW', 'EXPIRY')" not in ledger
    assert "consumption booking is not eligible" in effect
    assert "consumption has no terminal booking" in effect
    for reason in ("ATTENDED", "POLICY_NO_SHOW"):
        with pytest.raises(DatabaseError, match="consumption evidence is incomplete"):
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
                          gen_random_uuid(), %s, now(), now(), -1, %s, now(), %s, %s
                        )
                        """,
                        [str(gym.id), reason, str(owner.id), package_row],
                    )
    with pytest.raises(DatabaseError, match="this reason is not published"):
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
                      gen_random_uuid(), %s, now(), now(), -1, 'EXPIRY', now(), %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), package_row],
                )
    with pytest.raises(DatabaseError, match="refund evidence"):
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
                      gen_random_uuid(), %s, now(), now(), -1, 'REFUND', now(), %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), package_row],
                )
    with pytest.raises(DatabaseError, match="not eligible"):
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
                      gen_random_uuid(), %s, now(), now(), -3, 'CORRECTION', now(), %s, %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), package_row, grant_entry],
                )
    attended_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE trainers_service_session_ledger DISABLE TRIGGER sess_ledger_guard_trg"
        )
    try:
        with pytest.raises(DatabaseError, match="consumption booking is not eligible"):
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO trainers_service_session_ledger (
                          id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                          created_by_membership_id, member_service_package_id
                        ) VALUES (%s, %s, now(), now(), -1, 'ATTENDED', now(), %s, %s)
                        """,
                        [str(attended_id), str(gym.id), str(owner.id), package_row],
                    )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE trainers_service_session_ledger ENABLE TRIGGER sess_ledger_guard_trg"
            )
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE trainers_service_session_ledger DISABLE TRIGGER sess_ledger_guard_trg"
        )
        cursor.execute(
            "ALTER TABLE trainers_service_session_ledger DISABLE TRIGGER sess_ledger_effect_trg"
        )
    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                      created_by_membership_id, member_service_package_id
                    ) VALUES (%s, %s, now(), now(), -1, 'ATTENDED', now(), %s, %s)
                    """,
                    [str(attended_id), str(gym.id), str(owner.id), package_row],
                )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE trainers_service_session_ledger ENABLE TRIGGER sess_ledger_guard_trg"
            )
            cursor.execute(
                "ALTER TABLE trainers_service_session_ledger ENABLE TRIGGER sess_ledger_effect_trg"
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                """
                INSERT INTO trainers_service_session_ledger (
                  id, gym_id, created_at, updated_at, session_delta, reason, occurred_at,
                  created_by_membership_id, member_service_package_id, reverses_entry_id
                ) VALUES (gen_random_uuid(), %s, now(), now(), 1, 'CORRECTION', now(), %s, %s, %s)
                """,
                [str(gym.id), str(owner.id), package_row, str(attended_id)],
            )
    with pytest.raises(DatabaseError, match=r"duplicate key|sess_ledger_one_reversal"):
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
                      gen_random_uuid(), %s, now(), now(), 1, 'CORRECTION', now(), %s, %s, %s
                    )
                    """,
                    [str(gym.id), str(owner.id), package_row, str(attended_id)],
                )
    with pytest.raises(DatabaseError, match="consumption evidence is incomplete"):
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
                    [str(gym.id), str(owner.id), package_row],
                )
