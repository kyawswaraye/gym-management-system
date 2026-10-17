"""Query budget and PostgreSQL plans for peak check-in reads.

This is a deterministic PostgreSQL baseline, not a production load test.
Same-gym and other-gym volumes are fixed. Plans are forced away from a
sequential scan so the tenant indexes are visible on this data size.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import CheckIn, CheckInChallenge, CheckInDevice, Member
from apps.members.normalization import canonical_address, normalize_full_name
from apps.tenants.models import Gym, GymMembership
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
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

PAGE_LIMIT = 2
LARGE_ROWS = 300
# Measured open-list requests are 11 statements. History requests are 12.
# The ceiling leaves room for one extra auth or RLS statement.
CHECK_IN_QUERY_BUDGET = 14
OPEN_INDEX = "checkin_open_member_idx"
HISTORY_INDEX = "checkin_gym_member_hist_idx"
CHALLENGE_INDEX = "checkin_chal_hash_uniq"
DEVICE_INDEX = "checkin_dev_selector_uniq"
SNAPSHOT = {
    "eligible": True,
    "backdated": False,
    "membership_id": "00000000-0000-4000-8000-000000000001",
    "membership_status": "ACTIVE",
    "plan_code": "PLAN",
    "plan_name": "Plan",
    "start_date": "2024-01-01",
    "expiry_date": "2024-12-31",
    "evaluated_on": "2024-06-01",
}


def _require_postgresql() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Check-in query plans require PostgreSQL")


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _member(gym: Gym, actor_id: UUID, *, number: str, first_name: str) -> Member:
    now = timezone.now()
    return Member(
        gym=gym,
        member_number=number,
        first_name=first_name,
        last_name="Visit",
        normalized_full_name=normalize_full_name(first_name, "Visit"),
        phone=None,
        email=None,
        gender=None,
        join_date=now.date(),
        address=canonical_address({}),
        emergency_contact_name="",
        emergency_contact_phone="",
        notes="",
        authz_version=1,
        status=Member.Status.ACTIVE,
        archived_at=None,
        created_by_membership_id=actor_id,
        updated_by_membership_id=actor_id,
        created_at=now,
        updated_at=now,
    )


def _insert_members(gym: Gym, rows: list[Member]) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.bulk_create(rows, batch_size=100)


def _visit(
    gym: Gym,
    member: Member,
    actor_id: UUID,
    *,
    check_in_at: object,
    open_visit: bool,
) -> CheckIn:
    return CheckIn(
        gym=gym,
        member=member,
        membership=None,
        check_in_at=check_in_at,
        check_out_at=None if open_visit else check_in_at + timedelta(minutes=1),
        method=CheckIn.Method.MANUAL,
        recorded_by_membership_id=actor_id,
        device_id=None,
        source_metadata=None,
        idempotency_key=None,
        note="",
        eligibility_snapshot=SNAPSHOT,
        created_by_membership_id=actor_id,
    )


def _insert_visits(gym: Gym, rows: list[CheckIn]) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        CheckIn.objects.bulk_create(rows, batch_size=100)


def _capture(
    api: APIClient, headers: dict[str, str], path: str, params: dict[str, object]
) -> tuple[Any, list[dict[str, Any]]]:
    with CaptureQueriesContext(connection) as captured:
        response = api.get(path, data=params, **headers)
    return response, list(captured)


def _measure(
    api: APIClient, headers: dict[str, str], path: str, params: dict[str, object]
) -> tuple[Any, list[dict[str, Any]]]:
    warmed, _queries = _capture(api, headers, path, params)
    assert warmed.status_code == 200, warmed.content
    response, queries = _capture(api, headers, path, params)
    assert response.status_code == 200, response.content
    return response, queries


def _check_in_selects(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for query in queries:
        sql = " ".join(str(query.get("sql") or "").lower().split())
        if sql.startswith("select") and "members_check_in" in sql and " order by " in sql:
            selected.append(query)
    return selected


def _contains_uuid(text: str, value: UUID) -> bool:
    return value.hex in text.replace("-", "").lower()


def _assert_bounded_list(queries: list[dict[str, Any]], gym_id: UUID) -> str:
    assert len(queries) <= CHECK_IN_QUERY_BUDGET, [query.get("sql") for query in queries]
    selects = _check_in_selects(queries)
    assert len(selects) == 1, [query.get("sql") for query in queries]
    sql = str(selects[0]["sql"])
    lowered = " ".join(sql.lower().split())
    assert f"limit {PAGE_LIMIT + 1}" in lowered
    assert _contains_uuid(sql, gym_id), sql
    return sql


def _plan_nodes(plan: object) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "Plan" in node and "Node Type" not in node:
                walk(node["Plan"])
                return
            if "Node Type" in node:
                nodes.append(node)
            walk(node.get("Plans"))
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(plan)
    return nodes


def _explain(sql: str, gym_id: UUID, *, runtime_role: bool) -> object:
    if "%s" in sql:
        raise AssertionError(f"check-in SQL still has unbound placeholders: {sql}")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")
        if not runtime_role:
            cursor.execute("SET LOCAL row_security = off")
        else:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("EXPLAIN (FORMAT JSON) " + sql)
        row = cursor.fetchone()
    assert row is not None
    plan = row[0]
    if isinstance(plan, str):
        return json.loads(plan)
    return plan


def _assert_index(
    plan: object,
    *,
    index_name: str,
    gym_id: UUID | None,
    limit: bool,
) -> None:
    nodes = _plan_nodes(plan)
    rendered = json.dumps(plan)
    names = {node.get("Index Name") for node in nodes if node.get("Index Name")}
    types = {str(node.get("Node Type")) for node in nodes}
    assert index_name in names, rendered
    assert "Seq Scan" not in types, rendered
    if limit:
        assert "Limit" in types, rendered
    if index_name == OPEN_INDEX:
        sorts = [node for node in nodes if node.get("Node Type") in {"Sort", "Incremental Sort"}]
        assert sorts, rendered
        assert float(sorts[0].get("Plan Rows", LARGE_ROWS)) < LARGE_ROWS, rendered
    if gym_id is not None:
        assert _contains_uuid(rendered, gym_id) or "app_current_gym_id" in rendered, rendered


def _ids(response: Any) -> list[str]:
    return [str(row["id"]) for row in response.json()["data"]]


@override_settings(**jwt_settings())
def test_check_in_reads_keep_a_bounded_plan_at_peak_volume() -> None:
    _require_postgresql()
    client, platform_access = platform_token("check-in-plan-platform@example.test")
    payload = provision(
        client, platform_access, code="CPLAN-A", owner_email="check-in-plan-owner-a@example.test"
    )
    other_payload = provision(
        client,
        platform_access,
        code="CPLAN-B",
        owner_email="check-in-plan-owner-b@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    api = _client()
    subject = _member(gym, owner.id, number="P000001", first_name="Ada")
    neighbor = _member(gym, owner.id, number="P000002", first_name="Bea")
    outsider = _member(other_gym, other_owner.id, number="P000001", first_name="Ada")
    _insert_members(gym, [subject, neighbor])
    _insert_members(other_gym, [outsider])
    subject = Member.objects.get(gym=gym, member_number="P000001")
    outsider = Member.objects.get(gym=other_gym, member_number="P000001")
    base = timezone.now() - timedelta(hours=3)
    own_open = [
        _visit(
            gym,
            subject,
            owner.id,
            check_in_at=base - timedelta(minutes=offset),
            open_visit=True,
        )
        for offset in range(4)
    ]
    foreign_open = [
        _visit(
            other_gym,
            outsider,
            other_owner.id,
            check_in_at=base - timedelta(minutes=offset),
            open_visit=True,
        )
        for offset in range(4)
    ]
    _insert_visits(gym, own_open)
    _insert_visits(other_gym, foreign_open)
    history_path = f"/api/v1/members/{subject.id}/check-ins/"
    open_params = {"open": "true", "limit": PAGE_LIMIT}
    history_params = {"limit": PAGE_LIMIT}

    open_first = api.get("/api/v1/check-ins/", data=open_params, **headers)
    assert open_first.status_code == 200, open_first.content
    assert open_first.json()["meta"]["has_more"] is True
    open_cursor = open_first.json()["meta"]["next_after"]
    assert isinstance(open_cursor, str) and open_cursor
    open_second = api.get(
        "/api/v1/check-ins/",
        data={**open_params, "after": open_cursor},
        **headers,
    )
    assert open_second.status_code == 200, open_second.content
    history_first = api.get(history_path, data=history_params, **headers)
    assert history_first.status_code == 200, history_first.content
    history_cursor = history_first.json()["meta"]["next_after"]
    assert isinstance(history_cursor, str) and history_cursor
    history_second = api.get(
        history_path,
        data={**history_params, "after": history_cursor},
        **headers,
    )
    assert history_second.status_code == 200, history_second.content
    expected_open = [_ids(open_first), _ids(open_second)]
    expected_history = [_ids(history_first), _ids(history_second)]
    assert expected_open[0] != expected_open[1]
    assert expected_open == expected_history
    assert len(expected_open[0]) == len(expected_open[1]) == PAGE_LIMIT
    foreign_ids = {str(row.id) for row in foreign_open}
    rendered_own = open_second.content.decode() + history_second.content.decode()
    assert foreign_ids.isdisjoint(set(expected_open[0] + expected_open[1]))
    assert str(outsider.id) not in rendered_own
    stolen = api.get(
        "/api/v1/check-ins/",
        data={**open_params, "after": open_cursor},
        **other_headers,
    )
    assert stolen.status_code == 400
    assert "after" in stolen.json()["error"]["fields"]

    def _counts(open_after: str, history_after: str) -> dict[str, tuple[Any, list[dict[str, Any]]]]:
        return {
            "open": _measure(api, headers, "/api/v1/check-ins/", open_params),
            "open_page": _measure(
                api, headers, "/api/v1/check-ins/", {**open_params, "after": open_after}
            ),
            "history": _measure(api, headers, history_path, history_params),
            "history_page": _measure(
                api, headers, history_path, {**history_params, "after": history_after}
            ),
        }

    small = _counts(open_cursor, history_cursor)
    oldest = base - timedelta(minutes=3)
    _insert_visits(
        gym,
        [
            _visit(
                gym,
                subject if index % 2 == 0 else neighbor,
                owner.id,
                check_in_at=oldest - timedelta(minutes=index + 1),
                open_visit=False,
            )
            for index in range(LARGE_ROWS)
        ],
    )
    _insert_visits(
        other_gym,
        [
            _visit(
                other_gym,
                outsider,
                other_owner.id,
                check_in_at=oldest - timedelta(minutes=index + 1),
                open_visit=False,
            )
            for index in range(LARGE_ROWS)
        ],
    )
    issued = timezone.now() - timedelta(minutes=10)
    target_hash = hashlib.sha256(b"check-in-plan-target").hexdigest()
    challenges = []
    for index in range(LARGE_ROWS):
        for owner_id, target_gym in ((owner.id, gym), (other_owner.id, other_gym)):
            digest = (
                target_hash
                if index == 0 and target_gym == gym
                else hashlib.sha256(f"{target_gym.id}:{index}".encode()).hexdigest()
            )
            challenges.append(
                CheckInChallenge(
                    gym=target_gym,
                    challenge_hash=digest,
                    display_location="Lobby",
                    issued_at=issued,
                    expires_at=issued + timedelta(seconds=120),
                    consumed_at=None,
                    use_count=0,
                    max_uses=1,
                    issued_by_membership_id=owner_id,
                    display_device_id=None,
                    status=CheckInChallenge.Status.ACTIVE,
                    created_by_membership_id=owner_id,
                )
            )
    password_hash = make_password("not-a-device-secret", hasher="pbkdf2_sha256")
    devices = []
    target_selector = "deskselector000001"
    for index in range(LARGE_ROWS):
        for actor_id, target_gym in ((owner.id, gym), (other_owner.id, other_gym)):
            selector = (
                target_selector
                if index == 0 and target_gym == gym
                else f"desk{target_gym.id.hex[:8]}{index:05d}"
            )
            devices.append(
                CheckInDevice(
                    gym=target_gym,
                    label="Desk",
                    location="Lobby",
                    kind=CheckInDevice.Kind.DISPLAY,
                    status=CheckInDevice.Status.ACTIVE,
                    credential_selector=selector,
                    credential_hash=password_hash,
                    credential_version=1,
                    created_by_membership_id=actor_id,
                )
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        CheckInChallenge.objects.bulk_create(
            [row for row in challenges if row.gym_id == gym.id], batch_size=100
        )
        CheckInDevice.objects.bulk_create(
            [row for row in devices if row.gym_id == gym.id], batch_size=100
        )
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        CheckInChallenge.objects.bulk_create(
            [row for row in challenges if row.gym_id == other_gym.id], batch_size=100
        )
        CheckInDevice.objects.bulk_create(
            [row for row in devices if row.gym_id == other_gym.id], batch_size=100
        )
    with connection.cursor() as db_cursor:
        db_cursor.execute("ANALYZE members_check_in")
        db_cursor.execute("ANALYZE members_check_in_challenge")
        db_cursor.execute("ANALYZE members_check_in_device")

    large = _counts(open_cursor, history_cursor)
    assert _ids(large["open"][0]) == expected_open[0]
    assert _ids(large["open_page"][0]) == expected_open[1]
    assert _ids(large["history"][0]) == expected_history[0]
    assert _ids(large["history_page"][0]) == expected_history[1]
    assert str(outsider.id) not in large["open_page"][0].content.decode()
    index_for = {
        "open": OPEN_INDEX,
        "open_page": OPEN_INDEX,
        "history": HISTORY_INDEX,
        "history_page": HISTORY_INDEX,
    }
    for name, (_response, queries) in small.items():
        _large_response, large_queries = large[name]
        assert len(queries) == len(large_queries), (name, len(queries), len(large_queries))
        sql = _assert_bounded_list(large_queries, gym.id)
        assert not _contains_uuid(sql, other_gym.id), sql
        _assert_index(
            _explain(sql, gym.id, runtime_role=True),
            index_name=index_for[name],
            gym_id=gym.id,
            limit=True,
        )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with CaptureQueriesContext(connection) as captured:
            found = CheckInChallenge.objects.filter(challenge_hash=target_hash).first()
    assert found is not None and found.gym_id == gym.id
    challenge_sql = next(
        str(query["sql"])
        for query in captured
        if "members_check_in_challenge" in str(query["sql"]).lower()
        and "challenge_hash" in str(query["sql"]).lower()
    )
    challenge_plan = _explain(challenge_sql, gym.id, runtime_role=True)
    _assert_index(
        challenge_plan,
        index_name=CHALLENGE_INDEX,
        gym_id=None,
        limit=True,
    )
    assert "app_current_gym_id" in json.dumps(challenge_plan)
    device_sql = (
        "SELECT d.id FROM public.members_check_in_device d "
        "JOIN public.tenants_gym g ON g.id = d.gym_id "
        f"WHERE d.credential_selector = '{target_selector}' "
        "AND g.status = 'ACTIVE' "
        "AND g.subscription_status IN ('TRIAL', 'ACTIVE')"
    )
    _assert_index(
        _explain(device_sql, gym.id, runtime_role=False),
        index_name=DEVICE_INDEX,
        gym_id=None,
        limit=False,
    )
