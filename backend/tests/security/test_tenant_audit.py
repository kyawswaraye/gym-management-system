from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog, Outcome, PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import Gym, GymMembership, GymMembershipRole, Permission, Role
from apps.users.models import User
from tests.security.test_tenant_gym_settings import (
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

HIGH_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
LOW_ID = UUID("00000000-0000-4000-8000-000000000001")
MODULE = "auditread"
RAW_PASSWORD = "raw-password-value"
RAW_SECRET = "raw-secret-value"
RAW_CREDENTIAL = "raw-credential-value"
RAW_TOKEN = "raw-token-value"
RAW_LIST_TOKEN = "raw-list-token"
RAW_PLATFORM_PASSWORD = "platform-raw-password"
LEGACY_IP = "198.51.100.23"
LEGACY_AGENT = "LegacyAuditBrowser/9"
ITEM_FIELDS = {
    "id",
    "actor_membership_id",
    "impersonator_membership_id",
    "action",
    "module",
    "target_type",
    "target_id",
    "old_values",
    "new_values",
    "request_id",
    "outcome",
    "occurred_at",
}


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 1, hour, minute, tzinfo=UTC)


def _bootstrap(prefix: str) -> dict[str, object]:
    client, platform_access = platform_token(f"{prefix}-platform@example.test")
    gym_a_payload = provision(
        client,
        platform_access,
        code=f"{prefix}A",
        owner_email=f"{prefix}-owner-a@example.test",
    )
    gym_b_payload = provision(
        client,
        platform_access,
        code=f"{prefix}B",
        owner_email=f"{prefix}-owner-b@example.test",
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    other_gym_owner = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym_a, email=f"{prefix}-manager@example.test", role_key="MANAGER")
    accountant = add_staff(gym_a, email=f"{prefix}-accountant@example.test", role_key="ACCOUNTANT")
    receptionist = add_staff(
        gym_a, email=f"{prefix}-reception@example.test", role_key="RECEPTIONIST"
    )
    second_owner = _second_owner(gym_a, email=f"{prefix}-owner-2@example.test")
    return {
        "client": client,
        "platform_access": platform_access,
        "admin": User.objects.get(normalized_email=f"{prefix}-platform@example.test".lower()),
        "gym_a": gym_a,
        "gym_b": gym_b,
        "owner": owner,
        "second_owner": second_owner,
        "other_gym_owner": other_gym_owner,
        "manager": manager,
        "accountant": accountant,
        "receptionist": receptionist,
    }


def _second_owner(gym: Gym, email: str) -> GymMembership:
    user = User.objects.create_user(email, "correct horse battery staple")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=membership,
            role=Role.objects.get(gym=gym, key="OWNER"),
        )
    membership.user = user
    return membership


def _insert(
    gym: Gym,
    *,
    action: str,
    occurred_at: datetime,
    actor_membership_id: UUID | None,
    target_id: UUID | None = None,
    outcome: str = Outcome.SUCCESS,
    actor_user: User | None = None,
    impersonator_membership_id: UUID | None = None,
    old_values: dict[str, object] | None = None,
    new_values: dict[str, object] | None = None,
    audit_id: UUID | None = None,
    module: str = MODULE,
) -> AuditLog:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return AuditLog.objects.create(
            id=audit_id or uuid4(),
            gym=gym,
            actor_user=actor_user,
            actor_membership_id=actor_membership_id,
            impersonator_membership_id=impersonator_membership_id,
            action=action,
            module=module,
            target_type="Role",
            target_id=target_id or uuid4(),
            old_values=old_values or {},
            new_values=new_values or {"status": "ACTIVE"},
            request_id=f"req-{action.lower()}",
            ip_address=LEGACY_IP,
            user_agent=LEGACY_AGENT,
            outcome=outcome,
            occurred_at=occurred_at,
        )


