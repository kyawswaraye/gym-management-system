#!/usr/bin/env python3
"""Non-production filesystem backup and restore rehearsal.

This exercises pgBackRest bootstrap, differential backup, WAL archive, and
restore into a disposable volume. It does not run application verification,
upload WAL to an offsite provider, or prove RPO or RTO.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra" / "postgres"))

import backup_policy as policy  # noqa: E402


def _status_payload(stdout: str) -> dict[str, object]:
    text = stdout.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = json.loads(text.splitlines()[-1])
    if not isinstance(payload, dict):
        raise RuntimeError("backup status was not an object")
    return payload


PROJECT = "gym-backup-rehearsal"
COMPOSE = REPO / "infra" / "compose" / "docker-compose.backup-rehearsal.yml"
REPO_VOLUME = "gym-restore-repo-rehearsal01"
DATA_VOLUME = "gym-restore-data-rehearsal01"
RESTORE_PROJECT = "gym-restore-rehearsal01"
RESTORE_CONTAINER = "gym-restore-rehearsal01-db"
RESTORE_NETWORK = "gym-restore-rehearsal01-net"
SOURCE_CONTAINER = "gym-backup-rehearsal-postgres-1"


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(detail)
    return result


def _wait_health(timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                SOURCE_CONTAINER,
            ],
            check=False,
        )
        if result.stdout.strip() == "healthy":
            return
        time.sleep(2)
    logs = _run(["docker", "logs", "--tail", "80", SOURCE_CONTAINER], check=False)
    detail = (
        logs.stderr or logs.stdout or "rehearsal postgres did not become healthy"
    ).strip()
    raise RuntimeError(detail)


def _psql(sql: str, container: str = SOURCE_CONTAINER) -> str:
    result = _run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "--username=gym_admin",
            "--dbname=gym_platform",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            sql,
        ]
    )
    return result.stdout.strip()


def _write_secret(directory: Path, name: str, value: str) -> Path:
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.write(fd, value.encode("utf-8"))
    os.close(fd)
    return path


def _cleanup() -> None:
    _run(["docker", "rm", "-f", RESTORE_CONTAINER, SOURCE_CONTAINER], check=False)
    _run(
        ["docker", "network", "rm", RESTORE_NETWORK, "gym-backup-rehearsal_rehearsal"],
        check=False,
    )
    for volume in (
        "gym-backup-rehearsal_rehearsal_cluster",
        "gym-backup-rehearsal_rehearsal_socket",
        "gym-backup-rehearsal_rehearsal_spool",
        "gym-backup-rehearsal_rehearsal_log",
        DATA_VOLUME,
        REPO_VOLUME,
    ):
        disposable = volume.startswith("gym-backup-rehearsal_")
        named = policy.VOLUME_RE.fullmatch(volume) or policy.REPO_VOLUME_RE.fullmatch(
            volume
        )
        if disposable or named:
            _run(["docker", "volume", "rm", "-f", volume], check=False)


def main() -> int:
    if PROJECT == "gym-production":
        print("rehearsal project is production", file=sys.stderr)
        return 1
    image = os.environ.get("REHEARSAL_POSTGRES_IMAGE", "gym-postgres:rehearsal")
    if ":latest" in image:
        print("rehearsal image must not use latest", file=sys.stderr)
        return 1
    admin = secrets.token_hex(24)
    migration = secrets.token_hex(24)
    runtime = secrets.token_hex(24)
    cipher = secrets.token_urlsafe(32)
    _cleanup()
    shared = REPO / "secrets"
    shared.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="gym-backup-rehearsal-", dir=shared
        ) as temporary:
            secret_dir = Path(temporary)
            os.chmod(secret_dir, 0o700)
            cipher_file = _write_secret(secret_dir, "cipher", cipher)
            _run(["docker", "volume", "create", REPO_VOLUME])
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "chown",
                    "-v",
                    f"{REPO_VOLUME}:/var/lib/pgbackrest",
                    image,
                    "postgres:postgres",
                    "/var/lib/pgbackrest",
                ]
            )
            env = os.environ.copy()
            env.update(
                {
                    "REHEARSAL_POSTGRES_IMAGE": image,
                    "POSTGRES_DB": "gym_platform",
                    "POSTGRES_ADMIN_USER": "gym_admin",
                    "REHEARSAL_DB_PASSWORD": admin,
                    "REHEARSAL_MIGRATION_PASSWORD": migration,
                    "REHEARSAL_RUNTIME_PASSWORD": runtime,
                    "BACKUP_REPO_CIPHER_PASS_FILE": str(cipher_file),
                    "REHEARSAL_REPO_VOLUME": REPO_VOLUME,
                }
            )
            subprocess.run(
                ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), "up", "-d"],
                check=True,
                env=env,
                text=True,
            )
            _wait_health()
            _psql(
                "CREATE TABLE rehearsal_marker(id integer PRIMARY KEY, note text NOT NULL); "
                "INSERT INTO rehearsal_marker(id, note) "
                "VALUES (1, 'phase8-increment-3-before-full');"
            )
            bootstrapped = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    PROJECT,
                    "-f",
                    str(COMPOSE),
                    "exec",
                    "-T",
                    "-e",
                    "POSTGRES_PASSWORD=",
                    "postgres",
                    "gym-backup",
                    "bootstrap",
                ],
                check=False,
                env=env,
                text=True,
                capture_output=True,
            )
            if bootstrapped.returncode != 0:
                detail = (
                    bootstrapped.stderr.strip()
                    or bootstrapped.stdout.strip()
                    or "bootstrap failed"
                )
                raise RuntimeError(detail)
            boot_status = _status_payload(bootstrapped.stdout)
            if (
                boot_status.get("result") != "ok"
                or boot_status.get("rpo_proven") is not False
                or boot_status.get("rto_proven") is not False
            ):
                raise RuntimeError(
                    "bootstrap status is not an unproven ok rehearsal result"
                )
            _psql(
                "INSERT INTO rehearsal_marker(id, note) "
                "VALUES (2, 'phase8-increment-3-after-full'); SELECT pg_switch_wal();"
            )
            archived = False
            for _ in range(30):
                count = _psql("SELECT archived_count FROM pg_stat_archiver")
                if count.isdigit() and int(count) > 0:
                    archived = True
                    break
                time.sleep(2)
            if not archived:
                raise RuntimeError("WAL archive did not record a completed archive")
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    PROJECT,
                    "-f",
                    str(COMPOSE),
                    "exec",
                    "-T",
                    "-e",
                    "POSTGRES_PASSWORD=",
                    "postgres",
                    "gym-backup",
                    "diff",
                ],
                check=True,
                env=env,
                text=True,
            )
            checked = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    PROJECT,
                    "-f",
                    str(COMPOSE),
                    "exec",
                    "-T",
                    "-e",
                    "POSTGRES_PASSWORD=",
                    "postgres",
                    "gym-backup",
                    "check",
                ],
                check=False,
                env=env,
                text=True,
                capture_output=True,
            )
            if checked.returncode != 0:
                detail = (
                    checked.stderr.strip() or checked.stdout.strip() or "check failed"
                )
                raise RuntimeError(detail)
            status = _status_payload(checked.stdout)
            if status.get("result") != "ok" or status.get("rpo_proven") is not False:
                raise RuntimeError(
                    "freshness status is not an unproven ok rehearsal result"
                )
            request = {
                "RESTORE_CONFIRMATION": policy.CONFIRMATION,
                "RESTORE_TARGET": "disposable",
                "RESTORE_PROJECT": RESTORE_PROJECT,
                "RESTORE_VOLUME": DATA_VOLUME,
                "RESTORE_REPO_VOLUME": REPO_VOLUME,
                "RESTORE_STANZA": policy.STANZA,
                "RESTORE_REPOSITORY_ID": policy.REHEARSAL_REPOSITORY_ID,
                "BACKUP_REPO_MODE": policy.REHEARSAL_MODE,
                "BACKUP_REPO_PATH": "/var/lib/pgbackrest",
                "BACKUP_STANZA": policy.STANZA,
                "BACKUP_PGDATA": policy.RESTORE_PGDATA,
            }
            policy.validate_restore_request(request)
            _run(["docker", "volume", "create", DATA_VOLUME])
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "chown",
                    "-v",
                    f"{DATA_VOLUME}:/var/lib/gym-restore",
                    image,
                    "postgres:postgres",
                    "/var/lib/gym-restore",
                ]
            )
            _run(["docker", "network", "create", "--internal", RESTORE_NETWORK])
            restore_env = secret_dir / "restore.env"
            restore_env.write_text(
                "\n".join(
                    [
                        "BACKUP_REPO_MODE=rehearsal-posix",
                        "BACKUP_REPO_PATH=/var/lib/pgbackrest",
                        "BACKUP_STANZA=gym",
                        "BACKUP_PG_USER=gym_admin",
                        "BACKUP_PG_DATABASE=gym_platform",
                        "BACKUP_PGDATA=/var/lib/gym-restore",
                        "BACKUP_TIMEOUT_SECONDS=600",
                        "BACKUP_ARCHIVE_PUSH_TIMEOUT_SECONDS=60",
                        "PGBACKREST_CONFIG=/run/pgbackrest/pgbackrest.conf",
                        "RESTORE_CONFIRMATION=restore-disposable",
                        "RESTORE_TARGET=disposable",
                        f"RESTORE_PROJECT={RESTORE_PROJECT}",
                        f"RESTORE_VOLUME={DATA_VOLUME}",
                        f"RESTORE_REPO_VOLUME={REPO_VOLUME}",
                        "RESTORE_STANZA=gym",
                        "RESTORE_REPOSITORY_ID=posix:rehearsal",
                        "PGDATA=/var/lib/gym-restore",
                        "POSTGRES_DB=gym_platform",
                        "POSTGRES_USER=gym_admin",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(restore_env, 0o600)
            server_env = secret_dir / "server.env"
            server_env.write_text(
                "\n".join(
                    [
                        "PGDATA=/var/lib/gym-restore",
                        "POSTGRES_DB=gym_platform",
                        "POSTGRES_USER=gym_admin",
                        f"POSTGRES_PASSWORD={admin}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(server_env, 0o600)
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--env-file",
                    str(restore_env),
                    "--read-only",
                    "--tmpfs",
                    "/tmp",
                    "--tmpfs",
                    "/run/pgbackrest:uid=999,gid=999,mode=0700",
                    "--tmpfs",
                    "/var/spool/pgbackrest:uid=999,gid=999,mode=0700",
                    "--tmpfs",
                    "/var/log/pgbackrest:uid=999,gid=999,mode=0700",
                    "--tmpfs",
                    "/var/run/postgresql",
                    "-v",
                    f"{DATA_VOLUME}:/var/lib/gym-restore",
                    "-v",
                    f"{REPO_VOLUME}:/var/lib/pgbackrest:ro",
                    "-v",
                    f"{cipher_file}:/run/secrets/{policy.CIPHER_SECRET}:ro",
                    image,
                    "gym-backup",
                    "restore",
                ]
            )
            _run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    RESTORE_CONTAINER,
                    "--network",
                    RESTORE_NETWORK,
                    "--env-file",
                    str(server_env),
                    "--entrypoint",
                    policy.OFFICIAL_POSTGRES_ENTRYPOINT,
                    "--read-only",
                    "--tmpfs",
                    "/tmp",
                    "--tmpfs",
                    "/var/run/postgresql",
                    "-v",
                    f"{DATA_VOLUME}:/var/lib/gym-restore",
                    image,
                    "postgres",
                    "-c",
                    "archive_mode=off",
                    "-c",
                    "max_connections=20",
                ]
            )
            ready = False
            for _ in range(60):
                result = _run(
                    [
                        "docker",
                        "exec",
                        RESTORE_CONTAINER,
                        "pg_isready",
                        "-U",
                        "gym_admin",
                        "-d",
                        "gym_platform",
                    ],
                    check=False,
                )
                if result.returncode == 0:
                    ready = True
                    break
                time.sleep(2)
            if not ready:
                raise RuntimeError("restored postgres did not become ready")
            notes = _psql(
                "SELECT note FROM rehearsal_marker ORDER BY id", RESTORE_CONTAINER
            )
            expected = "phase8-increment-3-before-full\nphase8-increment-3-after-full"
            if notes != expected:
                raise RuntimeError("restored marker rows did not match")
        print(
            "filesystem rehearsal restored the marker rows. "
            "application_verification: not_run. offsite: false. "
            "rpo_proven: false. rto_proven: false."
        )
        return 0
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        logs = _run(["docker", "logs", "--tail", "120", SOURCE_CONTAINER], check=False)
        if logs.stdout or logs.stderr:
            print(logs.stdout or logs.stderr, file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        _cleanup()


if __name__ == "__main__":
    sys.exit(main())
