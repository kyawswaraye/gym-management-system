from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.members.services import gym_today
from apps.salaries import services as salary_services
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.tenants.models import (
    ExpenseCategory,
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Role,
    StaffProfile,
)
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
    return _client(), gym, owner, auth_headers(staff_token(owner))


def _profile(gym: Gym, membership: GymMembership, code: str, name: str) -> StaffProfile:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return StaffProfile.objects.create(
            gym=gym,
            gym_membership=membership,
            staff_code=code,
            display_name=name,
            phone="555-0100",
            joined_date=gym_today(gym),
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )


def _period_body(gym: Gym, description: str = "October salaries") -> dict[str, str]:
    today = gym_today(gym)
    start = today.replace(day=1)
    return {
        "period_start": start.isoformat(),
        "period_end": today.isoformat(),
        "business_date": today.isoformat(),
        "description": description,
    }


def _line(profile: StaffProfile, gym: Gym, method: str, amount: str) -> dict[str, str]:
    return {
        "staff_profile_id": str(profile.id),
        "amount": amount,
        "currency": gym.currency,
        "payment_method": method,
        "description": f"Pay {profile.display_name}",
        "external_reference": "",
    }


def _create(
    api: APIClient,
    headers: dict[str, str],
    gym: Gym,
    *,
    key: str | None = None,
) -> Any:
    return api.post(
        "/api/v1/salary-periods/",
        _period_body(gym),
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"sal-create-{uuid4().hex}",
        **headers,
    )


def _put_lines(
    api: APIClient,
    headers: dict[str, str],
    period_id: str,
    lines: list[dict[str, str]],
    *,
    key: str | None = None,
) -> Any:
    return api.put(
        f"/api/v1/salary-periods/{period_id}/lines/",
        {"lines": lines},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"sal-lines-{uuid4().hex}",
        **headers,
    )


def _open_till(api: APIClient, headers: dict[str, str], gym: Gym, register: str) -> str:
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": register,
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "500000.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-open-{uuid4().hex}",
        **headers,
    )
    assert opened.status_code == 201, opened.content
    return str(opened.json()["data"]["session"]["id"])