def _legacy_values() -> dict[str, object]:
    return {
        "password": RAW_PASSWORD,
        "nested": {
            "client_secret": RAW_SECRET,
            "apiCredential": RAW_CREDENTIAL,
            "refresh_token": RAW_TOKEN,
            "status": "ACTIVE",
        },
        "history": [{"access_token": RAW_LIST_TOKEN}],
        "note": "password reset completed",
    }


def _headers(membership: GymMembership) -> dict[str, str]:
    return auth_headers(staff_token(membership))


def _list(
    client: APIClient,
    headers: dict[str, str],
    **query: object,
) -> dict[str, object]:
    response = client.get("/api/v1/audit-logs/", query, **headers)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["success"] is True
    return body


@override_settings(**jwt_settings())
def test_owner_sees_the_gym_and_conditional_roles_see_only_themselves() -> None:
    world = _bootstrap("AU1")
    client = world["client"]
    gym_a = world["gym_a"]
    gym_b = world["gym_b"]
    owner = world["owner"]
    second_owner = world["second_owner"]
    other_owner = world["other_gym_owner"]
    manager = world["manager"]
    accountant = world["accountant"]
    admin = world["admin"]
    assert isinstance(client, APIClient)
    assert isinstance(gym_a, Gym)
    assert isinstance(gym_b, Gym)
    assert isinstance(owner, GymMembership)
    assert isinstance(second_owner, GymMembership)
    assert isinstance(other_owner, GymMembership)
    assert isinstance(manager, GymMembership)
    assert isinstance(accountant, GymMembership)
    assert isinstance(admin, User)

    owner_target = uuid4()
    foreign_target = uuid4()
    _insert(
        gym_a,
        action="OWNER_LATE",
        occurred_at=_at(12),
        actor_membership_id=owner.id,
        actor_user=owner.user,
        target_id=owner_target,
    )
    _insert(
        gym_a,
        action="TIE_HIGH",
        occurred_at=_at(11),
        actor_membership_id=owner.id,
        actor_user=owner.user,
        audit_id=HIGH_ID,
    )
    _insert(
        gym_a,
        action="TIE_LOW",
        occurred_at=_at(11),
        actor_membership_id=owner.id,
        actor_user=owner.user,
        audit_id=LOW_ID,
    )
    _insert(
        gym_a,
        action="MANAGER_ACT",
        occurred_at=_at(10),
        actor_membership_id=manager.id,
        actor_user=manager.user,
        old_values=_legacy_values(),
        new_values={"credential": RAW_CREDENTIAL},
    )
    _insert(
        gym_a,
        action="IMPERSONATED",
        occurred_at=_at(9, 30),
        actor_membership_id=manager.id,
        actor_user=manager.user,
        impersonator_membership_id=owner.id,
    )
    _insert(
        gym_a,
        action="ACCOUNTANT_ACT",
        occurred_at=_at(9),
        actor_membership_id=accountant.id,
        actor_user=accountant.user,
    )
    _insert(
        gym_a,
        action="PLATFORM_TENANT",
        occurred_at=_at(8),
        actor_membership_id=None,
        actor_user=admin,
        outcome=Outcome.DENIED,
        old_values={"password": RAW_PLATFORM_PASSWORD},
        new_values={"secret": RAW_SECRET},
    )
    _insert(
        gym_b,
        action="GYM_B_ONLY",
        occurred_at=_at(13),
        actor_membership_id=other_owner.id,
        actor_user=other_owner.user,
        target_id=foreign_target,
    )
    PlatformAuditLog.objects.create(
        actor_user=admin,
        action="PLATFORM_ONLY_PROBE",
        module="platform",
        target_gym=gym_a,
        target_type="Gym",
        target_id=gym_a.id,
        old_values={"password": RAW_PLATFORM_PASSWORD},
        new_values={"secret": RAW_SECRET},
        request_id="req_platform_probe",
        ip_address=LEGACY_IP,
        user_agent=LEGACY_AGENT,
        outcome=Outcome.SUCCESS,
        occurred_at=_at(14),
    )

    owner_headers = _headers(owner)
    before = AuditLog.objects.count()
    owner_body = _list(client, owner_headers, module=MODULE)
    assert AuditLog.objects.count() == before
    owner_actions = [row["action"] for row in owner_body["data"]]
    assert owner_actions == [
        "OWNER_LATE",
        "TIE_HIGH",
        "TIE_LOW",
        "MANAGER_ACT",
        "IMPERSONATED",
        "ACCOUNTANT_ACT",
        "PLATFORM_TENANT",
    ]
    assert owner_body["meta"] == {"has_more": False, "next_after": None, "scope": "GYM"}
    sample = owner_body["data"][3]
    assert set(sample) == ITEM_FIELDS
    assert sample["actor_membership_id"] == str(manager.id)
    assert sample["old_values"]["password"] == "[REDACTED]"
    assert sample["old_values"]["nested"]["client_secret"] == "[REDACTED]"
    assert sample["old_values"]["nested"]["status"] == "ACTIVE"
    assert sample["old_values"]["note"] == "password reset completed"
    assert sample["new_values"] == {"credential": "[REDACTED]"}
    rendered = json.dumps(owner_body)
    for secret in (
        RAW_PASSWORD,
        RAW_SECRET,
        RAW_CREDENTIAL,
        RAW_TOKEN,
        RAW_LIST_TOKEN,
        RAW_PLATFORM_PASSWORD,
        LEGACY_IP,
        LEGACY_AGENT,
        str(gym_a.id),
        str(owner.user_id),
        owner.user.email,
        owner.user.normalized_email,
        admin.email,
        admin.normalized_email,
        "PLATFORM_ONLY_PROBE",
        "GYM_B_ONLY",
        "GYM_PROVISIONED",
    ):
        assert secret not in rendered

    second_body = _list(client, _headers(second_owner), module=MODULE)
    assert [row["action"] for row in second_body["data"]] == owner_actions
    assert second_body["meta"]["scope"] == "GYM"

    manager_body = _list(client, _headers(manager), module=MODULE)
    assert [row["action"] for row in manager_body["data"]] == ["MANAGER_ACT", "IMPERSONATED"]
    assert manager_body["meta"]["scope"] == "SELF"
    assert manager_body["data"][0]["old_values"]["password"] == "[REDACTED]"
    assert manager_body["data"][1]["impersonator_membership_id"] == str(owner.id)
    manager_rendered = json.dumps(manager_body)
    assert "OWNER_LATE" not in manager_rendered
    assert "ACCOUNTANT_ACT" not in manager_rendered
    assert "PLATFORM_TENANT" not in manager_rendered
    assert "GYM_B_ONLY" not in manager_rendered
    assert RAW_PASSWORD not in manager_rendered
    assert owner.user.email not in manager_rendered
    assert owner.user.normalized_email not in manager_rendered

    accountant_body = _list(client, _headers(accountant), module=MODULE)
    assert [row["action"] for row in accountant_body["data"]] == ["ACCOUNTANT_ACT"]
    assert accountant_body["meta"]["scope"] == "SELF"
    assert accountant_body["data"][0]["actor_membership_id"] == str(accountant.id)

    other_body = _list(client, _headers(other_owner), module=MODULE)
    assert [row["action"] for row in other_body["data"]] == ["GYM_B_ONLY"]
    assert other_body["meta"]["scope"] == "GYM"
    assert "OWNER_LATE" not in json.dumps(other_body)

    hidden_target = _list(client, owner_headers, target_id=str(foreign_target))
    assert hidden_target["data"] == []
    assert hidden_target["meta"]["scope"] == "GYM"
    own_target = _list(client, owner_headers, target_id=str(owner_target))
    assert [row["action"] for row in own_target["data"]] == ["OWNER_LATE"]
    outside_self = _list(client, _headers(manager), target_id=str(owner_target))
    assert outside_self["data"] == []
    assert outside_self["meta"]["scope"] == "SELF"
    assert "OWNER_LATE" not in json.dumps(outside_self)


