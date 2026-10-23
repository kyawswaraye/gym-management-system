"""pgBackRest policy for the Phase 8 increment 3 repository baseline.

The production repository is encrypted S3-compatible storage. A filesystem
repository is allowed only for the labeled non-production rehearsal. This
module does not contact a backup provider and does not prove RPO or RTO.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

PGBACKREST_VERSION = "2.60.0"
PGBACKREST_VERSION_LINE = "pgBackRest 2.60.0"
PGBACKREST_SHA256 = "6d3ee1b5defdadc85228969b4be804096f5e65e54707bb95c7c4c4ce41f5d007"
PGBACKREST_URL = (
    "https://github.com/pgbackrest/pgbackrest/releases/download/"
    "release/2.60.0/pgbackrest-2.60.0.tar.gz"
)
POSTGRES_BASE = "postgres:18.6"
STANZA = "gym"
CIPHER = "aes-256-cbc"
PRODUCTION_PGDATA = "/var/lib/postgresql/18/docker"
RESTORE_PGDATA = "/var/lib/gym-restore"
CONFIG_PATH = "/run/pgbackrest/pgbackrest.conf"
LOCK_PATH = "/var/spool/pgbackrest/gym-backup.lock"
SPOOL_PATH = "/var/spool/pgbackrest"
LOG_PATH = "/var/log/pgbackrest"
SOCKET_PATH = "/var/run/postgresql"
SECRET_DIR = "/run/secrets"
CIPHER_SECRET = "backup_repo_cipher_pass"
ACCESS_SECRET = "backup_s3_access_key"
SECRET_KEY_SECRET = "backup_s3_secret_key"
MONITOR_DB_SECRET = "monitor_db_password"
METRICS_FILENAME = "backup.prom"
PLACEHOLDER = "replace-from-secret-store"
CONFIRMATION = "restore-disposable"
PRODUCTION_PROJECT = "gym-production"
PRODUCTION_VOLUME = "postgres_data"
REHEARSAL_MODE = "rehearsal-posix"
REHEARSAL_REPOSITORY_ID = "posix:rehearsal"
LOCAL_S3_MODE = "rehearsal-s3-local"
LOCAL_S3_PROJECT = "gym-staging-rehearsal"
LOCAL_S3_ENDPOINT = "https://object-backup:9000"
LOCAL_S3_CA = "/run/secrets/staging_object_ca"
RETENTION_FULL = 14
RETENTION_DIFF = 14
RETENTION_ARCHIVE = 14
ARCHIVE_PUSH_MAX_SECONDS = 120
FULL_MAX_SECONDS = 21600
DIFF_MAX_SECONDS = 14400
CHECK_MAX_SECONDS = 3600
BUSY_EXIT = 75
STATUS_SCHEMA = "gym-backup-status/v1"
PROJECT_RE = re.compile(r"gym-restore-[a-z0-9]{8,32}\Z")
NONCE_RE = re.compile(r"[a-f0-9]{32}\Z")
OFFICIAL_POSTGRES_ENTRYPOINT = "/usr/local/bin/docker-entrypoint.sh"
VOLUME_RE = re.compile(r"gym-restore-data-[a-z0-9]{8,32}\Z")
REPO_VOLUME_RE = re.compile(r"gym-restore-repo-[a-z0-9]{8,32}\Z")
USER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
REGION_RE = re.compile(r"[a-z0-9-]{1,64}\Z")
WAL_NAME_RE = re.compile(r"[0-9A-F]{24}(?:\.[0-9A-F]{8}\.backup)?\Z")
FORBIDDEN_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "minio", "silo", "pgsty"}
)
RUNTIME_ROLES = frozenset({"gym_runtime", "gym_migration", "gym_auth_bootstrap"})
APP_SECRET_VARIABLES = frozenset(
    {
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_SESSION_TOKEN",
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "MIGRATION_DB_PASSWORD",
        "RUNTIME_DB_PASSWORD",
        "DJANGO_SECRET_KEY",
        "JWT_PRIVATE_KEY",
        "JWT_PUBLIC_KEYS",
        "REFRESH_TOKEN_PEPPER",
        "MFA_TOKEN_PEPPER",
        "MFA_ENCRYPTION_KEY",
        "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY",
        "INVITATION_TOKEN_PEPPER",
        "PASSWORD_RESET_TOKEN_PEPPER",
        "BFF_SHARED_SECRET",
        "RESEND_API_KEY",
        "FCM_SERVICE_ACCOUNT_JSON",
        "PUSH_TOKEN_ENCRYPTION_KEY",
        "PUSH_TOKEN_FINGERPRINT_PEPPER",
    }
)
FORBIDDEN_PROCESS_ENV = APP_SECRET_VARIABLES | frozenset(
    {"BACKUP_REPO_CIPHER_PASS", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY"}
)
DATABASE_PROCESS_ENV = frozenset(
    {
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "MIGRATION_DB_PASSWORD",
        "RUNTIME_DB_PASSWORD",
    }
)
BACKUP_SERVICES = ("backup-bootstrap", "backup-full", "backup-diff", "backup-check")
BACKUP_SERVICE_ENV = frozenset(
    {
        "PGBACKREST_CONFIG",
        "BACKUP_OPERATION",
        "BACKUP_TIMEOUT_SECONDS",
        "BACKUP_MAX_AGE_SECONDS",
        "BACKUP_WAL_MAX_AGE_SECONDS",
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_REGION",
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_URI_STYLE",
        "BACKUP_REPO_PATH",
        "BACKUP_STANZA",
        "BACKUP_PG_USER",
        "BACKUP_PG_DATABASE",
        "BACKUP_METRICS_PATH",
        "PGDATA",
    }
)
POSTGRES_BACKUP_ENV = frozenset(
    {
        "PGBACKREST_CONFIG",
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_REGION",
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_URI_STYLE",
        "BACKUP_REPO_PATH",
        "BACKUP_STANZA",
        "BACKUP_PG_USER",
        "BACKUP_PG_DATABASE",
        "BACKUP_ARCHIVE_PUSH_TIMEOUT_SECONDS",
        "PGDATA",
    }
)
CLAIM_RE = re.compile(
    r"RPO\s*(?:<=|≤)\s*15[^\n]{0,80}\b(?:met|proven|achieved|satisfied|demonstrated)\b"
    r"|\bRTO\s*(?:<=|≤)\s*4[^\n]{0,80}\b(?:met|proven|achieved|satisfied|demonstrated)\b"
    r"|" + "backup freshness is " + "within policy"
    r"|\brpo_proven[\"']?\s*[:=]\s*(?:true|True|yes)\b"
    r"|\brto_proven[\"']?\s*[:=]\s*(?:true|True|yes)\b",
    re.IGNORECASE,
)
VARIABLE_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::\?[^}]*)?\}")
Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]


class BackupPolicyError(RuntimeError):
    """A backup or restore request failed a closed check."""


def _quote(value: str) -> str:
    if value == "" or any(
        character.isspace() or character in "#'\"\\" for character in value
    ):
        raise BackupPolicyError("backup configuration value cannot be encoded safely")
    return value


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if value.strip() == "":
        raise BackupPolicyError(f"{name} is required")
    return value.strip()


def _reject_forbidden_environment(
    env: Mapping[str, str], *, database_passwords: bool = False
) -> None:
    allowed = DATABASE_PROCESS_ENV if database_passwords else frozenset()
    present = sorted(
        name for name in FORBIDDEN_PROCESS_ENV - allowed if env.get(name, "") != ""
    )
    if present:
        raise BackupPolicyError(
            "backup process received application or inline backup secrets: "
            + ", ".join(present)
        )
    for name in (
        "BACKUP_RETENTION_FULL",
        "BACKUP_RETENTION_DIFF",
        "BACKUP_RETENTION_ARCHIVE",
        "BACKUP_CIPHER_TYPE",
    ):
        if name in env:
            raise BackupPolicyError(f"{name} cannot override the pinned backup policy")


def _secret_value(value: str, label: str) -> str:
    cleaned = value.strip("\n")
    if cleaned != value.strip() or cleaned == "" or "\n" in cleaned or "\r" in cleaned:
        raise BackupPolicyError(f"{label} must be one non-empty line")
    if cleaned == PLACEHOLDER:
        raise BackupPolicyError(f"{label} placeholder is refused")
    if any(character in "'\\#" or character.isspace() for character in cleaned):
        raise BackupPolicyError(f"{label} is not an acceptable dedicated secret")
    if len(cleaned) > 256 or len(cleaned) < 16:
        raise BackupPolicyError(f"{label} is not an acceptable dedicated secret")
    return cleaned


def _repository_mode(env: Mapping[str, str]) -> str:
    mode = env.get("BACKUP_REPO_MODE", "").strip()
    if mode == "":
        return "s3"
    if mode == REHEARSAL_MODE:
        return REHEARSAL_MODE
    if mode == LOCAL_S3_MODE:
        if env.get("COMPOSE_PROJECT_NAME", "") != LOCAL_S3_PROJECT:
            raise BackupPolicyError(
                "local S3 rehearsal mode requires the disposable staging project"
            )
        if env.get("BACKUP_S3_ENDPOINT", "") != LOCAL_S3_ENDPOINT:
            raise BackupPolicyError(
                "local S3 rehearsal endpoint is not the private staging service"
            )
        return LOCAL_S3_MODE
    raise BackupPolicyError(
        "backup repository mode is not production S3 or a labeled rehearsal"
    )


def _pgdata(env: Mapping[str, str]) -> str:
    target = env.get("RESTORE_TARGET", "").strip()
    path = env.get("BACKUP_PGDATA", "").strip() or PRODUCTION_PGDATA
    if target == "":
        if path != PRODUCTION_PGDATA:
            raise BackupPolicyError("production pgBackRest data directory is pinned")
        return path
    if target != "disposable" or path != RESTORE_PGDATA:
        raise BackupPolicyError(
            "restore pgBackRest data directory must be the disposable path"
        )
    return path


def _database_user(env: Mapping[str, str]) -> str:
    user = _require(env, "BACKUP_PG_USER")
    if USER_RE.fullmatch(user) is None or user in RUNTIME_ROLES:
        raise BackupPolicyError(
            "backup database user must be the dedicated superuser name"
        )
    return user


def parse_s3_endpoint(value: str) -> tuple[str, str]:
    if not value.startswith("https://") or "\\" in value:
        raise BackupPolicyError("backup endpoint must be an https URL")
    remainder = value[len("https://") :]
    if (
        remainder == ""
        or "/" in remainder
        or "?" in remainder
        or "#" in remainder
        or "@" in remainder
    ):
        raise BackupPolicyError("backup endpoint must be an https origin")
    host, separator, port = remainder.partition(":")
    host = host.lower().strip("[]")
    if separator == "":
        port = "443"
    if (
        host == ""
        or host in FORBIDDEN_HOSTS
        or host.endswith(".local")
        or host.endswith(".localhost")
        or port == ""
        or not port.isdigit()
        or not 1 <= int(port) <= 65535
    ):
        raise BackupPolicyError("backup endpoint host is not an offsite S3 origin")
    return host, port


def _reject_unresolved_repository(env: Mapping[str, str]) -> None:
    """Refuse template placeholders before any backup tool runs."""

    if _repository_mode(env) != "s3":
        return
    for name in (
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_REGION",
        "BACKUP_S3_BUCKET",
        "BACKUP_REPO_PATH",
    ):
        value = _require(env, name).lower()
        if ".example" in value or "replace-with-" in value or value == PLACEHOLDER:
            raise BackupPolicyError(f"{name} is an unresolved placeholder")


def repository_identity(env: Mapping[str, str]) -> str:
    mode = _repository_mode(env)
    if mode == REHEARSAL_MODE:
        return REHEARSAL_REPOSITORY_ID
    _reject_unresolved_repository(env)
    host, port = parse_s3_endpoint(_require(env, "BACKUP_S3_ENDPOINT"))
    region = _require(env, "BACKUP_S3_REGION")
    bucket = _require(env, "BACKUP_S3_BUCKET")
    style = _require(env, "BACKUP_S3_URI_STYLE")
    path = _require(env, "BACKUP_REPO_PATH")
    if REGION_RE.fullmatch(region) is None or BUCKET_RE.fullmatch(bucket) is None:
        raise BackupPolicyError("backup region or bucket is invalid")
    if style not in {"host", "path"}:
        raise BackupPolicyError("backup S3 URI style must be host or path")
    if not path.startswith("/") or ".." in path.split("/") or "'" in path:
        raise BackupPolicyError("backup repository path is invalid")
    return f"s3://{host}:{port}/{region}/{bucket}/{style}{path}"


def render_pgbackrest_config(
    env: Mapping[str, str],
    *,
    cipher_pass: str,
    access_key: str = "",
    secret_key: str = "",
    database_passwords: bool = False,
) -> str:
    """Return a root-only pgBackRest config. The caller must not log it."""

    _reject_forbidden_environment(env, database_passwords=database_passwords)
    _reject_unresolved_repository(env)
    if _require(env, "BACKUP_STANZA") != STANZA:
        raise BackupPolicyError("backup stanza must be gym")
    mode = _repository_mode(env)
    cipher = _secret_value(cipher_pass, "backup repository cipher passphrase")
    user = _database_user(env)
    database = _require(env, "BACKUP_PG_DATABASE")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database):
        raise BackupPolicyError("backup database name is invalid")
    path = _require(env, "BACKUP_REPO_PATH")
    if not path.startswith("/") or ".." in path.split("/") or "'" in path:
        raise BackupPolicyError("backup repository path is invalid")
    lines = [
        "# Provisional retention pending legal approval. Not an approved retention policy.",
        "[global]",
        f"repo1-cipher-type={CIPHER}",
        f"repo1-cipher-pass={_quote(cipher)}",
        f"repo1-path={_quote(path)}",
        f"repo1-retention-full={RETENTION_FULL}",
        f"repo1-retention-diff={RETENTION_DIFF}",
        "repo1-retention-archive-type=full",
        f"repo1-retention-archive={RETENTION_ARCHIVE}",
        f"spool-path={SPOOL_PATH}",
        f"log-path={LOG_PATH}",
        f"lock-path={SPOOL_PATH}",
        "log-level-console=warn",
        "log-level-file=info",
    ]
    if mode in {"s3", LOCAL_S3_MODE}:
        host, port = parse_s3_endpoint(_require(env, "BACKUP_S3_ENDPOINT"))
        region = _require(env, "BACKUP_S3_REGION")
        bucket = _require(env, "BACKUP_S3_BUCKET")
        style = _require(env, "BACKUP_S3_URI_STYLE")
        if REGION_RE.fullmatch(region) is None or BUCKET_RE.fullmatch(bucket) is None:
            raise BackupPolicyError("backup region or bucket is invalid")
        if style not in {"host", "path"}:
            raise BackupPolicyError("backup S3 URI style must be host or path")
        key = _secret_value(access_key, "backup storage access key")
        secret = _secret_value(secret_key, "backup storage secret key")
        lines.extend(
            [
                "repo1-type=s3",
                f"repo1-s3-endpoint={_quote(host)}",
                f"repo1-s3-port={port}",
                f"repo1-s3-region={_quote(region)}",
                f"repo1-s3-bucket={_quote(bucket)}",
                f"repo1-s3-uri-style={style}",
                "repo1-storage-verify-tls=y",
                f"repo1-s3-key={_quote(key)}",
                f"repo1-s3-key-secret={_quote(secret)}",
            ]
        )
        if mode == LOCAL_S3_MODE:
            lines.append(f"repo1-storage-ca-file={_quote(LOCAL_S3_CA)}")
    else:
        if access_key.strip() != "" or secret_key.strip() != "":
            raise BackupPolicyError(
                "filesystem rehearsal must not receive storage keys"
            )
        lines.append("repo1-type=posix")
    lines.extend(
        [
            "",
            f"[{STANZA}]",
            f"pg1-path={_quote(_pgdata(env))}",
            f"pg1-socket-path={SOCKET_PATH}",
            "pg1-port=5432",
            f"pg1-user={_quote(user)}",
            f"pg1-database={_quote(database)}",
            "",
        ]
    )
    return "\n".join(lines)


def _read_secret_file(path: Path, label: str, *, required: bool) -> str:
    if path.is_symlink() or not path.is_file():
        if required:
            raise BackupPolicyError(f"{label} file is missing")
        return ""
    if path.stat().st_size > 4096:
        raise BackupPolicyError(f"{label} file is too large")
    return path.read_text(encoding="utf-8")


def _require_local_ca(path: Path) -> None:
    """Require the staging CA file without copying it into the config text."""

    if path.is_symlink() or not path.is_file():
        raise BackupPolicyError("local staging object CA is missing")
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.stat().st_size == 0 or path.stat().st_size > 16384 or mode & 0o002:
        raise BackupPolicyError("local staging object CA is unsafe")


def write_config_file(directory: Path, text: str) -> Path:
    if directory.is_symlink() or not directory.is_dir():
        raise BackupPolicyError("pgBackRest config directory is unsafe")
    target = directory / "pgbackrest.conf"
    if target.is_symlink():
        raise BackupPolicyError("pgBackRest config path is unsafe")
    temporary = directory / f".pgbackrest.conf.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    mode = stat.S_IMODE(target.stat().st_mode)
    if mode != 0o600:
        raise BackupPolicyError("pgBackRest config mode is not private")
    return target


def load_process_config(secret_dir: Path = Path(SECRET_DIR)) -> str:
    env = os.environ
    mode = _repository_mode(env)
    cipher = _read_secret_file(
        secret_dir / CIPHER_SECRET,
        "backup repository cipher passphrase",
        required=True,
    )
    access = ""
    secret = ""
    if mode in {"s3", LOCAL_S3_MODE}:
        access = _read_secret_file(
            secret_dir / ACCESS_SECRET, "backup storage access key", required=True
        )
        secret = _read_secret_file(
            secret_dir / SECRET_KEY_SECRET, "backup storage secret key", required=True
        )
    if mode == LOCAL_S3_MODE:
        _require_local_ca(secret_dir / "staging_object_ca")
    return render_pgbackrest_config(
        env,
        cipher_pass=cipher,
        access_key=access,
        secret_key=secret,
        database_passwords=True,
    )


def _default_runner(
    command: Sequence[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(command),
        start_new_session=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, 9)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        raise BackupPolicyError("backup command timed out") from None
    return subprocess.CompletedProcess(
        list(command), process.returncode or 0, stdout, stderr
    )


def _redact(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if len(secret) >= 8:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _control_server(command: list[str], timeout: int, runner: Runner) -> None:
    """Start or stop PostgreSQL without waiting on file descriptors it inherits."""

    if runner is not _default_runner:
        _run_checked(command, timeout, runner, ())
        return
    process = subprocess.Popen(
        command,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, 9)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise BackupPolicyError("postgres control command timed out") from exc
        raise BackupPolicyError("postgres control command timed out") from None
    if code != 0:
        raise BackupPolicyError("postgres control command failed")


def _run_checked(
    command: Sequence[str],
    timeout: int,
    runner: Runner,
    secrets: Sequence[str],
) -> str:
    if any(item.startswith("--") and "pass" in item for item in command):
        raise BackupPolicyError("backup secrets must not be command arguments")
    result = runner(command, timeout)
    if result.returncode != 0:
        detail = _redact(
            (result.stderr or result.stdout or "command failed").strip(), secrets
        )
        raise BackupPolicyError(detail or "backup command failed")
    return result.stdout


def _pgbackrest(
    config: str, timeout: int, runner: Runner, secrets: Sequence[str], *args: str
) -> str:
    return _run_checked(
        ["pgbackrest", "--config", config, "--stanza", STANZA, *args],
        timeout,
        runner,
        secrets,
    )


def _lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise BackupPolicyError("backup lock is busy") from exc
    return fd


def _timeout(env: Mapping[str, str], ceiling: int) -> int:
    raw = _require(env, "BACKUP_TIMEOUT_SECONDS")
    if not raw.isdigit():
        raise BackupPolicyError("backup timeout must be an integer")
    value = int(raw)
    if value < 1 or value > ceiling:
        raise BackupPolicyError("backup timeout is outside the bounded range")
    return value


def _positive(env: Mapping[str, str], name: str) -> int:
    raw = _require(env, name)
    if not raw.isdigit() or int(raw) < 1:
        raise BackupPolicyError(f"{name} must be a positive integer")
    return int(raw)


def _stanza_ready(stdout: str) -> bool:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        return False
    status = payload[0].get("status")
    code = status.get("code") if isinstance(status, dict) else None
    databases = payload[0].get("db")
    return code == 0 and isinstance(databases, list) and len(databases) > 0


def _stanza_info(
    config: str, timeout: int, runner: Runner
) -> subprocess.CompletedProcess[str]:
    return runner(
        [
            "pgbackrest",
            "--config",
            config,
            "--stanza",
            STANZA,
            "info",
            "--output",
            "json",
        ],
        min(timeout, 120),
    )


def _require_existing_stanza(config: str, timeout: int, runner: Runner) -> None:
    info = _stanza_info(config, timeout, runner)
    if info.returncode != 0 or not _stanza_ready(info.stdout):
        raise BackupPolicyError("stanza is missing; run gym-backup bootstrap")


def run_backup(kind: str, *, runner: Runner | None = None, lock: bool = True) -> None:
    if kind not in {"full", "diff"}:
        raise BackupPolicyError("backup kind must be full or diff")
    _reject_forbidden_environment(os.environ)
    _reject_unresolved_repository(os.environ)
    env = os.environ
    timeout = _timeout(env, FULL_MAX_SECONDS if kind == "full" else DIFF_MAX_SECONDS)
    config = env.get("PGBACKREST_CONFIG", CONFIG_PATH)
    active = _lock(Path(LOCK_PATH)) if lock else -1
    try:
        chosen = runner or _default_runner
        _require_existing_stanza(config, timeout, chosen)
        _pgbackrest(config, timeout, chosen, (), "backup", "--type", kind)
    finally:
        if active >= 0:
            os.close(active)


def run_bootstrap(
    *, runner: Runner | None = None, now: int | None = None, lock: bool = True
) -> dict[str, Any]:
    """Create the stanza, take the first full backup, and validate WAL."""

    _reject_forbidden_environment(os.environ)
    _reject_unresolved_repository(os.environ)
    env = os.environ
    timeout = _timeout(env, FULL_MAX_SECONDS)
    config = env.get("PGBACKREST_CONFIG", CONFIG_PATH)
    active = _lock(Path(LOCK_PATH)) if lock else -1
    try:
        chosen = runner or _default_runner
        info = _stanza_info(config, timeout, chosen)
        if info.returncode != 0 or not _stanza_ready(info.stdout):
            _pgbackrest(config, min(timeout, 300), chosen, (), "stanza-create")
        _pgbackrest(config, timeout, chosen, (), "backup", "--type", "full")
        previous = os.environ.get("BACKUP_TIMEOUT_SECONDS")
        os.environ["BACKUP_TIMEOUT_SECONDS"] = str(min(timeout, CHECK_MAX_SECONDS))
        try:
            return run_check(runner=chosen, now=now, lock=False)
        finally:
            if previous is None:
                os.environ.pop("BACKUP_TIMEOUT_SECONDS", None)
            else:
                os.environ["BACKUP_TIMEOUT_SECONDS"] = previous
    finally:
        if active >= 0:
            os.close(active)


def _archiver_sql() -> str:
    return (
        "SELECT json_build_object("
        "'archive_mode', current_setting('archive_mode'),"
        "'last_archived_epoch', EXTRACT(EPOCH FROM last_archived_time),"
        "'last_failed_epoch', EXTRACT(EPOCH FROM last_failed_time),"
        "'archived_count', archived_count,"
        "'failed_count', failed_count,"
        "'last_archived_wal', last_archived_wal,"
        "'last_failed_wal', last_failed_wal)"
        " FROM pg_stat_archiver"
    )


def read_archiver(database: str, runner: Runner, timeout: int) -> dict[str, Any]:
    user = _database_user(os.environ)
    stdout = _run_checked(
        [
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "-h",
            SOCKET_PATH,
            "--dbname",
            database,
            "-At",
            "-c",
            _archiver_sql(),
        ],
        timeout,
        runner,
        (),
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BackupPolicyError("archiver status was not JSON") from exc
    if not isinstance(payload, dict):
        raise BackupPolicyError("archiver status was not an object")
    return payload


def _epoch(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise BackupPolicyError("archiver timestamp is invalid")
    if isinstance(value, (int, float)):
        return int(value)
    raise BackupPolicyError("archiver timestamp is invalid")


def evaluate_freshness(
    *,
    info: object,
    archiver: Mapping[str, Any],
    now: int,
    max_backup_age: int,
    max_wal_age: int,
    version_line: str,
    repo_mode: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if version_line != PGBACKREST_VERSION_LINE:
        errors.append("backup tool version drifted")
    if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict):
        errors.append("backup info is missing")
        stanza: dict[str, Any] = {}
    else:
        stanza = info[0]
    if stanza.get("name") not in {None, STANZA}:
        errors.append("backup stanza does not match gym")
    cipher = stanza.get("cipher")
    if cipher != CIPHER:
        errors.append("backup repository is not aes-256-cbc")
    backups = stanza.get("backup")
    chosen: dict[str, Any] | None = None
    if isinstance(backups, list):
        for item in backups:
            if not isinstance(item, dict) or item.get("error") is True:
                continue
            stamp = item.get("timestamp")
            stop = stamp.get("stop") if isinstance(stamp, dict) else None
            if isinstance(stop, int) and (chosen is None or stop > chosen["stop"]):
                chosen = {
                    "stop": stop,
                    "type": item.get("type"),
                    "label": item.get("label"),
                }
    backup_age = None if chosen is None else now - int(chosen["stop"])
    if chosen is None:
        errors.append("backup is missing")
    elif backup_age is None or backup_age < 0 or backup_age > max_backup_age:
        errors.append("backup is stale")
    if archiver.get("archive_mode") != "on":
        errors.append("archive_mode is disabled")
    archived = _epoch(archiver.get("last_archived_epoch"))
    failed = _epoch(archiver.get("last_failed_epoch"))
    archived_count = archiver.get("archived_count")
    wal_age = None if archived is None else now - archived
    if archived is None or archived_count in {0, "0", None}:
        errors.append("WAL archive is missing")
    elif wal_age is None or wal_age < 0 or wal_age > max_wal_age:
        errors.append("WAL archive is stale")
    if failed is not None and (archived is None or failed > archived):
        errors.append("WAL archive failed")
    if repo_mode not in {"s3", REHEARSAL_MODE, LOCAL_S3_MODE}:
        errors.append("backup repository mode is unknown")
    failed = {
        "WAL archive failed",
        "backup tool version drifted",
        "backup repository is not aes-256-cbc",
        "archive_mode is disabled",
        "backup repository mode is unknown",
        "backup stanza does not match gym",
    }
    if any(item in failed for item in errors):
        result = "failed"
    elif any("stale" in item for item in errors):
        result = "stale"
    elif errors:
        result = "missing"
    else:
        result = "ok"
    return {
        "schema": STATUS_SCHEMA,
        "result": result,
        "stanza": STANZA,
        "cipher": cipher if isinstance(cipher, str) else None,
        "repo_mode": repo_mode,
        "offsite": repo_mode == "s3",
        "pgbackrest_version": version_line,
        "backup_label": None if chosen is None else chosen.get("label"),
        "backup_type": None if chosen is None else chosen.get("type"),
        "backup_age_seconds": backup_age,
        "wal_age_seconds": wal_age,
        "archive_mode": archiver.get("archive_mode"),
        "errors": errors,
        "rpo_proven": False,
        "rto_proven": False,
        "retention_legal_approval": "not_approved",
        "alert_delivery": "not_claimed",
        "production_proof": False,
    }


RESULT_LABELS = frozenset({"ok", "stale", "missing", "failed", "busy", "rejected"})
_METRIC_AGE_LIMIT = 10 * 366 * 24 * 3600


def _metric_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > _METRIC_AGE_LIMIT:
        return None
    return value


def _result_label(status: Mapping[str, Any]) -> str:
    result = status.get("result")
    if isinstance(result, str) and result in RESULT_LABELS:
        return result
    return "rejected"


def render_backup_metrics(status: Mapping[str, Any]) -> str:
    """Allowlisted textfile. Error text and extra keys are not metrics."""

    result = _result_label(status)
    lines = [
        "# HELP gym_backup_result Current allowlisted backup status. Not a recovery proof.",
        "# TYPE gym_backup_result gauge",
        f'gym_backup_result{{result="{result}"}} 1',
        "# HELP gym_backup_rpo_proven Always 0 in this repository baseline.",
        "# TYPE gym_backup_rpo_proven gauge",
        "gym_backup_rpo_proven 0",
        "# HELP gym_backup_rto_proven Always 0 in this repository baseline.",
        "# TYPE gym_backup_rto_proven gauge",
        "gym_backup_rto_proven 0",
        "# HELP gym_backup_production_proof Always 0 in this repository baseline.",
        "# TYPE gym_backup_production_proof gauge",
        "gym_backup_production_proof 0",
        "# HELP gym_backup_offsite 1 when the status repository mode is s3.",
        "# TYPE gym_backup_offsite gauge",
        "gym_backup_offsite " + ("1" if status.get("repo_mode") == "s3" else "0"),
    ]
    age = _metric_number(status.get("backup_age_seconds"))
    if age is not None:
        lines.extend(
            [
                "# HELP gym_backup_age_seconds Age of the newest backup. Absent when unknown.",
                "# TYPE gym_backup_age_seconds gauge",
                f"gym_backup_age_seconds {age}",
            ]
        )
    wal_age = _metric_number(status.get("wal_age_seconds"))
    if wal_age is not None:
        lines.extend(
            [
                "# HELP gym_backup_wal_age_seconds Age of the newest archived WAL.",
                "# TYPE gym_backup_wal_age_seconds gauge",
                f"gym_backup_wal_age_seconds {wal_age}",
            ]
        )
    return "\n".join(lines) + "\n"


def _metrics_path(raw: str) -> Path | None:
    if raw != raw.strip() or raw == "" or "\n" in raw or "\r" in raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or path.name != METRICS_FILENAME:
        return None
    if ".." in path.parts:
        return None
    return path


def publish_backup_metrics(status: Mapping[str, Any]) -> None:
    path = _metrics_path(os.environ.get("BACKUP_METRICS_PATH", ""))
    if path is None:
        if os.environ.get("BACKUP_METRICS_PATH", "").strip() != "":
            print("backup metrics path is refused", file=sys.stderr)
        return
    body = render_backup_metrics(status).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
        )
        try:
            os.write(descriptor, body)
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError:
        print("backup metrics file was not written", file=sys.stderr)


def terminal_status(result: str) -> dict[str, Any]:
    label = result if result in {"busy", "failed"} else "failed"
    errors = ["backup lock is busy"] if label == "busy" else ["backup command failed"]
    return {
        "schema": STATUS_SCHEMA,
        "result": label,
        "errors": errors,
        "rpo_proven": False,
        "rto_proven": False,
        "production_proof": False,
        "alert_delivery": "not_claimed",
        "retention_legal_approval": "not_approved",
    }


def emit_status(status: Mapping[str, Any]) -> int:
    print(json.dumps(status, sort_keys=True))
    publish_backup_metrics(status)
    return status_exit(status)


def status_exit(status: Mapping[str, Any]) -> int:
    if status.get("rpo_proven") is not False or status.get("rto_proven") is not False:
        return 1
    if status.get("result") == "busy":
        return BUSY_EXIT
    return 0 if status.get("result") == "ok" and not status.get("errors") else 1


def run_check(
    *, runner: Runner | None = None, now: int | None = None, lock: bool = True
) -> dict[str, Any]:
    env = os.environ
    _reject_forbidden_environment(env)
    _reject_unresolved_repository(env)
    timeout = _timeout(env, CHECK_MAX_SECONDS)
    mode = _repository_mode(env)
    config = env.get("PGBACKREST_CONFIG", CONFIG_PATH)
    database = _require(env, "BACKUP_PG_DATABASE")
    try:
        active = _lock(Path(LOCK_PATH)) if lock else -1
    except BackupPolicyError:
        status = evaluate_freshness(
            info=[],
            archiver={},
            now=now if now is not None else int(time.time()),
            max_backup_age=_positive(env, "BACKUP_MAX_AGE_SECONDS"),
            max_wal_age=_positive(env, "BACKUP_WAL_MAX_AGE_SECONDS"),
            version_line=PGBACKREST_VERSION_LINE,
            repo_mode=mode,
        )
        status["result"] = "busy"
        status["errors"] = ["backup lock is busy"]
        return status
    try:
        chosen = runner or _default_runner
        _pgbackrest(config, timeout, chosen, (), "check")
        info = json.loads(
            _pgbackrest(config, timeout, chosen, (), "info", "--output", "json")
        )
        version = _run_checked(
            ["pgbackrest", "version"], min(timeout, 30), chosen, ()
        ).strip()
        archiver = read_archiver(database, chosen, min(timeout, 30))
        return evaluate_freshness(
            info=info,
            archiver=archiver,
            now=now if now is not None else int(time.time()),
            max_backup_age=_positive(env, "BACKUP_MAX_AGE_SECONDS"),
            max_wal_age=_positive(env, "BACKUP_WAL_MAX_AGE_SECONDS"),
            version_line=version,
            repo_mode=mode,
        )
    finally:
        if active >= 0:
            os.close(active)


def validate_wal_path(path: str) -> str:
    parts = path.split("/")
    relative = len(parts) == 2 and parts[0] == "pg_wal"
    absolute = path.startswith("/var/lib/postgresql/") and "/pg_wal/" in path
    if ".." in parts or (not relative and not absolute):
        raise BackupPolicyError("WAL path is outside the PostgreSQL data directory")
    name = parts[-1]
    if WAL_NAME_RE.fullmatch(name) is None:
        raise BackupPolicyError("WAL file name is invalid")
    return path


def archive_push(path: str, *, runner: Runner | None = None) -> None:
    env = os.environ
    _reject_forbidden_environment(env, database_passwords=True)
    _reject_unresolved_repository(env)
    raw = _require(env, "BACKUP_ARCHIVE_PUSH_TIMEOUT_SECONDS")
    if not raw.isdigit() or not 1 <= int(raw) <= ARCHIVE_PUSH_MAX_SECONDS:
        raise BackupPolicyError("archive push timeout is outside the bounded range")
    validated = validate_wal_path(path)
    config = env.get("PGBACKREST_CONFIG", CONFIG_PATH)
    _pgbackrest(
        config, int(raw), runner or _default_runner, (), "archive-push", validated
    )


def validate_restore_request(env: Mapping[str, str]) -> None:
    if env.get("RESTORE_CONFIRMATION", "") != CONFIRMATION:
        raise BackupPolicyError("restore confirmation token is required")
    if env.get("RESTORE_TARGET", "") != "disposable":
        raise BackupPolicyError("restore target must be disposable")
    project = env.get("RESTORE_PROJECT", "")
    volume = env.get("RESTORE_VOLUME", "")
    repo_volume = env.get("RESTORE_REPO_VOLUME", "")
    if (
        PROJECT_RE.fullmatch(project) is None
        or "production" in project
        or project == PRODUCTION_PROJECT
    ):
        raise BackupPolicyError(
            "restore project must be a disposable gym-restore project"
        )
    if (
        VOLUME_RE.fullmatch(volume) is None
        or "production" in volume
        or volume in {PRODUCTION_VOLUME, f"{PRODUCTION_PROJECT}_{PRODUCTION_VOLUME}"}
    ):
        raise BackupPolicyError("restore volume must be a new disposable volume")
    mode = _repository_mode(env)
    if mode == REHEARSAL_MODE:
        if REPO_VOLUME_RE.fullmatch(repo_volume) is None or "production" in repo_volume:
            raise BackupPolicyError("restore repository volume must be disposable")
    elif repo_volume:
        raise BackupPolicyError("S3 restore must not use a local repository volume")
    if env.get("RESTORE_STANZA", "") != STANZA:
        raise BackupPolicyError("restore stanza must be gym")
    if env.get("RESTORE_REPOSITORY_ID", "") != repository_identity(env):
        raise BackupPolicyError(
            "restore repository identity does not match the configured repository"
        )
    if env.get("BACKUP_PGDATA", RESTORE_PGDATA) != RESTORE_PGDATA:
        raise BackupPolicyError("restore data path must be the disposable path")
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME"):
        value = env.get(name, "")
        if "docker-compose.prod.yml" in value or value == PRODUCTION_PROJECT:
            raise BackupPolicyError(
                "restore cannot target the production compose project"
            )


def prepare_restore_volumes(
    *,
    mode: str,
    target: str,
    repo: str,
    volume_exists: Callable[[str], bool],
) -> list[tuple[str, str]]:
    """Decide volume actions without creating anything.

    The target must be absent. A filesystem rehearsal requires the source
    repository volume to exist already. S3 does not use a local source volume.
    """

    if volume_exists(target):
        raise BackupPolicyError("restore target volume already exists")
    if mode == REHEARSAL_MODE:
        if not volume_exists(repo):
            raise BackupPolicyError("filesystem repository volume does not exist")
        return [("create", target)]
    if mode == LOCAL_S3_MODE:
        raise BackupPolicyError("local staging object rehearsal is not a restore source")
    if mode != "s3":
        raise BackupPolicyError(
            "backup repository mode is not production S3 or the labeled rehearsal"
        )
    if repo:
        raise BackupPolicyError("S3 restore must not use a local repository volume")
    return [("create", target)]


def new_restore_nonce() -> str:
    return secrets.token_hex(16)


def restore_volume_labels(project: str, nonce: str) -> dict[str, str]:
    if PROJECT_RE.fullmatch(project) is None or "production" in project:
        raise BackupPolicyError(
            "restore project must be a disposable gym-restore project"
        )
    if NONCE_RE.fullmatch(nonce) is None:
        raise BackupPolicyError("restore volume nonce is invalid")
    return {
        "gym.restore.nonce": nonce,
        "gym.restore.project": project,
    }


def build_volume_create_command(name: str, labels: Mapping[str, str]) -> list[str]:
    if not labels:
        raise BackupPolicyError("restore volume labels are invalid")
    command = ["docker", "volume", "create"]
    for key in sorted(labels):
        value = labels[key]
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or any(character in key or character in value for character in "\n\r=")
        ):
            raise BackupPolicyError("restore volume labels are invalid")
        command.extend(["--label", f"{key}={value}"])
    command.append(name)
    return command


def build_volume_inspect_command(name: str) -> list[str]:
    return ["docker", "volume", "inspect", "--format", "{{json .Labels}}", name]


def parse_volume_inspect_labels(stdout: object) -> dict[str, str]:
    if not isinstance(stdout, str) or stdout.strip() == "":
        raise BackupPolicyError(
            "restore target volume labels do not match this restore"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BackupPolicyError(
            "restore target volume labels do not match this restore"
        ) from exc
    if not isinstance(payload, dict):
        raise BackupPolicyError(
            "restore target volume labels do not match this restore"
        )
    parsed: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BackupPolicyError(
                "restore target volume labels do not match this restore"
            )
        parsed[key] = value
    return parsed


def assert_volume_ownership(
    actual: Mapping[str, str], expected: Mapping[str, str]
) -> None:
    if dict(actual) != dict(expected):
        raise BackupPolicyError(
            "restore target volume labels do not match this restore"
        )


def accept_volume_inspect(
    result: subprocess.CompletedProcess[str], expected: Mapping[str, str]
) -> None:
    """Verify a captured volume inspect result. Output is never echoed."""

    if result.returncode != 0:
        raise BackupPolicyError("restore target volume could not be inspected")
    assert_volume_ownership(parse_volume_inspect_labels(result.stdout), expected)


def restore_storage_key_files(
    mode: str, access_key: Path | None, secret_key: Path | None
) -> tuple[Path, Path] | None:
    """Return S3 key files, or none for the filesystem rehearsal."""

    if mode == REHEARSAL_MODE:
        if access_key is not None or secret_key is not None:
            raise BackupPolicyError(
                "filesystem rehearsal must not receive storage keys"
            )
        return None
    if mode != "s3":
        raise BackupPolicyError(
            "backup repository mode is not production S3 or the labeled rehearsal"
        )
    if access_key is None or secret_key is None:
        raise BackupPolicyError("S3 restore requires both storage key files")
    return (access_key, secret_key)


def storage_key_mounts(files: tuple[Path, Path] | None) -> list[str]:
    if files is None:
        return []
    access_key, secret_key = files
    return [
        "-v",
        f"{access_key}:/run/secrets/{ACCESS_SECRET}:ro",
        "-v",
        f"{secret_key}:/run/secrets/{SECRET_KEY_SECRET}:ro",
    ]


def read_bounded_secret(path: Path, label: str) -> str:
    """Read one secret file. Errors name the label and never the value."""

    if path.is_symlink() or not path.is_file():
        raise BackupPolicyError(f"{label} file is missing or unsafe")
    size = path.stat().st_size
    if size <= 0 or size > 4096:
        raise BackupPolicyError(f"{label} file is empty or oversized")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise BackupPolicyError(f"{label} file is not text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackupPolicyError(f"{label} file is not text") from exc
    return _secret_value(text, label)


def validate_digest_image(value: str) -> None:
    if "@sha256:" not in value or value.endswith(":latest") or "/latest" in value:
        raise BackupPolicyError("restore image must be an exact repository digest")
    name, digest = value.rsplit("@", 1)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise BackupPolicyError("restore image must be an exact repository digest")
    if not name or ":" in name.split("/")[-1]:
        raise BackupPolicyError("restore image must not include a tag")
    parts = name.split("/")
    if len(parts) > 1 and ":" in parts[0]:
        port = parts[0].rsplit(":", 1)[1]
        if not port.isdigit():
            raise BackupPolicyError("restore image must be an exact repository digest")


def assert_empty_pgdata(path: Path) -> None:
    if path.is_symlink():
        raise BackupPolicyError("restore data path is unsafe")
    if path.exists() and any(path.iterdir()):
        raise BackupPolicyError("restore data directory is not empty")


def assert_restored_pgdata(path: Path) -> None:
    version = path / "PG_VERSION"
    if path.is_symlink() or not version.is_file() or version.is_symlink():
        raise BackupPolicyError("restore did not produce a PostgreSQL data directory")


def build_restore_command(config: str) -> list[str]:
    return [
        "pgbackrest",
        "--config",
        config,
        "--stanza",
        STANZA,
        "restore",
        "--pg1-path",
        RESTORE_PGDATA,
    ]


def assert_restore_command(command: Sequence[str]) -> None:
    if "--delta" in command or "--force" in command or "backup" in command:
        raise BackupPolicyError("restore command includes a destructive or backup flag")
    if RESTORE_PGDATA not in command or PRODUCTION_PGDATA in command:
        raise BackupPolicyError(
            "restore command is not limited to the disposable data path"
        )


def _finish_recovery(data: Path, runner: Runner, timeout: int) -> None:
    """Promote the restored cluster before it is started without repository access.

    pg_ctl must not wait for a TCP connection: the server listens only on its
    local socket while this promotion runs.
    """

    pg_ctl = "/usr/lib/postgresql/18/bin/pg_ctl"
    options = "-c archive_mode=off -c listen_addresses= -c unix_socket_directories=/var/run/postgresql"
    _control_server(
        [pg_ctl, "-D", str(data), "-W", "-o", options, "start"],
        min(timeout, 30),
        runner,
    )
    user = _database_user(os.environ)
    database = _require(os.environ, "BACKUP_PG_DATABASE")
    ready = False
    try:
        for _ in range(30):
            result = runner(
                [
                    "psql",
                    "-X",
                    "-q",
                    "-At",
                    "--username",
                    user,
                    "-h",
                    SOCKET_PATH,
                    "--dbname",
                    database,
                    "-c",
                    "SELECT pg_is_in_recovery()",
                ],
                10,
            )
            if result.returncode == 0 and result.stdout.strip() == "f":
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise BackupPolicyError("restored database stayed in recovery")
    finally:
        _control_server(
            [pg_ctl, "-D", str(data), "-W", "-m", "fast", "stop"],
            min(timeout, 30),
            runner,
        )
        stopped = False
        for _ in range(30):
            result = runner(["pg_isready", "-h", SOCKET_PATH, "-t", "1"], 5)
            if result.returncode != 0:
                stopped = True
                break
            time.sleep(1)
        if not stopped:
            raise BackupPolicyError("restored database did not stop")


def run_restore(*, runner: Runner | None = None) -> None:
    _reject_forbidden_environment(os.environ)
    _reject_unresolved_repository(os.environ)
    validate_restore_request(os.environ)
    data = Path(RESTORE_PGDATA)
    assert_empty_pgdata(data)
    data.mkdir(parents=True, exist_ok=True)
    config = os.environ.get("PGBACKREST_CONFIG", CONFIG_PATH)
    command = build_restore_command(config)
    assert_restore_command(command)
    timeout = _timeout(os.environ, FULL_MAX_SECONDS)
    chosen = runner or _default_runner
    _run_checked(command, timeout, chosen, ())
    assert_restored_pgdata(data)
    _finish_recovery(data, chosen, timeout)


def render_config_main() -> int:
    try:
        text = load_process_config()
        write_config_file(Path(CONFIG_PATH).parent, text)
    except (BackupPolicyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("pgbackrest config rendered")
    return 0


def archive_push_main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("WAL archive push takes one path", file=sys.stderr)
        return 1
    try:
        archive_push(argv[0])
    except (BackupPolicyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {
        "render-config",
        "bootstrap",
        "full",
        "diff",
        "check",
        "restore",
    }:
        print(
            "usage: gym-backup render-config|bootstrap|full|diff|check|restore",
            file=sys.stderr,
        )
        return 2
    command = args[0]
    try:
        if command == "render-config":
            return render_config_main()
        if command == "bootstrap":
            return emit_status(run_bootstrap())
        if command in {"full", "diff"}:
            run_backup(command)
            try:
                status = run_check()
            except (BackupPolicyError, OSError, json.JSONDecodeError):
                publish_backup_metrics(terminal_status("failed"))
            else:
                publish_backup_metrics(status)
            return 0
        if command == "check":
            return emit_status(run_check())
        run_restore()
    except BackupPolicyError as exc:
        if str(exc) == "backup lock is busy":
            return emit_status(terminal_status("busy"))
        publish_backup_metrics(terminal_status("failed"))
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        publish_backup_metrics(terminal_status("failed"))
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _compose_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def lint_postgres_dockerfile(text: str) -> list[str]:
    errors: list[str] = []
    if not text.startswith(f"FROM {POSTGRES_BASE}\n"):
        errors.append("PostgreSQL image must start from postgres:18.6")
    if text.count(f"FROM {POSTGRES_BASE}\n") != 1:
        errors.append("PostgreSQL image must use one approved base")
    pinned = (PGBACKREST_VERSION, PGBACKREST_SHA256, PGBACKREST_URL)
    if any(item not in text for item in pinned):
        errors.append("pgBackRest version, URL, or checksum drifted")
    if "sha256sum -c" not in text:
        errors.append("pgBackRest install must verify the published checksum")
    if "ARG " in text:
        errors.append(
            "PostgreSQL image must not accept build args that can swap the pin"
        )
    if re.search(r"(?<![\w.-])latest(?![\w.-])", text):
        errors.append("PostgreSQL image must not use a latest tag")
    if "COPY . " in text or "COPY . ." in text:
        errors.append("PostgreSQL image must copy explicit paths")
    if "apt-get install pgbackrest" in text or "apt-get install -y pgbackrest" in text:
        errors.append("pgBackRest must be built from the pinned source archive")
    if "USER postgres" not in text:
        errors.append("PostgreSQL image must end as the postgres user")
    if any(
        marker in text for marker in ("AKIA", "-----BEGIN", "replace-from-secret-store")
    ):
        errors.append("PostgreSQL image contains a secret marker")
    return errors


def lint_recovery_claims(text: str) -> list[str]:
    if CLAIM_RE.search(text):
        return ["recovery text claims an unproven RPO or RTO result"]
    return []


def _service_map(document: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    services = document.get("services")
    if not isinstance(services, dict):
        return None
    service = services.get(name)
    return service if isinstance(service, dict) else None


def lint_production_compose(text: str, document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if REHEARSAL_MODE in text or "repo1-type=posix" in text:
        errors.append("production compose must not select the filesystem repository")
    if LOCAL_S3_MODE in text:
        errors.append(
            "production compose must not select the local staging object rehearsal"
        )
    if "docker.sock" in text or "privileged:" in text:
        errors.append(
            "production compose must not grant a Docker socket or privileged mode"
        )
    postgres = _service_map(document, "postgres") or {}
    joined = " ".join(str(item) for item in postgres.get("command", []))
    for setting in (
        "archive_mode=on",
        "wal_level=replica",
        "archive_command=/usr/local/bin/pgbackrest-archive-push %p",
        "archive_timeout=60",
        "max_wal_senders=3",
        "max_connections=40",
    ):
        if setting not in joined:
            errors.append(f"postgres is missing {setting}")
    if "archive_mode=off" in joined:
        errors.append("production archive_mode is disabled")
    environment = postgres.get("environment")
    if not isinstance(environment, dict):
        errors.append("postgres environment is missing")
        environment = {}
    database_secrets = DATABASE_PROCESS_ENV
    inline_backup_secrets = {
        "BACKUP_REPO_CIPHER_PASS",
        "BACKUP_S3_ACCESS_KEY",
        "BACKUP_S3_SECRET_KEY",
    }
    for name, value in environment.items():
        if name not in POSTGRES_BACKUP_ENV | {
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_MIGRATION_PASSWORD",
            "POSTGRES_RUNTIME_PASSWORD",
        }:
            errors.append(f"postgres environment is not allow-listed: {name}")
        referenced = set(VARIABLE_RE.findall(str(value)))
        unexpected = referenced & (APP_SECRET_VARIABLES - database_secrets)
        if referenced & inline_backup_secrets or unexpected:
            errors.append(
                f"postgres environment reuses an application secret variable: {name}"
            )
    if environment.get("BACKUP_STANZA") != "${BACKUP_STANZA:?Set BACKUP_STANZA}":
        errors.append("production stanza must come from BACKUP_STANZA")
    if environment.get("BACKUP_ARCHIVE_PUSH_TIMEOUT_SECONDS") != "60":
        errors.append("archive push timeout drifted")
    if environment.get("PGBACKREST_CONFIG") != CONFIG_PATH:
        errors.append("pgBackRest config path drifted")
    secrets = postgres.get("secrets")
    if secrets != [CIPHER_SECRET, ACCESS_SECRET, SECRET_KEY_SECRET, MONITOR_DB_SECRET]:
        errors.append(
            "postgres must mount the backup secrets and the separate monitor password"
        )
    postgres_volumes = postgres.get("volumes")
    if (
        not isinstance(postgres_volumes, list)
        or "backup_metrics:/var/lib/gym-backup-metrics" not in postgres_volumes
    ):
        errors.append("postgres must initialize the backup metrics volume")
    networks = postgres.get("networks")
    if networks != ["data", "backup"]:
        errors.append(
            "postgres must keep the private data network and the backup egress network"
        )
    for name, operation, timeout, profile in (
        ("backup-bootstrap", "bootstrap", "14400", "bootstrap"),
        ("backup-full", "full", "14400", "backup"),
        ("backup-diff", "diff", "7200", "backup"),
        ("backup-check", "check", "1800", "backup"),
    ):
        service = _service_map(document, name)
        if service is None:
            errors.append(f"{name} is missing")
            continue
        if service.get("profiles") != [profile]:
            errors.append(
                f"{name} must use the {profile} profile and not start by default"
            )
        if service.get("restart") != "no":
            errors.append(f"{name} must not restart")
        if service.get("command") != ["gym-backup", operation]:
            errors.append(f"{name} command must be gym-backup {operation}")
        if service.get("ports") or "docker.sock" in json.dumps(service):
            errors.append(f"{name} must not publish a port or mount the Docker socket")
        env = service.get("environment")
        if not isinstance(env, dict):
            errors.append(f"{name} environment is missing")
            continue
        if set(env) - BACKUP_SERVICE_ENV:
            errors.append(
                f"{name} receives an environment value outside the backup allow-list"
            )
        if env.get("BACKUP_TIMEOUT_SECONDS") != timeout:
            errors.append(f"{name} timeout drifted")
        backup_age = env.get("BACKUP_MAX_AGE_SECONDS")
        wal_age = env.get("BACKUP_WAL_MAX_AGE_SECONDS")
        if backup_age != "86400" or wal_age != "900":
            errors.append(f"{name} freshness thresholds drifted")
        if env.get("BACKUP_METRICS_PATH") != "/var/lib/gym-backup-metrics/backup.prom":
            errors.append(f"{name} must write the fixed backup metrics textfile")
        volumes = service.get("volumes")
        if (
            not isinstance(volumes, list)
            or "backup_metrics:/var/lib/gym-backup-metrics" not in volumes
        ):
            errors.append(f"{name} must mount the backup metrics volume")
        for value in env.values():
            referenced = VARIABLE_RE.findall(str(value))
            reused = [item for item in referenced if item in APP_SECRET_VARIABLES]
            if reused:
                errors.append(f"{name} reuses application secret variables: {reused}")
        if service.get("secrets") != [CIPHER_SECRET, ACCESS_SECRET, SECRET_KEY_SECRET]:
            errors.append(f"{name} must mount only the dedicated backup secrets")
    for forbidden in ("api", "web", "migrate", "caddy", "clamav"):
        service = _service_map(document, forbidden) or {}
        if "secrets" in service:
            errors.append(f"{forbidden} must not receive backup secrets")
        rendered = json.dumps(service.get("environment", {}))
        if "BACKUP_" in rendered or "PGBACKREST" in rendered:
            errors.append(f"{forbidden} must not receive backup configuration")
    network = document.get("networks")
    backup_network = network.get("backup") if isinstance(network, dict) else None
    if (
        not isinstance(backup_network, dict)
        or backup_network.get("internal") != "false"
    ):
        errors.append("backup network must allow offsite egress and publish no ports")
    data_network = network.get("data") if isinstance(network, dict) else None
    if not isinstance(data_network, dict) or data_network.get("internal") != "true":
        errors.append("data network must stay internal")
    return errors


def lint_restore_compose(text: str) -> list[str]:
    errors: list[str] = []
    if "NON-PRODUCTION" not in text:
        errors.append("restore compose must be labeled non-production")
    if "name: ${RESTORE_PROJECT:?Set RESTORE_PROJECT}" not in text:
        errors.append("restore compose project must be an explicit variable")
    if PRODUCTION_VOLUME in text or "docker-compose.prod.yml" in text:
        errors.append("restore compose references a production volume or project file")
    if "archive_mode=off" not in text or "archive_mode=on" in text:
        errors.append("restored PostgreSQL must keep archive_mode off")
    if "docker.sock" in text or "privileged:" in text or "ports:" in text:
        errors.append("restore compose must not publish ports or gain host control")
    if "internal: true" not in text or "internal: false" in text:
        errors.append("restore network must be internal after the one-shot fetch")
    if "/var/lib/pgbackrest" in text:
        errors.append("restore compose must not mount a local repository as the source")
    if OFFICIAL_POSTGRES_ENTRYPOINT not in text or "gym-postgres-entrypoint" in text:
        errors.append("restored PostgreSQL must use the official entrypoint")
    for marker in (
        "BACKUP_",
        "PGBACKREST",
        "backup_repo_cipher",
        "backup_s3_",
        "secrets:",
    ):
        if marker in text:
            errors.append(
                "restored services must not receive backup repository configuration"
            )
    if "verify_restore_rehearsal" not in text:
        errors.append("restore compose skips verification")
    if RESTORE_PGDATA not in text or PRODUCTION_PGDATA in text:
        errors.append("restore compose data path must be the disposable path")
    if ":-" in text:
        errors.append("restore compose must not default a repository or target value")
    if "\n  data:" in text or "\n  edge:" in text or "\n  app:" in text:
        errors.append("restore compose must not join a production network")
    if "- data" in text or "- edge" in text or "- app" in text or "- scan" in text:
        errors.append("restore services must stay on the disposable network")
    return errors


def lint_rehearsal_compose(text: str) -> list[str]:
    errors: list[str] = []
    if "NON-PRODUCTION filesystem repository" not in text or REHEARSAL_MODE not in text:
        errors.append(
            "rehearsal compose must be labeled as a non-production filesystem repository"
        )
    if "name: gym-backup-rehearsal" not in text:
        errors.append("rehearsal compose project must stay gym-backup-rehearsal")
    if PRODUCTION_VOLUME in text or PRODUCTION_PROJECT in text:
        errors.append("rehearsal compose references production")
    if "internal: true" not in text:
        errors.append("rehearsal network must not be an offsite production path")
    return errors


def lint_restore_script(text: str) -> list[str]:
    errors: list[str] = []
    validate_at = text.find("validate_restore_request")
    volumes_at = text.find("prepare_restore_volumes")
    ownership_at = text.find("accept_volume_inspect")
    command_at = text.find("build_restore_command")
    verify_at = text.find("verify_restore_rehearsal")
    chown_at = text.find('"chown"')
    if min(validate_at, volumes_at, ownership_at, command_at, verify_at, chown_at) < 0:
        errors.append("restore script is missing a required gate")
    elif not (validate_at < volumes_at < ownership_at < command_at < verify_at):
        errors.append(
            "restore must validate, require a new volume, restore, then verify"
        )
    elif not (ownership_at < chown_at):
        errors.append("restore must verify volume ownership before changing it")
    if (
        "build_volume_create_command" not in text
        or "build_volume_inspect_command" not in text
    ):
        errors.append("restore must label the new volume and inspect it before use")
    if "capture_output=True" not in text:
        errors.append("restore commands must capture output")
    if "restore_storage_key_files" not in text:
        errors.append("restore must require storage keys only for S3")
    if (
        '"--access-key-file", required=True' in text
        or '"--secret-key-file", required=True' in text
    ):
        errors.append("filesystem rehearsal must not require storage key files")
    if "assert_empty_pgdata(" in text:
        errors.append("restore script must not check the host data path")
    if re.search(r"--db-password(?!-file)\b", text) or re.search(
        r"--django-secret(?!-file)\b", text
    ):
        errors.append("restore script accepts a secret on the command line")
    if "--db-password-file" not in text or "--django-secret-file" not in text:
        errors.append("restore script must read secrets from files")
    if "validate_digest_image" not in text:
        errors.append("restore script must require digest images")
    if "--delta" in text or "--force" in text:
        errors.append("restore script enables a destructive pgBackRest flag")
    if PRODUCTION_PROJECT not in text or PRODUCTION_VOLUME not in text:
        errors.append("restore script must refuse the production project and volume")
    if "skip-verify" in text or "skip_verify" in text:
        errors.append("restore script allows skipped verification")
    return errors


def lint_env_separation(text: str) -> list[str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    errors: list[str] = []
    if values.get("BACKUP_S3_ENDPOINT") == values.get("MINIO_ENDPOINT_URL"):
        errors.append("backup endpoint reuses the application object-store endpoint")
    if values.get("BACKUP_S3_BUCKET") == values.get("MINIO_BUCKET"):
        errors.append("backup bucket reuses the application object-store bucket")
    endpoint = values.get("BACKUP_S3_ENDPOINT", "")
    if not endpoint.startswith("https://") or any(
        host in endpoint
        for host in ("localhost", "127.0.0.1", "minio", "pgsty", "silo")
    ):
        errors.append("backup endpoint is not an offsite https origin")
    if values.get("BACKUP_STANZA") != STANZA:
        errors.append("backup stanza example drifted")
    return errors


def lint_secret_examples(repo: Path) -> list[str]:
    errors: list[str] = []
    root = repo / "infra" / "environments" / "production"
    names = (
        "backup-repo-cipher-pass.example",
        "backup-s3-access-key.example",
        "backup-s3-secret-key.example",
    )
    seen: set[Path] = set()
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            errors.append(f"backup secret example is missing: {name}")
            continue
        resolved = path.resolve()
        if resolved in seen:
            errors.append("backup secret examples must be separate files")
        seen.add(resolved)
        if path.read_text(encoding="utf-8") != PLACEHOLDER + "\n":
            errors.append(f"{name} must contain only the placeholder")
    return errors


def lint_backup_baseline(repo: Path, document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    compose = repo / "infra" / "compose" / "docker-compose.prod.yml"
    dockerfile = repo / "infra" / "postgres" / "Dockerfile"
    restore = repo / "infra" / "compose" / "docker-compose.restore.yml"
    rehearsal = repo / "infra" / "compose" / "docker-compose.backup-rehearsal.yml"
    script = repo / "scripts" / "restore_rehearsal.py"
    runbook = repo / "docs" / "runbooks" / "phase8-increment-3.md"
    policy = repo / "infra" / "postgres" / "backup_policy.py"
    for path in (dockerfile, restore, rehearsal, script, runbook, policy):
        if not path.is_file():
            errors.append(f"backup baseline file is missing: {path.name}")
    if errors:
        return errors
    compose_text = _compose_text(compose)
    errors.extend(lint_production_compose(compose_text, document))
    errors.extend(lint_postgres_dockerfile(_compose_text(dockerfile)))
    errors.extend(lint_restore_compose(_compose_text(restore)))
    errors.extend(lint_rehearsal_compose(_compose_text(rehearsal)))
    errors.extend(lint_restore_script(_compose_text(script)))
    errors.extend(lint_secret_examples(repo))
    errors.extend(
        lint_env_separation(
            (
                repo / "infra" / "environments" / "production" / "compose.env.example"
            ).read_text(encoding="utf-8")
        )
    )
    for path in (runbook, script, policy, restore, rehearsal):
        errors.extend(lint_recovery_claims(_compose_text(path)))
    verification = repo / "backend" / "apps" / "common" / "restore_verification.py"
    if not verification.is_file() or "not_proven" not in verification.read_text(
        encoding="utf-8"
    ):
        errors.append("restore verification must keep RPO and RTO unproven")
    errors.extend(lint_recovery_claims(verification.read_text(encoding="utf-8")))
    timers = sorted((repo / "infra" / "backup" / "examples").glob("*.timer"))
    services = sorted((repo / "infra" / "backup" / "examples").glob("*.service"))
    if len(timers) < 3 or len(services) < 3:
        errors.append("backup timer examples are missing")
    for path in [*timers, *services]:
        text = path.read_text(encoding="utf-8")
        if "docker.sock" in text or "privileged" in text:
            errors.append(f"{path.name} grants host Docker control")
        errors.extend(lint_recovery_claims(text))
    example = repo / "infra" / "backup" / "examples" / "gym-backup-full.timer"
    if "Example only" not in example.read_text(encoding="utf-8"):
        errors.append("backup timers must be labeled as examples")
    if "bootstrap" not in example.read_text(encoding="utf-8").lower():
        errors.append("full backup timer must wait for bootstrap")
    entrypoint = repo / "infra" / "postgres" / "bin" / "gym-postgres-entrypoint"
    if not entrypoint.is_file() or "bootstrap" in entrypoint.read_text(
        encoding="utf-8"
    ):
        errors.append("database entrypoint must not run backup bootstrap")
    return errors


if __name__ == "__main__":
    sys.exit(main())
