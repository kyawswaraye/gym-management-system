"""Trainer-only memberships stay out of non-PT finance routes."""

from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    Permission,
    Role,
    RolePermission,
)
from apps.users.models import User
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _member, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_DELEGABLE = (
    "payments.view",
    "payments.create",
    "payments.update",
    "payments.submit",
    "payments.void",
    "payments.refund",
    "receipts.view",
    "receipts.void",
    "reports.view",
    "reports.financial",
    "expenses.view",
    "expenses.create",
    "expenses.update",
    "expenses.submit",
    "expenses.void",
    "expense_categories.manage",
    "cashbook.view",
    "cashbook.open",
    "cashbook.adjust",
    "cashbook.reconcile",
    "salaries.view",
    "salaries.create",
    "salaries.update",
    "salaries.submit",
    "salaries.void",
    "inventory.view",
    "inventory.manage",
    "inventory.adjust",
    "retail.view",
    "retail.create",
    "retail.update",
    "retail.submit",
    "retail.void",
    "files.upload",
    "files.view",
)
_OBJECT_TABLES = (
    "members_member",
    "members_payment",
    "members_receipt",
    "expenses_expense",
    "tenants_expense_category",
    "cashbook_cash_session",
    "cashbook_cash_transaction",
    "salaries_salary_period",
    "salaries_staff_salary_line",
    "inventory_product",
    "inventory_stock_movement",
    "sales_retail_sale",
    "sales_retail_return",
    "files_file_asset",
    "audit_audit_log",
    "tenants_idempotency_record",
)
# method, path template, expected status. Hard-reserved approvals stay 403.
_ROUTES = (
    ("GET", "/api/v1/payments/", 404),
    ("POST", "/api/v1/payments/", 404),
    ("GET", "/api/v1/payments/{id}/", 404),
    ("PATCH", "/api/v1/payments/{id}/", 404),
    ("POST", "/api/v1/payments/{id}/submit/", 404),
    ("POST", "/api/v1/payments/{id}/approve/", 403),
    ("POST", "/api/v1/payments/{id}/reject/", 403),
    ("POST", "/api/v1/payments/{id}/cancel/", 404),
    ("POST", "/api/v1/payments/{id}/void/", 404),
    ("POST", "/api/v1/payments/{id}/refund/", 404),
    ("GET", "/api/v1/receipts/", 404),
    ("GET", "/api/v1/receipts/{id}/", 404),
    ("POST", "/api/v1/receipts/{id}/void/", 404),
    ("GET", "/api/v1/receipts/{id}/download/", 404),
    ("GET", "/api/v1/sales/daily/", 404),
    ("GET", "/api/v1/expense-categories/", 404),
    ("POST", "/api/v1/expense-categories/", 404),
    ("PATCH", "/api/v1/expense-categories/{id}/", 404),
    ("GET", "/api/v1/expenses/", 404),
    ("POST", "/api/v1/expenses/", 404),
    ("GET", "/api/v1/expenses/{id}/", 404),
    ("PATCH", "/api/v1/expenses/{id}/", 404),
    ("POST", "/api/v1/expenses/{id}/submit/", 404),
    ("POST", "/api/v1/expenses/{id}/approve/", 403),
    ("POST", "/api/v1/expenses/{id}/reject/", 403),
    ("POST", "/api/v1/expenses/{id}/cancel/", 404),
    ("POST", "/api/v1/expenses/{id}/void/", 404),
    ("GET", "/api/v1/cash-sessions/", 404),
    ("POST", "/api/v1/cash-sessions/open/", 404),
    ("GET", "/api/v1/cash-sessions/{id}/", 404),
    ("POST", "/api/v1/cash-sessions/{id}/adjustments/", 404),
    ("POST", "/api/v1/cash-sessions/{id}/reconcile/", 404),
    ("GET", "/api/v1/cash-transactions/", 404),
    ("GET", "/api/v1/salary-periods/", 404),
    ("POST", "/api/v1/salary-periods/", 404),
    ("GET", "/api/v1/salary-periods/{id}/", 404),
    ("PATCH", "/api/v1/salary-periods/{id}/", 404),
    ("PUT", "/api/v1/salary-periods/{id}/lines/", 404),
    ("POST", "/api/v1/salary-periods/{id}/submit/", 404),
    ("POST", "/api/v1/salary-periods/{id}/approve/", 403),
    ("POST", "/api/v1/salary-periods/{id}/void/", 404),
    ("POST", "/api/v1/salary-periods/{id}/reject/", 403),
    ("POST", "/api/v1/salary-periods/{id}/cancel/", 404),
    ("GET", "/api/v1/product-categories/", 404),
    ("POST", "/api/v1/product-categories/", 404),
    ("GET", "/api/v1/products/low-stock/", 404),
    ("GET", "/api/v1/products/", 404),
    ("POST", "/api/v1/products/", 404),
    ("GET", "/api/v1/products/{id}/", 404),
    ("PATCH", "/api/v1/products/{id}/", 404),
    ("GET", "/api/v1/stock-movements/", 404),
    ("POST", "/api/v1/stock-movements/adjust/", 404),
    ("GET", "/api/v1/retail-sales/", 404),
    ("POST", "/api/v1/retail-sales/", 404),
    ("GET", "/api/v1/retail-sales/{id}/", 404),
    ("PATCH", "/api/v1/retail-sales/{id}/", 404),
    ("POST", "/api/v1/retail-sales/{id}/submit/", 404),
    ("POST", "/api/v1/retail-sales/{id}/approve/", 403),
    ("POST", "/api/v1/retail-sales/{id}/void/", 404),
    ("POST", "/api/v1/retail-sales/{id}/return/", 404),
    ("POST", "/api/v1/retail-sales/{id}/reject/", 403),
    ("POST", "/api/v1/retail-sales/{id}/cancel/", 404),
    ("POST", "/api/v1/files/upload-intents/", 404),
    ("POST", "/api/v1/files/{id}/complete/", 404),
    ("GET", "/api/v1/files/{id}/download-url/", 404),
    ("DELETE", "/api/v1/files/{id}/", 404),
    ("GET", "/api/v1/dashboard/", 404),
)
_IDEMPOTENCY_KEY = "trainer-finance-scope-key"


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable(gym: Gym, code: str, *, enabled: bool) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code, defaults={"name": code, "description": code}
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": enabled,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _grant(gym: Gym, owner: GymMembership, membership: GymMembership) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        role = Role.objects.create(
            gym=gym,
            key="FINANCE",
            name="Finance Desk",
            created_by_membership_id=owner.id,
        )
        permissions = list(Permission.objects.filter(code__in=_DELEGABLE))
        assert {row.code for row in permissions} == set(_DELEGABLE)
        for permission in permissions:
            RolePermission.objects.create(
                gym=gym,
                role=role,
                permission=permission,
                created_by_membership_id=owner.id,
            )
        GymMembershipRole.objects.create(gym=gym, gym_membership=membership, role=role)


