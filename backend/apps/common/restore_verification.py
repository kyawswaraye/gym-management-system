"""Read-only judgments for an isolated restore rehearsal.

The checks do not mutate business data and do not download object bytes.
Live backup-provider evidence stays separate from these automated results.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

EXPECTED_DATABASE = "gym_platform"
EXPECTED_VERSION_NUM = 180006
REQUIRED_FORCED_TABLES = frozenset(
    {
        "members_member",
        "members_payment",
        "members_receipt",
        "cashbook_cash_session",
        "cashbook_cash_transaction",
        "files_file_asset",
        "audit_audit_log",
        "expenses_expense",
        "sales_retail_sale",
        "tenants_gym_membership",
    }
)
REQUIRED_CONSTRAINTS = frozenset(
    {
        "cashsession_expected_ck",
        "payment_amount_positive_ck",
        "fileasset_sha256_format_ck",
        "receipt_one_issued_per_payment",
    }
)
REQUIRED_TRIGGERS = frozenset(
    {
        "payment_guard_trg",
        "receipt_guard_trg",
        "expense_guard_trg",
        "retail_sale_guard_trg",
    }
)
LIVE_EVIDENCE = {
    "offsite_wal_upload": "not_claimed",
    "measured_freshness_against_a_live_repository": "not_claimed",
    "backup_provider_approval": "not_claimed",
    "retention_legal_approval": "not_approved",
    "rpo_le_15_minutes": "not_proven",
    "rto_le_4_hours": "not_proven",
    "alert_delivery": "not_claimed",
    "production_rollout": "not_claimed",
}
ROLE_EXPECTATIONS = {
    "gym_migration": (False, False, False, False, True, False),
    "gym_runtime": (False, False, False, False, True, False),
    "gym_auth_bootstrap": (False, False, False, False, False, True),
}


class RestoreCursor(Protocol):
    def execute(self, sql: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


def judge_identity(database: object, version_num: object, in_recovery: object) -> str:
    if (
        database != EXPECTED_DATABASE
        or version_num != EXPECTED_VERSION_NUM
        or in_recovery is not False
    ):
        return "fail"
    return "pass"


def judge_superuser(value: object) -> str:
    return "pass" if value == "on" else "fail"


def judge_roles(rows: Sequence[Sequence[object]]) -> str:
    found: dict[str, tuple[object, ...]] = {}
    for row in rows:
        if len(row) != 7 or not isinstance(row[0], str):
            return "fail"
        found[row[0]] = tuple(row[1:])
    if set(found) != set(ROLE_EXPECTATIONS):
        return "fail"
    for name, expected in ROLE_EXPECTATIONS.items():
        if found[name] != expected:
            return "fail"
    return "pass"


def judge_grants(
    *,
    runtime_select: object,
    runtime_create_schema: object,
    runtime_connect: object,
    runtime_owned_tables: object,
    migration_owned_tables: object,
) -> str:
    if (
        runtime_select is True
        and runtime_create_schema is False
        and runtime_connect is True
        and runtime_owned_tables == 0
        and isinstance(migration_owned_tables, int)
        and migration_owned_tables > 0
    ):
        return "pass"
    return "fail"


def judge_rls(rows: Sequence[Sequence[object]]) -> str:
    seen: dict[str, tuple[object, object, object]] = {}
    for row in rows:
        name = row[0] if row else None
        if len(row) != 4 or not isinstance(name, str):
            return "fail"
        enabled, forced, policies = row[1], row[2], row[3]
        seen[name] = (enabled, forced, policies)
        if policies not in {0, None} and forced is not True:
            return "fail"
        if enabled is True and forced is not True:
            return "fail"
    for name in REQUIRED_FORCED_TABLES:
        enabled, forced, policies = seen.get(name, (None, None, None))
        if (
            enabled is not True
            or forced is not True
            or not isinstance(policies, int)
            or policies < 1
        ):
            return "fail"
    return "pass"


def judge_names(present: set[str], required: frozenset[str]) -> str:
    return "pass" if required <= present else "fail"


def judge_finance(session_identity: object, movement_identity: object, receipts: object) -> str:
    if session_identity == 0 and movement_identity == 0 and receipts == 0:
        return "pass"
    return "fail"


def judge_audit(tenant_rows: object, platform_rows: object) -> str:
    if isinstance(tenant_rows, int) and tenant_rows > 0 and isinstance(platform_rows, int):
        return "pass"
    return "fail"


def judge_files(assets: object, bad_checksums: object, missing_keys: object) -> str:
    if isinstance(assets, int) and assets >= 0 and bad_checksums == 0 and missing_keys == 0:
        return "pass"
    return "fail"


def judge_migrations(disk: set[tuple[str, str]], applied: set[tuple[str, str]]) -> str:
    if disk and disk == applied:
        return "pass"
    return "fail"


def judge_isolation(
    *,
    gym_count: int,
    hidden_without_context: object,
    hidden_for_other_gym: object,
    visible_for_own_gym: object,
) -> str:
    if gym_count < 2:
        return "not_exercised"
    if hidden_without_context == 0 and hidden_for_other_gym == 0 and visible_for_own_gym == 1:
        return "pass"
    return "fail"


def finalize(automated: dict[str, str], details: dict[str, object]) -> dict[str, object]:
    return {
        "ok": all(value == "pass" for value in automated.values()) and bool(automated),
        "automated": automated,
        "details": details,
        "live_evidence": dict(LIVE_EVIDENCE),
        "objects_downloaded": False,
        "business_data_mutated": False,
    }


def _one(
    cursor: RestoreCursor, sql: str, params: Sequence[object] | None = None
) -> tuple[object, ...]:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("restore verification query returned no row")
    return row


def collect_restore_report(cursor: RestoreCursor) -> dict[str, object]:
    """Run the read-only inventory. The caller owns the transaction."""

    cursor.execute("SET TRANSACTION READ ONLY")
    database, version_num, in_recovery = _one(
        cursor,
        "SELECT current_database(), current_setting('server_version_num')::integer, "
        "pg_is_in_recovery()",
    )
    superuser = _one(cursor, "SELECT current_setting('is_superuser')")[0]
    cursor.execute(
        "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, "
        "rolbypassrls FROM pg_roles WHERE rolname IN "
        "('gym_migration', 'gym_runtime', 'gym_auth_bootstrap')"
    )
    roles = cursor.fetchall()
    grants = _one(
        cursor,
        "SELECT has_table_privilege('gym_runtime', 'public.members_member', 'SELECT'), "
        "has_schema_privilege('gym_runtime', 'public', 'CREATE'), "
        "has_database_privilege('gym_runtime', current_database(), 'CONNECT'), "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_roles r ON r.oid = c.relowner WHERE n.nspname = 'public' "
        "AND c.relkind = 'r' AND r.rolname = 'gym_runtime'), "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_roles r ON r.oid = c.relowner WHERE n.nspname = 'public' "
        "AND c.relkind = 'r' AND r.rolname = 'gym_migration')",
    )
    cursor.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    )
    rls_rows = cursor.fetchall()
    cursor.execute("SELECT conname FROM pg_constraint WHERE contype IN ('c', 'u', 'f', 't', 'x')")
    constraints = {row[0] for row in cursor.fetchall() if isinstance(row[0], str)}
    cursor.execute(
        "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
    )
    triggers = {row[0] for row in cursor.fetchall() if isinstance(row[0], str)}
    finance = _one(
        cursor,
        "SELECT "
        "(SELECT count(*) FROM cashbook_cash_session s WHERE s.expected_closing IS DISTINCT FROM "
        "(s.opening_cash + s.cash_sales - s.cash_expenses + s.manual_net)), "
        "(SELECT count(*) FROM cashbook_cash_session s "
        "CROSS JOIN LATERAL app_cash_session_totals(s.gym_id, s.id) totals "
        "WHERE s.cash_sales IS DISTINCT FROM totals.sales "
        "OR s.cash_expenses IS DISTINCT FROM totals.expenses "
        "OR s.manual_net IS DISTINCT FROM totals.manual_net), "
        "(SELECT count(*) FROM members_receipt r JOIN members_payment p ON p.id = r.payment_id "
        "WHERE r.status = 'ISSUED' AND (r.amount IS DISTINCT FROM p.amount "
        "OR r.gym_id IS DISTINCT FROM p.gym_id OR r.currency IS DISTINCT FROM p.currency "
        "OR p.status <> 'POSTED'))",
    )
    audit = _one(
        cursor,
        "SELECT (SELECT count(*) FROM audit_audit_log), "
        "(SELECT count(*) FROM audit_platform_audit_log)",
    )
    files = _one(
        cursor,
        "SELECT count(*), count(*) FILTER (WHERE sha256 !~ '^[0-9a-f]{64}$'), "
        "count(*) FILTER (WHERE object_key IS NULL OR btrim(object_key) = ''), "
        "coalesce(md5(string_agg(sha256, '' ORDER BY sha256)), '') FROM files_file_asset",
    )
    cursor.execute(
        "SELECT gym_id::text, id::text FROM members_member ORDER BY gym_id::text, id::text"
    )
    members = [(str(gym), str(member)) for gym, member in cursor.fetchall()]
    gyms = list(dict.fromkeys(gym for gym, _member in members))
    isolation = "not_exercised"
    if len(gyms) >= 2 and members:
        own_gym, member_id = members[0]
        other_gym = next(gym for gym in gyms if gym != own_gym)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT set_config('app.gym_id', '', true)")
        hidden = _one(
            cursor, "SELECT count(*) FROM members_member WHERE id = %s::uuid", [member_id]
        )
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [other_gym])
        other = _one(cursor, "SELECT count(*) FROM members_member WHERE id = %s::uuid", [member_id])
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [own_gym])
        own = _one(cursor, "SELECT count(*) FROM members_member WHERE id = %s::uuid", [member_id])
        cursor.execute("RESET ROLE")
        isolation = judge_isolation(
            gym_count=len(gyms),
            hidden_without_context=hidden[0],
            hidden_for_other_gym=other[0],
            visible_for_own_gym=own[0],
        )
    automated = {
        "postgres_reachable": "pass",
        "recovery_complete": judge_identity(database, version_num, in_recovery),
        "database_identity": judge_identity(database, version_num, False)
        if in_recovery is False
        else "fail",
        "superuser_inventory": judge_superuser(superuser),
        "roles": judge_roles(roles),
        "runtime_grants": judge_grants(
            runtime_select=grants[0],
            runtime_create_schema=grants[1],
            runtime_connect=grants[2],
            runtime_owned_tables=grants[3],
            migration_owned_tables=grants[4],
        ),
        "forced_rls": judge_rls(rls_rows),
        "missing_context_and_two_gym_isolation": isolation,
        "constraints": judge_names(constraints, REQUIRED_CONSTRAINTS),
        "triggers": judge_names(triggers, REQUIRED_TRIGGERS),
        "finance_reconciliation": judge_finance(finance[0], finance[1], finance[2]),
        "audit_evidence": judge_audit(audit[0], audit[1]),
        "file_reference_inventory": judge_files(files[0], files[1], files[2]),
    }
    return finalize(
        automated,
        {
            "database": database,
            "server_version_num": version_num,
            "in_recovery": in_recovery,
            "file_asset_count": files[0],
            "file_checksum_digest": files[3],
            "audit_rows": audit[0],
            "platform_audit_rows": audit[1],
            "gym_count": len(gyms),
        },
    )
