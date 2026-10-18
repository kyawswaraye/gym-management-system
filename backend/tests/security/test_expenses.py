from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.models import CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.expenses.models import Expense
from apps.files.models import FileAsset
from apps.members.services import gym_today
from apps.tenants.models import ExpenseCategory, Feature, Gym, GymFeatureEntitlement, GymMembership
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
    _enable(gym, "members")
    _enable(gym, "memberships")
    return _client(), gym, owner, auth_headers(staff_token(owner))


def _category(gym: Gym, system_key: str) -> ExpenseCategory:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return ExpenseCategory.objects.get(gym_id=gym.id, system_key=system_key)


def _draft(
    api: APIClient,
    headers: dict[str, str],
    *,
    gym: Gym,
    category_id: str,
    method: str = "CASH",
    amount: str = "25.00",
    key: str | None = None,
) -> object:
    return api.post(
        "/api/v1/expenses/",
        {
            "category_id": category_id,
            "business_date": gym_today(gym).isoformat(),
            "description": "Internet bill",
            "amount": amount,
            "currency": gym.currency,
            "payment_method": method,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"exp-create-{uuid4().hex}",
        **headers,
    )


@override_settings(**jwt_settings())
def test_cash_approval_posts_one_out_movement() -> None:
    api, gym, _owner, _headers = _gym("EXPPOST")
    receptionist = add_staff(gym, email="exppost-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="exppost-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    category = _category(gym, "INTERNET")
    created = _draft(api, reception_headers, gym=gym, category_id=str(category.id))
    assert created.status_code == 201, created.content
    expense_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "DRAFT"

    denied = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-deny-{uuid4().hex}",
        **reception_headers,
    )
    assert denied.status_code == 403

    submitted = api.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-submit-{uuid4().hex}",
        **reception_headers,
    )
    assert submitted.status_code == 200, submitted.content

    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "80.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-open-{uuid4().hex}",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]

    approved = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="expense-approve-key-01",
        **manager_headers,
    )
    assert approved.status_code == 200, approved.content
    body = approved.json()["data"]
    assert body["expense"]["status"] == "POSTED"
    assert body["cash_transaction"]["direction"] == "OUT"
    assert body["cash_transaction"]["amount"] == "25.00"
    replay = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {"cash_session_id": session_id},
        format="json",
        HTTP_IDEMPOTENCY_KEY="expense-approve-key-01",
        **manager_headers,
    )
    assert replay.status_code == 200
    assert replay.content == approved.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            CashTransaction.objects.filter(
                gym_id=gym.id,
                source_type=CashTransaction.SourceType.EXPENSE,
                source_id=expense_id,
            ).count()
            == 1
        )
        session = opened.json()["data"]["session"]
        refreshed = api.get(f"/api/v1/cash-sessions/{session['id']}/", **manager_headers)
        assert refreshed.json()["data"]["cash_expenses"] == "25.00"
    assert AuditLog.objects.filter(gym_id=gym.id, action="EXPENSE_APPROVED_POSTED").exists()


