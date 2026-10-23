#!/usr/bin/env python3
"""Create gym_monitor on a database that already has a data volume.

docker-entrypoint-initdb.d does not run again for an existing volume. This
command is the explicit admin bootstrap. It does not reuse the admin,
migration, or runtime password. Without --apply it changes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIRMATION = "monitor-role-bootstrap"
PLACEHOLDER = "replace-from-secret-store"
ROLE = "gym_monitor"
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}\Z")


class MonitorBootstrapError(RuntimeError):
    """The monitor role request failed a closed check."""


def _password(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MonitorBootstrapError(f"{label} file is not readable") from exc
    if value.endswith("\n"):
        value = value[:-1]
    if value == "" or value != value.strip() or "\n" in value or "\r" in value:
        raise MonitorBootstrapError(f"{label} must be one non-empty line")
    if value == PLACEHOLDER:
        raise MonitorBootstrapError(f"{label} placeholder is refused")
    return value


def validate_monitor_bootstrap(
    *,
    admin_password_file: Path,
    monitor_password_file: Path,
    migration_password_file: Path | None = None,
    runtime_password_file: Path | None = None,
) -> str:
    if admin_password_file.resolve() == monitor_password_file.resolve():
        raise MonitorBootstrapError("monitor password file must not be the admin file")
    admin = _password(admin_password_file, "admin password")
    monitor = _password(monitor_password_file, "monitor password")
    if monitor == admin:
        raise MonitorBootstrapError("monitor password must differ from the admin password")
    for path, label in (
        (migration_password_file, "migration password"),
        (runtime_password_file, "runtime password"),
    ):
        if path is None:
            continue
        if path.resolve() == monitor_password_file.resolve():
            raise MonitorBootstrapError(f"monitor password file must not be the {label} file")
        other = _password(path, label)
        if monitor == other:
            raise MonitorBootstrapError(f"monitor password must differ from the {label}")
    return monitor


def monitor_role_sql(monitor_password: str) -> str:
    if any(char in monitor_password for char in ("\x00", "\n", "\r")):
        raise MonitorBootstrapError("monitor password must be one line")
    literal = "'" + monitor_password.replace("'", "''") + "'"
    return f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;
ALTER ROLE {ROLE} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  PASSWORD {literal};
GRANT CONNECT ON DATABASE :"db_name" TO {ROLE};
GRANT pg_monitor TO {ROLE};
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-password-file", type=Path, required=True)
    parser.add_argument("--monitor-password-file", type=Path, required=True)
    parser.add_argument("--migration-password-file", type=Path)
    parser.add_argument("--runtime-password-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="5432")
    parser.add_argument("--username", default="gym_admin")
    parser.add_argument("--database", default="gym_platform")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args(argv)
    try:
        monitor = validate_monitor_bootstrap(
            admin_password_file=args.admin_password_file,
            monitor_password_file=args.monitor_password_file,
            migration_password_file=args.migration_password_file,
            runtime_password_file=args.runtime_password_file,
        )
        sql = monitor_role_sql(monitor)
    except MonitorBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if "SUPERUSER" in sql and "NOSUPERUSER" not in sql:
        print("monitor role SQL drifted", file=sys.stderr)
        return 1
    if not _NAME.fullmatch(args.database) or not _NAME.fullmatch(args.username):
        print("database and username must be simple identifiers", file=sys.stderr)
        return 1
    if not _HOST.fullmatch(args.host) or not args.port.isdigit():
        print("host or port is refused", file=sys.stderr)
        return 1
    if not args.apply:
        print("monitor role bootstrap validated; existing volumes stay unchanged")
        print("pass --apply and confirmation monitor-role-bootstrap to create the role")
        return 0
    if args.confirmation != CONFIRMATION:
        print("confirmation must be monitor-role-bootstrap", file=sys.stderr)
        return 2
    admin = args.admin_password_file.read_text(encoding="utf-8").removesuffix("\n")
    env = os.environ.copy()
    env["PGPASSWORD"] = admin
    result = subprocess.run(
        [
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "--set",
            f"db_name={args.database}",
            "-h",
            args.host,
            "-p",
            args.port,
            "-U",
            args.username,
            "-d",
            args.database,
        ],
        input=sql,
        text=True,
        env=env,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("monitor role bootstrap failed", file=sys.stderr)
        return result.returncode
    print("gym_monitor bootstrap applied; this is not a live monitoring proof")
    return 0


if __name__ == "__main__":
    sys.exit(main())