@override_settings(**jwt_settings())
def test_approval_posts_one_salary_expense_and_cash_movement_per_cash_line() -> None:
    api, gym, _owner, _headers = _gym("SALPOST")
    receptionist = add_staff(gym, email="salpost-reception@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="salpost-trainer@example.test", role_key="TRAINER")
    manager = add_staff(gym, email="salpost-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    desk = _profile(gym, receptionist, "DESK", "Front Desk")
    coach = _profile(gym, trainer, "COACH", "Coach")
    created = _create(api, reception_headers, gym)
    assert created.status_code == 201, created.content
    period_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "DRAFT"
    assert created.json()["data"]["lines"] == []

    replaced = _put_lines(
        api,
        reception_headers,
        period_id,
        [
            _line(desk, gym, "CASH", "160000.00"),
            _line(coach, gym, "BANK_TRANSFER", "80000.00"),
        ],
    )
    assert replaced.status_code == 200, replaced.content
    lines = replaced.json()["data"]["lines"]
    assert {line["amount"] for line in lines} == {"160000.00", "80000.00"}
    payee = lines[0]["staff"]
    assert set(payee) == {"id", "staff_code", "display_name"}
    detail = api.get(f"/api/v1/salary-periods/{period_id}/", **reception_headers)
    assert detail.status_code == 200, detail.content
    assert {line["amount"] for line in detail.json()["data"]["lines"]} == {
        "160000.00",
        "80000.00",
    }
    listed = api.get("/api/v1/salary-periods/", **reception_headers)
    assert listed.status_code == 200, listed.content
    listed_row = next(row for row in listed.json()["data"] if row["id"] == period_id)
    assert len(listed_row["lines"]) == 2
    cash_line = next(line for line in lines if line["payment_method"] == "CASH")
    denied = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-deny-{uuid4().hex}",
        **reception_headers,
    )
    assert denied.status_code == 403
    submitted = api.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-submit-{uuid4().hex}",
        **reception_headers,
    )
    assert submitted.status_code == 200, submitted.content
    assert submitted.json()["data"]["status"] == "PENDING_APPROVAL"
    session_id = _open_till(api, reception_headers, gym, "FRONT")
    approved = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": session_id}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY="salary-approve-key-01",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    body = approved.json()["data"]
    assert body["salary_period"]["status"] == "POSTED"
    assert len(body["expenses"]) == 2
    assert {row["amount"] for row in body["expenses"]} == {"160000.00", "80000.00"}
    assert len(body["cash_transactions"]) == 1
    assert body["cash_transactions"][0]["direction"] == "OUT"
    assert body["cash_transactions"][0]["amount"] == "160000.00"
    replay = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": cash_line["id"], "cash_session_id": session_id}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY="salary-approve-key-01",
        **manager_headers,
    )
    assert replay.status_code == 200
    assert replay.content == approved.content
    hidden = api.get(f"/api/v1/salary-periods/{period_id}/", **reception_headers)
    assert hidden.status_code == 404
    listed = api.get("/api/v1/salary-periods/?status=POSTED", **reception_headers)
    assert listed.status_code == 200
    assert listed.json()["data"] == []
    visible = api.get(f"/api/v1/salary-periods/{period_id}/", **manager_headers)
    assert visible.status_code == 200
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = ExpenseCategory.objects.get(gym_id=gym.id, system_key="STAFF_SALARY")
        expenses = list(Expense.objects.filter(gym_id=gym.id, category_id=category.id))
        assert len(expenses) == 2
        assert {expense.status for expense in expenses} == {"POSTED"}
        cash_expense = next(expense for expense in expenses if expense.payment_method == "CASH")
        assert (
            CashTransaction.objects.filter(
                gym_id=gym.id,
                source_type=CashTransaction.SourceType.EXPENSE,
                source_id=cash_expense.id,
            ).count()
            == 1
        )
        bank = next(expense for expense in expenses if expense.payment_method == "BANK_TRANSFER")
        assert bank.cash_transaction_id is None
        linked = StaffSalaryLine.objects.filter(gym_id=gym.id, salary_period_id=period_id)
        linked_ids = set(linked.values_list("expense_id", flat=True))
        assert linked_ids == {expense.id for expense in expenses}
    expense_hidden = api.get(f"/api/v1/expenses/{cash_expense.id}/", **reception_headers)
    assert expense_hidden.status_code == 404
    expense_visible = api.get(f"/api/v1/expenses/{cash_expense.id}/", **manager_headers)
    assert expense_visible.status_code == 200
    assert AuditLog.objects.filter(gym_id=gym.id, action="SALARY_PERIOD_APPROVED_POSTED").exists()


