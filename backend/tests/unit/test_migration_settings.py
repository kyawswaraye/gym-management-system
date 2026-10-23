from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _setup(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DJANGO_SETTINGS_MODULE": "config.settings.migration",
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


def test_migration_settings_start_without_provider_secrets() -> None:
    result = _setup(
        {
            "DATABASE_ROLE_MODE": "MIGRATION",
            "DJANGO_SECRET_KEY": "offline-migration-secret-not-a-live-value-0001",
            "MIGRATION_DB_USER": "gym_migration",
            "MIGRATION_DB_PASSWORD": "offline-migration-db-password",
            "DB_NAME": "gym_platform",
            "DB_HOST": "127.0.0.1",
        }
    )
    assert result.returncode == 0, result.stderr


def test_migration_settings_reject_runtime_mode() -> None:
    result = _setup(
        {
            "DATABASE_ROLE_MODE": "runtime",
            "DJANGO_SECRET_KEY": "offline-migration-secret-not-a-live-value-0001",
            "MIGRATION_DB_USER": "gym_migration",
            "MIGRATION_DB_PASSWORD": "offline-migration-db-password",
        }
    )
    assert result.returncode != 0
    assert "DATABASE_ROLE_MODE=MIGRATION" in result.stderr


def test_migration_settings_reject_missing_identity() -> None:
    result = _setup(
        {
            "DATABASE_ROLE_MODE": "MIGRATION",
            "DJANGO_SECRET_KEY": "offline-migration-secret-not-a-live-value-0001",
        }
    )
    assert result.returncode != 0
    assert "gym_migration" in result.stderr
