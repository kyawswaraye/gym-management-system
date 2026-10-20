import inspect
import re
from pathlib import Path

import pytest
from django.core.management.base import CommandError
from django.db import connection
from django.db.transaction import TransactionManagementError
from django.db.utils import ProgrammingError

from apps.tenants.e2e import assert_e2e_seed_allowed
from apps.tenants.e2e_runtime import e2e_reuses_storage_keys
from apps.tenants.management.commands import seed_e2e
from apps.tenants.management.commands.seed_e2e import (
    _DISABLE_AUDIT_TRIGGERS,
    _ENABLE_AUDIT_TRIGGERS,
    Command,
    _delete_gym_tree,
    _purge_e2e_rows,
    _with_e2e_cleanup_triggers,
)

_E2E_BACKEND = Path(__file__).resolve().parents[3] / "scripts" / "e2e-backend.sh"


def test_seed_allows_only_the_dedicated_e2e_database() -> None:
    assert_e2e_seed_allowed(
        database_name="gym_platform_e2e",
        settings_module="config.settings.e2e",
        seed_flag="1",
    )


def test_seed_refuses_the_application_database() -> None:
    with pytest.raises(CommandError, match="gym_platform_e2e"):
        assert_e2e_seed_allowed(
            database_name="gym_platform",
            settings_module="config.settings.e2e",
            seed_flag="1",
        )


def test_seed_refuses_to_run_without_the_explicit_flag() -> None:
    with pytest.raises(CommandError, match="E2E_SEED"):
        assert_e2e_seed_allowed(
            database_name="gym_platform_e2e",
            settings_module="config.settings.e2e",
            seed_flag="",
        )


def test_seed_refuses_reused_object_keys_and_drops_members_first() -> None:
    assert e2e_reuses_storage_keys() is False
    assert "e2e_reuses_storage_keys" in inspect.getsource(Command.handle)
    source = inspect.getsource(_delete_gym_tree)
    assert source.index("CheckIn.objects.filter") < source.index("Member.objects.filter")
    assert source.index("CheckIn.objects.filter") < source.index("Membership.objects.filter")
    assert source.index("CheckIn.objects.filter") < source.index("CheckInDevice.objects.filter")
    assert source.index("CheckInChallenge.objects.filter") < source.index(
        "CheckInDevice.objects.filter"
    )
    assert source.index("CheckInDevice.objects.filter") < source.index(
        "GymMembership.objects.filter"
    )
    handle = inspect.getsource(Command.handle)
    assert "E2E_MEMBER_PASSWORD" in handle
    assert "E2E_MANAGER_PASSWORD" in handle
    assert "E2E_RECEPTION_PASSWORD" in handle
    assert "E2E_TRAINER_PASSWORD" in handle
    assert "E2E_BEA_PASSWORD" in handle
    assert "e2e-manager@example.test" not in handle
    assert "e2e-member@example.test" not in handle
    assert "e2e-reception@example.test" not in handle
    assert "e2e-trainer@example.test" not in handle
    assert source.index("Member.objects.filter") < source.index("FileAsset.objects.filter")


def test_seed_refuses_local_and_production_settings() -> None:
    with pytest.raises(CommandError, match=r"config\.settings\.e2e"):
        assert_e2e_seed_allowed(
            database_name="gym_platform_e2e",
            settings_module="config.settings.local",
            seed_flag="1",
        )


def _allow_e2e_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_SEED", "1")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.e2e")
    monkeypatch.setitem(connection.settings_dict, "NAME", "gym_platform_e2e")


def test_prefix_cleanup_refuses_the_application_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_SEED", "1")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.e2e")
    monkeypatch.setitem(connection.settings_dict, "NAME", "gym_platform")

    def forbidden() -> None:
        raise AssertionError("cleanup SQL must not run")

    monkeypatch.setattr(seed_e2e.connection, "cursor", forbidden)
    with pytest.raises(CommandError, match="gym_platform_e2e"):
        _purge_e2e_rows()


def test_prefix_cleanup_refuses_to_disable_triggers_without_the_seed_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_SEED", "")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.local")
    monkeypatch.setitem(connection.settings_dict, "NAME", "gym_platform")

    def forbidden() -> None:
        raise AssertionError("cleanup SQL must not run")

    monkeypatch.setattr(seed_e2e.connection, "cursor", forbidden)
    with pytest.raises(CommandError, match="E2E_SEED"):
        _with_e2e_cleanup_triggers(lambda: None)


def test_cleanup_does_not_disable_finance_protections() -> None:
    joined = " ".join(_DISABLE_AUDIT_TRIGGERS + _ENABLE_AUDIT_TRIGGERS).lower()
    for protected in (
        "cash",
        "payment",
        "expense",
        "salary",
        "inventory",
        "idempotency",
        "receipt",
        "retail",
        "stock",
    ):
        assert protected not in joined
    assert "finally" not in inspect.getsource(_with_e2e_cleanup_triggers)


def test_failed_prefix_cleanup_preserves_the_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_e2e_cleanup(monkeypatch)

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def execute(self, statement: str) -> None:
            if "ENABLE TRIGGER" in statement:
                raise TransactionManagementError(
                    "An error occurred in the current transaction. You can't "
                    "execute queries until the end of the 'atomic' block."
                )

    class Atomic:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(seed_e2e.transaction, "atomic", lambda: Atomic())
    monkeypatch.setattr(seed_e2e.connection, "cursor", lambda: Cursor())

    def fail() -> None:
        raise ProgrammingError("protected expense category cannot be deleted")

    with pytest.raises(ProgrammingError, match="protected expense category"):
        _with_e2e_cleanup_triggers(fail)


def test_successful_prefix_cleanup_reenables_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_e2e_cleanup(monkeypatch)
    executed: list[str] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def execute(self, statement: str) -> None:
            executed.append(statement)

    class Atomic:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(seed_e2e.transaction, "atomic", lambda: Atomic())
    monkeypatch.setattr(seed_e2e.connection, "cursor", lambda: Cursor())
    _with_e2e_cleanup_triggers(lambda: executed.append("operation"))

    assert executed[0].startswith("ALTER TABLE audit_audit_log DISABLE TRIGGER")
    assert executed[-1].startswith("ALTER TABLE tenants_staff_invitation_role ENABLE TRIGGER")
    assert "operation" in executed
    assert executed.index("operation") < next(
        index for index, statement in enumerate(executed) if "ENABLE TRIGGER" in statement
    )


def test_e2e_backend_recreates_only_the_named_database() -> None:
    script = _E2E_BACKEND.read_text()
    guard = script.index("Refusing to prepare a database other than gym_platform_e2e.")
    drop = script.index("DROP DATABASE IF EXISTS gym_platform_e2e;")
    assert guard < drop
    assert re.findall(r"DROP DATABASE[^;]*;", script) == [
        "DROP DATABASE IF EXISTS gym_platform_e2e;"
    ]
    assert "DROP DATABASE IF EXISTS ${" not in script
    assert 'DROP DATABASE IF EXISTS "$DB_NAME"' not in script
    assert "CREATE DATABASE gym_platform_e2e OWNER gym_migration" in script
    assert 'e2e_reset="${E2E_RESET:-1}"' in script
    assert '[[ "${e2e_reset}" == "1" ]]' in script
    assert "E2E_RESET must be 0 or 1." in script