@override_settings(**jwt_settings())
def test_cursors_are_bound_and_invalid_queries_fail_closed() -> None:
    world = _bootstrap("AU2")
    client = world["client"]
    gym_a = world["gym_a"]
    owner = world["owner"]
    second_owner = world["second_owner"]
    other_owner = world["other_gym_owner"]
    manager = world["manager"]
    assert isinstance(client, APIClient)
    assert isinstance(gym_a, Gym)
    assert isinstance(owner, GymMembership)
    assert isinstance(second_owner, GymMembership)
    assert isinstance(other_owner, GymMembership)
    assert isinstance(manager, GymMembership)
    for action, stamp, audit_id in (
        ("OWNER_LATE", _at(12), None),
        ("TIE_HIGH", _at(11), HIGH_ID),
        ("TIE_LOW", _at(11), LOW_ID),
    ):
        _insert(
            gym_a,
            action=action,
            occurred_at=stamp,
            actor_membership_id=owner.id,
            actor_user=owner.user,
            audit_id=audit_id,
        )
    _insert(
        gym_a,
        action="MANAGER_ACT",
        occurred_at=_at(10),
        actor_membership_id=manager.id,
        actor_user=manager.user,
    )

    owner_headers = _headers(owner)
    first = _list(client, owner_headers, module=MODULE, limit=1)
    assert [row["action"] for row in first["data"]] == ["OWNER_LATE"]
    assert first["meta"]["has_more"] is True
    cursor = first["meta"]["next_after"]
    assert isinstance(cursor, str) and cursor
    second = _list(client, owner_headers, module=MODULE, limit=1, after=cursor)
    assert [row["action"] for row in second["data"]] == ["TIE_HIGH"]
    third = _list(
        client,
        _headers(second_owner),
        module=MODULE,
        limit=1,
        after=second["meta"]["next_after"],
    )
    assert [row["action"] for row in third["data"]] == ["TIE_LOW"]
    assert third["meta"]["scope"] == "GYM"

    reused = client.get(
        "/api/v1/audit-logs/",
        {"module": "roles", "limit": 1, "after": cursor},
        **owner_headers,
    )
    changed_outcome = client.get(
        "/api/v1/audit-logs/",
        {"module": MODULE, "outcome": "SUCCESS", "limit": 1, "after": cursor},
        **owner_headers,
    )
    manager_reuse = client.get(
        "/api/v1/audit-logs/",
        {"module": MODULE, "limit": 1, "after": cursor},
        **_headers(manager),
    )
    other_gym = client.get(
        "/api/v1/audit-logs/",
        {"module": MODULE, "limit": 1, "after": cursor},
        **_headers(other_owner),
    )
    tampered = f"{cursor[:-1]}A" if not cursor.endswith("A") else f"{cursor[:-1]}B"
    forged = client.get(
        "/api/v1/audit-logs/",
        {"module": MODULE, "limit": 1, "after": tampered},
        **owner_headers,
    )
    for response in (reused, changed_outcome, manager_reuse, other_gym, forged):
        assert response.status_code == 400
        assert "Invalid cursor." in response.json()["error"]["fields"]["after"]

    duplicate = client.get("/api/v1/audit-logs/?action=CREATED&action=UPDATED", **owner_headers)
    unknown = client.get(
        "/api/v1/audit-logs/",
        {"gym_id": str(gym_a.id)},
        **owner_headers,
    )
    high_limit = client.get("/api/v1/audit-logs/", {"limit": 101}, **owner_headers)
    bad_outcome = client.get("/api/v1/audit-logs/", {"outcome": "MAYBE"}, **owner_headers)
    assert duplicate.status_code == unknown.status_code == 400
    assert high_limit.status_code == bad_outcome.status_code == 400
    assert duplicate.json()["error"]["fields"]["action"] == ["Duplicate value."]
    assert "gym_id" in unknown.json()["error"]["fields"]


