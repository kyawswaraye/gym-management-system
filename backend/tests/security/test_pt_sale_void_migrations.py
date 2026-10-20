"""Round-trip the PT sale void migrations back to the pre-void schema."""

from __future__ import annotations

import os
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

TRAINERS_BEFORE = ("trainers", "0013_member_package_ledger_guards")
MEMBERS_BEFORE = ("members", "0023_pt_payment_guard")
TRAINERS_VOID = "0016_pt_sale_refund_reversal_link"
MEMBERS_VOID = "0024_pt_sale_payment_void"
MEMBERS_HEAD = ("members", "0026_member_device_rls_and_guards")
TRAINERS_HEAD = ("trainers", "0021_pt_commission_payout_window")
CLASSES_HEAD = ("classes", "0008_member_self_actors")
VOID_FUNCTIONS = (
    "app_guard_pt_sale",
    "app_pt_sale_commit",
    "app_guard_pt_commission",
    "app_check_pt_commission_aggregates",
    "app_guard_member_package",
    "app_guard_session_ledger",
    "app_check_member_package_balance",
)


def _restore_migration_head() -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _applied(app: str) -> set[str]:
    return set(MigrationRecorder.Migration.objects.filter(app=app).values_list("name", flat=True))


def _function_def(name: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [f"{name}()"])
        return str(cursor.fetchone()[0])


