"""Round-trip the booking migrations and the shared SQL they replace."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import InternalError, ProgrammingError

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

TRAINERS_PRE = ("trainers", "0017_class_schedule_catalog")
CLASSES_PRE = ("classes", "0004_class_schedule_guards")
TRAINERS_TABLES = ("trainers", "0018_booking_foundation")
CLASSES_TABLES = ("classes", "0005_booking_foundation")
CLASSES_GUARDS = ("classes", "0006_class_booking_guards")
CLASSES_TERMINAL = ("classes", "0008_member_self_actors")
TRAINERS_LINKS = ("trainers", "0019_booking_ledger_links")
TRAINERS_TERMINAL = ("trainers", "0020_pt_booking_terminal_time")
TRAINERS_PAYOUT = ("trainers", "0022_owner_or_desk_may_approve_own_pt_sale")

BLOCKS = "app_class_occurrence_blocks_regeneration(uuid, uuid)"
TERMINAL = "app_class_occurrence_has_terminal_booking(uuid, uuid)"
CANCEL = "app_cancel_scheduled_class_bookings(uuid, uuid, uuid, timestamp with time zone, text)"
BOOKING_FUNCTIONS = (
    "app_guard_class_booking()",
    "app_guard_class_booking_delete()",
    "app_guard_pt_booking()",
    "app_guard_pt_booking_delete()",
    "app_assert_booking_ledger_effect()",
)
BOOKING_TABLES = ("classes_class_booking", "trainers_pt_booking")
LEDGER_LINKS = (
    "sess_ledger_pt_booking_same_gym_fk",
    "sess_ledger_class_booking_same_gym_fk",
)
BOOKING_CONSTRAINTS = (
    *LEDGER_LINKS,
    "pt_book_trainer_overlap_excl",
    "pt_book_member_overlap_excl",
    "pt_book_meter_ck",
    "class_book_meter_ck",
    "class_book_occurrence_same_gym_fk",
)
CONSUME_INDEXES = (
    "sess_ledger_one_pt_consume_uq",
    "sess_ledger_one_class_consume_uq",
)


def _restore_migration_head() -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _applied(app: str) -> set[str]:
    return set(MigrationRecorder.Migration.objects.filter(app=app).values_list("name", flat=True))


def _plan(targets: list[tuple[str, str]]) -> list[tuple[str, bool]]:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return [
        (f"{migration.app_label}.{migration.name}", backwards)
        for migration, backwards in executor.migration_plan(targets)
    ]


def _migrate(targets: list[tuple[str, str]]) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def _function_def(signature: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _function_exists(signature: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
    return bool(row and row[0])


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"public.{name}"])
        row = cursor.fetchone()
    return bool(row and row[0])


def _constraint(name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
            [name],
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def _column_type(table: str, column: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            """,
            [table, column],
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def _trigger_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE tgrelid = %s::regclass AND NOT tgisinternal
            """,
            [table],
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _forced_rls(table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relrowsecurity AND relforcerowsecurity
            FROM pg_class
            WHERE oid = %s::regclass
            """,
            [table],
        )
        row = cursor.fetchone()
    assert row is not None
    return bool(row[0])


def _runtime_table(privilege: str, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege('gym_runtime', %s, %s)",
            [table, privilege],
        )
        row = cursor.fetchone()
    assert row is not None
    return bool(row[0])


def _runtime_can_execute(signature: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('gym_runtime', %s, 'EXECUTE')",
            [signature],
        )
        row = cursor.fetchone()
    assert row is not None
    return bool(row[0])


def _call_blocks() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT app_class_occurrence_blocks_regeneration(
              '00000000-0000-0000-0000-000000000001'::uuid,
              '00000000-0000-0000-0000-000000000002'::uuid
            )
            """
        )
        row = cursor.fetchone()
    assert row is not None
    return bool(row[0])


def _call_cancel() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT app_cancel_scheduled_class_bookings(
              '00000000-0000-0000-0000-000000000001'::uuid,
              '00000000-0000-0000-0000-000000000002'::uuid,
              '00000000-0000-0000-0000-000000000003'::uuid,
              now(),
              'rollback probe'
            )
            """
        )