@override_settings(**jwt_settings())
def test_reject_cancel_and_protected_category() -> None:
    api, gym, _owner, headers = _gym("EXPSTOP")
    manager = add_staff(gym, email="expstop-manager@example.test", role_key="MANAGER")
    manager_headers = auth_headers(staff_token(manager))
    internet = _category(gym, "INTERNET")
    salary = _category(gym, "STAFF_SALARY")
    blocked = _draft(api, headers, gym=gym, category_id=str(salary.id))
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "PROTECTED_EXPENSE_CATEGORY"

    pending = _draft(api, headers, gym=gym, category_id=str(internet.id), method="BANK_TRANSFER")
    pending_id = pending.json()["data"]["id"]
    api.post(
        f"/api/v1/expenses/{pending_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-sub-{uuid4().hex}",
        **headers,
    )
    rejected = api.post(
        f"/api/v1/expenses/{pending_id}/reject/",
        {"rejection_reason": "Duplicate bill"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-rej-{uuid4().hex}",
        **manager_headers,
    )
    assert rejected.status_code == 200, rejected.content
    assert rejected.json()["data"]["status"] == "REJECTED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CashTransaction.objects.filter(gym_id=gym.id, source_id=pending_id).count() == 0

    draft = _draft(api, headers, gym=gym, category_id=str(internet.id), amount="5.00")
    draft_id = draft.json()["data"]["id"]
    cancelled = api.post(
        f"/api/v1/expenses/{draft_id}/cancel/",
        {"reason": "Entered twice"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-can-{uuid4().hex}",
        **headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "CANCELLED"


@override_settings(**jwt_settings())
def test_category_manage_permissions_and_reception_scope() -> None:
    api, gym, _owner, headers = _gym("EXPCAT")
    receptionist = add_staff(gym, email="expcat-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="expcat-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    listed = api.get("/api/v1/expense-categories/", **headers)
    assert listed.status_code == 200
    codes = {row["code"] for row in listed.json()["data"]}
    assert "RENT" in codes
    protected = next(row for row in listed.json()["data"] if row["system_key"] == "RENT")
    rename = api.patch(
        f"/api/v1/expense-categories/{protected['id']}/",
        {"name": "Hacked"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-rename-{uuid4().hex}",
        **headers,
    )
    assert rename.status_code == 422
    denied = api.post(
        "/api/v1/expense-categories/",
        {"code": "SNACKS", "name": "Snacks"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-cat-r-{uuid4().hex}",
        **reception_headers,
    )
    assert denied.status_code == 403
    created = api.post(
        "/api/v1/expense-categories/",
        {"code": "SNACKS", "name": "Snacks"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-cat-m-{uuid4().hex}",
        **manager_headers,
    )
    assert created.status_code == 201, created.content
    internet = _category(gym, "INTERNET")
    other = _draft(api, manager_headers, gym=gym, category_id=str(internet.id))
    own = _draft(api, reception_headers, gym=gym, category_id=str(internet.id), amount="8.00")
    hidden = api.get("/api/v1/expenses/", **reception_headers)
    ids = {row["id"] for row in hidden.json()["data"]}
    assert own.json()["data"]["id"] in ids
    assert other.json()["data"]["id"] not in ids
    missing = api.get(f"/api/v1/expenses/{other.json()['data']['id']}/", **reception_headers)
    assert missing.status_code == 404


@override_settings(**jwt_settings())
def test_tenant_isolation_mass_assignment_and_concurrency() -> None:
    api_a, gym_a, _owner_a, headers_a = _gym("EXPA")
    api_b, gym_b, _owner_b, headers_b = _gym("EXPB")
    cat_a = _category(gym_a, "RENT")
    cat_b = _category(gym_b, "RENT")
    created = _draft(api_a, headers_a, gym=gym_a, category_id=str(cat_a.id), method="BANK_TRANSFER")
    expense_id = created.json()["data"]["id"]
    foreign = api_b.get(f"/api/v1/expenses/{expense_id}/", **headers_b)
    assert foreign.status_code == 404
    wrong_cat = _draft(api_a, headers_a, gym=gym_a, category_id=str(cat_b.id))
    assert wrong_cat.status_code == 400
    posted_edit = api_a.patch(
        f"/api/v1/expenses/{expense_id}/",
        {"status": "POSTED", "amount": "1.00"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-mass-{uuid4().hex}",
        **headers_a,
    )
    assert posted_edit.status_code == 400
    api_a.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-subc-{uuid4().hex}",
        **headers_a,
    )
    manager = add_staff(gym_a, email="expa-manager@example.test", role_key="MANAGER")
    manager_headers = auth_headers(staff_token(manager))

    def _approve(key: str) -> int:
        response = api_a.post(
            f"/api/v1/expenses/{expense_id}/approve/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            **manager_headers,
        )
        return int(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_approve, "exp-conc-one-00001")
        second = pool.submit(_approve, "exp-conc-two-00002")
        codes = sorted([first.result(), second.result()])
    assert codes[0] == 200
    assert codes[1] in {200, 409}
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        posted = Expense.objects.filter(gym_id=gym_a.id, id=expense_id, status="POSTED")
        assert posted.count() == 1
        assert CashTransaction.objects.filter(gym_id=gym_a.id, source_id=expense_id).count() == 0


@override_settings(**jwt_settings())
def test_database_guards_and_runtime_cannot_void() -> None:
    api, gym, owner, headers = _gym("EXPDB")
    internet = _category(gym, "INTERNET")
    created = _draft(api, headers, gym=gym, category_id=str(internet.id), method="BANK_TRANSFER")
    expense_id = created.json()["data"]["id"]
    api.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-db-sub-{uuid4().hex}",
        **headers,
    )
    manager = add_staff(gym, email="expdb-manager@example.test", role_key="MANAGER")
    api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-db-ap-{uuid4().hex}",
        **auth_headers(staff_token(manager)),
    )

    def _as_runtime(cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")

    with pytest.raises(DatabaseError, match="posted expense is immutable"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    "UPDATE expenses_expense SET amount = %s WHERE id = %s",
                    [Decimal("99.00"), expense_id],
                )
    with pytest.raises(DatabaseError, match="void evidence does not match"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    UPDATE expenses_expense
                    SET status = 'VOID', voided_at = NOW(), reason = 'nope'
                    WHERE id = %s
                    """,
                    [expense_id],
                )
    with pytest.raises(DatabaseError, match="runtime inserts must start as drafts"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    INSERT INTO expenses_expense (
                      id, gym_id, category_id, business_date, description, amount, currency,
                      payment_method, status, created_with_reception_scope,
                      submitted_with_reception_scope, created_by_membership_id,
                      updated_by_membership_id, created_at, updated_at
                    ) VALUES (
                      %s, %s, %s, %s, 'Posted insert', 1.00, %s, 'CASH', 'POSTED',
                      false, false, %s, %s, NOW(), NOW()
                    )
                    """,
                    [
                        uuid4(),
                        gym.id,
                        internet.id,
                        gym_today(gym),
                        gym.currency,
                        owner.id,
                        owner.id,
                    ],
                )
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    DELETE FROM tenants_expense_category
                    WHERE system_key = 'RENT' AND gym_id = %s
                    """,
                    [gym.id],
                )
    with pytest.raises(DatabaseError, match="protected expense category is immutable"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                _as_runtime(cursor)
                cursor.execute(
                    """
                    UPDATE tenants_expense_category
                    SET name = 'Hacked', normalized_name = 'HACKED'
                    WHERE system_key = 'RENT' AND gym_id = %s
                    """,
                    [gym.id],
                )


@override_settings(**jwt_settings())
def test_cash_rules_idempotency_and_receipt_gate() -> None:
    api, gym, owner, headers = _gym("EXPRULE")
    receptionist = add_staff(gym, email="exprule-reception@example.test", role_key="RECEPTIONIST")
    other = add_staff(gym, email="exprule-other@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="exprule-manager@example.test", role_key="MANAGER")
    reception_headers = auth_headers(staff_token(receptionist))
    other_headers = auth_headers(staff_token(other))
    manager_headers = auth_headers(staff_token(manager))
    internet = _category(gym, "INTERNET")

    denied = api.post(
        f"/api/v1/expenses/{uuid4()}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-self-{uuid4().hex}",
        **headers,
    )
    assert denied.status_code == 404
    created = _draft(api, headers, gym=gym, category_id=str(internet.id), method="BANK_TRANSFER")
    expense_id = created.json()["data"]["id"]
    api.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-self-sub-{uuid4().hex}",
        **headers,
    )
    self_approved = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-self-ap-{uuid4().hex}",
        **headers,
    )
    assert self_approved.status_code == 403

    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "SIDE",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "40.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-side-{uuid4().hex}",
        **other_headers,
    )
    assert opened.status_code == 201, opened.content
    foreign_session = opened.json()["data"]["session"]["id"]
    non_cash = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {"cash_session_id": foreign_session},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-noncash-{uuid4().hex}",
        **manager_headers,
    )
    assert non_cash.status_code == 400

    cash = _draft(api, reception_headers, gym=gym, category_id=str(internet.id), amount="9.00")
    cash_id = cash.json()["data"]["id"]
    api.post(
        f"/api/v1/expenses/{cash_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-cash-sub-{uuid4().hex}",
        **reception_headers,
    )
    wrong_till = api.post(
        f"/api/v1/expenses/{cash_id}/approve/",
        {"cash_session_id": foreign_session},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-till-{uuid4().hex}",
        **manager_headers,
    )
    assert wrong_till.status_code == 400, wrong_till.content
    hidden_patch = api.patch(
        f"/api/v1/expenses/{expense_id}/",
        {"description": "Not mine"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-hide-{uuid4().hex}",
        **reception_headers,
    )
    assert hidden_patch.status_code == 404
    reused = _draft(
        api,
        headers,
        gym=gym,
        category_id=str(internet.id),
        amount="3.00",
        key="expense-create-reuse-1",
    )
    assert reused.status_code == 201
    conflict = _draft(
        api,
        headers,
        gym=gym,
        category_id=str(internet.id),
        amount="4.00",
        key="expense-create-reuse-1",
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        receipt = FileAsset.objects.create(
            gym=gym,
            object_key=f"{gym.id}/{uuid4()}",
            original_name="bill.jpg",
            content_type="image/jpeg",
            byte_size=128,
            sha256="ab" * 32,
            upload_content_type="image/jpeg",
            upload_byte_size=128,
            upload_sha256="ab" * 32,
            purpose="EXPENSE_RECEIPT",
            owner_type="GYM",
            owner_id=gym.id,
            scan_status=FileAsset.ScanStatus.PENDING,
            retention_status=FileAsset.RetentionStatus.QUARANTINED,
            uploaded_by_membership_id=owner.id,
        )
    dirty = _draft(api, headers, gym=gym, category_id=str(internet.id), amount="6.00")
    dirty_id = dirty.json()["data"]["id"]
    attached = api.patch(
        f"/api/v1/expenses/{dirty_id}/",
        {"receipt_file_asset_id": str(receipt.id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-file-{uuid4().hex}",
        **headers,
    )
    assert attached.status_code == 200, attached.content
    blocked = api.post(
        f"/api/v1/expenses/{dirty_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"exp-dirty-{uuid4().hex}",
        **headers,
    )
    assert blocked.status_code == 400
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CashTransaction.objects.filter(gym_id=gym.id).count() == 0