@override_settings(**jwt_settings())
def test_platform_missing_permission_and_stale_auth_are_rejected() -> None:
    world = _bootstrap("AU3")
    client = world["client"]
    gym_a = world["gym_a"]
    owner = world["owner"]
    manager = world["manager"]
    receptionist = world["receptionist"]
    platform_access = world["platform_access"]
    assert isinstance(client, APIClient)
    assert isinstance(gym_a, Gym)
    assert isinstance(owner, GymMembership)
    assert isinstance(manager, GymMembership)
    assert isinstance(receptionist, GymMembership)
    assert isinstance(platform_access, str)
    _insert(
        gym_a,
        action="OWNER_LATE",
        occurred_at=_at(12),
        actor_membership_id=owner.id,
        actor_user=owner.user,
    )

    anonymous = APIClient().get("/api/v1/audit-logs/")
    platform = client.get(
        "/api/v1/audit-logs/",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
    )
    missing = client.get("/api/v1/audit-logs/", **_headers(receptionist))
    assert anonymous.status_code == 403
    assert anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"
    assert platform.status_code == 401
    assert platform.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "PERMISSION_DENIED"
    assert "OWNER_LATE" not in missing.content.decode()

    Permission.objects.filter(code="audit.view").update(is_active=False)
    inactive = client.get("/api/v1/audit-logs/", **_headers(owner))
    assert inactive.status_code == 403
    assert inactive.json()["error"]["code"] == "PERMISSION_DENIED"
    Permission.objects.filter(code="audit.view").update(is_active=True)

    manager_headers = _headers(manager)
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        manager.status = GymMembership.Status.SUSPENDED
        manager.save(update_fields=["status", "updated_at"])
    suspended = client.get("/api/v1/audit-logs/", **manager_headers)
    assert suspended.status_code == 401
    assert suspended.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    owner_headers = _headers(owner)
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        owner.authz_version += 1
        owner.save(update_fields=["authz_version", "updated_at"])
    stale = client.get("/api/v1/audit-logs/", **owner_headers)
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    owner.refresh_from_db()
    refreshed = _list(client, _headers(owner))
    assert refreshed["meta"]["scope"] == "GYM"
    assert any(row["action"] == "OWNER_LATE" for row in refreshed["data"])

    fresh_headers = _headers(owner)
    owner.user.is_active = False
    owner.user.save(update_fields=["is_active", "updated_at"])
    inactive_user = client.get("/api/v1/audit-logs/", **fresh_headers)
    assert inactive_user.status_code == 401
    assert inactive_user.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    owner.user.is_active = True
    owner.user.save(update_fields=["is_active", "updated_at"])
    gym_a.status = Gym.Status.SUSPENDED
    gym_a.save(update_fields=["status", "updated_at"])
    suspended_gym = client.get("/api/v1/audit-logs/", **fresh_headers)
    assert suspended_gym.status_code == 401
    assert suspended_gym.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@override_settings(**jwt_settings())