def _effects(gym: Gym) -> tuple[int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            AuditLog.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
        )


def _call(
    api: APIClient,
    headers: dict[str, str],
    method: str,
    path: str,
    probe: str,
) -> tuple[object, str]:
    url = path.format(id=probe)
    extra = dict(headers)
    if method == "GET":
        url = f"{url}?member={probe}"
        caller = api.get
        payload: dict[str, str] | None = None
    else:
        extra["HTTP_IDEMPOTENCY_KEY"] = _IDEMPOTENCY_KEY
        caller = {"POST": api.post, "PATCH": api.patch, "PUT": api.put, "DELETE": api.delete}[
            method
        ]
        payload = {"member_id": probe, "purpose": "EXPENSE_RECEIPT"}
    with CaptureQueriesContext(connection) as captured:
        if payload is None:
            response = caller(url, **extra)
        else:
            response = caller(url, payload, format="json", **extra)
    sql = " ".join(query["sql"].lower() for query in captured.captured_queries)
    return response, sql


@override_settings(**jwt_settings())
def test_trainer_only_finance_routes_stay_closed_before_lookup() -> None:
    client, platform_access = platform_token("finance-scope-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="FIN-S",
        owner_email="finance-scope-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    for code in ("finance", "reports", "pt"):
        _enable(gym, code, enabled=True)
    trainer = add_staff(gym, email="finance-scope-ada@example.test", role_key="TRAINER")
    _profile(gym, trainer, code="FIN-ADA", name="Ada Finance")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    probe = _member(api, owner_headers, first_name="Nila")
    bare_headers = auth_headers(staff_token(trainer))
    missing, missing_sql = _call(api, bare_headers, "GET", "/api/v1/payments/", probe)
    assert missing.status_code == 403, missing.content
    assert "members_payment" not in missing_sql
    assert probe not in missing_sql

    _grant(gym, owner, trainer)
    trainer.refresh_from_db()
    headers = auth_headers(staff_token(trainer))
    _enable(gym, "finance", enabled=False)
    unentitled, unentitled_sql = _call(api, headers, "GET", "/api/v1/payments/", probe)
    assert unentitled.status_code == 403, unentitled.content
    assert "members_payment" not in unentitled_sql
    _enable(gym, "finance", enabled=True)

    before = _effects(gym)
    for method, path, expected in _ROUTES:
        response, sql = _call(api, headers, method, path, probe)
        assert response.status_code == expected, (method, path, response.content)
        rendered = response.content.decode()
        assert probe not in rendered
        assert "Nila" not in rendered
        # A file id may be read only to tell a profile photo from an expense receipt.
        file_identity = path.startswith("/api/v1/files/") and "upload-intents" not in path
        if not file_identity:
            assert probe not in sql
        for table in _OBJECT_TABLES:
            if table == "files_file_asset" and file_identity:
                continue
            assert table not in sql, (method, path, table)
        assert "insert into" not in sql
        assert "update " not in sql
    assert _effects(gym) == before

    for path in (
        "/api/v1/trainers/",
        "/api/v1/service-packages/",
        "/api/v1/pt-sales/",
        "/api/v1/pt-commissions/",
        "/api/v1/pt-bookings/",
        "/api/v1/class-bookings/",
    ):
        opened = api.get(path, **headers)
        assert opened.status_code == 200, (path, opened.content)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        user = User.objects.create_user(f"finance-clerk-{uuid4().hex[:8]}@example.test", PASSWORD)
        clerk = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=clerk,
            role=Role.objects.get(gym=gym, key="FINANCE"),
        )
    clerk.user = user
    clerk_headers = auth_headers(staff_token(clerk))
    listed = api.get(f"/api/v1/payments/?member={probe}", **clerk_headers)
    assert listed.status_code == 200, listed.content
    receipts = api.get(f"/api/v1/receipts/?member={probe}", **clerk_headers)
    assert receipts.status_code == 200, receipts.content
    drafted = api.post(
        "/api/v1/payments/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="trainer-finance-clerk-key",
        **clerk_headers,
    )
    assert drafted.status_code == 400, drafted.content

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
        trainer.refresh_from_db()
    manager_headers = auth_headers(staff_token(trainer))
    managed = api.get(f"/api/v1/payments/?member={probe}", **manager_headers)
    assert managed.status_code == 200, managed.content
    managed_receipts = api.get(f"/api/v1/receipts/?member={probe}", **manager_headers)
    assert managed_receipts.status_code == 200, managed_receipts.content
