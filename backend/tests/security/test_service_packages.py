"""PT service-package catalog publication."""

from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection, connections, transaction
from django.db.utils import InternalError, ProgrammingError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    Role,
    RolePermission,
    SystemRoleTemplate,
)
from apps.trainers.exceptions import ServicePackageConflict
from apps.trainers.models import ServicePackage
from apps.trainers.services import create_service_package
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

PACKAGE_FIELDS = {
    "id",
    "type",
    "code",
    "name",
    "session_count",
    "validity_value",
    "validity_unit",
    "price",
    "currency",
    "class_type_id",
    "is_active",
    "created_at",
    "updated_at",
}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "PT",
        "code": "PT_10",
        "name": "Ten sessions",
        "session_count": 10,
        "validity_value": 3,
        "validity_unit": "MONTH",
        "price": "1500.00",
    }
    payload.update(overrides)
    return payload


def _codes(gym: Gym, role_key: str) -> set[str]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return set(
            RolePermission.objects.filter(gym=gym, role__key=role_key).values_list(
                "permission__code", flat=True
            )
        )


@override_settings(**jwt_settings())
def test_service_package_catalog_is_pt_only_and_tenant_scoped() -> None:
    client, platform_access = platform_token("package-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-A", owner_email="package-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PKG-B", owner_email="package-owner-b@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    accountant = add_staff(gym, email="package-accountant@example.test", role_key="ACCOUNTANT")
    trainer = add_staff(gym, email="package-trainer@example.test", role_key="TRAINER")
    receptionist = add_staff(gym, email="package-reception@example.test", role_key="RECEPTIONIST")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    receptionist_headers = auth_headers(staff_token(receptionist))

    owner_codes = _codes(gym, "OWNER")
    manager_codes = _codes(gym, "MANAGER")
    assert {"pt.schedule.view", "pt.schedule.manage", "pt.sessions.adjust"} <= owner_codes
    assert {"pt.schedule.view", "pt.schedule.manage", "pt.sessions.adjust"} <= manager_codes
    assert "pt.schedule.manage" not in _codes(gym, "ACCOUNTANT")
    assert "pt.sessions.adjust" not in _codes(gym, "ACCOUNTANT")
    assert "pt.schedule.view" in _codes(gym, "ACCOUNTANT")
    assert {"pt.schedule.view", "pt.schedule.manage"} <= _codes(gym, "RECEPTIONIST")
    assert "pt.sessions.adjust" not in _codes(gym, "RECEPTIONIST")
    assert {"pt.schedule.view", "pt.schedule.manage"} <= _codes(gym, "TRAINER")
    assert "pt.sessions.adjust" not in _codes(gym, "TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        custom = Role.objects.create(
            gym=gym,
            key="PTDESK",
            name="PT desk",
            created_by_membership_id=owner.id,
        )
        template = SystemRoleTemplate.objects.get(key="OWNER")
        version_before = template.version
    schedule = importlib.import_module(
        "apps.tenants.migrations.0026_pt_schedule_permission_catalog"
    )
    schedule.grant_pt_schedule_permissions(apps, None)
    assert SystemRoleTemplate.objects.get(key="OWNER").version == version_before
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not RolePermission.objects.filter(role=custom).exists()

    assert resolve("/api/v1/member-service-packages/").url_name == "member-service-package-list"

    denied = api.get("/api/v1/service-packages/", **accountant_headers)
    assert denied.status_code == 200
    blocked = api.post("/api/v1/service-packages/", _body(), format="json", **accountant_headers)
    assert blocked.status_code == 403

    rejected_class = api.post(
        "/api/v1/service-packages/",
        _body(type="CLASS", code="CLASS_1", name="Morning yoga"),
        format="json",
        **owner_headers,
    )
    assert rejected_class.status_code == 400
    assert "class_type_id" in rejected_class.json()["error"]["fields"]
    owned = api.post(
        "/api/v1/service-packages/",
        _body(
            name="  Ten   sessions ",
            price="10.50",
            gym_id=str(gym.id),
            currency="USD",
            class_type_id=str(uuid4()),
            is_active=False,
        ),
        format="json",
        **owner_headers,
    )
    assert owned.status_code == 400
    created = api.post(
        "/api/v1/service-packages/",
        _body(name="  Ten   sessions ", price="10.50"),
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    body = created.json()["data"]
    assert set(body) == PACKAGE_FIELDS
    assert body["name"] == "Ten sessions"
    assert body["price"] == "10.50"
    assert body["currency"] == gym.currency
    assert body["class_type_id"] is None
    assert body["is_active"] is True
    assert body["type"] == "PT"
    duplicate_name = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_11", name="ten sessions"),
        format="json",
        **owner_headers,
    )
    assert duplicate_name.status_code == 409
    assert duplicate_name.json()["error"]["code"] == "SERVICE_PACKAGE_CONFLICT"
    duplicate_code = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_10", name="Another pack"),
        format="json",
        **owner_headers,
    )
    assert duplicate_code.status_code == 409
    second = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_20", name="Twenty sessions", price="0.00"),
        format="json",
        **trainer_headers,
    )
    assert second.status_code == 201, second.json()
    reception = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_30", name="Thirty sessions"),
        format="json",
        **receptionist_headers,
    )
    assert reception.status_code == 201, reception.json()

    with CaptureQueriesContext(connection) as captured:
        listed = api.get("/api/v1/service-packages/?limit=1", **owner_headers)
    assert listed.status_code == 200
    page = listed.json()
    assert page["meta"]["has_more"] is True
    assert [row["code"] for row in page["data"]] == ["PT_10"]
    package_queries = [
        query
        for query in captured.captured_queries
        if "trainers_service_package" in query["sql"].lower()
    ]
    assert len(package_queries) == 1
    assert "limit" in package_queries[0]["sql"].lower()
    followed = api.get(
        "/api/v1/service-packages/",
        {"limit": 1, "after": page["meta"]["next_after"]},
        **owner_headers,
    )
    assert followed.status_code == 200
    assert [row["code"] for row in followed.json()["data"]] == ["PT_20"]
    foreign_cursor = api.get(
        "/api/v1/service-packages/",
        {"after": page["meta"]["next_after"]},
        **accountant_headers,
    )
    assert foreign_cursor.status_code == 400
    classes = api.get("/api/v1/service-packages/?type=CLASS", **owner_headers)
    assert classes.json()["data"] == []
    inactive = api.get("/api/v1/service-packages/?is_active=false", **owner_headers)
    assert inactive.json()["data"] == []
    active = api.get("/api/v1/service-packages/?type=PT&is_active=true", **owner_headers)
    assert {row["code"] for row in active.json()["data"]} == {"PT_10", "PT_20", "PT_30"}
    unknown = api.get(f"/api/v1/service-packages/?gym_id={gym.id}", **owner_headers)
    assert unknown.status_code == 400

    other_created = api.post(
        "/api/v1/service-packages/",
        _body(code="PT_10", name="Ten sessions"),
        format="json",
        **other_headers,
    )
    assert other_created.status_code == 201, other_created.json()
    other_list = api.get("/api/v1/service-packages/", **other_headers)
    assert [row["code"] for row in other_list.json()["data"]] == ["PT_10"]
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert ServicePackage.objects.filter(code="PT_20").count() == 0
        assert ServicePackage.objects.filter(code="PT_10").count() == 1

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        audit = AuditLog.objects.filter(target_id=body["id"], action="SERVICE_PACKAGE_CREATED")
        assert audit.count() == 1
        assert audit.get().new_values["price"] == "10.50"
        assert audit.get().module == "trainers"

    row_id = body["id"]
    with pytest.raises((InternalError, ProgrammingError), match="service_package_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ServicePackage.objects.filter(id=row_id).update(name="Changed")
    with pytest.raises((InternalError, ProgrammingError), match="service_package_guard"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ServicePackage.objects.filter(id=row_id).delete()

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'trainers_service_package', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_service_package', 'INSERT'),
              has_table_privilege('gym_runtime', 'trainers_service_package', 'UPDATE'),
              has_table_privilege('gym_runtime', 'trainers_service_package', 'DELETE'),
              has_function_privilege('public', 'app_guard_service_package()', 'EXECUTE'),
              has_function_privilege('gym_runtime', 'app_guard_service_package()', 'EXECUTE')
            """
        )
        assert cursor.fetchone() == (True, True, False, False, False, True)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="pt").update(enabled=False)
    assert api.get("/api/v1/service-packages/", **owner_headers).status_code == 403
    assert (
        api.post(
            "/api/v1/service-packages/", _body(code="PT_40"), format="json", **owner_headers
        ).status_code
        == 403
    )


@override_settings(**jwt_settings())
def test_service_package_code_race_is_deterministic() -> None:
    client, platform_access = platform_token("package-race-platform@example.test")
    payload = provision(
        client, platform_access, code="PKG-R", owner_email="package-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    barrier = Barrier(2, timeout=20)

    def create_same() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                gym_row = Gym.objects.get(id=gym.id)
            try:
                create_service_package(
                    gym=gym_row,
                    actor=actor,
                    package_type="PT",
                    code="PT_RACE",
                    name="Race pack",
                    session_count=4,
                    validity_value=1,
                    validity_unit="MONTH",
                    price=Decimal("20.00"),
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            except ServicePackageConflict:
                return "conflict"
            return "created"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _index: create_same(), (1, 2)))
    assert outcomes == ["conflict", "created"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert ServicePackage.objects.filter(code="PT_RACE").count() == 1
