"""Query budget and PostgreSQL plans for the member list.

``member_gym_name_idx`` is a plain btree on ``(gym_id, normalized_full_name)``
and the column uses the database default collation, not ``C`` or a pattern
opclass. The captured plan is a gym-equality index scan, a limit, and an
incremental sort for the ``id`` tie-break. Case-insensitive contains
(``UPPER(column) LIKE '%term%'``) stays a filter, as do status, join dates, and
the name/id keyset. An anchored prefix comparison is not the published search.
The same statement carries a correlated active-assignment badge subquery. That
probe may use the assignment badge index or the active-range exclusion index,
and it does not replace the member name index.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
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

NAME_INDEX = "member_gym_name_idx"
# Measured list requests are 10 statements. The member SELECT includes one
# correlated active-assignment badge subquery. The ceiling allows one extra
# auth or RLS statement. A second member SELECT fails on its own assertion.
MEMBER_LIST_QUERY_BUDGET = 12
PAGE_LIMIT = 2
SEARCH = "ada"
JOINED_FROM = "2024-01-01"
JOINED_TO = "2024-12-31"
STATUS = "ACTIVE"
LARGE_ROWS = 300
EXPECTED_NAME_INDEX = (
    "CREATE INDEX member_gym_name_idx ON public.members_member "
    "USING btree (gym_id, normalized_full_name)"
)


def _require_postgresql() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Member list query plans require PostgreSQL")


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _member(
    gym: Gym,
    actor_id: UUID,
    *,
    number: str,
    first_name: str,
    last_name: str,
    join_date: date,
    status: str = Member.Status.ACTIVE,
) -> Member:
    now = timezone.now()
    return Member(
        gym=gym,
        member_number=number,
        first_name=first_name,
        last_name=last_name,
        normalized_full_name=normalize_full_name(first_name, last_name),
        phone=None,
        email=None,
        gender=None,
        join_date=join_date,
        address=canonical_address({}),
        emergency_contact_name="",
        emergency_contact_phone="",
        notes="",
        authz_version=1,
        status=status,
        archived_at=None,
        created_by_membership_id=actor_id,
        updated_by_membership_id=actor_id,
        created_at=now,
        updated_at=now,
    )


def _insert(gym: Gym, rows: list[Member]) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.bulk_create(rows, batch_size=100)


def _combined(after: str = "") -> dict[str, object]:
    params: dict[str, object] = {
        "status": STATUS,
        "search": SEARCH,
        "joined_from": JOINED_FROM,
        "joined_to": JOINED_TO,
        "is_pt_client": "false",
        "limit": PAGE_LIMIT,
    }
    if after:
        params["after"] = after
    return params


def _capture(
    api: APIClient, headers: dict[str, str], params: dict[str, object]
) -> tuple[Any, list[dict[str, Any]]]:
    with CaptureQueriesContext(connection) as captured:
        response = api.get("/api/v1/members/", data=params, **headers)
    return response, list(captured)


def _measure(
    api: APIClient, headers: dict[str, str], params: dict[str, object]
) -> tuple[Any, list[dict[str, Any]]]:
    warmed, _queries = _capture(api, headers, params)
    assert warmed.status_code == 200, warmed.content
    response, queries = _capture(api, headers, params)
    assert response.status_code == 200, response.content
    return response, queries


def _list_selects(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for query in queries:
        sql = str(query.get("sql") or "")
        lowered = " ".join(sql.lower().split())
        if (
            lowered.startswith("select")
            and " from " in lowered
            and "members_member" in lowered
            and "normalized_full_name" in lowered
            and " order by " in lowered
        ):
            selected.append(query)
    return selected


def _contains_uuid(text: str, value: UUID) -> bool:
    return value.hex in text.replace("-", "").lower()


def _assert_one_bounded_list_query(queries: list[dict[str, Any]], gym_id: UUID) -> str:
    assert len(queries) <= MEMBER_LIST_QUERY_BUDGET, [query.get("sql") for query in queries]
    selects = _list_selects(queries)
    assert len(selects) == 1, [query.get("sql") for query in queries]
    sql = str(selects[0]["sql"])
    lowered = " ".join(sql.lower().split())
    assert " join " not in lowered
    assert f"limit {PAGE_LIMIT + 1}" in lowered
    assert _contains_uuid(sql, gym_id), sql
    return sql


def _plan_nodes(plan: object) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "Plan" in node:
                walk(node["Plan"])
                return
            nodes.append(node)
            walk(node.get("Plans"))
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(plan)
    return nodes


def _explain(sql: str, gym_id: UUID) -> object:
    """EXPLAIN the captured statement under RLS without rebinding parameters.

    ``CaptureQueriesContext`` stores the SQL PostgreSQL executed. LIKE patterns
    keep their ``%`` wildcards in that text. psycopg treats ``%`` as a placeholder
    only when parameters are passed, so the statement is executed unchanged.
    Verbose output keeps the assignment probe's ``member_id`` correlation visible.
    """

    if "%s" in sql:
        raise AssertionError(f"member list SQL still has unbound placeholders: {sql}")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")
        cursor.execute("SET LOCAL enable_sort = off")
        cursor.execute("SET LOCAL enable_incremental_sort = on")
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + sql)
        row = cursor.fetchone()
    assert row is not None
    plan = row[0]
    if isinstance(plan, str):
        return json.loads(plan)
    return plan


def _assert_tenant_name_index(
    plan: object,
    gym_id: UUID,
    *,
    contains_search: bool,
    filtered: bool,
) -> None:
    nodes = _plan_nodes(plan)
    rendered = json.dumps(plan)
    scans = [
        node
        for node in nodes
        if node.get("Node Type") in {"Index Scan", "Index Only Scan"}
        and node.get("Index Name") == NAME_INDEX
        and node.get("Relation Name") == "members_member"
    ]
    member_indexes = {
        node.get("Index Name")
        for node in nodes
        if node.get("Relation Name") == "members_member" and node.get("Index Name")
    }
    assignment_indexes = {
        node.get("Index Name")
        for node in nodes
        if node.get("Relation Name") == "trainers_trainer_assignment" and node.get("Index Name")
    }
    node_types = {str(node.get("Node Type")) for node in nodes}
    assert scans, rendered
    assert member_indexes == {NAME_INDEX}, rendered
    assert assignment_indexes <= {
        "trainer_asg_member_badge_idx",
        "trainer_asg_active_overlap_excl",
    }, rendered
    assert assignment_indexes, rendered
    assignment_detail = " ".join(
        f"{node.get('Index Cond') or ''} {node.get('Filter') or ''} {node.get('Output') or ''}"
        for node in nodes
        if node.get("Relation Name") == "trainers_trainer_assignment"
    )
    assert "member_id" in assignment_detail, rendered
    assert nodes[0].get("Node Type") == "Limit", rendered
    assert "Incremental Sort" in node_types, rendered
    assert "Sort" not in node_types, rendered
    assert "Seq Scan" not in node_types, rendered
    index_cond = " ".join(str(node.get("Index Cond") or "") for node in scans)
    filters = " ".join(str(node.get("Filter") or "") for node in nodes)
    assert "gym_id" in index_cond, rendered
    assert _contains_uuid(index_cond, gym_id), rendered
    assert "~~" not in index_cond and "LIKE" not in index_cond.upper(), rendered
    assert "app_current_gym_id" in rendered, rendered
    if contains_search:
        assert "~~" in filters or "LIKE" in filters.upper(), rendered
        assert "UPPER" in filters.upper(), rendered
        assert "%ada%" in filters.casefold(), rendered
    else:
        assert "~~" not in filters and "LIKE" not in filters.upper(), rendered
    if filtered:
        assert "status" in filters.casefold(), rendered
        assert "join_date" in filters.casefold(), rendered


@override_settings(**jwt_settings())
def test_member_list_query_budget_and_tenant_leading_plan() -> None:
    _require_postgresql()
    client, platform_access = platform_token("member-list-plan-platform@example.test")
    payload = provision(
        client, platform_access, code="MLST-A", owner_email="member-list-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="MLST-B", owner_email="member-list-owner-b@example.test"
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
    window = date(2024, 6, 1)
    seeded = [
        _member(
            gym,
            owner.id,
            number="P000001",
            first_name="Ada",
            last_name="Able",
            join_date=date(2024, 3, 1),
        ),
        _member(
            gym,
            owner.id,
            number="P000002",
            first_name="Ada",
            last_name="Baker",
            join_date=date(2024, 4, 1),
        ),
        _member(
            gym,
            owner.id,
            number="P000003",
            first_name="Ada",
            last_name="Carter",
            join_date=date(2024, 5, 1),
        ),
        _member(
            gym,
            owner.id,
            number="P000004",
            first_name="Ada",
            last_name="Davis",
            join_date=date(2024, 6, 1),
        ),
        _member(
            gym,
            owner.id,
            number="P000005",
            first_name="Ada",
            last_name="Early",
            join_date=date(2020, 1, 15),
        ),
        _member(
            gym,
            owner.id,
            number="P000006",
            first_name="Ada",
            last_name="Suspended",
            join_date=date(2024, 7, 1),
            status=Member.Status.SUSPENDED,
        ),
        _member(
            gym,
            owner.id,
            number="P000007",
            first_name="Mia",
            last_name="North",
            join_date=date(2024, 8, 1),
        ),
        _member(
            gym,
            owner.id,
            number="P000008",
            first_name="Zoe",
            last_name="Young",
            join_date=date(2023, 1, 1),
        ),
    ]
    _insert(gym, seeded)
    leak = _member(
        other_gym,
        other_owner.id,
        number="P000001",
        first_name="Ada",
        last_name="Leak",
        join_date=window,
    )
    _insert(other_gym, [leak])

    first = api.get("/api/v1/members/", data=_combined(), **headers)
    assert first.status_code == 200, first.content
    first_page = first.json()
    assert [row["last_name"] for row in first_page["data"]] == ["Able", "Baker"]
    assert first_page["meta"]["has_more"] is True
    cursor = first_page["meta"]["next_after"]
    assert isinstance(cursor, str) and cursor
    second = api.get("/api/v1/members/", data=_combined(cursor), **headers)
    assert second.status_code == 200, second.content
    assert [row["last_name"] for row in second.json()["data"]] == ["Carter", "Davis"]
    assert str(leak.id) not in second.content.decode()
    foreign_list = api.get("/api/v1/members/", data={"search": SEARCH}, **other_headers)
    assert foreign_list.status_code == 200, foreign_list.content
    foreign_ids = {row["id"] for row in foreign_list.json()["data"]}
    assert foreign_ids == {str(leak.id)}
    own_ids = {
        row["id"]
        for row in api.get("/api/v1/members/", data={"search": SEARCH}, **headers).json()["data"]
    }
    assert str(leak.id) not in own_ids
    assert foreign_ids.isdisjoint(own_ids)

    stolen = api.get("/api/v1/members/", data=_combined(cursor), **other_headers)
    assert stolen.status_code == 400
    assert "after" in stolen.json()["error"]["fields"]
    assert "Leak" not in stolen.content.decode()
    assert str(gym.id) not in stolen.content.decode()
    rejections = (
        {**_combined(cursor), "status": "SUSPENDED"},
        {**_combined(cursor), "search": "zoe"},
        {**_combined(cursor), "joined_from": "2023-01-01"},
        {**_combined(cursor), "joined_to": "2024-06-01"},
        {**_combined(cursor), "is_pt_client": "true"},
    )
    for params in rejections:
        rejected = api.get("/api/v1/members/", data=params, **headers)
        assert rejected.status_code == 400, rejected.content
        assert "after" in rejected.json()["error"]["fields"]
        assert "Able" not in rejected.content.decode()
    same_scope = api.get(
        "/api/v1/members/",
        data={**_combined(cursor), "limit": 1},
        **headers,
    )
    assert same_scope.status_code == 200, same_scope.content
    assert [row["last_name"] for row in same_scope.json()["data"]] == ["Carter"]

    def _counts(after: str) -> dict[str, tuple[Any, list[dict[str, Any]]]]:
        measured = {
            "unfiltered": _measure(api, headers, {"limit": PAGE_LIMIT}),
            "search": _measure(api, headers, {"limit": PAGE_LIMIT, "search": SEARCH}),
            "filtered_page": _measure(api, headers, _combined(after)),
            "pt_unavailable": _measure(api, headers, {"limit": PAGE_LIMIT, "is_pt_client": "true"}),
        }
        return measured

    small = _counts(cursor)
    assert [row["last_name"] for row in small["unfiltered"][0].json()["data"]] == ["Able", "Baker"]
    assert [row["last_name"] for row in small["search"][0].json()["data"]] == ["Able", "Baker"]
    assert small["search"][0].json()["meta"]["has_more"] is True
    assert [row["last_name"] for row in small["filtered_page"][0].json()["data"]] == [
        "Carter",
        "Davis",
    ]
    assert small["pt_unavailable"][0].json()["data"] == []
    assert small["pt_unavailable"][0].json()["meta"] == {"has_more": False, "next_after": None}
    for name, (_response, queries) in small.items():
        if name == "pt_unavailable":
            assert small["pt_unavailable"][0].json()["data"] == []
            _assert_one_bounded_list_query(queries, gym.id)
            continue
        small_statement = _assert_one_bounded_list_query(queries, gym.id)
        assert not _contains_uuid(small_statement, other_gym.id), small_statement

    _insert(
        gym,
        [
            _member(
                gym,
                owner.id,
                number=f"L{index:06d}",
                first_name="Bulk",
                last_name=f"{index:04d}",
                join_date=window if index % 3 else date(2019, 1, 1),
                status=Member.Status.ACTIVE if index % 4 else Member.Status.SUSPENDED,
            )
            for index in range(LARGE_ROWS)
        ],
    )
    with connection.cursor() as db_cursor:
        db_cursor.execute("ANALYZE members_member")
        db_cursor.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = %s
            """,
            [NAME_INDEX],
        )
        indexdef = db_cursor.fetchone()[0]
        db_cursor.execute(
            """
            SELECT pg_database.datcollate, pg_collation.collname
            FROM pg_database
            CROSS JOIN pg_attribute AS attribute
            LEFT JOIN pg_collation ON pg_collation.oid = attribute.attcollation
            WHERE pg_database.datname = current_database()
              AND attribute.attrelid = 'public.members_member'::regclass
              AND attribute.attname = 'normalized_full_name'
            """
        )
        collation_row = db_cursor.fetchone()
    database_collation, column_collation = collation_row
    assert indexdef == EXPECTED_NAME_INDEX
    assert "pattern_ops" not in indexdef
    assert "gin" not in indexdef.lower()
    assert database_collation
    assert column_collation == "default"

    large = _counts(cursor)
    assert [row["last_name"] for row in large["filtered_page"][0].json()["data"]] == [
        "Carter",
        "Davis",
    ]
    assert large["pt_unavailable"][0].json()["data"] == []
    for name, (_response, queries) in small.items():
        large_response, large_queries = large[name]
        assert len(queries) == len(large_queries), name
        assert len(large_queries) <= MEMBER_LIST_QUERY_BUDGET, name
        if name == "pt_unavailable":
            assert large_response.json()["data"] == []
            _assert_one_bounded_list_query(large_queries, gym.id)
            continue
        large_sql = _assert_one_bounded_list_query(large_queries, gym.id)
        assert not _contains_uuid(large_sql, other_gym.id), large_sql
        _assert_tenant_name_index(
            _explain(large_sql, gym.id),
            gym.id,
            contains_search=name in {"search", "filtered_page"},
            filtered=name == "filtered_page",
        )
    list_count = len(large["unfiltered"][1])
    assert list_count == len(large["search"][1]) == len(large["filtered_page"][1])
    assert len(large["pt_unavailable"][1]) == list_count