def _reject_ledger(*, reason: str, with_pt_booking: bool, match: str) -> None:
    with pytest.raises((InternalError, ProgrammingError), match=match):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, member_service_package_id,
                      session_delta, reason, occurred_at, created_by_membership_id,
                      pt_booking_id, class_booking_id
                    ) VALUES (
                      gen_random_uuid(), gen_random_uuid(), now(), now(), gen_random_uuid(),
                      -1, %s, now(), gen_random_uuid(), %s, NULL
                    )
                    """,
                    [reason, str(uuid4()) if with_pt_booking else None],
                )


def _assert_ledger_columns_kept() -> None:
    for column in ("pt_booking_id", "class_booking_id"):
        assert _column_type("trainers_service_session_ledger", column) == "uuid"


def _assert_placeholder_occurrences() -> None:
    blocked = _function_def(BLOCKS)
    terminal = _function_def(TERMINAL)
    cancel = _function_def(CANCEL)
    assert "SELECT false" in blocked
    assert "classes_class_booking" not in blocked
    assert "SELECT false" in terminal
    assert "classes_class_booking" not in terminal
    assert "classes_class_booking" not in cancel
    assert _call_blocks() is False
    _call_cancel()
    assert _runtime_can_execute(BLOCKS)
    assert _runtime_can_execute(TERMINAL)


def _assert_booking_occurrences() -> None:
    blocked = _function_def(BLOCKS)
    terminal = _function_def(TERMINAL)
    cancel = _function_def(CANCEL)
    assert "BOOKED" in blocked
    assert "ATTENDED" in blocked
    assert "SELECT false" not in blocked
    assert "ATTENDED" in terminal
    assert "NO_SHOW" in terminal
    assert "SELECT false" not in terminal
    assert "classes_class_booking" in cancel
    assert "CANCELLED" in cancel
    assert _call_blocks() is False
    _call_cancel()
    assert _runtime_can_execute(BLOCKS)
    assert _runtime_can_execute(TERMINAL)


def _assert_pre_booking_ledger() -> None:
    ledger = _function_def("app_guard_session_ledger()")
    assert "IN ('ATTENDED', 'POLICY_NO_SHOW', 'EXPIRY')" in ledger
    assert "consumption evidence is incomplete" not in ledger
    assert "this reason is not published" in ledger
    assert _runtime_can_execute("app_guard_session_ledger()")
    _reject_ledger(
        reason="ATTENDED",
        with_pt_booking=True,
        match="this reason is not published",
    )
    _reject_ledger(
        reason="POLICY_NO_SHOW",
        with_pt_booking=False,
        match="this reason is not published",
    )


def _assert_booking_ledger() -> None:
    ledger = _function_def("app_guard_session_ledger()")
    assert "consumption evidence is incomplete" in ledger
    assert "consumption booking is not eligible" in ledger
    assert "IN ('ATTENDED', 'POLICY_NO_SHOW', 'EXPIRY')" not in ledger
    assert "NEW.reason = 'EXPIRY'" in ledger
    assert _runtime_can_execute("app_guard_session_ledger()")
    _reject_ledger(
        reason="ATTENDED",
        with_pt_booking=False,
        match="consumption evidence is incomplete",
    )
    _reject_ledger(
        reason="POLICY_NO_SHOW",
        with_pt_booking=False,
        match="consumption evidence is incomplete",
    )
    _reject_ledger(
        reason="ATTENDED",
        with_pt_booking=True,
        match="consumption booking is not eligible",
    )
    _reject_ledger(
        reason="EXPIRY",
        with_pt_booking=False,
        match="this reason is not published",
    )


def _assert_booking_guards_absent() -> None:
    for signature in BOOKING_FUNCTIONS:
        assert not _function_exists(signature)
    for name in (*BOOKING_CONSTRAINTS, *CONSUME_INDEXES):
        assert _constraint(name) is None
        assert not _table_exists(name)
    assert "sess_ledger_effect_trg" not in _trigger_names("trainers_service_session_ledger")


def _assert_pre_booking_schema(*, tables: bool) -> None:
    _assert_ledger_columns_kept()
    _assert_placeholder_occurrences()
    _assert_pre_booking_ledger()
    _assert_booking_guards_absent()
    for table in BOOKING_TABLES:
        assert _table_exists(table) is tables
    if not tables:
        return
    for table in BOOKING_TABLES:
        assert not _forced_rls(table)
        assert not _runtime_table("SELECT", table)
        assert not _runtime_table("INSERT", table)
        assert not _runtime_table("UPDATE", table)
        assert not _runtime_table("DELETE", table)
    assert "class_booking_guard_trg" not in _trigger_names("classes_class_booking")
    assert "class_book_effect_trg" not in _trigger_names("classes_class_booking")
    assert "pt_booking_guard_trg" not in _trigger_names("trainers_pt_booking")
    assert "pt_book_effect_trg" not in _trigger_names("trainers_pt_booking")


def _assert_booking_schema() -> None:
    _assert_ledger_columns_kept()
    _assert_booking_occurrences()
    _assert_booking_ledger()
    for signature in BOOKING_FUNCTIONS:
        assert _function_exists(signature)
        assert _runtime_can_execute(signature)
    for name in BOOKING_CONSTRAINTS:
        assert _constraint(name) is not None
    for name in CONSUME_INDEXES:
        assert _table_exists(name)
    for table in BOOKING_TABLES:
        assert _table_exists(table)
        assert _forced_rls(table)
        assert _runtime_table("SELECT", table)
        assert _runtime_table("INSERT", table)
        assert _runtime_table("UPDATE", table)
        assert not _runtime_table("DELETE", table)
    class_triggers = _trigger_names("classes_class_booking")
    assert {"class_booking_guard_trg", "class_booking_delete_trg", "class_book_effect_trg"} <= (
        class_triggers
    )
    pt_triggers = _trigger_names("trainers_pt_booking")
    assert {"pt_booking_guard_trg", "pt_booking_delete_trg", "pt_book_effect_trg"} <= pt_triggers
    assert "sess_ledger_effect_trg" in _trigger_names("trainers_service_session_ledger")


def _backwards(plan: list[tuple[str, bool]]) -> list[str]:
    return [name for name, backwards in plan if backwards]


def test_booking_migrations_round_trip_restores_shared_seams() -> None:
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    loader.graph.ensure_not_cyclic()
    try:
        rolled = _backwards(_plan([TRAINERS_PRE, CLASSES_PRE]))
        assert rolled.index("trainers.0019_booking_ledger_links") < rolled.index(
            "classes.0006_class_booking_guards"
        )
        assert rolled.index("classes.0006_class_booking_guards") < rolled.index(
            "classes.0005_booking_foundation"
        )
        assert rolled.index("classes.0006_class_booking_guards") < rolled.index(
            "trainers.0018_booking_foundation"
        )
        assert rolled.index("trainers.0019_booking_ledger_links") < rolled.index(
            "trainers.0018_booking_foundation"
        )
        _migrate([TRAINERS_PRE, CLASSES_PRE])
        assert "0017_class_schedule_catalog" in _applied("trainers")
        assert "0018_booking_foundation" not in _applied("trainers")
        assert "0019_booking_ledger_links" not in _applied("trainers")
        assert "0004_class_schedule_guards" in _applied("classes")
        assert "0005_booking_foundation" not in _applied("classes")
        assert "0006_class_booking_guards" not in _applied("classes")
        _assert_pre_booking_schema(tables=False)

        _migrate([CLASSES_GUARDS, TRAINERS_LINKS])
        assert "0006_class_booking_guards" in _applied("classes")
        assert "0019_booking_ledger_links" in _applied("trainers")
        _assert_booking_schema()

        partial = _backwards(_plan([TRAINERS_TABLES, CLASSES_TABLES]))
        assert partial.index("trainers.0019_booking_ledger_links") < partial.index(
            "classes.0006_class_booking_guards"
        )
        assert "trainers.0018_booking_foundation" not in partial
        assert "classes.0005_booking_foundation" not in partial
        _migrate([TRAINERS_TABLES, CLASSES_TABLES])
        assert "0018_booking_foundation" in _applied("trainers")
        assert "0019_booking_ledger_links" not in _applied("trainers")
        assert "0005_booking_foundation" in _applied("classes")
        assert "0006_class_booking_guards" not in _applied("classes")
        _assert_pre_booking_schema(tables=True)

        _migrate([CLASSES_GUARDS, TRAINERS_LINKS])
        assert "0006_class_booking_guards" in _applied("classes")
        assert "0019_booking_ledger_links" in _applied("trainers")
        _assert_booking_schema()
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        loader.graph.ensure_not_cyclic()
        assert loader.graph.leaf_nodes("classes") == [CLASSES_TERMINAL]
        assert loader.graph.leaf_nodes("trainers") == [TRAINERS_PAYOUT]
    finally:
        _restore_migration_head()


def test_terminal_time_and_payout_window_migrations_round_trip() -> None:
    try:
        assert "attendance is before the start" in _function_def("app_guard_pt_booking()")
        assert "no-show is before the end" in _function_def("app_guard_pt_booking()")
        assert "attendance is before the start" in _function_def("app_guard_class_booking()")
        assert "no-show is before the end" in _function_def("app_guard_class_booking()")
        assert "payout date is outside the sale window" in _function_def(
            "app_guard_pt_commission_payment()"
        )
        _migrate([TRAINERS_LINKS, CLASSES_GUARDS])
        assert "0020_pt_booking_terminal_time" not in _applied("trainers")
        assert "0021_pt_commission_payout_window" not in _applied("trainers")
        assert "0007_class_booking_terminal_time" not in _applied("classes")
        assert "attendance is before the start" not in _function_def("app_guard_pt_booking()")
        assert "attendance is before the start" not in _function_def("app_guard_class_booking()")
        assert "payout date is outside the sale window" not in _function_def(
            "app_guard_pt_commission_payment()"
        )
        _migrate([TRAINERS_PAYOUT, CLASSES_TERMINAL])
        assert "0021_pt_commission_payout_window" in _applied("trainers")
        assert "0007_class_booking_terminal_time" in _applied("classes")
        assert "attendance is before the start" in _function_def("app_guard_pt_booking()")
        assert "attendance is before the start" in _function_def("app_guard_class_booking()")
        assert "payout date is outside the sale window" in _function_def(
            "app_guard_pt_commission_payment()"
        )
    finally:
        _restore_migration_head()
