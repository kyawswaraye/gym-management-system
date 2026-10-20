"""Round-trip the class catalog migrations and the package-link guard."""

from __future__ import annotations

import os

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _restore_migration_head() -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _function_def(signature: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"public.{name}"])
        row = cursor.fetchone()
    return row is not None and row[0] is not None


def test_class_catalog_migrations_round_trip_and_stay_acyclic() -> None:
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    loader.graph.ensure_not_cyclic()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE trainers_service_package DISABLE TRIGGER service_package_guard_trg"
            )
            cursor.execute("DELETE FROM trainers_service_package WHERE type = 'CLASS'")
            cursor.execute(
                "ALTER TABLE trainers_service_package ENABLE TRIGGER service_package_guard_trg"
            )
        executor = MigrationExecutor(connection)
        executor.migrate([("trainers", "0016_pt_sale_refund_reversal_link")])
        restored = _function_def("app_guard_service_package()")
        assert "only PT packages without a class type" in restored
        executor = MigrationExecutor(connection)
        executor.migrate([("classes", None)])
        assert not _table_exists("classes_class_type")
        assert not _table_exists("classes_class_occurrence")
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        current = _function_def("app_guard_service_package()")
        assert "a CLASS package requires an active class type" in current
        regeneration = _function_def("app_class_occurrence_blocks_regeneration(uuid, uuid)")
        assert "BOOKED" in regeneration
        assert "SELECT false" not in regeneration
        assert _table_exists("classes_class_occurrence")
        assert _table_exists("classes_class_booking")
        assert _table_exists("trainers_pt_booking")
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        loader.graph.ensure_not_cyclic()
        assert loader.graph.leaf_nodes("classes") == [("classes", "0008_member_self_actors")]
        assert loader.graph.leaf_nodes("trainers") == [
            ("trainers", "0021_pt_commission_payout_window")
        ]
        assert ("classes", "0004_class_schedule_guards") not in loader.graph.forwards_plan(
            ("trainers", "0017_class_schedule_catalog")
        )
    finally:
        _restore_migration_head()