def test_runtime_audit_stays_append_only_isolated_and_indexed() -> None:
    world = _bootstrap("AU4")
    client = world["client"]
    gym_a = world["gym_a"]
    gym_b = world["gym_b"]
    owner = world["owner"]
    other_owner = world["other_gym_owner"]
    assert isinstance(client, APIClient)
    assert isinstance(gym_a, Gym)
    assert isinstance(gym_b, Gym)
    assert isinstance(owner, GymMembership)
    assert isinstance(other_owner, GymMembership)
    row = _insert(
        gym_a,
        action="OWNER_LATE",
        occurred_at=_at(12),
        actor_membership_id=owner.id,
        actor_user=owner.user,
    )
    foreign = _insert(
        gym_b,
        action="GYM_B_ONLY",
        occurred_at=_at(13),
        actor_membership_id=other_owner.id,
        actor_user=other_owner.user,
    )

    headers = _headers(owner)
    with CaptureQueriesContext(connection) as captured:
        listed = client.get("/api/v1/audit-logs/", {"module": MODULE}, **headers)
    assert listed.status_code == 200
    audit_queries = [
        query for query in captured.captured_queries if "audit_audit_log" in query["sql"]
    ]
    assert len(audit_queries) == 1
    assert len(captured.captured_queries) <= 12
    sql = audit_queries[0]["sql"]
    rendered_query = f"{sql} {audit_queries[0].get('params', '')}"
    assert "ORDER BY" in sql.upper()
    assert "occurred_at" in sql
    assert "DESC" in sql.upper()
    assert "ip_address" not in sql
    assert "user_agent" not in sql
    assert "users_user" not in sql.lower()
    assert gym_a.id.hex in rendered_query
    assert gym_b.id.hex not in rendered_query

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'audit_gym_time_id_idx'
            """
        )
        indexdef = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT count(*) FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'audit_gym_target_time_idx'
            """
        )
        target_index = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT c.relname, t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal
              AND t.tgname IN ('audit_log_append_only', 'platform_audit_log_append_only')
            ORDER BY c.relname, t.tgname
            """
        )
        triggers = cursor.fetchall()
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'audit_audit_log', 'SELECT'),
              has_table_privilege('gym_runtime', 'audit_audit_log', 'INSERT'),
              has_table_privilege('gym_runtime', 'audit_audit_log', 'UPDATE'),
              has_table_privilege('gym_runtime', 'audit_audit_log', 'DELETE'),
              has_table_privilege('gym_runtime', 'audit_platform_audit_log', 'SELECT'),
              has_table_privilege('gym_runtime', 'audit_platform_audit_log', 'INSERT'),
              has_table_privilege('gym_runtime', 'audit_platform_audit_log', 'UPDATE'),
              has_table_privilege('gym_runtime', 'audit_platform_audit_log', 'DELETE')
            """
        )
        privileges = cursor.fetchone()
    assert "gym_id" in indexdef
    assert "occurred_at DESC" in indexdef
    assert "id DESC" in indexdef
    assert target_index == 1
    assert triggers == [
        ("audit_audit_log", "audit_log_append_only"),
        ("audit_platform_audit_log", "platform_audit_log_append_only"),
    ]
    assert privileges == (True, True, False, False, True, True, False, False)

    explain_sql = sql if "%s" not in sql else None
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        if explain_sql is None:
            cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}", audit_queries[0]["params"])
        else:
            cursor.execute(f"EXPLAIN (FORMAT JSON) {explain_sql}")
        plan = json.dumps(cursor.fetchone()[0])
    assert "audit_gym_time_id_idx" in plan

    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            AuditLog.objects.filter(id=row.id).update(action="MUTATED")
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            AuditLog.objects.filter(id=row.id).delete()
    row.refresh_from_db()
    assert row.action == "OWNER_LATE"

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM audit_audit_log WHERE id = %s", [foreign.id])
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM audit_audit_log WHERE id = %s", [row.id])
        assert cursor.fetchone()[0] == 1
    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                """
                INSERT INTO audit_audit_log (
                  id, gym_id, action, module, target_type, target_id, old_values,
                  new_values, request_id, user_agent, outcome, occurred_at,
                  created_at, updated_at
                ) VALUES (
                  gen_random_uuid(), %s, 'FORGED', 'audit', 'Gym', gen_random_uuid(),
                  '{}'::jsonb, '{}'::jsonb, 'req_forged', '', 'SUCCESS', now(), now(), now()
                )
                """,
                [gym_b.id],
            )