@override_settings(**jwt_settings())
def test_reject_cancel_and_manual_salary_category_have_no_financial_effect() -> None:
    api, gym, _owner, headers = _gym("SALSTOP")
    receptionist = add_staff(gym, email="salstop-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="salstop-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    desk = _profile(gym, receptionist, "DESK", "Front Desk")
    created = _create(api, reception_headers, gym)
    period_id = created.json()["data"]["id"]
    _put_lines(api, reception_headers, period_id, [_line(desk, gym, "CASH", "1000.00")])
    cancelled = api.post(
        f"/api/v1/salary-periods/{period_id}/cancel/",
        {"reason": "Entered the wrong month"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-cancel-{uuid4().hex}",
        **reception_headers,
    )
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    pending = _create(api, reception_headers, gym)
    pending_id = pending.json()["data"]["id"]
    _put_lines(api, reception_headers, pending_id, [_line(desk, gym, "BANK_TRANSFER", "1000.00")])
    api.post(
        f"/api/v1/salary-periods/{pending_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-sub-{uuid4().hex}",
        **reception_headers,
    )
    rejected = api.post(
        f"/api/v1/salary-periods/{pending_id}/reject/",
        {"rejection_reason": "Amounts need review"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-rej-{uuid4().hex}",
        **manager_headers,
    )
    assert rejected.status_code == 200, rejected.content
    assert rejected.json()["data"]["status"] == "REJECTED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        category = ExpenseCategory.objects.get(gym_id=gym.id, system_key="STAFF_SALARY")
        assert Expense.objects.filter(gym_id=gym.id, category_id=category.id).count() == 0
        assert CashTransaction.objects.filter(gym_id=gym.id).count() == 0
        assert StaffSalaryLine.objects.filter(gym_id=gym.id, expense_id__isnull=False).count() == 0
    blocked = api.post(
        "/api/v1/expenses/",
        {
            "category_id": str(category.id),
            "business_date": gym_today(gym).isoformat(),
            "description": "Manual salary",
            "amount": "10.00",
            "currency": gym.currency,
            "payment_method": "CASH",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-manual-{uuid4().hex}",
        **headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"


@override_settings(**jwt_settings())
def test_reception_scope_mappings_and_cross_gym_relationships() -> None:
    api_a, gym_a, _owner_a, _headers_a = _gym("SALA")
    api_b, gym_b, _owner_b, headers_b = _gym("SALB")
    receptionist = add_staff(gym_a, email="sala-reception@example.test", role_key="RECEPTIONIST")
    other = add_staff(gym_a, email="sala-other@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym_a, email="sala-trainer@example.test", role_key="TRAINER")
    foreign_staff = add_staff(gym_b, email="salb-trainer@example.test", role_key="TRAINER")
    manager = add_staff(gym_a, email="sala-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    other_headers = auth_headers(staff_token(other))
    manager_headers = auth_headers(staff_token(manager))
    desk = _profile(gym_a, receptionist, "DESK", "Front Desk")
    coach = _profile(gym_a, trainer, "COACH", "Coach")
    foreign = _profile(gym_b, foreign_staff, "AWAY", "Away")
    own = _create(api_a, reception_headers, gym_a)
    period_id = own.json()["data"]["id"]
    other_period = _create(api_a, other_headers, gym_a)
    other_id = other_period.json()["data"]["id"]
    hidden = api_a.get(f"/api/v1/salary-periods/{other_id}/", **reception_headers)
    assert hidden.status_code == 404
    hidden_lines = _put_lines(
        api_a, reception_headers, other_id, [_line(desk, gym_a, "CASH", "10.00")]
    )
    assert hidden_lines.status_code == 404
    cross = _put_lines(
        api_a,
        reception_headers,
        period_id,
        [_line(foreign, gym_a, "CASH", "10.00")],
    )
    assert cross.status_code == 400
    replaced = _put_lines(
        api_a,
        reception_headers,
        period_id,
        [_line(desk, gym_a, "CASH", "50.00"), _line(coach, gym_a, "CASH", "70.00")],
    )
    assert replaced.status_code == 200, replaced.content
    assert len(replaced.json()["data"]["lines"]) == 2
    only_coach = _put_lines(
        api_a, reception_headers, period_id, [_line(coach, gym_a, "CASH", "70.00")]
    )
    assert only_coach.status_code == 200, only_coach.content
    kept = [line["staff_profile_id"] for line in only_coach.json()["data"]["lines"]]
    assert kept == [str(coach.id)]
    api_a.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-scope-sub-{uuid4().hex}",
        **reception_headers,
    )
    line_id = only_coach.json()["data"]["lines"][0]["id"]
    foreign_till = _open_till(api_a, other_headers, gym_a, "SIDE")
    own_till = _open_till(api_a, reception_headers, gym_a, "FRONT")
    wrong_till = api_a.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": line_id, "cash_session_id": foreign_till}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-wrong-till-{uuid4().hex}",
        **manager_headers,
    )
    assert wrong_till.status_code == 400
    missing = api_a.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-missing-map-{uuid4().hex}",
        **manager_headers,
    )
    assert missing.status_code == 400
    non_cash = _create(api_a, reception_headers, gym_a)
    non_cash_id = non_cash.json()["data"]["id"]
    bank = _put_lines(
        api_a, reception_headers, non_cash_id, [_line(coach, gym_a, "BANK_TRANSFER", "40.00")]
    )
    bank_line = bank.json()["data"]["lines"][0]["id"]
    api_a.post(
        f"/api/v1/salary-periods/{non_cash_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-bank-sub-{uuid4().hex}",
        **reception_headers,
    )
    mapped_bank = api_a.post(
        f"/api/v1/salary-periods/{non_cash_id}/approve/",
        {"cash_sessions": [{"line_id": bank_line, "cash_session_id": own_till}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-bank-map-{uuid4().hex}",
        **manager_headers,
    )
    assert mapped_bank.status_code == 400
    foreign_get = api_b.get(f"/api/v1/salary-periods/{period_id}/", **headers_b)
    assert foreign_get.status_code == 404
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM salaries_salary_period WHERE id = %s",
                [period_id],
            )
            assert cursor.fetchone()[0] == 0
    approved = api_a.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": line_id, "cash_session_id": own_till}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-own-till-{uuid4().hex}",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    assert len(approved.json()["data"]["cash_transactions"]) == 1
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        line = StaffSalaryLine.objects.get(gym_id=gym_a.id, id=line_id)
        assert line.cash_session_id is not None
        assert str(line.cash_session_id) == own_till
        assert line.expense_id is not None
        assert line.expense.category.system_key == "STAFF_SALARY"


@override_settings(**jwt_settings())
def test_self_approval_mass_assignment_idempotency_and_concurrency() -> None:
    api, gym, _owner, owner_headers = _gym("SALRULE")
    manager = add_staff(gym, email="salrule-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="salrule-reception@example.test", role_key="RECEPTIONIST")
    trainer = add_staff(gym, email="salrule-trainer@example.test", role_key="TRAINER")
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    coach = _profile(gym, trainer, "COACH", "Coach")
    mass = api.post(
        "/api/v1/salary-periods/",
        {**_period_body(gym), "status": "POSTED", "gym_id": str(gym.id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-mass-{uuid4().hex}",
        **owner_headers,
    )
    assert mass.status_code == 400
    created = _create(api, owner_headers, gym)
    period_id = created.json()["data"]["id"]
    lines = _put_lines(api, owner_headers, period_id, [_line(coach, gym, "BANK_TRANSFER", "90.00")])
    line_id = lines.json()["data"]["lines"][0]["id"]
    api.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-self-sub-{uuid4().hex}",
        **owner_headers,
    )
    owner_self = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="salary-hash-mismatch-01",
        **owner_headers,
    )
    assert owner_self.status_code == 200, owner_self.content
    mismatch = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {"cash_sessions": [{"line_id": line_id, "cash_session_id": str(uuid4())}]},
        format="json",
        HTTP_IDEMPOTENCY_KEY="salary-hash-mismatch-01",
        **owner_headers,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    manager_own = _create(api, manager_headers, gym)
    manager_own_id = manager_own.json()["data"]["id"]
    _put_lines(
        api, manager_headers, manager_own_id, [_line(coach, gym, "BANK_TRANSFER", "45.00")]
    )
    api.post(
        f"/api/v1/salary-periods/{manager_own_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-mgr-self-sub-{uuid4().hex}",
        **manager_headers,
    )
    manager_self = api.post(
        f"/api/v1/salary-periods/{manager_own_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-mgr-self-ap-{uuid4().hex}",
        **manager_headers,
    )
    assert manager_self.status_code == 403
    assert (
        api.post(
            f"/api/v1/salary-periods/{manager_own_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"sal-mgr-owner-ap-{uuid4().hex}",
            **owner_headers,
        ).status_code
        == 200
    )

    desk = add_staff(gym, email="salrule-desk@example.test", role_key="MANAGER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=desk,
            role=Role.objects.get(gym=gym, key="RECEPTIONIST"),
        )
    desk_headers = auth_headers(staff_token(desk))
    desk_own = _create(api, desk_headers, gym)
    desk_own_id = desk_own.json()["data"]["id"]
    _put_lines(api, desk_headers, desk_own_id, [_line(coach, gym, "BANK_TRANSFER", "55.00")])
    api.post(
        f"/api/v1/salary-periods/{desk_own_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-desk-self-sub-{uuid4().hex}",
        **desk_headers,
    )
    desk_self = api.post(
        f"/api/v1/salary-periods/{desk_own_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-desk-self-ap-{uuid4().hex}",
        **desk_headers,
    )
    assert desk_self.status_code == 200, desk_self.content

    reception_denied = api.post(
        f"/api/v1/salary-periods/{manager_own_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-rec-deny-{uuid4().hex}",
        **reception_headers,
    )
    assert reception_denied.status_code in {403, 404}

    second = _create(api, manager_headers, gym)
    second_id = second.json()["data"]["id"]
    _put_lines(api, manager_headers, second_id, [_line(coach, gym, "BANK_TRANSFER", "30.00")])
    api.post(
        f"/api/v1/salary-periods/{second_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-conc-sub-{uuid4().hex}",
        **manager_headers,
    )

    def _approve(key: str) -> int:
        response = api.post(
            f"/api/v1/salary-periods/{second_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            **owner_headers,
        )
        return int(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_approve, "sal-conc-one-000001")
        second_call = pool.submit(_approve, "sal-conc-two-000002")
        codes = sorted([first.result(), second_call.result()])
    assert codes[0] == 200
    assert codes[1] in {200, 409}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        posted = SalaryPeriod.objects.filter(gym_id=gym.id, id=second_id, status="POSTED")
        assert posted.count() == 1
        assert Expense.objects.filter(gym_id=gym.id, amount=Decimal("30.00")).count() == 1

    third = _create(api, manager_headers, gym)
    third_id = third.json()["data"]["id"]
    _put_lines(api, manager_headers, third_id, [_line(coach, gym, "BANK_TRANSFER", "15.00")])
    api.post(
        f"/api/v1/salary-periods/{third_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-race-sub-{uuid4().hex}",
        **manager_headers,
    )

    def _reject() -> int:
        response = api.post(
            f"/api/v1/salary-periods/{third_id}/reject/",
            {"rejection_reason": "Duplicate batch"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="sal-race-reject-0001",
            **owner_headers,
        )
        return int(response.status_code)

    def _approve_third() -> int:
        response = api.post(
            f"/api/v1/salary-periods/{third_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="sal-race-approve-001",
            **owner_headers,
        )
        return int(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reject_code = pool.submit(_reject)
        approve_code = pool.submit(_approve_third)
        outcomes = {reject_code.result(), approve_code.result()}
    assert 200 in outcomes
    assert outcomes <= {200, 409}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = SalaryPeriod.objects.get(gym_id=gym.id, id=third_id)
        linked = StaffSalaryLine.objects.filter(gym_id=gym.id, salary_period_id=third_id)
        if period.status == "POSTED":
            assert linked.filter(expense_id__isnull=False).count() == 1
        else:
            assert period.status == "REJECTED"
            assert linked.filter(expense_id__isnull=False).count() == 0
            assert Expense.objects.filter(gym_id=gym.id, amount=Decimal("15.00")).count() == 0


@override_settings(**jwt_settings())
def test_database_guards_runtime_cannot_void_or_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    api, gym, owner, headers = _gym("SALDB")
    manager = add_staff(gym, email="saldb-manager@example.test", role_key="MANAGER")
    trainer = add_staff(gym, email="saldb-trainer@example.test", role_key="TRAINER")
    other = add_staff(gym, email="saldb-other@example.test", role_key="TRAINER")
    manager_headers = auth_headers(staff_token(manager))
    coach = _profile(gym, trainer, "COACH", "Coach")
    spare = _profile(gym, other, "SPARE", "Spare")
    created = _create(api, headers, gym)
    period_id = created.json()["data"]["id"]
    first = _put_lines(
        api,
        headers,
        period_id,
        [_line(coach, gym, "CASH", "20.00"), _line(spare, gym, "CASH", "30.00")],
    )
    assert first.status_code == 200, first.content
    line_ids = [line["id"] for line in first.json()["data"]["lines"]]
    session_id = _open_till(api, headers, gym, "OWNER")
    api.post(
        f"/api/v1/salary-periods/{period_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-db-sub-{uuid4().hex}",
        **headers,
    )
    calls = {"n": 0}
    original = salary_services.post_staff_salary_expense

    def _fail_second(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValidationError({"cash_sessions": ["forced rollback"]})
        return original(**kwargs)

    monkeypatch.setattr(salary_services, "post_staff_salary_expense", _fail_second)
    rolled = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {
            "cash_sessions": [
                {"line_id": line_ids[0], "cash_session_id": session_id},
                {"line_id": line_ids[1], "cash_session_id": session_id},
            ]
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-rollback-{uuid4().hex}",
        **manager_headers,
    )
    assert rolled.status_code == 400
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        period = SalaryPeriod.objects.get(gym_id=gym.id, id=period_id)
        assert period.status == "PENDING_APPROVAL"
        assert Expense.objects.filter(gym_id=gym.id).count() == 0
        assert CashTransaction.objects.filter(gym_id=gym.id).count() == 0
        assert StaffSalaryLine.objects.filter(gym_id=gym.id, expense_id__isnull=False).count() == 0
    monkeypatch.undo()
    approved = api.post(
        f"/api/v1/salary-periods/{period_id}/approve/",
        {
            "cash_sessions": [
                {"line_id": line_ids[0], "cash_session_id": session_id},
                {"line_id": line_ids[1], "cash_session_id": session_id},
            ]
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-db-ap-{uuid4().hex}",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    assert len(approved.json()["data"]["cash_transactions"]) == 2
    edited = api.patch(
        f"/api/v1/salary-periods/{period_id}/",
        {"description": "Changed"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sal-edit-{uuid4().hex}",
        **headers,
    )
    assert edited.status_code == 400

    def _as_runtime(cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")

    with pytest.raises(DatabaseError, match="posted salary period is immutable"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    "UPDATE salaries_salary_period SET description = %s WHERE id = %s",
                    ["Hacked", period_id],
                )
    with pytest.raises(DatabaseError, match="each line requires one salary reversal"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    UPDATE salaries_salary_period
                    SET status = 'VOID',
                        voided_at = NOW(),
                        voided_by_membership_id = %s,
                        void_business_date = business_date,
                        reason = 'incomplete'
                    WHERE id = %s
                    """,
                    [str(owner.id), period_id],
                )
    with pytest.raises(DatabaseError, match="runtime inserts must start as drafts"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    INSERT INTO salaries_salary_period (
                      id, gym_id, period_start, period_end, business_date, description, status,
                      created_with_reception_scope, submitted_with_reception_scope,
                      created_by_membership_id, updated_by_membership_id, created_at, updated_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, 'Posted insert', 'POSTED',
                      false, false, %s, %s, NOW(), NOW()
                    )
                    """,
                    [
                        uuid4(),
                        gym.id,
                        gym_today(gym),
                        gym_today(gym),
                        gym_today(gym),
                        owner.id,
                        owner.id,
                    ],
                )
    with pytest.raises(DatabaseError, match="permission denied for table salaries_salary_period"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute("DELETE FROM salaries_salary_period WHERE id = %s", [period_id])
    with pytest.raises(DatabaseError, match="salary periods cannot be deleted"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("DELETE FROM salaries_salary_period WHERE id = %s", [period_id])
    with pytest.raises(DatabaseError, match="posted line is immutable"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    "UPDATE salaries_staff_salary_line SET amount = %s WHERE salary_period_id = %s",
                    [Decimal("1.00"), period_id],
                )