def _constraint(name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
            [name],
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def _void_columns() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'trainers_pt_sale'
              AND column_name IN ('voided_at', 'voided_by_membership_id')
            """
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _runtime_can_execute(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('gym_runtime', %s, 'EXECUTE')",
            [f"{name}()"],
        )
        return bool(cursor.fetchone()[0])


def _assert_pre_void_schema() -> None:
    assert _void_columns() == set()
    assert _constraint("pt_sale_voided_by_same_gym_fk") is None
    status = _constraint("pt_sale_status_ck")
    assert status is not None
    assert "VOID" not in status
    reason = _constraint("pt_sale_reason_ck")
    assert reason is not None
    assert "VOID" not in reason
    assert "CANCELLED" in reason
    amounts = _constraint("pt_comm_amounts_ck")
    assert amounts is not None
    assert "VOID" not in amounts
    assert "outstanding_amount" in amounts
    assert "commission_amount - paid_amount" in amounts.replace("(", "").replace(")", "")
    reversal = _constraint("sess_ledger_reversal_ck")
    assert reversal is not None
    assert "REFUND" not in reversal
    assert "CORRECTION" in reversal
    assert "reverses_entry_id IS NOT NULL" in reversal
    assert "reverses_entry_id IS NULL" in reversal
    sale_guard = _function_def("app_guard_pt_sale")
    assert "CANCELLED', 'POSTED', 'REJECTED'" in sale_guard
    assert "void evidence is incomplete" not in sale_guard
    assert "voided_at" not in sale_guard
    commit = _function_def("app_pt_sale_commit")
    assert "a posted sale requires one commission" in commit
    assert "voided sale" not in commit
    assert "voided_at" not in commit
    commission = _function_def("app_guard_pt_commission")
    assert "status is not payable" in commission
    assert "void commission evidence is incomplete" not in commission
    aggregates = _function_def("app_check_pt_commission_aggregates")
    assert "PARTIALLY_PAID" in aggregates
    assert "parent.status = 'VOID'" not in aggregates
    package = _function_def("app_guard_member_package")
    assert "void is not published" in package
    assert "void requires a full refund" not in package
    ledger = _function_def("app_guard_session_ledger")
    assert "ATTENDED', 'POLICY_NO_SHOW', 'REFUND', 'EXPIRY'" in ledger
    assert "this reason is not published" in ledger
    assert "refund evidence is incomplete" not in ledger
    assert "consumption evidence is incomplete" not in ledger
    balance = _function_def("app_check_member_package_balance")
    assert "package status does not match the ledger" in balance
    assert "pkg.status = 'VOID'" not in balance
    payment = _function_def("app_guard_payment_pt")
    assert "a new PT payment requires the sale" in payment
    assert "a PT reversal requires the sale charge" not in payment
    for name in (*VOID_FUNCTIONS, "app_guard_payment_pt"):
        assert _runtime_can_execute(name)


def _assert_current_schema() -> None:
    assert _void_columns() == {"voided_at", "voided_by_membership_id"}
    assert _constraint("pt_sale_voided_by_same_gym_fk") is not None
    status = _constraint("pt_sale_status_ck")
    assert status is not None and "VOID" in status
    reason = _constraint("pt_sale_reason_ck")
    assert reason is not None and "VOID" in reason
    amounts = _constraint("pt_comm_amounts_ck")
    assert amounts is not None and "VOID" in amounts
    reversal = _constraint("sess_ledger_reversal_ck")
    assert reversal is not None
    assert "REFUND" in reversal
    assert "CORRECTION" in reversal
    sale_guard = _function_def("app_guard_pt_sale")
    assert "void evidence is incomplete" in sale_guard
    assert "voided_at" in sale_guard
    commit = _function_def("app_pt_sale_commit")
    assert "voided sale package refund is incomplete" in commit
    ledger = _function_def("app_guard_session_ledger")
    assert "refund evidence is incomplete" in ledger
    assert "this reason is not published" in ledger
    assert "consumption evidence is incomplete" in ledger
    assert "NEW.reason = 'EXPIRY'" in ledger
    assert "IN ('ATTENDED', 'POLICY_NO_SHOW', 'EXPIRY')" not in ledger
    package = _function_def("app_guard_member_package")
    assert "void requires a full refund" in package
    balance = _function_def("app_check_member_package_balance")
    assert "pkg.status = 'VOID'" in balance
    payment = _function_def("app_guard_payment_pt")
    assert "a PT reversal requires the sale charge" in payment
    for name in (*VOID_FUNCTIONS, "app_guard_payment_pt"):
        assert _runtime_can_execute(name)


def _same_app_rollback(graph: object, target: tuple[str, str]) -> list[tuple[str, str]]:
    node_map = graph.node_map  # type: ignore[attr-defined]
    children = sorted(child.key for child in node_map[target].children if child[0] == target[0])
    assert children
    rolled: list[tuple[str, str]] = []
    for child in children:
        for node in graph.backwards_plan(child):  # type: ignore[attr-defined]
            if node not in rolled:
                rolled.append(node)
    return rolled


def test_pt_sale_void_migrations_round_trip_to_the_pre_void_schema() -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    graph = executor.loader.graph
    graph.ensure_not_cyclic()
    assert graph.leaf_nodes("trainers") == [TRAINERS_HEAD]
    assert graph.leaf_nodes("classes") == [CLASSES_HEAD]
    assert graph.leaf_nodes("members") == [MEMBERS_HEAD]
    assert ("trainers", TRAINERS_VOID) not in graph.leaf_nodes()
    assert ("members", MEMBERS_VOID) not in graph.leaf_nodes()
    member_children = {child.key for child in graph.node_map[("members", MEMBERS_VOID)].children}
    assert ("trainers", "0018_booking_foundation") in member_children
    assert ("classes", "0005_booking_foundation") in member_children
    assert TRAINERS_BEFORE in graph.forwards_plan(("trainers", TRAINERS_VOID))
    assert MEMBERS_BEFORE in graph.forwards_plan(("members", MEMBERS_VOID))
    try:
        plan = executor.migration_plan([TRAINERS_BEFORE, MEMBERS_BEFORE])
        rolled_back = [(migration.app_label, migration.name) for migration, backwards in plan]
        assert all(backwards for _migration, backwards in plan)
        expected: list[tuple[str, str]] = []
        for target in (TRAINERS_BEFORE, MEMBERS_BEFORE):
            for node in _same_app_rollback(graph, target):
                if node not in expected:
                    expected.append(node)
        assert rolled_back == expected
        required = {
            ("trainers", "0014_pt_sale_void_fields"),
            ("trainers", "0015_pt_sale_void_guards"),
            ("trainers", TRAINERS_VOID),
            ("trainers", "0017_class_schedule_catalog"),
            ("trainers", "0018_booking_foundation"),
            ("trainers", "0019_booking_ledger_links"),
            ("trainers", "0020_pt_booking_terminal_time"),
            TRAINERS_HEAD,
            ("members", MEMBERS_VOID),
            ("members", "0025_phase7_notifications"),
            MEMBERS_HEAD,
            ("classes", "0005_booking_foundation"),
            ("classes", "0006_class_booking_guards"),
            CLASSES_HEAD,
        }
        assert required <= set(rolled_back)
        assert TRAINERS_BEFORE not in rolled_back
        assert MEMBERS_BEFORE not in rolled_back
        order = {node: index for index, node in enumerate(rolled_back)}
        for node in rolled_back:
            for parent in graph.node_map[node].parents:
                if parent in order:
                    assert order[node] < order[parent]
        executor.migrate([TRAINERS_BEFORE, MEMBERS_BEFORE])
        assert TRAINERS_VOID not in _applied("trainers")
        assert "0014_pt_sale_void_fields" not in _applied("trainers")
        assert "0021_pt_commission_payout_window" not in _applied("trainers")
        assert "0013_member_package_ledger_guards" in _applied("trainers")
        assert MEMBERS_VOID not in _applied("members")
        assert "0023_pt_payment_guard" in _applied("members")
        assert "0007_class_booking_terminal_time" not in _applied("classes")
        _assert_pre_void_schema()
        rolled = StringIO()
        call_command("migrate", "--plan", stdout=rolled)
        planned = rolled.getvalue()
        assert planned.index("0014_pt_sale_void_fields") < planned.index("0015_pt_sale_void_guards")
        assert planned.index("0015_pt_sale_void_guards") < planned.index(TRAINERS_VOID)
        assert planned.index(TRAINERS_VOID) < planned.index("0017_class_schedule_catalog")
        assert planned.index("0019_booking_ledger_links") < planned.index(
            "0020_pt_booking_terminal_time"
        )
        assert planned.index("0020_pt_booking_terminal_time") < planned.index(
            "0021_pt_commission_payout_window"
        )
        assert planned.index("0006_class_booking_guards") < planned.index(
            "0007_class_booking_terminal_time"
        )
        assert MEMBERS_VOID in planned
        executor.loader.build_graph()
        executor.loader.graph.ensure_not_cyclic()
        executor.migrate(executor.loader.graph.leaf_nodes())
        assert TRAINERS_VOID in _applied("trainers")
        assert "0021_pt_commission_payout_window" in _applied("trainers")
        assert MEMBERS_VOID in _applied("members")
        assert "0007_class_booking_terminal_time" in _applied("classes")
        _assert_current_schema()
        current = StringIO()
        call_command("migrate", "--plan", stdout=current)
        assert "No planned migration operations" in current.getvalue()
        executor.loader.build_graph()
        executor.loader.graph.ensure_not_cyclic()
        assert executor.loader.graph.leaf_nodes("trainers") == [TRAINERS_HEAD]
        assert executor.loader.graph.leaf_nodes("classes") == [CLASSES_HEAD]
        assert executor.loader.graph.leaf_nodes("members") == [MEMBERS_HEAD]
    finally:
        _restore_migration_head()
