from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from apps.common.restore_verification import (
    REQUIRED_CONSTRAINTS,
    REQUIRED_FORCED_TABLES,
    REQUIRED_TRIGGERS,
    collect_restore_report,
    judge_audit,
    judge_files,
    judge_finance,
    judge_grants,
    judge_identity,
    judge_isolation,
    judge_migrations,
    judge_names,
    judge_rls,
    judge_roles,
    judge_superuser,
)

BACKEND = Path(__file__).resolve().parents[2]


class ScriptedCursor:
    def __init__(
        self, ones: list[tuple[object, ...]], many: list[list[tuple[object, ...]]]
    ) -> None:
        self.ones = list(ones)
        self.many = list(many)
        self.sql: list[str] = []

    def execute(self, sql: str, params: object = None) -> None:
        self.sql.append(sql)

    def fetchone(self) -> tuple[object, ...]:
        return self.ones.pop(0)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.many.pop(0)


def _roles() -> list[tuple[object, ...]]:
    return [
        ("gym_migration", False, False, False, False, True, False),
        ("gym_runtime", False, False, False, False, True, False),
        ("gym_auth_bootstrap", False, False, False, False, False, True),
    ]


def _rls() -> list[tuple[object, ...]]:
    return [(name, True, True, 1) for name in sorted(REQUIRED_FORCED_TABLES)]


def test_judgments_fail_closed() -> None:
    assert judge_identity("gym_platform", 180006, False) == "pass"
    assert judge_identity("gym_platform", 180006, True) == "fail"
    assert judge_identity("other", 180006, False) == "fail"
    assert judge_superuser("on") == "pass"
    assert judge_superuser("off") == "fail"
    assert judge_roles(_roles()) == "pass"
    assert judge_roles(_roles()[:-1]) == "fail"
    assert (
        judge_grants(
            runtime_select=True,
            runtime_create_schema=False,
            runtime_connect=True,
            runtime_owned_tables=0,
            migration_owned_tables=4,
        )
        == "pass"
    )
    assert (
        judge_grants(
            runtime_select=True,
            runtime_create_schema=True,
            runtime_connect=True,
            runtime_owned_tables=0,
            migration_owned_tables=4,
        )
        == "fail"
    )
    assert judge_rls(_rls()) == "pass"
    unforced = _rls()
    unforced[0] = (unforced[0][0], True, False, 1)
    assert judge_rls(unforced) == "fail"
    assert judge_names(set(REQUIRED_CONSTRAINTS), REQUIRED_CONSTRAINTS) == "pass"
    assert judge_names(set(), REQUIRED_TRIGGERS) == "fail"
    assert judge_finance(0, 0, 0) == "pass"
    assert judge_finance(1, 0, 0) == "fail"
    assert judge_audit(2, 0) == "pass"
    assert judge_audit(0, 3) == "fail"
    assert judge_files(1, 0, 0) == "pass"
    assert judge_files(1, 1, 0) == "fail"
    pair = {("members", "0001_initial")}
    assert judge_migrations(pair, set(pair)) == "pass"
    assert judge_migrations(set(), set()) == "fail"
    assert (
        judge_isolation(
            gym_count=1,
            hidden_without_context=0,
            hidden_for_other_gym=0,
            visible_for_own_gym=1,
        )
        == "not_exercised"
    )
    assert (
        judge_isolation(
            gym_count=2,
            hidden_without_context=0,
            hidden_for_other_gym=0,
            visible_for_own_gym=1,
        )
        == "pass"
    )


def test_collect_is_read_only_and_keeps_live_evidence_unproven() -> None:
    cursor = ScriptedCursor(
        ones=[
            ("gym_platform", 180006, False),
            ("on",),
            (True, False, True, 0, 8),
            (0, 0, 0),
            (3, 1),
            (2, 0, 0, "abc"),
        ],
        many=[
            _roles(),
            _rls(),
            [(name,) for name in REQUIRED_CONSTRAINTS],
            [(name,) for name in REQUIRED_TRIGGERS],
            [("gym-a", "member-a")],
        ],
    )
    report = collect_restore_report(cursor)
    assert report["ok"] is False
    automated = report["automated"]
    assert isinstance(automated, dict)
    assert automated["recovery_complete"] == "pass"
    assert automated["missing_context_and_two_gym_isolation"] == "not_exercised"
    evidence = report["live_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["rpo_le_15_minutes"] == "not_proven"
    assert evidence["rto_le_4_hours"] == "not_proven"
    assert report["objects_downloaded"] is False
    assert report["business_data_mutated"] is False
    for sql in cursor.sql:
        verb = sql.lstrip().split(None, 1)[0].upper()
        assert verb in {"SELECT", "SET", "RESET"}


def test_two_gym_isolation_uses_runtime_role_without_writing() -> None:
    cursor = ScriptedCursor(
        ones=[
            ("gym_platform", 180006, False),
            ("on",),
            (True, False, True, 0, 8),
            (0, 0, 0),
            (3, 1),
            (2, 0, 0, "abc"),
            (0,),
            (0,),
            (1,),
        ],
        many=[
            _roles(),
            _rls(),
            [(name,) for name in REQUIRED_CONSTRAINTS],
            [(name,) for name in REQUIRED_TRIGGERS],
            [("gym-a", "member-a"), ("gym-b", "member-b")],
        ],
    )
    report = collect_restore_report(cursor)
    automated = report["automated"]
    assert isinstance(automated, dict)
    assert automated["missing_context_and_two_gym_isolation"] == "pass"
    assert report["ok"] is True
    assert any("SET LOCAL ROLE gym_runtime" in sql for sql in cursor.sql)
    assert all(
        sql.lstrip().split(None, 1)[0].upper() in {"SELECT", "SET", "RESET"} for sql in cursor.sql
    )


def _setup(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DJANGO_SETTINGS_MODULE": "config.settings.restore_verify",
        "DATABASE_ROLE_MODE": "RESTORE_VERIFY",
        "DJANGO_SECRET_KEY": "offline-restore-secret-not-a-live-value-0001",
        "RESTORE_TARGET": "disposable",
        "RESTORE_CONFIRMATION": "restore-disposable",
        "RESTORE_PROJECT": "gym-restore-rehearsal01",
        "RESTORE_DB_HOST": "postgres",
        "RESTORE_DB_USER": "gym_admin",
        "RESTORE_DB_PASSWORD": "offline-restore-db-password",
        "DB_NAME": "gym_platform",
    }
    clean.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=BACKEND,
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )


def test_restore_settings_start_without_provider_secrets() -> None:
    result = _setup({})
    assert result.returncode == 0, result.stderr


def test_restore_settings_reject_provider_secrets_and_production_targets() -> None:
    secret = _setup({"MINIO_SECRET_KEY": "application-object-secret"})
    assert secret.returncode != 0
    assert "MINIO_SECRET_KEY" in secret.stderr
    host = _setup({"RESTORE_DB_HOST": "127.0.0.1"})
    assert host.returncode != 0
    assert "postgres" in host.stderr
    project = _setup({"RESTORE_PROJECT": "gym-production"})
    assert project.returncode != 0
    assert "disposable project" in project.stderr
    user = _setup({"RESTORE_DB_USER": "gym_runtime"})
    assert user.returncode != 0
    assert "superuser" in user.stderr
