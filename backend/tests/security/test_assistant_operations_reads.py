"""PostgreSQL coverage for salary period and cash session reads."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.assistant.operations_reads import execute_registered_read
from apps.audit.models import AuditLog
from apps.cashbook.models import CashSession
from apps.cashbook.services import open_cash_session
from apps.common.db.tenant_context import set_gym_context
from apps.members.payment_serializers import money_text
from apps.members.services import gym_today
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.salaries.services import create_period, replace_lines
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    Role,
)
from tests.security.test_assistant_member_reads import _owner
from tests.security.test_assistant_schedule import _goal, _profile
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings

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
    request.request_id = "req_operations_read"
    return request


def _read(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    payload: dict[str, object],
    *,
    codes: frozenset[str],
    roles: frozenset[str] = frozenset(),
):
    return execute_registered_read(
        request=_request(gym, actor, codes, roles),
        view=APIView(),
        tool_name=tool_name,
        payload=payload,
    )


def _periods(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_salary_periods", payload, codes=frozenset({"salaries.view"}))


def _sessions(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_cash_sessions", payload, codes=frozenset({"cashbook.view"}))


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
            SalaryPeriod.objects.filter(gym=gym).count(),
            StaffSalaryLine.objects.filter(gym=gym).count(),
            CashSession.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


@override_settings(**jwt_settings())
def test_salary_and_cash_reads_use_stored_rows() -> None:
    gym, owner = _owner("SP01", "sp01-owner@example.test")
    other_gym, other_owner = _owner("SP02", "sp02-owner@example.test")
    receptionist = add_staff(gym, email="sp01-desk@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="sp01-manager@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="sp01-trainer@example.test", role_key="TRAINER")
    accountant = add_staff(gym, email="sp01-books@example.test", role_key="ACCOUNTANT")
    _grant(gym, manager, "RECEPTIONIST")
    today = gym_today(gym)
    yesterday = today - timedelta(days=1)
    zone = ZoneInfo(gym.timezone)
    assert gym.timezone == "Asia/Yangon"
    assert today == datetime.now(zone).date()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        profile = _profile(gym, trainer, code="ST01", name="Nilar Tun")
        profile.phone = "+959000000099"
        profile.save(update_fields=["phone", "updated_at"])
        owner_period = create_period(
            gym=gym,
            actor=owner,
            fields={
                "period_start": yesterday,
                "period_end": yesterday,
                "business_date": yesterday,
                "description": "November pay",
            },
            request_id="req-period-owner",
            ip_address=None,
            user_agent="",
        )
        replace_lines(
            gym=gym,
            actor=owner,
            period_id=owner_period.id,
            lines=[
                {
                    "staff_profile_id": profile.id,
                    "amount": Decimal("250000.00"),
                    "currency": gym.currency,
                    "payment_method": "BANK_TRANSFER",
                    "external_reference": "ACCT-998877",
                    "description": "Base pay",
                }
            ],
            request_id="req-lines-owner",
            ip_address=None,
            user_agent="",
        )
        desk_period = create_period(
            gym=gym,
            actor=receptionist,
            fields={
                "period_start": today,
                "period_end": today,
                "business_date": today,
                "description": "Desk pay",
            },
            request_id="req-period-desk",
            ip_address=None,
            user_agent="",
        )
        manager_period = create_period(
            gym=gym,
            actor=manager,
            fields={
                "period_start": today,
                "period_end": today,
                "business_date": today,
                "description": "Floor pay",
            },
            request_id="req-period-manager",
            ip_address=None,
            user_agent="",
        )
        owner_session = open_cash_session(
            gym=gym,
            actor=owner,
            register_code="FRONT",
            business_date=today,
            opening_cash=Decimal("50000.00"),
            request_id="req-till-owner",
            ip_address=None,
            user_agent="",
        )
        desk_session = open_cash_session(
            gym=gym,
            actor=receptionist,
            register_code="DESK",
            business_date=today,
            opening_cash=Decimal("12000.00"),
            request_id="req-till-desk",
            ip_address=None,
            user_agent="",
        )
        manager_session = open_cash_session(
            gym=gym,
            actor=manager,
            register_code="SIDE",
            business_date=today,
            opening_cash=Decimal("8000.00"),
            request_id="req-till-manager",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        foreign_period = create_period(
            gym=other_gym,
            actor=other_owner,
            fields={
                "period_start": today,
                "period_end": today,
                "business_date": today,
                "description": "Other gym pay",
            },
            request_id="req-period-other",
            ip_address=None,
            user_agent="",
        )
        foreign_session = open_cash_session(
            gym=other_gym,
            actor=other_owner,
            register_code="FRONT",
            business_date=gym_today(other_gym),
            opening_cash=Decimal("1000.00"),
            request_id="req-till-other",
            ip_address=None,
            user_agent="",
        )
    goal = _goal(gym, owner)
    before = _counts(gym, goal)

    period_ids = {str(owner_period.id), str(desk_period.id), str(manager_period.id)}
    session_ids = {str(owner_session.id), str(desk_session.id), str(manager_session.id)}
    listed = _periods(gym, owner, {})
    assert _ids(listed) == period_ids
    owner_row = next(row for row in listed["results"] if row["id"] == str(owner_period.id))
    assert owner_row["status"] == SalaryPeriod.Status.DRAFT
    assert owner_row["business_date"] == yesterday.isoformat()
    assert owner_row["period_start"] == yesterday.isoformat()
    assert owner_row["period_end"] == yesterday.isoformat()
    assert owner_row["description"] == "November pay"
    assert "total_amount" not in owner_row
    assert "period_total" not in owner_row
    line = owner_row["lines"][0]
    assert line["id"]
    assert line["staff_profile_id"] == str(profile.id)
    assert line["staff_code"] == "ST01"
    assert line["display_name"] == "Nilar Tun"
    assert line["amount"] == money_text(Decimal("250000.00"))
    assert line["currency"] == gym.currency
    assert line["payment_method"] == "BANK_TRANSFER"
    assert "external_reference" not in line
    assert "ACCT-998877" not in str(listed)
    assert "+959000000099" not in str(listed)
    created_utc = owner_period.created_at.astimezone(ZoneInfo("UTC")).date()
    if created_utc != owner_period.business_date:
        missed = _periods(gym, owner, {"business_date": created_utc.isoformat()})
        assert str(owner_period.id) not in _ids(missed)
    assert _ids(_periods(gym, owner, {"business_date": yesterday.isoformat()})) == {
        str(owner_period.id)
    }
    assert _ids(_periods(gym, owner, {"business_date": today.isoformat()})) == {
        str(desk_period.id),
        str(manager_period.id),
    }
    assert _ids(_periods(gym, owner, {"status": "DRAFT"})) == period_ids
    for status in ("PENDING_APPROVAL", "POSTED", "REJECTED", "CANCELLED", "VOID"):
        assert _periods(gym, owner, {"status": status})["results"] == []
    assert _periods(gym, owner, {"business_date": "2020-01-01"})["results"] == []
    exact = _periods(gym, owner, {"period_id": str(owner_period.id)})
    assert _ids(exact) == {str(owner_period.id)}
    desk_periods = _read(
        gym,
        receptionist,
        "search_salary_periods",
        {},
        codes=frozenset(),
        roles=frozenset({"RECEPTIONIST"}),
    )
    assert _ids(desk_periods) == {str(desk_period.id)}
    manager_periods = _read(
        gym,
        manager,
        "search_salary_periods",
        {},
        codes=frozenset(),
        roles=frozenset({"MANAGER", "RECEPTIONIST"}),
    )
    assert _ids(manager_periods) == period_ids
    with pytest.raises(ExecutionRejected) as desk_miss:
        _read(
            gym,
            receptionist,
            "search_salary_periods",
            {"period_id": str(owner_period.id)},
            codes=frozenset(),
            roles=frozenset({"RECEPTIONIST"}),
        )
    assert desk_miss.value.code == "not_found"
    manager_exact = _read(
        gym,
        manager,
        "search_salary_periods",
        {"period_id": str(owner_period.id)},
        codes=frozenset(),
        roles=frozenset({"MANAGER", "RECEPTIONIST"}),
    )
    assert _ids(manager_exact) == {str(owner_period.id)}
    with pytest.raises(ExecutionRejected) as foreign_period_miss:
        _periods(gym, owner, {"period_id": str(foreign_period.id)})
    assert foreign_period_miss.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as unknown_period:
        _periods(gym, owner, {"period_id": str(uuid4())})
    assert unknown_period.value.code == "not_found"
    with pytest.raises(ValidationError):
        _periods(gym, owner, {"status": "SUBMITTED"})
    with pytest.raises(ValidationError):
        _periods(gym, owner, {"date_from": yesterday.isoformat()})
    with pytest.raises(ExecutionRejected) as limited_period:
        _periods(gym, owner, {"limit": 26})
    assert limited_period.value.code == "invalid_limit"
    with pytest.raises(ExecutionRejected) as other_gym_period:
        _periods(gym, owner, {"gym_id": str(other_gym.id)})
    assert other_gym_period.value.code == "gym_mismatch"

    seen_periods: set[str] = set()
    period_cursor: object = None
    for _page_number in range(5):
        payload: dict[str, object] = {"limit": 1}
        if isinstance(period_cursor, str):
            payload["after"] = period_cursor
        page = _periods(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        seen_periods |= found
        if page["has_more"] is False:
            break
        period_cursor = page["next_after"]
    assert seen_periods == period_ids
    with pytest.raises(ValidationError):
        _periods(other_gym, other_owner, {"limit": 1, "after": period_cursor})

    tills = _sessions(gym, owner, {})
    assert _ids(tills) == session_ids
    owner_till = next(row for row in tills["results"] if row["id"] == str(owner_session.id))
    assert owner_till["status"] == CashSession.Status.OPEN
    assert owner_till["business_date"] == owner_session.business_date.isoformat()
    assert owner_till["business_date"] == today.isoformat()
    assert owner_till["register_code"] == "FRONT"
    assert owner_till["currency"] == gym.currency
    assert owner_till["opening_cash"] == money_text(owner_session.opening_cash)
    assert owner_till["cash_sales"] == money_text(owner_session.cash_sales)
    assert owner_till["cash_expenses"] == money_text(owner_session.cash_expenses)
    assert owner_till["manual_net"] == money_text(owner_session.manual_net)
    assert owner_till["expected_closing"] == money_text(owner_session.expected_closing)
    assert owner_till["actual_closing"] is None
    assert owner_till["difference"] is None
    assert owner_till["closed_at"] is None
    assert "difference_reason" not in owner_till
    assert "reconciliation_status" not in owner_till
    assert "balance" not in owner_till
    assert _ids(_sessions(gym, owner, {"business_date": today.isoformat()})) == session_ids
    assert _sessions(gym, owner, {"business_date": yesterday.isoformat()})["results"] == []
    assert _ids(_sessions(gym, owner, {"status": "OPEN"})) == session_ids
    assert _sessions(gym, owner, {"status": "CLOSED"})["results"] == []
    assert _sessions(gym, owner, {"status": "VOID"})["results"] == []
    assert _ids(_sessions(gym, owner, {"register_code": "FRONT"})) == {str(owner_session.id)}
    assert _sessions(gym, owner, {"register_code": "MISSING"})["results"] == []
    desk_tills = _read(
        gym,
        receptionist,
        "search_cash_sessions",
        {},
        codes=frozenset(),
        roles=frozenset({"RECEPTIONIST"}),
    )
    assert _ids(desk_tills) == {str(desk_session.id)}
    manager_tills = _read(
        gym,
        manager,
        "search_cash_sessions",
        {},
        codes=frozenset(),
        roles=frozenset({"MANAGER", "RECEPTIONIST"}),
    )
    assert _ids(manager_tills) == {str(manager_session.id)}
    with pytest.raises(ExecutionRejected) as desk_till_miss:
        _read(
            gym,
            receptionist,
            "search_cash_sessions",
            {"session_id": str(owner_session.id)},
            codes=frozenset(),
            roles=frozenset({"RECEPTIONIST"}),
        )
    assert desk_till_miss.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as manager_till_miss:
        _read(
            gym,
            manager,
            "search_cash_sessions",
            {"session_id": str(owner_session.id)},
            codes=frozenset(),
            roles=frozenset({"MANAGER", "RECEPTIONIST"}),
        )
    assert manager_till_miss.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as foreign_till:
        _sessions(gym, owner, {"session_id": str(foreign_session.id)})
    assert foreign_till.value.code == "not_found"
    with pytest.raises(ValidationError):
        _sessions(gym, owner, {"status": "RECONCILED"})
    with pytest.raises(ValidationError):
        _sessions(gym, owner, {"register_code": "front desk"})
    with pytest.raises(ValidationError):
        _sessions(gym, owner, {"date_to": today.isoformat()})
    with pytest.raises(ExecutionRejected) as limited_till:
        _sessions(gym, owner, {"limit": 26})
    assert limited_till.value.code == "invalid_limit"

    seen_tills: set[str] = set()
    till_cursor: object = None
    for _page_number in range(5):
        payload = {"limit": 1}
        if isinstance(till_cursor, str):
            payload["after"] = till_cursor
        page = _sessions(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        seen_tills |= found
        if page["has_more"] is False:
            break
        till_cursor = page["next_after"]
    assert seen_tills == session_ids
    with pytest.raises(ValidationError):
        _sessions(other_gym, other_owner, {"limit": 1, "after": till_cursor})

    with pytest.raises(PermissionDenied):
        _read(
            gym,
            accountant,
            "search_salary_periods",
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"ACCOUNTANT"}),
        )
    with pytest.raises(PermissionDenied):
        _read(
            gym,
            accountant,
            "search_cash_sessions",
            {},
            codes=frozenset({"gym.view"}),
            roles=frozenset({"ACCOUNTANT"}),
        )
    with pytest.raises(Http404):
        _read(
            gym,
            trainer,
            "search_salary_periods",
            {},
            codes=frozenset({"salaries.view"}),
            roles=frozenset({"TRAINER"}),
        )
    with pytest.raises(Http404):
        _read(
            gym,
            trainer,
            "search_cash_sessions",
            {},
            codes=frozenset({"cashbook.view"}),
            roles=frozenset({"TRAINER"}),
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="finance").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _periods(gym, owner, {})
    with pytest.raises(PermissionDenied):
        _sessions(gym, owner, {})

    owner_period.refresh_from_db()
    owner_session.refresh_from_db()
    assert owner_period.status == SalaryPeriod.Status.DRAFT
    assert owner_session.status == CashSession.Status.OPEN
    assert isinstance(owner_period.id, UUID)
    assert isinstance(owner_session.id, UUID)
    assert _counts(gym, goal) == before
