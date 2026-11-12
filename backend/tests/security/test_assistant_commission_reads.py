"""PostgreSQL coverage for PT commission and payout reads."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.commission_reads import execute_registered_read
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.payment_serializers import money_text
from apps.members.services import gym_today
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    Role,
    StaffProfile,
)
from apps.trainers.models import PTCommission, PTCommissionPayment
from tests.security.test_assistant_schedule import _goal
from tests.security.test_pt_commissions import _enable, _pay, _post_sale
from tests.security.test_tenant_gym_settings import (
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


def _request(
    gym: Gym,
    actor: GymMembership,
    codes: frozenset[str],
    roles: frozenset[str] = frozenset(),
) -> Request:
    request = Request(APIRequestFactory().get("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.role_keys = roles
    request.request_id = "req_commission_read"
    return request


def _read(
    gym: Gym,
    actor: GymMembership,
    payload: dict[str, object],
    *,
    codes: frozenset[str],
    roles: frozenset[str] = frozenset(),
):
    return execute_registered_read(
        request=_request(gym, actor, codes, roles),
        view=APIView(),
        tool_name="search_pt_commissions",
        payload=payload,
    )


def _ids(page: dict[str, object]) -> set[str]:
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows}


def _grant(gym: Gym, membership: GymMembership, role_key: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=membership,
            role=Role.objects.get(gym=gym, key=role_key),
        )


def _counts(gym: Gym, goal: Goal) -> tuple[int, ...]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            PTCommission.objects.filter(gym=gym).count(),
            PTCommissionPayment.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


@override_settings(**jwt_settings())
def test_commission_reads_use_stored_rows() -> None:
    api, platform_access = platform_token("cmrd-platform@example.test")
    first = provision(api, platform_access, code="CMR1", owner_email="cmr1-owner@example.test")
    second = provision(api, platform_access, code="CMR2", owner_email="cmr2-owner@example.test")
    gym = Gym.objects.get(id=first["gym"]["id"])
    other_gym = Gym.objects.get(id=second["gym"]["id"])
    _enable(gym, "finance")
    _enable(gym, "pt")
    _enable(other_gym, "finance")
    _enable(other_gym, "pt")
    owner = GymMembership.objects.select_related("user").get(
        id=first["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=second["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="cmr1-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="cmr1-desk@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="cmr1-books@example.test", role_key="ACCOUNTANT")
    ada_membership = add_staff(gym, email="cmr1-ada@example.test", role_key="TRAINER")
    bea_membership = add_staff(gym, email="cmr1-bea@example.test", role_key="TRAINER")
    ada = _profile(gym, ada_membership, code="ADA", name="Ada Coach")
    bea = _profile(gym, bea_membership, code="BEA", name="Bea Coach")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        StaffProfile.objects.filter(id__in=[ada.id, bea.id]).update(
            default_commission_percent="33.33",
            phone="+959000000099",
        )
    today = gym_today(gym)
    yesterday = today - timedelta(days=1)
    zone = ZoneInfo(gym.timezone)
    assert gym.timezone == "Asia/Yangon"
    assert today == datetime.now(zone).date()
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    other_headers = auth_headers(staff_token(other_owner))
    ada_member = _member(api, owner_headers, first_name="Nila")
    bea_member = _member(api, owner_headers, first_name="Omar")
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(ada.id),
            member_id=ada_member,
            start=yesterday.isoformat(),
        ).status_code
        == 201
    )
    assert (
        _assign(
            api,
            owner_headers,
            trainer_id=str(bea.id),
            member_id=bea_member,
            start=yesterday.isoformat(),
        ).status_code
        == 201
    )
    _ada_sale, ada_commission_id = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=ada_member,
        trainer_id=str(ada.id),
        day=yesterday.isoformat(),
    )
    _bea_sale, bea_commission_id = _post_sale(
        api,
        owner_headers,
        manager_headers,
        member_id=bea_member,
        trainer_id=str(bea.id),
        day=today.isoformat(),
    )
    other_member = _member(api, other_headers, first_name="Other")
    other_trainer = add_staff(other_gym, email="cmr2-ada@example.test", role_key="TRAINER")
    other_profile = _profile(other_gym, other_trainer, code="OTH", name="Other Coach")
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        StaffProfile.objects.filter(id=other_profile.id).update(default_commission_percent="33.33")
    other_manager = add_staff(other_gym, email="cmr2-manager@example.test", role_key="MANAGER")
    assert (
        _assign(
            api,
            other_headers,
            trainer_id=str(other_profile.id),
            member_id=other_member,
            start=today.isoformat(),
        ).status_code
        == 201
    )
    _other_sale, foreign_commission_id = _post_sale(
        api,
        other_headers,
        auth_headers(staff_token(other_manager)),
        member_id=other_member,
        trainer_id=str(other_profile.id),
        day=today.isoformat(),
    )
    paid = _pay(
        api,
        owner_headers,
        ada_commission_id,
        {
            "amount": "1.00",
            "business_date": today.isoformat(),
            "payment_method": "BANK_TRANSFER",
            "policy_note": "secret-policy",
            "reference_number": "ACCT-19",
        },
    )
    assert paid.status_code == 200, paid.content
    _grant(gym, manager, "RECEPTIONIST")
    view = frozenset({"pt.commissions.view"})
    goal = _goal(gym, owner)
    before = _counts(gym, goal)

    listed = _read(gym, owner, {}, codes=view)
    assert _ids(listed) == {ada_commission_id, bea_commission_id}
    ada_row = next(row for row in listed["results"] if row["id"] == ada_commission_id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = PTCommission.objects.select_related("pt_sale").get(id=ada_commission_id)
        payout = PTCommissionPayment.objects.get(pt_commission_id=ada_commission_id)
    assert ada_row["status"] == stored.status
    assert ada_row["service_date"] == yesterday.isoformat()
    assert ada_row["service_date"] == stored.pt_sale.service_date.isoformat()
    assert ada_row["staff_code"] == "ADA"
    assert ada_row["display_name"] == "Ada Coach"
    assert ada_row["commission_amount"] == money_text(stored.commission_amount)
    assert ada_row["paid_amount"] == money_text(stored.paid_amount)
    assert ada_row["outstanding_amount"] == money_text(stored.outstanding_amount)
    assert ada_row["paid_at"] is None
    assert "gross_fee_snapshot" not in ada_row
    payment = ada_row["payments"][0]
    assert payment["id"] == str(payout.id)
    assert payment["amount"] == money_text(payout.amount)
    assert payment["business_date"] == today.isoformat()
    assert payment["business_date"] == payout.business_date.isoformat()
    assert payment["business_date"] != ada_row["service_date"]
    assert payment["payment_method"] == "BANK_TRANSFER"
    assert payment["status"] == payout.status
    assert payment["cash_session_id"] is None
    assert "secret-policy" not in str(listed)
    assert "ACCT-19" not in str(listed)
    assert "+959000000099" not in str(listed)
    created_utc = stored.created_at.astimezone(ZoneInfo("UTC")).date()
    if created_utc != stored.pt_sale.service_date:
        missed = _read(
            gym,
            owner,
            {
                "service_date_from": created_utc.isoformat(),
                "service_date_to": created_utc.isoformat(),
            },
            codes=view,
        )
        assert ada_commission_id not in _ids(missed)
    assert _ids(
        _read(
            gym,
            owner,
            {
                "service_date_from": yesterday.isoformat(),
                "service_date_to": yesterday.isoformat(),
            },
            codes=view,
        )
    ) == {ada_commission_id}
    assert _ids(
        _read(
            gym,
            owner,
            {"service_date_from": today.isoformat(), "service_date_to": today.isoformat()},
            codes=view,
        )
    ) == {bea_commission_id}
    assert _ids(_read(gym, owner, {"status": "PARTIALLY_PAID"}, codes=view)) == {ada_commission_id}
    assert _ids(_read(gym, owner, {"status": "ACCRUED"}, codes=view)) == {bea_commission_id}
    assert _read(gym, owner, {"status": "PAID"}, codes=view)["results"] == []
    assert _read(gym, owner, {"status": "VOID"}, codes=view)["results"] == []
    assert (
        _read(
            gym,
            owner,
            {"service_date_from": "2020-01-01", "service_date_to": "2020-01-01"},
            codes=view,
        )["results"]
        == []
    )
    exact = _read(gym, owner, {"commission_id": ada_commission_id}, codes=view)
    assert _ids(exact) == {ada_commission_id}
    ada_seen = _read(
        gym,
        ada_membership,
        {},
        codes=view,
        roles=frozenset({"TRAINER"}),
    )
    assert _ids(ada_seen) == {ada_commission_id}
    bea_seen = _read(
        gym,
        bea_membership,
        {},
        codes=view,
        roles=frozenset({"TRAINER"}),
    )
    assert _ids(bea_seen) == {bea_commission_id}
    assert bea_seen["results"][0]["payments"] == []
    manager_seen = _read(
        gym,
        manager,
        {},
        codes=view,
        roles=frozenset({"MANAGER", "RECEPTIONIST"}),
    )
    assert _ids(manager_seen) == {ada_commission_id, bea_commission_id}
    with pytest.raises(PermissionDenied):
        _read(
            gym,
            receptionist,
            {},
            codes=frozenset(),
            roles=frozenset({"RECEPTIONIST"}),
        )
    with pytest.raises(PermissionDenied):
        _read(
            gym,
            accountant,
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"ACCOUNTANT"}),
        )
    with pytest.raises(PermissionDenied):
        _read(
            gym,
            ada_membership,
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"TRAINER"}),
        )
    with pytest.raises(ExecutionRejected) as foreign_miss:
        _read(gym, owner, {"commission_id": foreign_commission_id}, codes=view)
    assert foreign_miss.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as unknown:
        _read(gym, owner, {"commission_id": str(uuid4())}, codes=view)
    assert unknown.value.code == "not_found"
    with pytest.raises(ValidationError):
        _read(gym, owner, {"status": "SUBMITTED"}, codes=view)
    with pytest.raises(ValidationError):
        _read(gym, owner, {"business_date": today.isoformat()}, codes=view)
    with pytest.raises(ValidationError):
        _read(
            gym,
            owner,
            {"service_date_from": today.isoformat(), "service_date_to": yesterday.isoformat()},
            codes=view,
        )
    with pytest.raises(ExecutionRejected) as limited:
        _read(gym, owner, {"limit": 26}, codes=view)
    assert limited.value.code == "invalid_limit"
    with pytest.raises(ExecutionRejected) as other_gym_miss:
        _read(gym, owner, {"gym_id": str(other_gym.id)}, codes=view)
    assert other_gym_miss.value.code == "gym_mismatch"

    seen: set[str] = set()
    cursor: object = None
    for _page_number in range(5):
        payload: dict[str, object] = {"limit": 1}
        if isinstance(cursor, str):
            payload["after"] = cursor
        page = _read(gym, owner, payload, codes=view)
        found = _ids(page)
        assert len(found) == 1
        seen |= found
        if page["has_more"] is False:
            break
        cursor = page["next_after"]
    assert seen == {ada_commission_id, bea_commission_id}
    with pytest.raises(ValidationError):
        _read(other_gym, other_owner, {"limit": 1, "after": cursor}, codes=view)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="pt").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _read(gym, owner, {}, codes=view)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored.refresh_from_db()
        payout.refresh_from_db()
    assert stored.status == PTCommission.Status.PARTIALLY_PAID
    assert payout.status == PTCommissionPayment.Status.POSTED
    assert isinstance(stored.id, UUID)
    assert isinstance(payout.id, UUID)
    assert _counts(gym, goal) == before
