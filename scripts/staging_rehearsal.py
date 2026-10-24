#!/usr/bin/env python3
"""Disposable local staging rehearsal for Phase 8 increment 6.

The harness builds the production Dockerfiles, publishes them to an ephemeral
loopback registry, and starts the production Compose file with a staging
overlay. Evidence is local only. Live production proof flags stay false.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

PROJECT = "gym-staging-rehearsal"
REHEARSAL_LABEL = "gym.staging-rehearsal"
REHEARSAL_LABEL_VALUE = PROJECT
REQUIRED_TARGET_JOBS = (
    "prometheus",
    "caddy",
    "node",
    "postgres",
    "blackbox-web",
    "blackbox-api",
    "blackbox-tls",
)
PRODUCTION_PROJECT = "gym-production"
LIVE_STAGING_PROJECT = "gym-staging"
CLEANUP_TOKEN = "destroy-disposable-staging-rehearsal"
REGISTRY_NAME = "gym-staging-rehearsal-registry"
SCHEMA = "gym-staging-rehearsal/v1"
MIGRATION_LIMIT_SECONDS = 600
CLAMAV_TCP_TIMEOUT = 420
LOOPBACK_PORTS = (8080, 8443)
MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json"
)
OBJECT_STORE_DIGEST = (
    "docker.io/pgsty/silo@sha256:aaac6bce13eb99316b3c88bd20143fc3c38ab607585256a411a095c44ee57f62"
)
THIRD_PARTY_TAGS = {
    "CADDY_IMAGE": "caddy:2.11.7",
    "CLAMAV_IMAGE": "clamav/clamav:1.4",
    "PROMETHEUS_IMAGE": "prom/prometheus:v3.15.0",
    "ALERTMANAGER_IMAGE": "prom/alertmanager:v0.34.1",
    "GRAFANA_IMAGE": "grafana/grafana:13.2.4",
    "NODE_EXPORTER_IMAGE": "prom/node-exporter:v1.12.1",
    "BLACKBOX_EXPORTER_IMAGE": "prom/blackbox-exporter:v0.29.0",
    "POSTGRES_EXPORTER_IMAGE": "prometheuscommunity/postgres-exporter:v0.20.1",
    "REGISTRY_IMAGE": "registry:2",
}
PROOF_FLAGS = (
    "production_deploy_proven",
    "ghcr_proven",
    "protected_environment_proven",
    "offsite_backup_proven",
    "external_provider_proven",
    "rpo_proven",
    "rto_proven",
    "alert_delivery_proven",
    "staging_vps_proven",
    "release_attestation_proven",
    "rollback_proven",
    "backup_proven",
    "restore_proven",
    "smoke_proven",
)
FORBIDDEN_DIRECTORIES = (
    "/etc/gym/production",
    "/etc/gym/staging",
    "/var/lib/gym-deploy/production",
    "/var/lib/gym-deploy/staging",
    "/var/lib/gym-deploy/incoming",
)
FORBIDDEN_READS = (
    "/etc/gym/production",
    "/var/lib/gym-deploy/production",
    "infra/environments/production/compose.env",
)
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
PROD_COMPOSE = Path("infra/compose/docker-compose.prod.yml")
OVERLAY = Path("infra/compose/docker-compose.staging-rehearsal.yml")
RUNBOOK = Path("docs/runbooks/phase8-increment-6.md")


class RehearsalError(RuntimeError):
    """The rehearsal failed closed."""


def proof_flags() -> dict[str, bool]:
    return {key: False for key in PROOF_FLAGS}


def assert_project(name: str) -> None:
    if name != PROJECT:
        raise RehearsalError("rehearsal project name is not the disposable project")


def assert_digest_reference(reference: str) -> str:
    if reference.count("@") != 1 or ":latest" in reference or "ghcr.io" in reference:
        raise RehearsalError("image reference is mutable")
    repository, digest = reference.rsplit("@", 1)
    if DIGEST_RE.fullmatch(digest) is None or ":" in repository.rsplit("/", 1)[-1]:
        raise RehearsalError("image reference is mutable")
    return reference


def clamav_pong(payload: bytes) -> bool:
    return payload.startswith(b"PONG")


def assert_digest_match(expected: Mapping[str, str], actual: Mapping[str, str]) -> None:
    missing = [name for name in expected if name not in actual]
    if missing:
        raise RehearsalError("running images do not match the rehearsal set")
    for name, reference in expected.items():
        if reference.rsplit("@", 1)[-1] != actual[name].rsplit("@", 1)[-1]:
            raise RehearsalError("image digest was substituted")
        assert_digest_reference(reference)
        assert_digest_reference(actual[name])


def assert_ports_free(occupied: set[tuple[str, int]], required: set[tuple[str, int]]) -> None:
    if occupied & required:
        raise RehearsalError("rehearsal port is occupied")
    for host, _port in required:
        if host != "127.0.0.1":
            raise RehearsalError("published port is not loopback")


def assert_published_bindings(bindings: Sequence[tuple[str, int, str]]) -> None:
    seen: set[tuple[str, int]] = set()
    for host, port, service in bindings:
        if service != "caddy":
            raise RehearsalError("private service publishes a host port")
        if host != "127.0.0.1" or port not in LOOPBACK_PORTS:
            raise RehearsalError("published port is not loopback")
        seen.add((host, port))
    if seen != {("127.0.0.1", port) for port in LOOPBACK_PORTS}:
        raise RehearsalError("caddy loopback ports are incomplete")


def assert_host_clear(names: Sequence[str], existing_paths: Sequence[str]) -> None:
    for name in names:
        if name in {PRODUCTION_PROJECT, LIVE_STAGING_PROJECT}:
            raise RehearsalError("production or live staging state is present")
        if name.startswith("gym-production") or name.startswith("gym-staging_"):
            raise RehearsalError("production or live staging state is present")
    for path in existing_paths:
        raise RehearsalError(f"production directory is present: {path}")


def assert_fresh(names: Sequence[str]) -> None:
    for name in names:
        if name == PROJECT or name.startswith(f"{PROJECT}_") or name == REGISTRY_NAME:
            raise RehearsalError("rehearsal state already exists; cleanup is explicit")


def assert_local_docker_host(host: str) -> str:
    value = host.strip()
    if not value.startswith("unix://"):
        raise RehearsalError("docker host is not a local unix socket")
    path = Path(value.removeprefix("unix://"))
    if not path.is_absolute() or ".." in path.parts:
        raise RehearsalError("docker host is not a local unix socket")
    return value


def assert_command_has_no_secrets(args: Sequence[str], secret_values: Sequence[str]) -> None:
    blob = "\n".join(args)
    for secret in secret_values:
        if len(secret) >= 8 and secret in blob:
            raise RehearsalError("secret leak in command")


def redact(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for secret in sorted((item for item in secret_values if len(item) >= 8), key=len, reverse=True):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def load_rehearsal_ca(cafile: Path | None) -> ssl.SSLContext:
    if cafile is None or cafile.is_symlink() or not cafile.is_file():
        raise RehearsalError("local TLS trust is missing")
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(cafile))
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise RehearsalError("TLS verification is disabled")
    return context


def interpret_migration(payload: Mapping[str, Any]) -> dict[str, object]:
    if payload.get("exit_code") != 0:
        raise RehearsalError("migration failed")
    if payload.get("pending") != 0:
        raise RehearsalError("migrations are pending")
    if payload.get("schema_owner") != "gym_migration":
        raise RehearsalError("migration role does not own the schema")
    if payload.get("runtime_owned_tables") != 0:
        raise RehearsalError("runtime role owns schema objects")
    if payload.get("runtime_schema_create") is not False:
        raise RehearsalError("runtime role can alter schema")
    if payload.get("migration_owned_tables") in (None, 0):
        raise RehearsalError("migration role does not own schema objects")
    duration = payload.get("duration_seconds")
    if (
        not isinstance(duration, int | float)
        or isinstance(duration, bool)
        or duration < 0
        or duration > MIGRATION_LIMIT_SECONDS
    ):
        raise RehearsalError("migration duration is outside the bound")
    if payload.get("settings_module") != "config.settings.migration":
        raise RehearsalError("migration identity settings drifted")
    if payload.get("role_mode") != "MIGRATION" or payload.get("role_user") != "gym_migration":
        raise RehearsalError("migration identity drifted")
    return {
        "kind": "runtime",
        "status": "pass",
        "duration_seconds": duration,
        "pending": 0,
    }


def interpret_isolation(payload: Mapping[str, Any]) -> dict[str, object]:
    if payload.get("isolated") is not True:
        raise RehearsalError("tenant isolation failed")
    if payload.get("cross_gym_rows") != 0 or payload.get("empty_context_rows") != 0:
        raise RehearsalError("tenant isolation failed")
    if payload.get("cross_update_rows") != 0:
        raise RehearsalError("tenant isolation failed")
    if payload.get("runtime_schema_write") != "denied":
        raise RehearsalError("runtime role can alter schema")
    for key in PROOF_FLAGS:
        if key in payload and payload[key] is not False:
            raise RehearsalError("live proof flag cannot be set")
    return {"kind": "runtime", "status": "pass", "gyms": 2}


def seal_evidence(body: dict[str, Any], secret_values: Sequence[str]) -> dict[str, Any]:
    for key in PROOF_FLAGS:
        if body.get(key) is True:
            raise RehearsalError("live proof flag cannot be set")
        body[key] = False
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    payload = {key: value for key, value in body.items() if key != "checksum"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    for secret in secret_values:
        if len(secret) >= 8 and secret in encoded:
            raise RehearsalError("secret leak")
    body["checksum"] = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return body


def _reject_symlink_components(path: Path) -> None:
    if not path.is_absolute():
        raise RehearsalError("rehearsal path must be absolute")
    if ".." in path.parts or "." in path.parts[1:]:
        raise RehearsalError("path traversal was refused")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise RehearsalError("symlink was refused")


def allowed_state_dirs(repo: Path) -> set[Path]:
    return {
        (repo.resolve() / ".staging-rehearsal"),
        Path("/var/lib/gym-staging-rehearsal"),
    }


def resolve_state_dir(path: Path, repo: Path) -> Path:
    _reject_symlink_components(path)
    resolved = path.resolve()
    if resolved not in allowed_state_dirs(repo):
        raise RehearsalError("state directory is not an allowed rehearsal root")
    text = str(resolved)
    for forbidden in FORBIDDEN_DIRECTORIES:
        if text == forbidden or text.startswith(forbidden + "/"):
            raise RehearsalError("state directory is a production path")
    return resolved


def assert_owner_only(path: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise RehearsalError("symlink was refused")
    mode = stat.S_IMODE(path.stat().st_mode)
    expected = 0o700 if directory else 0o600
    if mode != expected or path.stat().st_uid != os.getuid():
        raise RehearsalError("rehearsal path is not owner-only")


def authorize_cleanup(*, token_text: str, state_dir: Path, repo: Path, project: str) -> Path:
    assert_project(project)
    if token_text.strip() != CLEANUP_TOKEN:
        raise RehearsalError("cleanup token was refused")
    return resolve_state_dir(state_dir, repo)


def acquire_rehearsal_lock(path: Path) -> IO[str]:
    if path.is_symlink():
        raise RehearsalError("symlink was refused")
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RehearsalError("rehearsal is already running") from exc
    return handle


def lint_repository(repo: Path) -> list[str]:
    errors: list[str] = []
    overlay = repo / OVERLAY
    prod = repo / PROD_COMPOSE
    caddy = repo / "infra/caddy/Caddyfile.staging-rehearsal"
    settings = repo / "backend/config/settings/staging_rehearsal.py"
    production_settings = repo / "backend/config/settings/production.py"
    runbook = repo / RUNBOOK
    gitignore = repo / ".gitignore"
    for path in (
        overlay,
        prod,
        caddy,
        settings,
        production_settings,
        runbook,
        gitignore,
    ):
        if path.is_symlink() or not path.is_file():
            errors.append(f"staging rehearsal file is missing: {path.relative_to(repo)}")
    if errors:
        return errors
    overlay_text = overlay.read_text(encoding="utf-8")
    prod_text = prod.read_text(encoding="utf-8")
    caddy_text = caddy.read_text(encoding="utf-8")
    if "name: gym-production" not in prod_text or "name: gym-staging-rehearsal" in prod_text:
        errors.append("production compose project name drifted")
    if "image: ${API_IMAGE:?Set API_IMAGE to a repository@sha256 digest}" not in prod_text:
        errors.append("production compose is no longer digest-only")
    if "rehearsal-s3-local" in prod_text or ":latest" in prod_text:
        errors.append("production compose contains a rehearsal or mutable reference")
    if "name: gym-staging-rehearsal" not in overlay_text:
        errors.append("staging overlay project name drifted")
    for marker in (
        "127.0.0.1:8080:80",
        "127.0.0.1:8443:443",
        "ports: !override",
        "rehearsal-s3-local",
        "config.settings.staging_rehearsal",
        "django.core.mail.backends.locmem.EmailBackend",
        "PUSH_BACKEND: locmem",
        "tls internal",
        OBJECT_STORE_DIGEST,
        "internal: true",
        "CLAMAV_NO_FRESHCLAMD",
        "read-only rootfs",
    ):
        if marker not in overlay_text and marker not in caddy_text:
            errors.append(f"staging overlay is missing {marker}")
    for banned in (
        "docker.sock",
        "privileged:",
        "network_mode: host",
        'pid: "host"',
        "pid: host",
        ":latest",
        "insecure_skip_verify",
        "ghcr.io",
        "0.0.0.0:",
    ):
        if banned in overlay_text or banned in caddy_text:
            errors.append(f"staging overlay contains {banned}")
    if "tls internal" not in caddy_text or 'respond "Not found" 404' not in caddy_text:
        errors.append("staging Caddyfile does not keep the BFF boundary")
    if "/api/v1/member-auth" not in caddy_text or "local_certs" not in caddy_text:
        errors.append("staging Caddyfile does not keep member routing and local TLS")
    if "require_resend_backend" not in production_settings.read_text(encoding="utf-8"):
        errors.append("production settings no longer require the Resend backend")
    settings_text = settings.read_text(encoding="utf-8")
    if "STAGING_REHEARSAL" not in settings_text or "locmem" not in settings_text:
        errors.append("staging settings do not keep non-delivery mail and push")
    weakened = (
        "require_resend_backend" in settings_text
        or "PRODUCTION_DEPLOY_PROVEN = True" in settings_text
    )
    if weakened:
        errors.append("staging settings weaken the production proof boundary")
    if ".staging-rehearsal/" not in gitignore.read_text(encoding="utf-8"):
        errors.append("staging rehearsal state is not gitignored")
    runbook_text = runbook.read_text(encoding="utf-8")
    for phrase in (
        "Phase 8 is not complete",
        CLEANUP_TOKEN,
        PROJECT,
        "production_deploy_proven",
        "ghcr_proven",
        "offsite_backup_proven",
        "rpo_proven",
        "rto_proven",
        "local evidence",
        "loopback",
    ):
        if phrase not in runbook_text:
            errors.append(f"staging runbook is missing {phrase}")
    for phrase in (
        "staging deploy is proven",
        "GHCR push is proven",
        "RPO is proven",
        "production deployment is proven",
    ):
        if phrase in runbook_text:
            errors.append(f"staging runbook claims {phrase}")
    load_command = (
        repo / "backend/apps/tenants/management/commands/staging_load_characterization.py"
    )
    load_rules = repo / "backend/apps/common/load_characterization.py"
    load_runbook = repo / "docs/runbooks/phase8-increment-7.md"
    for path in (load_command, load_rules, load_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    load_module = _load_rules(repo)
    errors.extend(
        load_module.lint_texts(
            command=load_command.read_text(encoding="utf-8"),
            runbook=load_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    finance_command = (
        repo / "backend/apps/tenants/management/commands/staging_finance_characterization.py"
    )
    finance_rules = repo / "backend/apps/common/finance_characterization.py"
    finance_runbook = repo / "docs/runbooks/phase8-increment-8.md"
    for path in (finance_command, finance_rules, finance_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    finance_module = _load_finance_rules(repo)
    errors.extend(
        finance_module.lint_texts(
            command=finance_command.read_text(encoding="utf-8"),
            runbook=finance_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    pt_command = repo / "backend/apps/tenants/management/commands/staging_pt_characterization.py"
    pt_rules = repo / "backend/apps/common/pt_characterization.py"
    pt_runbook = repo / "docs/runbooks/phase8-increment-9.md"
    for path in (pt_command, pt_rules, pt_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    pt_module = _load_pt_rules(repo)
    errors.extend(
        pt_module.lint_texts(
            command=pt_command.read_text(encoding="utf-8"),
            runbook=pt_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    cash_command = (
        repo / "backend/apps/tenants/management/commands/staging_pt_cash_characterization.py"
    )
    cash_rules = repo / "backend/apps/common/pt_cash_characterization.py"
    cash_runbook = repo / "docs/runbooks/phase8-increment-10.md"
    for path in (cash_command, cash_rules, cash_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    cash_module = _load_pt_cash_rules(repo)
    errors.extend(
        cash_module.lint_texts(
            command=cash_command.read_text(encoding="utf-8"),
            runbook=cash_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    endurance_command = (
        repo / "backend/apps/tenants/management/commands/staging_endurance_characterization.py"
    )
    endurance_rules = repo / "backend/apps/common/endurance_characterization.py"
    endurance_runbook = repo / "docs/runbooks/phase8-increment-11.md"
    for path in (endurance_command, endurance_rules, endurance_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    endurance_module = _load_endurance_rules(repo)
    errors.extend(
        endurance_module.lint_texts(
            command=endurance_command.read_text(encoding="utf-8"),
            runbook=endurance_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    security_command = (
        repo / "backend/apps/tenants/management/commands/staging_security_characterization.py"
    )
    security_rules = repo / "backend/apps/common/security_characterization.py"
    security_runbook = repo / "docs/runbooks/phase8-increment-12.md"
    for path in (security_command, security_rules, security_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    security_module = _load_security_rules(repo)
    errors.extend(
        security_module.lint_texts(
            command=security_command.read_text(encoding="utf-8"),
            runbook=security_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    identity_command = (
        repo
        / "backend/apps/tenants/management/commands/staging_identity_security_characterization.py"
    )
    identity_rules = repo / "backend/apps/common/identity_security_characterization.py"
    identity_runbook = repo / "docs/runbooks/phase8-increment-13.md"
    for path in (identity_command, identity_rules, identity_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    identity_module = _load_identity_rules(repo)
    errors.extend(
        identity_module.lint_texts(
            command=identity_command.read_text(encoding="utf-8"),
            runbook=identity_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    isolation_command = (
        repo / "backend/apps/tenants/management/commands/staging_isolation_characterization.py"
    )
    isolation_rules = repo / "backend/apps/common/isolation_characterization.py"
    isolation_runbook = repo / "docs/runbooks/phase8-increment-14.md"
    for path in (isolation_command, isolation_rules, isolation_runbook):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(repo)}")
            return errors
    isolation_module = _load_isolation_rules(repo)
    errors.extend(
        isolation_module.lint_texts(
            command=isolation_command.read_text(encoding="utf-8"),
            runbook=isolation_runbook.read_text(encoding="utf-8"),
            harness=(repo / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
        )
    )
    return errors


def _load_finance_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/finance_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_finance_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("finance characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_pt_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/pt_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_pt_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("pt characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_rules(repo: Path) -> Any:
    path = repo / "backend/apps/common/load_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_load_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("load characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise RehearsalError("short write failed")
        view = view[written:]


def _write_private(path: Path, text: str) -> None:
    if path.is_symlink():
        raise RehearsalError("symlink was refused")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    assert_owner_only(path, directory=False)


def _replace_private(path: Path, text: str) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise RehearsalError("symlink was refused")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RehearsalError("evidence path is unsafe")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    assert_owner_only(path, directory=False)


def reset_generated_files(state_dir: Path) -> None:
    """Drop certs, secrets, and the empty Docker config from a failed attempt.

    Evidence stays in place. This runs only after the host has no rehearsal
    containers or volumes, so an explicit cleanup is still required for those.
    """
    for name in ("secrets", "certs", "docker"):
        root = state_dir / name
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise RehearsalError("generated path is unsafe")
        if root.stat().st_uid != os.getuid():
            raise RehearsalError("generated path is unsafe")
        children = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for child in children:
            if child.is_symlink():
                if root.name == "docker" and child.name == "cli-plugins":
                    child.unlink()
                    continue
                raise RehearsalError("symlink was refused")
            if child.is_file():
                if child.stat().st_uid != os.getuid():
                    raise RehearsalError("generated path is unsafe")
                child.unlink()
            elif child.is_dir():
                try:
                    child.rmdir()
                except OSError as exc:
                    raise RehearsalError("generated path is unsafe") from exc
    for name in ("blackbox.rendered.yml", "caddy-root.crt", "load-caddy-root.crt"):
        path = state_dir / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
            raise RehearsalError("generated path is unsafe")
        path.unlink()


def _openssl(args: Sequence[str]) -> None:
    result = subprocess.run(
        ["openssl", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RehearsalError("local certificate generation failed")


def _issue_cert(directory: Path, name: str, san_config: str) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    config = directory / f"{name}.cnf"
    _write_private(config, san_config)
    key = directory / f"{name}.key"
    cert = directory / f"{name}.crt"
    _openssl(
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "2",
            "-nodes",
            "-config",
            str(config),
            "-extensions",
            "v3",
        ]
    )
    os.chmod(key, 0o600)
    os.chmod(cert, 0o600)


def _run(
    args: Sequence[str],
    *,
    env: dict[str, str] | None,
    timeout: int,
    secret_values: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    assert_command_has_no_secrets(args, secret_values)
    try:
        result = subprocess.run(
            list(args),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RehearsalError("command exceeded its bound") from exc
    result.stdout = redact(result.stdout, secret_values)
    result.stderr = redact(result.stderr, secret_values)
    return result


def _checked(
    args: Sequence[str],
    *,
    env: dict[str, str] | None,
    timeout: int,
    secret_values: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    result = _run(args, env=env, timeout=timeout, secret_values=secret_values)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RehearsalError(detail[-500:] or "command failed")
    return result


def _repo_digest(reference: str, env: dict[str, str], secret_values: Sequence[str]) -> str:
    result = _checked(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", reference],
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    values = json.loads(result.stdout or "[]")
    if not isinstance(values, list):
        raise RehearsalError("image digest inspection failed")
    matches = [
        item
        for item in values
        if isinstance(item, str) and "@sha256:" in item and "ghcr.io" not in item
    ]
    digests = {item.rsplit("@", 1)[-1] for item in matches}
    if len(digests) != 1:
        raise RehearsalError("pulled image has no single digest")
    chosen = sorted(matches, key=len)[0]
    return assert_digest_reference(chosen)


def _https_exchange(
    path: str, cafile: Path, request_id: str | None
) -> tuple[int, bytes, dict[str, str]]:
    context = load_rehearsal_ca(cafile)
    raw = socket.create_connection(("127.0.0.1", 8443), 10)
    try:
        tls = context.wrap_socket(raw, server_hostname="localhost")
    except ssl.SSLError as exc:
        raw.close()
        raise RehearsalError("local TLS verification failed") from exc
    header = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
    if request_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise RehearsalError("request id is invalid")
        header += f"X-Request-Id: {request_id}\r\n"
    header += "\r\n"
    tls.sendall(header.encode("ascii"))
    chunks: list[bytes] = []
    size = 0
    while size < 200_000:
        chunk = tls.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    tls.close()
    data = b"".join(chunks)
    head, _, body = data.partition(b"\r\n\r\n")
    parts = head.split(b" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise RehearsalError("TLS response status is invalid")
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            key, value = line.split(b":", 1)
            headers[key.decode("ascii", "replace").lower()] = value.decode(
                "latin-1", "replace"
            ).strip()
    return int(parts[1]), body[:65536], headers


def _remove_compose_overrides(env: dict[str, str], env_file: Path) -> None:
    """Make the generated env file authoritative over the caller's shell."""
    if env_file.is_symlink() or not env_file.is_file():
        raise RehearsalError("compose environment file is unsafe")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, _value = line.partition("=")
        if separator != "=" or re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None:
            raise RehearsalError("compose environment file is invalid")
        env.pop(name, None)


def _compose(repo: Path, env_file: Path, env: dict[str, str], extra: Sequence[str]) -> list[str]:
    _remove_compose_overrides(env, env_file)
    return [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(repo / PROD_COMPOSE),
        "-f",
        str(repo / OVERLAY),
        "--env-file",
        str(env_file),
        *extra,
    ]


def _existing_forbidden_paths() -> list[str]:
    return [item for item in FORBIDDEN_DIRECTORIES if Path(item).exists()]


def _link_docker_cli_plugins(docker_config: Path) -> None:
    """Expose the local Compose plugin without copying Docker credentials."""
    destination = docker_config / "cli-plugins"
    if destination.is_symlink():
        return
    if destination.exists():
        raise RehearsalError("docker cli plugin path is unsafe")
    candidates = (
        Path.home() / ".docker" / "cli-plugins",
        Path("/usr/local/lib/docker/cli-plugins"),
        Path("/opt/homebrew/lib/docker/cli-plugins"),
        Path("/usr/libexec/docker/cli-plugins"),
    )
    source = next((item for item in candidates if item.is_dir()), None)
    if source is None:
        raise RehearsalError("docker compose plugin is unavailable")
    destination.symlink_to(source, target_is_directory=True)


def _phase(message: str) -> None:
    if not re.fullmatch(r"[a-z][a-z ]{0,80}", message):
        raise RehearsalError("phase name is invalid")
    print(message, file=sys.stderr, flush=True)


def _local_docker_host(env: dict[str, str]) -> str:
    result = _checked(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        env=env,
        timeout=30,
        secret_values=[],
    )
    host = assert_local_docker_host(result.stdout.strip())
    if not Path(host.removeprefix("unix://")).exists():
        raise RehearsalError("docker host is not a local unix socket")
    return host


def _docker_names(env: dict[str, str], secret_values: Sequence[str]) -> list[str]:
    names: list[str] = []
    for args in (
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        ["docker", "network", "ls", "--format", "{{.Name}}"],
    ):
        result = _checked(args, env=env, timeout=30, secret_values=secret_values)
        names.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
    return names


def _generate_secrets() -> dict[str, str]:
    values: dict[str, str] = {}
    names = (
        "admin",
        "migration",
        "runtime",
        "monitor",
        "grafana",
        "alert_smtp",
        "cipher",
        "object_access",
        "object_secret",
        "backup_object_access",
        "backup_object_secret",
        "django",
        "bff",
        "refresh",
        "mfa_token",
        "mfa_encryption",
        "finance",
        "invitation",
        "reset",
        "push_encryption",
        "push_pepper",
    )
    seen: set[str] = set()
    for name in names:
        value = secrets.token_hex(32)
        if HEX_RE.fullmatch(value) is None or value in seen:
            raise RehearsalError("generated rehearsal secret is unsafe")
        seen.add(value)
        values[name] = value
    return values


def _format_env(values: Mapping[str, str]) -> str:
    lines: list[str] = []
    for key in sorted(values):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise RehearsalError("env name is invalid")
        value = values[key]
        if "\n" in value or "\r" in value or "\x00" in value:
            raise RehearsalError("env value must be one line")
        if re.fullmatch(r"[A-Za-z0-9_@:/.\-]*", value):
            lines.append(f"{key}={value}")
        else:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + "\n"


def _prepare_state(state_dir: Path) -> tuple[Path, Path, Path]:
    state_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(state_dir, 0o700)
    assert_owner_only(state_dir, directory=True)
    for name in ("secrets", "evidence", "certs", "docker"):
        path = state_dir / name
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        assert_owner_only(path, directory=True)
    return state_dir / "secrets", state_dir / "evidence", state_dir / "certs"


def _identity_payload(stdout: str) -> dict[str, Any]:
    text = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RehearsalError("migration identity was not an object")
    return payload


def _pending_migrations(text: str) -> int:
    return sum(1 for line in text.splitlines() if "[ ]" in line)


def run_preflight(repo: Path) -> dict[str, Any]:
    errors = lint_repository(repo)
    if errors:
        raise RehearsalError("; ".join(errors))
    body: dict[str, Any] = {
        "kind": "config",
        "checks": {
            "config_validation": {"kind": "config", "status": "pass"},
            "commands_completed": {"kind": "command", "status": "not-run"},
            "runtime_checks": {"kind": "runtime", "status": "not-run"},
        },
        "commands_completed": False,
        "note": "config validation only; local commands and runtime were not run",
    }
    return seal_evidence(body, [])


def execute_run(repo: Path, state_dir: Path, *, require_clamav_tcp: bool = True) -> dict[str, Any]:
    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    errors = lint_repository(repo)
    if errors:
        raise RehearsalError("; ".join(errors))
    secrets_dir, evidence_dir, cert_dir = _prepare_state(state_dir)
    lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
    secret_values: list[str] = []
    body: dict[str, Any] = {
        "kind": "local-disposable-staging-rehearsal",
        "checks": {
            "config_validation": {"kind": "config", "status": "pass"},
            "commands_completed": {"kind": "command", "status": "not-run"},
            "runtime_checks": {"kind": "runtime", "status": "not-run"},
        },
        "commands_completed": False,
        "images": [],
        "blockers": [],
    }
    try:
        env = os.environ.copy()
        env["DOCKER_HOST"] = _local_docker_host(env)
        docker_config = state_dir / "docker"
        env["DOCKER_CONFIG"] = str(docker_config)
        names = _docker_names(env, secret_values)
        assert_host_clear(names, _existing_forbidden_paths())
        assert_fresh(names)
        for port in LOOPBACK_PORTS:
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RehearsalError("rehearsal port is occupied") from exc
            finally:
                probe.close()
        reset_generated_files(state_dir)
        _write_private(docker_config / "config.json", "{}\n")
        _link_docker_cli_plugins(docker_config)
        _phase("issuing local materials")
        generated = _generate_secrets()
        secret_values.extend(generated.values())
        _issue_materials(repo, state_dir, cert_dir, secrets_dir, generated, secret_values)
        _phase("pulling third party images")
        third_party, image_platforms = _pull_third_party(env, secret_values)
        registry_ref = third_party.pop("REGISTRY_IMAGE")
        _phase("building and publishing images")
        built = _build_and_publish(repo, cert_dir, docker_config, registry_ref, env, secret_values)
        body["images"] = image_records(built, third_party, image_platforms, registry_ref)
        image_platforms.pop("REGISTRY_IMAGE", None)
        env_file = secrets_dir / "compose.env"
        _write_private(
            env_file,
            _format_env(_env_values(state_dir, secrets_dir, generated, built, third_party)),
        )
        _checked(
            _compose(repo, env_file, env, ["config", "--quiet"]),
            env=env,
            timeout=60,
            secret_values=secret_values,
        )
        _phase("starting data services")
        _checked(
            _compose(
                repo,
                env_file,
                env,
                ["up", "-d", "object-store", "object-backup", "postgres", "clamav"],
            ),
            env=env,
            timeout=180,
            secret_values=secret_values,
        )
        _wait_healthy(
            repo,
            env_file,
            env,
            secret_values,
            ("object-store", "object-backup", "postgres", "clamav"),
            420,
        )
        if require_clamav_tcp:
            _wait_clamav_tcp(repo, env_file, env, secret_values, CLAMAV_TCP_TIMEOUT)
        _phase("running migrations")
        migration = _migrate(repo, env_file, env, secret_values)
        interpret_migration(migration)
        body["migration"] = {
            "duration_seconds": migration["duration_seconds"],
            "pending": 0,
            "schema_owner": "gym_migration",
        }
        _phase("starting application services")
        _checked(
            _compose(repo, env_file, env, ["up", "-d"]),
            env=env,
            timeout=180,
            secret_values=secret_values,
        )
        _wait_healthy(
            repo,
            env_file,
            env,
            secret_values,
            ("caddy", "web", "api", "postgres", "prometheus"),
            300,
        )
        runtime: dict[str, object] = {}
        body["runtime"] = runtime
        _phase("running local runtime checks")
        body["checks"]["runtime_checks"] = {"kind": "runtime", "status": "running"}
        _runtime_checks(
            repo,
            env_file,
            env,
            secret_values,
            state_dir,
            built,
            third_party,
            runtime,
            require_clamav_tcp=require_clamav_tcp,
        )
        body["checks"]["runtime_checks"] = {"kind": "runtime", "status": "pass"}
        body["runtime"] = runtime
        body["migration"] = {
            "duration_seconds": migration["duration_seconds"],
            "pending": 0,
            "schema_owner": "gym_migration",
        }
        body["checks"]["commands_completed"] = {
            "kind": "command",
            "status": "pass",
            "note": "local process exit only",
        }
        body["commands_completed"] = True
        sealed = seal_evidence(body, secret_values)
        _replace_private(
            evidence_dir / "rehearsal.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
        return sealed
    except Exception as exc:
        if body["checks"]["runtime_checks"]["status"] == "running":
            body["checks"]["runtime_checks"] = {"kind": "runtime", "status": "fail"}
        body["checks"]["commands_completed"] = {"kind": "command", "status": "fail"}
        body["commands_completed"] = False
        body["blockers"] = [redact(str(exc), secret_values)[-500:]]
        try:
            sealed = seal_evidence(body, secret_values)
        except RehearsalError:
            sealed = seal_evidence(
                {
                    "kind": "local-disposable-staging-rehearsal",
                    "checks": body["checks"],
                    "commands_completed": False,
                    "blockers": ["rehearsal failed and the detail was withheld"],
                },
                [],
            )
        _replace_private(
            evidence_dir / "rehearsal.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
        raise RehearsalError(redact(str(exc), secret_values)[-500:]) from None
    finally:
        lock.close()


def _issue_materials(
    repo: Path,
    state_dir: Path,
    cert_dir: Path,
    secrets_dir: Path,
    generated: Mapping[str, str],
    secret_values: list[str],
) -> None:
    ca_config = (
        """
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = gym-staging-rehearsal
[v3]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
""".strip()
        + "\n"
    )
    _issue_cert(cert_dir / "ca", "ca", ca_config)
    backup_config = (
        """
[req]
distinguished_name = dn
req_extensions = v3
x509_extensions = v3
prompt = no
[dn]
CN = object-backup
[v3]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:object-backup
""".strip()
        + "\n"
    )
    backup_dir = cert_dir / "object-backup"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    _openssl(
        [
            "req",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(backup_dir / "private.key"),
            "-out",
            str(backup_dir / "server.csr"),
            "-nodes",
            "-config",
            str(_write_config(backup_dir / "server.cnf", backup_config)),
        ]
    )
    os.chmod(backup_dir / "private.key", 0o600)
    _openssl(
        [
            "x509",
            "-req",
            "-in",
            str(backup_dir / "server.csr"),
            "-CA",
            str(cert_dir / "ca" / "ca.crt"),
            "-CAkey",
            str(cert_dir / "ca" / "ca.key"),
            "-CAcreateserial",
            "-CAserial",
            str(cert_dir / "ca" / "ca.srl"),
            "-out",
            str(backup_dir / "public.crt"),
            "-days",
            "2",
            "-extfile",
            str(backup_dir / "server.cnf"),
            "-extensions",
            "v3",
        ]
    )
    os.chmod(backup_dir / "public.crt", 0o644)
    registry_config = (
        """
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = 127.0.0.1
[v3]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1,DNS:localhost
""".strip()
        + "\n"
    )
    _issue_cert(cert_dir / "registry", "server", registry_config)
    key = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if key.returncode != 0:
        raise RehearsalError("local JWT key generation failed")
    private = key.stdout.decode("utf-8")
    public = subprocess.run(
        ["openssl", "pkey", "-pubout"],
        input=private.encode("utf-8"),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if public.returncode != 0:
        raise RehearsalError("local JWT key generation failed")
    secret_values.extend([private, public.stdout.decode("utf-8")])
    _write_private(secrets_dir / "jwt-private.pem", private)
    _write_private(secrets_dir / "jwt-public.pem", public.stdout.decode("utf-8"))
    for name, label in (
        ("cipher", "backup-repo-cipher-pass"),
        ("backup_object_access", "backup-s3-access-key"),
        ("backup_object_secret", "backup-s3-secret-key"),
        ("monitor", "monitor-db-password"),
        ("grafana", "grafana-admin-password"),
        ("alert_smtp", "alert-smtp-password"),
    ):
        _write_private(secrets_dir / label, generated[name] + "\n")
    source = (repo / "infra/observability/blackbox/blackbox.yml").read_text(encoding="utf-8")
    rendered = render_staging_blackbox(source)
    _write_private(state_dir / "blackbox.rendered.yml", rendered)


def render_staging_blackbox(source: str) -> str:
    """Render the tracked blackbox file for the loopback rehearsal host.

    Production host normalization rejects a single-label name. This keeps TLS
    verification on and only substitutes server_name localhost.
    """
    import deployment_control as control

    host = "localhost"
    if "insecure_skip_verify: false" not in source:
        raise RehearsalError("blackbox TLS verification must stay enabled")
    if re.search(r"(?m)^[ \t]*insecure_skip_verify:[ \t]*true[ \t]*$", source):
        raise RehearsalError("blackbox must not disable certificate verification")
    matches = list(control.SERVER_NAME_RE.finditer(source))
    if len(matches) != 1:
        raise RehearsalError("blackbox config must contain one server_name")
    rendered = control.SERVER_NAME_RE.sub(
        lambda match: f"{match.group('indent')}server_name: {host}",
        source,
        count=1,
    )
    if (
        "server_name: localhost" not in rendered
        or "insecure_skip_verify: false" not in rendered
        or "insecure_skip_verify: true" in rendered
        or len(control.SERVER_NAME_RE.findall(rendered)) != 1
    ):
        raise RehearsalError("rendered blackbox config lost TLS verification")
    return rendered


def _write_config(path: Path, text: str) -> Path:
    _write_private(path, text)
    return path


def host_manifest_missing(text: str) -> bool:
    lowered = text.lower()
    return "no matching manifest" in lowered and "arm64" in lowered


def _pull_third_party(
    env: dict[str, str], secret_values: Sequence[str]
) -> tuple[dict[str, str], dict[str, str]]:
    resolved: dict[str, str] = {}
    platforms: dict[str, str] = {"OBJECT_STORE_IMAGE": "host"}
    _checked(
        ["docker", "pull", OBJECT_STORE_DIGEST],
        env=env,
        timeout=600,
        secret_values=secret_values,
    )
    for key, tag in THIRD_PARTY_TAGS.items():
        result = _run(
            ["docker", "pull", tag],
            env=env,
            timeout=600,
            secret_values=secret_values,
        )
        platform_name = "host"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            if not host_manifest_missing(detail):
                raise RehearsalError(detail[-500:] or "command failed")
            _phase("pulling emulated platform")
            _checked(
                ["docker", "pull", "--platform", "linux/amd64", tag],
                env=env,
                timeout=600,
                secret_values=secret_values,
            )
            platform_name = "linux/amd64"
        resolved[key] = _repo_digest(tag, env, secret_values)
        platforms[key] = platform_name
    return resolved, platforms


def _build_and_publish(
    repo: Path,
    cert_dir: Path,
    docker_config: Path,
    registry_ref: str,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, str]:
    builds = {
        "API_IMAGE": (repo / "backend/Dockerfile", repo / "backend", "gym-api"),
        "WEB_IMAGE": (repo / "web/Dockerfile", repo / "web", "gym-web"),
        "POSTGRES_IMAGE": (
            repo / "infra/postgres/Dockerfile",
            repo / "infra/postgres",
            "gym-postgres",
        ),
    }
    local_tags: dict[str, str] = {}
    for key, (dockerfile, context, name) in builds.items():
        _phase(f"building {name.replace('-', ' ')}")
        tag = f"gym-staging-rehearsal/{name}:build"
        _checked(
            [
                "docker",
                "build",
                "--label",
                f"{REHEARSAL_LABEL}={REHEARSAL_LABEL_VALUE}",
                "-f",
                str(dockerfile),
                "-t",
                tag,
                str(context),
            ],
            env=env,
            timeout=1800,
            secret_values=secret_values,
        )
        local_tags[key] = tag
    _checked(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REGISTRY_NAME,
            "-p",
            "127.0.0.1:0:5000",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "256m",
            "-v",
            f"{cert_dir / 'registry' / 'server.crt'}:/certs/server.crt:ro",
            "-v",
            f"{cert_dir / 'registry' / 'server.key'}:/certs/server.key:ro",
            "-e",
            "REGISTRY_HTTP_ADDR=0.0.0.0:5000",
            "-e",
            "REGISTRY_HTTP_TLS_CERTIFICATE=/certs/server.crt",
            "-e",
            "REGISTRY_HTTP_TLS_KEY=/certs/server.key",
            registry_ref,
        ],
        env=env,
        timeout=60,
        secret_values=secret_values,
    )
    inspected = _checked(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Ports}}",
            REGISTRY_NAME,
        ],
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    ports = json.loads(inspected.stdout)
    binding = ports.get("5000/tcp")
    if not isinstance(binding, list) or not binding:
        raise RehearsalError("loopback registry did not publish a port")
    host_ip = binding[0].get("HostIp")
    host_port = binding[0].get("HostPort")
    if host_ip != "127.0.0.1" or not str(host_port).isdigit():
        raise RehearsalError("loopback registry binding is not loopback")
    port = str(host_port)
    certs = docker_config / "certs.d" / f"127.0.0.1:{port}"
    certs.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(docker_config / "certs.d", 0o700)
    os.chmod(certs, 0o700)
    ca_source = (cert_dir / "registry" / "server.crt").read_text(encoding="utf-8")
    _write_private(certs / "ca.crt", ca_source)
    published: dict[str, str] = {}
    context = load_rehearsal_ca(certs / "ca.crt")
    for key, tag in local_tags.items():
        name = tag.rsplit("/", 1)[-1].split(":", 1)[0]
        remote = f"127.0.0.1:{port}/{name}:rehearsal"
        _checked(
            ["docker", "tag", tag, remote],
            env=env,
            timeout=30,
            secret_values=secret_values,
        )
        _checked(
            ["docker", "push", remote],
            env=env,
            timeout=600,
            secret_values=secret_values,
        )
        request = urllib.request.Request(
            f"https://127.0.0.1:{port}/v2/{name}/manifests/rehearsal",
            headers={"Accept": MANIFEST_ACCEPT},
            method="GET",
        )
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
            response.read(1024)
        reference = assert_digest_reference(f"127.0.0.1:{port}/{name}@{digest}")
        published[key] = reference
    return published


def _env_values(
    state_dir: Path,
    secrets_dir: Path,
    generated: Mapping[str, str],
    built: Mapping[str, str],
    third_party: Mapping[str, str],
) -> dict[str, str]:
    values = {
        "PUBLIC_HOST": "localhost",
        "ACME_EMAIL": "ops@localhost",
        "APP_ORIGINS": "https://localhost:8443",
        "APP_PUBLIC_URL": "https://localhost:8443",
        "DEFAULT_FROM_EMAIL": "Gym <no-reply@localhost>",
        "POSTGRES_DB": "gym_platform",
        "POSTGRES_ADMIN_USER": "gym_admin",
        "POSTGRES_ADMIN_PASSWORD": generated["admin"],
        "MIGRATION_DB_USER": "gym_migration",
        "MIGRATION_DB_PASSWORD": generated["migration"],
        "RUNTIME_DB_USER": "gym_runtime",
        "RUNTIME_DB_PASSWORD": generated["runtime"],
        "DJANGO_SECRET_KEY": generated["django"],
        "JWT_KEY_ID": "staging-rehearsal",
        "JWT_PRIVATE_KEY": "file-backed",
        "JWT_PUBLIC_KEYS": "file-backed",
        "REFRESH_TOKEN_PEPPER": generated["refresh"],
        "MFA_TOKEN_PEPPER": generated["mfa_token"],
        "MFA_ENCRYPTION_KEY": generated["mfa_encryption"],
        "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": generated["finance"],
        "INVITATION_TOKEN_PEPPER": generated["invitation"],
        "PASSWORD_RESET_TOKEN_PEPPER": generated["reset"],
        "BFF_SHARED_SECRET": generated["bff"],
        "RESEND_API_KEY": "staging-rehearsal-unused",
        "FCM_PROJECT_ID": "staging-rehearsal-unused",
        "FCM_SERVICE_ACCOUNT_JSON": "staging-rehearsal-unused",
        "PUSH_TOKEN_ENCRYPTION_KEY": generated["push_encryption"],
        "PUSH_TOKEN_FINGERPRINT_PEPPER": generated["push_pepper"],
        "MINIO_ENDPOINT_URL": "http://object-store:9000",
        "MINIO_PUBLIC_URL": "http://object-store:9000",
        "MINIO_BUCKET": "gym-staging-rehearsal-app",
        "MINIO_ACCESS_KEY": generated["object_access"],
        "MINIO_SECRET_KEY": generated["object_secret"],
        "MINIO_SESSION_TOKEN": "",
        "OBJECT_STORE_BUCKET_ALLOWLIST": "gym-staging-rehearsal-app",
        "OBJECT_STORE_ORIGIN_ALLOWLIST": "http://object-store:9000",
        "OBJECT_STORE_ROOT_USER": generated["object_access"],
        "OBJECT_STORE_ROOT_PASSWORD": generated["object_secret"],
        "BACKUP_OBJECT_ROOT_USER": generated["backup_object_access"],
        "BACKUP_OBJECT_ROOT_PASSWORD": generated["backup_object_secret"],
        "BACKUP_S3_ENDPOINT": "https://object-backup:9000",
        "BACKUP_S3_REGION": "us-east-1",
        "BACKUP_S3_BUCKET": "gym-staging-rehearsal-backup",
        "BACKUP_S3_URI_STYLE": "path",
        "BACKUP_REPO_PATH": "/gym-platform",
        "BACKUP_STANZA": "gym",
        "BACKUP_REPO_CIPHER_PASS_FILE": str(secrets_dir / "backup-repo-cipher-pass"),
        "BACKUP_S3_ACCESS_KEY_FILE": str(secrets_dir / "backup-s3-access-key"),
        "BACKUP_S3_SECRET_KEY_FILE": str(secrets_dir / "backup-s3-secret-key"),
        "MONITOR_DB_PASSWORD_FILE": str(secrets_dir / "monitor-db-password"),
        "GRAFANA_ADMIN_PASSWORD_FILE": str(secrets_dir / "grafana-admin-password"),
        "ALERT_DELIVERY_MODE": "non-production",
        "ALERT_EMAIL_FROM": "alerts@example.invalid",
        "ALERT_EMAIL_TO": "ops@example.invalid",
        "ALERT_SMTP_PASSWORD_FILE": str(secrets_dir / "alert-smtp-password"),
        "STAGING_BLACKBOX_FILE": str(state_dir / "blackbox.rendered.yml"),
        "STAGING_BACKUP_CERT_DIR": str(state_dir / "certs" / "object-backup"),
        "STAGING_BACKUP_CA_FILE": str(state_dir / "certs" / "ca" / "ca.crt"),
        "STAGING_JWT_PRIVATE_FILE": str(secrets_dir / "jwt-private.pem"),
        "STAGING_JWT_PUBLIC_FILE": str(secrets_dir / "jwt-public.pem"),
    }
    values.update(built)
    values.update(third_party)
    return values


def _wait_healthy(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
    services: Sequence[str],
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    pending = list(services)
    while time.monotonic() < deadline:
        result = _run(
            _compose(repo, env_file, env, ["ps", "--format", "json"]),
            env=env,
            timeout=30,
            secret_values=secret_values,
        )
        rows = {
            row.get("Service"): row
            for row in _ps_rows(result.stdout)
            if isinstance(row.get("Service"), str)
        }
        pending = []
        for name in services:
            row = rows.get(name)
            health = row.get("Health") if isinstance(row, dict) else None
            if health != "healthy":
                pending.append(name)
        if not pending:
            return
        time.sleep(3)
    raise RehearsalError("service did not become healthy: " + ",".join(pending))


def _wait_clamav_tcp(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
    timeout: int,
) -> None:
    """Wait until clamd answers nPING. The image healthcheck uses the unix socket."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            _compose(
                repo,
                env_file,
                env,
                [
                    "exec",
                    "-T",
                    "clamav",
                    "/bin/sh",
                    "-c",
                    "printf 'nPING\\n' | nc -w 5 127.0.0.1 3310",
                ],
            ),
            env=env,
            timeout=15,
            secret_values=secret_values,
        )
        if clamav_pong(result.stdout.encode("utf-8", errors="ignore")):
            return
        api_result = _run(
            _compose(
                repo,
                env_file,
                env,
                [
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    (
                        "import socket;s=socket.create_connection(('clamav',3310),5);"
                        "s.sendall(b'nPING\\n');"
                        "print(s.recv(64).decode('latin-1','replace'),end='')"
                    ),
                ],
            ),
            env=env,
            timeout=15,
            secret_values=secret_values,
        )
        if clamav_pong(api_result.stdout.encode("utf-8", errors="ignore")):
            return
        time.sleep(3)
    raise RehearsalError("ClamAV TCP port did not answer")


def _migrate(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, Any]:
    started = time.monotonic()
    migrate_args = ["--profile", "migrate", "run", "--rm", "--no-deps", "migrate"]
    result = _run(
        _compose(repo, env_file, env, migrate_args),
        env=env,
        timeout=MIGRATION_LIMIT_SECONDS,
        secret_values=secret_values,
    )
    duration = round(time.monotonic() - started, 3)
    identity = _checked(
        _compose(
            repo,
            env_file,
            env,
            [
                "--profile",
                "migrate",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "migrate",
                "-c",
                (
                    "import json,os; print(json.dumps({"
                    "'settings': os.environ.get('DJANGO_SETTINGS_MODULE',''), "
                    "'role_mode': os.environ.get('DATABASE_ROLE_MODE',''), "
                    "'role_user': os.environ.get('MIGRATION_DB_USER','')}))"
                ),
            ],
        ),
        env=env,
        timeout=60,
        secret_values=secret_values,
    )
    plan = _checked(
        _compose(
            repo,
            env_file,
            env,
            [
                "--profile",
                "migrate",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "migrate",
                "manage.py",
                "showmigrations",
                "--plan",
            ],
        ),
        env=env,
        timeout=120,
        secret_values=secret_values,
    )
    posture = _admin_json(repo, env_file, env, secret_values)
    identified = _identity_payload(identity.stdout)
    return {
        "exit_code": result.returncode,
        "duration_seconds": duration,
        "pending": _pending_migrations(plan.stdout),
        "schema_owner": posture.get("schema_owner"),
        "runtime_owned_tables": posture.get("runtime_owned_tables"),
        "migration_owned_tables": posture.get("migration_owned_tables"),
        "runtime_schema_create": posture.get("runtime_schema_create"),
        "settings_module": identified.get("settings"),
        "role_mode": identified.get("role_mode"),
        "role_user": identified.get("role_user"),
    }


def _admin_json(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, Any]:
    sql = (
        "SELECT json_build_object("
        "'schema_owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
        "WHERE nspname = 'public'),"
        "'runtime_owned_tables', (SELECT count(*) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = 'gym_runtime'),"
        "'migration_owned_tables', (SELECT count(*) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = 'gym_migration'),"
        "'runtime_schema_create', has_schema_privilege('gym_runtime', 'public', 'CREATE')"
        ")"
    )
    result = _checked(
        _compose(
            repo,
            env_file,
            env,
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "--username",
                "gym_admin",
                "--dbname",
                "gym_platform",
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
                "-c",
                sql,
            ],
        ),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    payload = json.loads(result.stdout.strip())
    if not isinstance(payload, dict):
        raise RehearsalError("schema posture was not an object")
    return payload


def _runtime_checks(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
    state_dir: Path,
    built: Mapping[str, str],
    third_party: Mapping[str, str],
    report: dict[str, object],
    *,
    require_clamav_tcp: bool = True,
) -> None:
    import deployment_smoke as smoke

    cafile = state_dir / "caddy-root.crt"
    copied = _checked(
        _compose(
            repo,
            env_file,
            env,
            [
                "exec",
                "-T",
                "caddy",
                "cat",
                "/data/caddy/pki/authorities/local/root.crt",
            ],
        ),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    _write_private(cafile, copied.stdout)
    request_id = "rehearsal-request-0001"
    login_status, login_body, login_headers = _https_exchange("/login", cafile, request_id)
    smoke.interpret_public("login", login_status, login_body, "localhost", "localhost")
    if login_headers.get("x-request-id") != request_id:
        raise RehearsalError("request id was not preserved")
    if "strict-transport-security" not in login_headers:
        raise RehearsalError("TLS response is missing HSTS")
    for name, path in (
        ("staff-api", "/api/v1/auth/login/"),
        ("private-health", "/api/v1/health/"),
        ("member-boundary", "/api/v1/member-auth/login/"),
    ):
        status, probe_body, _headers = _https_exchange(path, cafile, None)
        smoke.interpret_public(name, status, probe_body, "localhost", "localhost")
    platform_status, platform_body, _headers = _https_exchange(
        "/api/v1/platform/gyms/", cafile, None
    )
    smoke.interpret_public("staff-api", platform_status, platform_body, "localhost", "localhost")
    report["request_id"] = "pass"
    report["tls"] = "pass"
    report["routing"] = "pass"
    isolation = _json_command(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_isolation_smoke",
            ],
        ),
        env,
        secret_values,
        120,
    )
    interpret_isolation(isolation)
    report["isolation"] = "pass"
    if require_clamav_tcp:
        try:
            _wait_healthy(repo, env_file, env, secret_values, ("clamav",), 420)
        except RehearsalError as exc:
            report["clamav"] = "fail"
            raise RehearsalError("ClamAV did not become healthy") from exc
        report["clamav"] = "pass"
        _phase("checking connectivity")
        _wait_clamav_tcp(repo, env_file, env, secret_values, CLAMAV_TCP_TIMEOUT)
    else:
        report["clamav"] = "skipped"
        _phase("checking connectivity")
    connectivity = _connectivity_probe(repo, env_file, env, secret_values)
    report["connectivity"] = {
        "object_store": connectivity.get("object_store"),
        "clamav": connectivity.get("clamav"),
    }
    if connectivity.get("object_store") != "pass":
        raise RehearsalError("object store connectivity failed")
    if require_clamav_tcp and connectivity.get("clamav") != "pass":
        raise RehearsalError("object store or ClamAV connectivity failed")
    _phase("checking published ports")
    ps = _checked(
        _compose(repo, env_file, env, ["ps", "--format", "json"]),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    _assert_ps_ports(ps.stdout)
    running = _running_images(repo, env_file, env, secret_values)
    expected = {**_service_images(built, third_party)}
    assert_digest_match(expected, running)
    _phase("checking observability")
    targets = _read_observability(repo, env_file, env, secret_values)
    _phase("checking local backup")
    backup = _backup_status(repo, env_file, env, secret_values)
    report["observability"] = targets
    report["backup"] = backup
    interpret_observability(targets)
    interpret_backup(backup)
    report["observability_status"] = "pass"
    report["backup_status"] = "pass"


def _service_images(built: Mapping[str, str], third_party: Mapping[str, str]) -> dict[str, str]:
    return {
        "api": built["API_IMAGE"],
        "web": built["WEB_IMAGE"],
        "postgres": built["POSTGRES_IMAGE"],
        "caddy": third_party["CADDY_IMAGE"],
        "clamav": third_party["CLAMAV_IMAGE"],
        "prometheus": third_party["PROMETHEUS_IMAGE"],
        "alertmanager": third_party["ALERTMANAGER_IMAGE"],
        "grafana": third_party["GRAFANA_IMAGE"],
        "node-exporter": third_party["NODE_EXPORTER_IMAGE"],
        "blackbox": third_party["BLACKBOX_EXPORTER_IMAGE"],
        "postgres-exporter": third_party["POSTGRES_EXPORTER_IMAGE"],
        "object-store": OBJECT_STORE_DIGEST,
        "object-backup": OBJECT_STORE_DIGEST,
    }


def _running_images(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, str]:
    result = _checked(
        _compose(repo, env_file, env, ["ps", "--format", "json"]),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    rows = _ps_rows(result.stdout)
    found: dict[str, str] = {}
    for row in rows:
        service = row.get("Service")
        image = row.get("Image")
        if isinstance(service, str) and isinstance(image, str):
            found[service] = image
    return found


def _ps_rows(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _publisher_host(publisher: Mapping[str, Any]) -> str:
    host_ip = publisher.get("HostIp")
    if isinstance(host_ip, str) and host_ip:
        return host_ip
    url = publisher.get("URL")
    if isinstance(url, str) and url.startswith("127.0.0.1:"):
        return "127.0.0.1"
    if isinstance(url, str):
        return url
    return ""


def _assert_ps_ports(text: str) -> None:
    bindings: list[tuple[str, int, str]] = []
    for row in _ps_rows(text):
        service = row.get("Service")
        publishers = row.get("Publishers") or []
        if not isinstance(service, str) or not isinstance(publishers, list):
            raise RehearsalError("compose status is invalid")
        for publisher in publishers:
            if not isinstance(publisher, dict) or not publisher.get("PublishedPort"):
                continue
            host = _publisher_host(publisher)
            bindings.append((host, int(publisher["PublishedPort"]), service))
    assert_published_bindings(bindings)


def _connectivity_probe(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(6):
        result = _run(
            _compose(
                repo,
                env_file,
                env,
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "api",
                    "manage.py",
                    "staging_connectivity_probe",
                ],
            ),
            env=env,
            timeout=60,
            secret_values=secret_values,
        )
        lines = result.stdout.strip().splitlines()
        if lines:
            try:
                payload = json.loads(lines[-1])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                last = payload
        if (
            result.returncode == 0
            and last.get("object_store") == "pass"
            and last.get("clamav") == "pass"
        ):
            return last
        if attempt < 5:
            time.sleep(5)
    return last


def _json_command(
    args: Sequence[str],
    env: dict[str, str],
    secret_values: Sequence[str],
    timeout: int,
) -> dict[str, Any]:
    result = _checked(args, env=env, timeout=timeout, secret_values=secret_values)
    line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise RehearsalError("command JSON was not an object")
    return payload


def _observability(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, object]:
    ready = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "exec",
                "-T",
                "prometheus",
                "/bin/wget",
                "-q",
                "-O",
                "-",
                "-T",
                "5",
                "http://127.0.0.1:9090/-/ready",
            ],
        ),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    targets = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "exec",
                "-T",
                "prometheus",
                "/bin/wget",
                "-q",
                "-O",
                "-",
                "-T",
                "5",
                "http://127.0.0.1:9090/api/v1/targets",
            ],
        ),
        env=env,
        timeout=30,
        secret_values=secret_values,
    )
    jobs: list[str] = []
    if targets.returncode == 0 and targets.stdout.strip().startswith("{"):
        payload = json.loads(targets.stdout)
        active = payload.get("data", {}).get("activeTargets", [])
        if isinstance(active, list):
            for item in active:
                if isinstance(item, dict) and isinstance(item.get("labels"), dict):
                    job = item["labels"].get("job")
                    health = item.get("health")
                    if isinstance(job, str) and isinstance(health, str):
                        jobs.append(f"{job}:{health}")
    return {
        "prometheus_ready": ready.returncode == 0,
        "targets": sorted(jobs),
        "alert_delivery_proven": False,
    }


def _read_observability(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, object]:
    deadline = time.monotonic() + 120
    last = _observability(repo, env_file, env, secret_values)
    while time.monotonic() < deadline:
        try:
            interpret_observability(last)
            return last
        except RehearsalError:
            time.sleep(3)
            last = _observability(repo, env_file, env, secret_values)
    return last


def _backup_status(
    repo: Path,
    env_file: Path,
    env: dict[str, str],
    secret_values: Sequence[str],
) -> dict[str, object]:
    bootstrap = _run(
        _compose(
            repo,
            env_file,
            env,
            ["--profile", "bootstrap", "run", "--rm", "--no-deps", "backup-bootstrap"],
        ),
        env=env,
        timeout=600,
        secret_values=secret_values,
    )
    full = _run(
        _compose(
            repo,
            env_file,
            env,
            ["--profile", "backup", "run", "--rm", "--no-deps", "backup-full"],
        ),
        env=env,
        timeout=600,
        secret_values=secret_values,
    )
    return {
        "bootstrap_exit": bootstrap.returncode,
        "full_exit": full.returncode,
        "local_only": True,
        "offsite_backup_proven": False,
        "rpo_proven": False,
        "rto_proven": False,
    }


def rehearsal_ref_is_exact(ref: str) -> bool:
    if re.fullmatch(r"gym-staging-rehearsal/gym-(api|web|postgres):build", ref):
        return True
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)/gym-(api|web|postgres):rehearsal", ref)
    if match is None:
        return False
    port = int(match.group(1))
    return 1 <= port <= 65535


def labeled_image_ids_to_remove(rows: Sequence[tuple[str, str]]) -> list[str]:
    """Return dangling image IDs whose only identity is this rehearsal label.

    A labeled image that also has a non-rehearsal tag is refused entirely.
    """
    grouped: dict[str, list[str]] = {}
    for image_id, ref in rows:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RehearsalError("rehearsal image id is invalid")
        grouped.setdefault(image_id, []).append(ref)
    dangling: list[str] = []
    for image_id, refs in grouped.items():
        real = [ref for ref in refs if ref != "<none>:<none>"]
        foreign = [ref for ref in real if not rehearsal_ref_is_exact(ref)]
        if foreign:
            raise RehearsalError("rehearsal label is on a shared image")
        if not real:
            dangling.append(image_id)
    return dangling


def image_records(
    built: Mapping[str, str],
    third_party: Mapping[str, str],
    platforms: Mapping[str, str],
    registry_ref: str,
) -> list[dict[str, str]]:
    records = [
        {
            "role": key,
            "reference": value,
            "registry_kind": "loopback-rehearsal",
            "platform": "host",
        }
        for key, value in built.items()
    ]
    records.extend(
        {
            "role": key,
            "reference": value,
            "registry_kind": "pulled-third-party",
            "platform": platforms.get(key, "host"),
        }
        for key, value in third_party.items()
    )
    records.append(
        {
            "role": "registry",
            "reference": registry_ref,
            "registry_kind": "pulled-third-party",
            "platform": platforms.get("REGISTRY_IMAGE", "host"),
        }
    )
    records.append(
        {
            "role": "object-store",
            "reference": OBJECT_STORE_DIGEST,
            "registry_kind": "pulled-third-party",
            "platform": platforms.get("OBJECT_STORE_IMAGE", "host"),
        }
    )
    return records


def interpret_observability(report: Mapping[str, object]) -> None:
    if report.get("prometheus_ready") is not True:
        raise RehearsalError("prometheus is not ready")
    if report.get("alert_delivery_proven") is not False:
        raise RehearsalError("alert delivery was claimed")
    targets = report.get("targets")
    if not isinstance(targets, list):
        raise RehearsalError("prometheus targets are missing")
    health: dict[str, str] = {}
    for item in targets:
        if not isinstance(item, str) or item.count(":") != 1:
            raise RehearsalError("prometheus target is invalid")
        job, status = item.split(":", 1)
        health[job] = status
    if any(health.get(job) != "up" for job in REQUIRED_TARGET_JOBS):
        raise RehearsalError("prometheus target is not healthy")


def interpret_backup(report: Mapping[str, object]) -> None:
    if report.get("bootstrap_exit") != 0 or report.get("full_exit") != 0:
        raise RehearsalError("local backup command failed")
    if report.get("local_only") is not True:
        raise RehearsalError("backup status is not local-only")
    claimed = (
        report.get("offsite_backup_proven") is not False
        or report.get("rpo_proven") is not False
        or report.get("rto_proven") is not False
    )
    if claimed:
        raise RehearsalError("backup proof was claimed")


def assert_cleanup_token_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RehearsalError("cleanup token file is unsafe")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise RehearsalError("cleanup token file is unsafe")


def cleanup_redaction_values(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        raw = line.split("=", 1)[1]
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if raw.startswith("sha256:") or "@sha256:" in raw:
            continue
        if re.fullmatch(r"[0-9a-f]{64}", raw) is None:
            continue
        values.append(raw)
    if not values:
        raise RehearsalError("cleanup redaction set is empty")
    return values


def remove_generated_trees(state_dir: Path) -> None:
    reset_generated_files(state_dir)
    for name in ("secrets", "certs", "docker"):
        root = state_dir / name
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
            raise RehearsalError("generated path is unsafe")
        try:
            root.rmdir()
        except OSError as exc:
            raise RehearsalError("generated path is unsafe") from exc


def execute_cleanup(repo: Path, state_dir: Path, token_file: Path) -> None:
    assert_cleanup_token_file(token_file)
    token = token_file.read_text(encoding="utf-8")
    resolved = authorize_cleanup(
        token_text=token, state_dir=state_dir, repo=repo.resolve(), project=PROJECT
    )
    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env_file = resolved / "secrets" / "compose.env"
    if env_file.is_file() and not env_file.is_symlink():
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
        _checked(
            _compose(repo.resolve(), env_file, env, ["down", "-v", "--remove-orphans"]),
            env=env,
            timeout=180,
            secret_values=secret_values,
        )
    _run(
        ["docker", "rm", "-f", REGISTRY_NAME],
        env=env,
        timeout=60,
        secret_values=[],
    )
    listed = _checked(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        env=env,
        timeout=30,
        secret_values=[],
    )
    for name in listed.stdout.splitlines():
        if name.startswith(f"{PROJECT}_"):
            _checked(
                ["docker", "volume", "rm", "-f", name],
                env=env,
                timeout=60,
                secret_values=[],
            )
    networks = _checked(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        env=env,
        timeout=30,
        secret_values=[],
    )
    for name in networks.stdout.splitlines():
        if name.startswith(f"{PROJECT}_"):
            _run(["docker", "network", "rm", name], env=env, timeout=30, secret_values=[])
    _remove_rehearsal_images(env)
    remove_generated_trees(resolved)


def _remove_rehearsal_images(env: dict[str, str]) -> None:
    listed = _checked(
        ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
        env=env,
        timeout=30,
        secret_values=[],
    )
    refs = [
        line.strip() for line in listed.stdout.splitlines() if rehearsal_ref_is_exact(line.strip())
    ]
    for ref in refs:
        _checked(["docker", "rmi", ref], env=env, timeout=60, secret_values=[])
    labeled = _checked(
        [
            "docker",
            "image",
            "ls",
            "--no-trunc",
            "--filter",
            f"label={REHEARSAL_LABEL}={REHEARSAL_LABEL_VALUE}",
            "--format",
            "{{.ID}}\t{{.Repository}}:{{.Tag}}",
        ],
        env=env,
        timeout=30,
        secret_values=[],
    )
    rows: list[tuple[str, str]] = []
    for line in labeled.stdout.splitlines():
        if "\t" not in line:
            continue
        image_id, ref = line.split("\t", 1)
        rows.append((image_id.strip(), ref.strip()))
    for image_id in labeled_image_ids_to_remove(rows):
        _checked(["docker", "rmi", image_id], env=env, timeout=60, secret_values=[])


def _publish_load_ca(state_dir: Path) -> Path:
    """Copy the public Caddy trust anchor so the unprivileged load process can read it."""

    source = state_dir / "caddy-root.crt"
    if source.is_symlink() or not source.is_file() or source.stat().st_uid != os.getuid():
        raise RehearsalError("local TLS trust is missing")
    text = source.read_text(encoding="utf-8")
    if "PRIVATE KEY" in text or "BEGIN CERTIFICATE" not in text:
        raise RehearsalError("local TLS trust is missing")
    target = state_dir / "load-caddy-root.crt"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RehearsalError("local TLS trust is missing")
    if target.exists():
        if target.stat().st_uid != os.getuid():
            raise RehearsalError("local TLS trust is missing")
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        _write_all(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def _last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            continue
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    raise RehearsalError("load command output was not json")


def _seal_load_failure(state_dir: Path, rules: Any, profile: str, stage: str) -> None:
    evidence = state_dir / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document(profile, stage), [])
    except (OSError, RehearsalError, rules.LoadSpecError):
        return
    try:
        _replace_private(
            evidence / "load.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, RehearsalError):
        return


def _execute_load_container(
    repo: Path, state_dir: Path, rules: Any, profile: str
) -> dict[str, Any]:
    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_load_failure(state_dir, rules, profile, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_load_failure(state_dir, rules, profile, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running load characterization")
    timeout = rules.resolve_profile(profile).duration_seconds + 180
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_LOAD_PROFILE=" + profile,
                "-e",
                "STAGING_LOAD_PROJECT=" + PROJECT,
                "-e",
                "STAGING_LOAD_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_load_characterization",
                "--profile",
                profile,
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_load_failure(state_dir, rules, profile, "characterization")
        raise RehearsalError("load command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.LoadSpecError:
        _seal_load_failure(state_dir, rules, profile, "evidence")
        raise RehearsalError("load evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "load.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_load_status") != "pass":
        raise RehearsalError("local load characterization failed")
    return sealed


def _seal_finance_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("receipt", stage), [])
    except (OSError, RehearsalError, rules.FinanceSpecError):
        return
    try:
        _replace_private(
            evidence / "finance.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, RehearsalError):
        return


def _execute_finance_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed receipt profile. This does not replace load.json."""

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_finance_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_finance_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running finance characterization")
    timeout = rules.resolve_profile("receipt").duration_seconds + 180
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_FINANCE_PROFILE=receipt",
                "-e",
                "STAGING_FINANCE_PROJECT=" + PROJECT,
                "-e",
                "STAGING_FINANCE_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_finance_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_finance_failure(state_dir, rules, "characterization")
        raise RehearsalError("finance command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.FinanceSpecError:
        _seal_finance_failure(state_dir, rules, "evidence")
        raise RehearsalError("finance evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "finance.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_finance_status") != "pass":
        raise RehearsalError("local finance characterization failed")
    return sealed


def execute_finance(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed receipt profile.

    Evidence is finance.json. This does not replace load.json. Ordinary run
    and CI do not execute it. Cleanup remains explicit.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_finance_rules(repo)
    rules.resolve_profile("receipt")
    try:
        execute_run(repo, state_dir)
    except RehearsalError:
        _seal_finance_failure(state_dir, rules, "rehearsal")
        raise
    lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
    try:
        return _execute_finance_container(repo, state_dir, rules)
    finally:
        lock.close()


def _hold_prior_rehearsal(state_dir: Path) -> str | None:
    path = state_dir / "evidence" / "rehearsal.json"
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _restore_prior_rehearsal(
    state_dir: Path, prior: str | None, *, sidecar: str = "rehearsal-pt.json"
) -> None:
    """Keep the previous rehearsal seal and retain this command's seal beside it."""

    if sidecar not in {
        "rehearsal-pt.json",
        "rehearsal-pt-cash.json",
        "rehearsal-endurance.json",
        "rehearsal-security.json",
        "rehearsal-identity-security.json",
        "rehearsal-isolation.json",
    }:
        return
    if prior is None:
        return
    path = state_dir / "evidence" / "rehearsal.json"
    if path.is_symlink():
        return
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current == prior:
        return
    if current:
        _replace_private(state_dir / "evidence" / sidecar, current)
    _replace_private(path, prior)


def _existing_pt_passed(evidence: Path) -> bool:
    path = evidence / "pt.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_pt_status") == "pass"


def _seal_pt_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        return
    if stage == "rehearsal" and _existing_pt_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("pt", stage), [])
    except (OSError, RehearsalError, rules.PtSpecError):
        return
    try:
        _replace_private(
            evidence / "pt.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, RehearsalError):
        return


def _execute_pt_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed pt profile. This does not replace load.json."""

    # This does not replace finance.json.

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_pt_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_pt_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running pt characterization")
    timeout = rules.resolve_profile("pt").duration_seconds + 180
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_PT_PROFILE=pt",
                "-e",
                "STAGING_PT_PROJECT=" + PROJECT,
                "-e",
                "STAGING_PT_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_pt_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_pt_failure(state_dir, rules, "characterization")
        raise RehearsalError("pt command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.PtSpecError:
        _seal_pt_failure(state_dir, rules, "evidence")
        raise RehearsalError("pt evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "pt.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_pt_status") != "pass":
        raise RehearsalError("local pt characterization failed")
    return sealed


def _load_pt_cash_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/pt_cash_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_pt_cash_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("pt-cash characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_endurance_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/endurance_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_endurance_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("endurance characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_security_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/security_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_security_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("security characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_identity_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/identity_security_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_identity_security_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("identity characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_isolation_rules(repo: Path) -> Any:
    backend = repo / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    path = repo / "backend/apps/common/isolation_characterization.py"
    spec = importlib.util.spec_from_file_location("gym_isolation_characterization", path)
    if spec is None or spec.loader is None:
        raise RehearsalError("isolation characterization module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _existing_endurance_passed(evidence: Path) -> bool:
    path = evidence / "endurance.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_endurance_status") == "pass"


def _seal_endurance_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        return
    if stage == "rehearsal" and _existing_endurance_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("endurance", stage), [])
    except (OSError, RehearsalError, rules.EnduranceSpecError):
        return
    try:
        _replace_private(
            evidence / "endurance.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, RehearsalError):
        return


def _execute_endurance_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed endurance profile. This does not replace pt.json.

    This does not replace pt-cash.json.
    """

    # This does not replace load.json.
    # This does not replace finance.json.

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_endurance_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_endurance_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running endurance characterization")
    timeout = rules.resolve_profile("endurance").duration_seconds + 240
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_ENDURANCE_PROFILE=endurance",
                "-e",
                "STAGING_ENDURANCE_PROJECT=" + PROJECT,
                "-e",
                "STAGING_ENDURANCE_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_endurance_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_endurance_failure(state_dir, rules, "characterization")
        raise RehearsalError("endurance command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.EnduranceSpecError:
        _seal_endurance_failure(state_dir, rules, "evidence")
        raise RehearsalError("endurance evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "endurance.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_endurance_status") != "pass":
        raise RehearsalError("local endurance characterization failed")
    return sealed


def execute_endurance(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed endurance profile.

    Evidence is endurance.json. This does not replace pt.json. This does not
    replace pt-cash.json. This does not replace load.json. This does not
    replace finance.json. A previous rehearsal.json is restored after the
    command. A rehearsal-gate failure leaves an existing passing endurance.json
    in place. Ordinary run and CI do not execute it. Cleanup remains explicit.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_endurance_rules(repo)
    rules.resolve_profile("endurance")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir)
        except RehearsalError:
            _seal_endurance_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_endurance_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(state_dir, prior_rehearsal, sidecar="rehearsal-endurance.json")


def _existing_security_passed(evidence: Path) -> bool:
    path = evidence / "security.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_security_status") == "pass"


def _seal_security_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stage == "rehearsal" and _existing_security_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("security", stage), [])
    except rules.SecuritySpecError:
        return
    _replace_private(
        evidence / "security.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )


def _execute_security_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed security profile. This does not replace endurance.json.

    This does not replace pt.json. This does not replace pt-cash.json.
    This does not replace load.json. This does not replace finance.json.
    """

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_security_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_security_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running security characterization")
    timeout = rules.DURATION_SECONDS + 300
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_SECURITY_PROFILE=security",
                "-e",
                "STAGING_SECURITY_PROJECT=" + PROJECT,
                "-e",
                "STAGING_SECURITY_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_security_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_security_failure(state_dir, rules, "characterization")
        raise RehearsalError("security command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.SecuritySpecError:
        _seal_security_failure(state_dir, rules, "evidence")
        raise RehearsalError("security evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "security.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_security_status") != "pass":
        raise RehearsalError("local security characterization failed")
    return sealed


def execute_security(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed security profile.

    Evidence is security.json. This does not replace endurance.json.
    This does not replace pt.json. This does not replace pt-cash.json.
    This does not replace load.json. This does not replace finance.json.
    A previous rehearsal.json is restored after the command. A rehearsal-gate
    failure leaves an existing passing security.json in place. Ordinary run
    and CI do not execute it. Cleanup remains explicit.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_security_rules(repo)
    rules.resolve_profile("security")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir)
        except RehearsalError:
            _seal_security_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_security_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(state_dir, prior_rehearsal, sidecar="rehearsal-security.json")


def _existing_identity_passed(evidence: Path) -> bool:
    path = evidence / "identity-security.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_identity_status") == "pass"


def _seal_identity_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stage == "rehearsal" and _existing_identity_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("identity-security", stage), [])
    except rules.IdentitySpecError:
        return
    _replace_private(
        evidence / "identity-security.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )


def _execute_identity_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed identity profile. This does not replace security.json.

    This does not replace endurance.json. This does not replace pt.json.
    This does not replace pt-cash.json. This does not replace load.json.
    This does not replace finance.json.
    """

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_identity_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_identity_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running identity characterization")
    timeout = rules.DURATION_SECONDS + 300
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_IDENTITY_PROFILE=identity-security",
                "-e",
                "STAGING_IDENTITY_PROJECT=" + PROJECT,
                "-e",
                "STAGING_IDENTITY_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_identity_security_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_identity_failure(state_dir, rules, "characterization")
        raise RehearsalError("identity command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.IdentitySpecError:
        _seal_identity_failure(state_dir, rules, "evidence")
        raise RehearsalError("identity evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "identity-security.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_identity_status") != "pass":
        raise RehearsalError("local identity characterization failed")
    return sealed


def execute_identity(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed identity-security profile.

    Evidence is identity-security.json. This does not replace security.json.
    This does not replace endurance.json. This does not replace pt.json.
    This does not replace pt-cash.json. This does not replace load.json.
    This does not replace finance.json. The coordinator is
    staging_identity_security_characterization. A previous rehearsal.json is
    restored after the command. A rehearsal-gate failure leaves an existing
    passing identity-security.json in place. Ordinary run and CI do not
    execute it. Cleanup remains explicit. Identity characterization
    does not wait for ClamAV TCP; that scanner path is not part of this matrix.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_identity_rules(repo)
    rules.resolve_profile("identity-security")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir, require_clamav_tcp=False)
        except RehearsalError:
            _seal_identity_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_identity_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(
            state_dir, prior_rehearsal, sidecar="rehearsal-identity-security.json"
        )


def _existing_isolation_passed(evidence: Path) -> bool:
    path = evidence / "isolation.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_isolation_status") == "pass"


def _seal_isolation_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stage == "rehearsal" and _existing_isolation_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("isolation", stage), [])
    except rules.IsolationSpecError:
        return
    _replace_private(
        evidence / "isolation.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )


def _execute_isolation_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed isolation profile. This does not replace identity-security.json.

    This does not replace security.json. This does not replace endurance.json.
    This does not replace pt.json. This does not replace pt-cash.json.
    This does not replace load.json. This does not replace finance.json.
    """

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_isolation_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_isolation_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running isolation characterization")
    timeout = rules.DURATION_SECONDS + 300
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_ISOLATION_PROFILE=isolation",
                "-e",
                "STAGING_ISOLATION_PROJECT=" + PROJECT,
                "-e",
                "STAGING_ISOLATION_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_isolation_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_isolation_failure(state_dir, rules, "characterization")
        raise RehearsalError("isolation command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.IsolationSpecError:
        _seal_isolation_failure(state_dir, rules, "evidence")
        raise RehearsalError("isolation evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "isolation.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_isolation_status") != "pass":
        raise RehearsalError("local isolation characterization failed")
    return sealed


def execute_isolation(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed isolation profile.

    Evidence is isolation.json. This does not replace identity-security.json.
    This does not replace security.json. This does not replace endurance.json.
    This does not replace pt.json. This does not replace pt-cash.json.
    This does not replace load.json. This does not replace finance.json. The
    coordinator is staging_isolation_characterization. A previous rehearsal.json
    is restored after the command. A rehearsal-gate failure leaves an existing
    passing isolation.json in place. Ordinary run and CI do not execute it.
    Cleanup remains explicit. Isolation characterization does not wait for
    ClamAV TCP; that scanner path is not part of this matrix.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_isolation_rules(repo)
    rules.resolve_profile("isolation")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir, require_clamav_tcp=False)
        except RehearsalError:
            _seal_isolation_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_isolation_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(state_dir, prior_rehearsal, sidecar="rehearsal-isolation.json")


def _existing_pt_cash_passed(evidence: Path) -> bool:
    path = evidence / "pt-cash.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("local_pt_cash_status") == "pass"


def _seal_pt_cash_failure(state_dir: Path, rules: Any, stage: str) -> None:
    evidence = state_dir / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        return
    if stage == "rehearsal" and _existing_pt_cash_passed(evidence):
        return
    try:
        sealed = rules.seal_evidence(rules.failure_document("pt-cash", stage), [])
    except (OSError, RehearsalError, rules.PtCashSpecError):
        return
    try:
        _replace_private(
            evidence / "pt-cash.json",
            json.dumps(sealed, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, RehearsalError):
        return


def _execute_pt_cash_container(repo: Path, state_dir: Path, rules: Any) -> dict[str, Any]:
    """Run the fixed pt-cash profile. This does not replace pt.json."""

    # This does not replace load.json.
    # This does not replace finance.json.

    env = os.environ.copy()
    env["DOCKER_HOST"] = _local_docker_host(env)
    env["DOCKER_CONFIG"] = str(state_dir / "docker")
    env_file = state_dir / "secrets" / "compose.env"
    if env_file.is_symlink() or not env_file.is_file():
        _seal_pt_cash_failure(state_dir, rules, "rehearsal")
        raise RehearsalError("rehearsal environment is missing")
    try:
        secret_values = cleanup_redaction_values(env_file.read_text(encoding="utf-8"))
    except RehearsalError:
        _seal_pt_cash_failure(state_dir, rules, "rehearsal")
        raise
    cafile = _publish_load_ca(state_dir)
    _phase("running pt cash characterization")
    timeout = rules.resolve_profile("pt-cash").duration_seconds + 180
    result = _run(
        _compose(
            repo,
            env_file,
            env,
            [
                "run",
                "--rm",
                "--no-deps",
                "-v",
                f"{cafile}:/run/staging-load/caddy-root.crt:ro",
                "-e",
                "STAGING_PT_CASH_PROFILE=pt-cash",
                "-e",
                "STAGING_PT_CASH_PROJECT=" + PROJECT,
                "-e",
                "STAGING_PT_CASH_CA_FILE=/run/staging-load/caddy-root.crt",
                "--entrypoint",
                "python",
                "api",
                "manage.py",
                "staging_pt_cash_characterization",
            ],
        ),
        env=env,
        timeout=timeout,
        secret_values=secret_values,
    )
    try:
        payload = _last_json_object(result.stdout)
    except (RehearsalError, json.JSONDecodeError):
        _seal_pt_cash_failure(state_dir, rules, "characterization")
        raise RehearsalError("pt-cash command output was not json") from None
    try:
        sealed = rules.seal_evidence(payload, secret_values)
    except rules.PtCashSpecError:
        _seal_pt_cash_failure(state_dir, rules, "evidence")
        raise RehearsalError("pt-cash evidence was refused") from None
    _replace_private(
        state_dir / "evidence" / "pt-cash.json",
        json.dumps(sealed, sort_keys=True, indent=2) + "\n",
    )
    if sealed.get("local_pt_cash_status") != "pass":
        raise RehearsalError("local pt-cash characterization failed")
    return sealed


def execute_pt_cash(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed pt-cash profile.

    Evidence is pt-cash.json. This does not replace pt.json. This does not
    replace load.json. This does not replace finance.json. A previous
    rehearsal.json is restored after the command. A rehearsal-gate failure
    leaves an existing passing pt-cash.json in place. Ordinary run and CI do
    not execute it. Cleanup remains explicit.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_pt_cash_rules(repo)
    rules.resolve_profile("pt-cash")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir)
        except RehearsalError:
            _seal_pt_cash_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_pt_cash_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(state_dir, prior_rehearsal, sidecar="rehearsal-pt-cash.json")


def execute_pt(repo: Path, state_dir: Path) -> dict[str, Any]:
    """Run the rehearsal, then the fixed pt profile.

    Evidence is pt.json. This does not replace load.json. This does not replace
    finance.json. A previous rehearsal.json is restored after the
    command. A rehearsal-gate failure leaves an existing passing pt.json in
    place. Ordinary run and CI do not execute it. Cleanup remains explicit.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_pt_rules(repo)
    rules.resolve_profile("pt")
    prior_rehearsal = _hold_prior_rehearsal(state_dir)
    try:
        try:
            execute_run(repo, state_dir)
        except RehearsalError:
            _seal_pt_failure(state_dir, rules, "rehearsal")
            raise
        lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
        try:
            return _execute_pt_container(repo, state_dir, rules)
        finally:
            lock.close()
    finally:
        _restore_prior_rehearsal(state_dir, prior_rehearsal)


def execute_load(repo: Path, state_dir: Path, profile: str) -> dict[str, Any]:
    """Run the increment 6 rehearsal, then one fixed load profile.

    The profile is opt-in. Ordinary ``run`` and CI do not execute it. The
    stack stays up; cleanup remains the explicit increment 6 command.
    """

    repo = repo.resolve()
    state_dir = resolve_state_dir(state_dir, repo)
    rules = _load_rules(repo)
    rules.resolve_profile(profile)
    try:
        execute_run(repo, state_dir)
    except RehearsalError:
        _seal_load_failure(state_dir, rules, profile, "rehearsal")
        raise
    lock = acquire_rehearsal_lock(state_dir / "rehearsal.lock")
    try:
        return _execute_load_container(repo, state_dir, rules, profile)
    finally:
        lock.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "run",
            "load",
            "finance",
            "pt",
            "pt-cash",
            "endurance",
            "security",
            "identity-security",
            "isolation",
            "cleanup",
        ),
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--confirmation-file", default="")
    parser.add_argument("--profile", choices=("short", "bounded"), default="short")
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo = Path(args.repo).resolve()
    try:
        if args.command == "preflight":
            print(json.dumps(run_preflight(repo), sort_keys=True))
            return 0
        state_dir = Path(args.state_dir) if args.state_dir else repo / ".staging-rehearsal"
        if args.command == "load":
            sealed = execute_load(repo, state_dir, args.profile)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": args.profile,
                        "evidence": "load.json",
                        "checksum": sealed.get("checksum"),
                        "local_load_status": sealed.get("local_load_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_financial_reconciliation_status": sealed.get(
                            "local_financial_reconciliation_status"
                        ),
                        "http_p95_ms": sealed.get("http_p95_ms"),
                        "postgres_connection_peak": sealed.get("postgres_connection_peak"),
                        "postgres_max_connections": sealed.get("postgres_max_connections"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "finance":
            sealed = execute_finance(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "receipt",
                        "evidence": "finance.json",
                        "checksum": sealed.get("checksum"),
                        "local_finance_status": sealed.get("local_finance_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_financial_reconciliation_status": sealed.get(
                            "local_financial_reconciliation_status"
                        ),
                        "local_soak_status": "not-run",
                        "http_p95_ms": sealed.get("http_p95_ms"),
                        "postgres_connection_peak": sealed.get("postgres_connection_peak"),
                        "postgres_max_connections": sealed.get("postgres_max_connections"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "pt":
            sealed = execute_pt(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "pt",
                        "evidence": "pt.json",
                        "checksum": sealed.get("checksum"),
                        "local_pt_status": sealed.get("local_pt_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_financial_reconciliation_status": sealed.get(
                            "local_financial_reconciliation_status"
                        ),
                        "local_soak_status": "not-run",
                        "http_p95_ms": sealed.get("http_p95_ms"),
                        "postgres_connection_peak": sealed.get("postgres_connection_peak"),
                        "postgres_max_connections": sealed.get("postgres_max_connections"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "pt-cash":
            sealed = execute_pt_cash(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "pt-cash",
                        "evidence": "pt-cash.json",
                        "checksum": sealed.get("checksum"),
                        "local_pt_cash_status": sealed.get("local_pt_cash_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_financial_reconciliation_status": sealed.get(
                            "local_financial_reconciliation_status"
                        ),
                        "local_soak_status": "not-run",
                        "http_p95_ms": sealed.get("http_p95_ms"),
                        "postgres_connection_peak": sealed.get("postgres_connection_peak"),
                        "postgres_max_connections": sealed.get("postgres_max_connections"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "endurance":
            sealed = execute_endurance(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "endurance",
                        "evidence": "endurance.json",
                        "checksum": sealed.get("checksum"),
                        "local_endurance_status": sealed.get("local_endurance_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_financial_reconciliation_status": sealed.get(
                            "local_financial_reconciliation_status"
                        ),
                        "local_soak_status": "not-run",
                        "duration_seconds": sealed.get("duration_seconds"),
                        "pace_seconds": sealed.get("pace_seconds"),
                        "workers": sealed.get("workers"),
                        "completed_operations": sealed.get("completed_operations"),
                        "cancelled_operations": sealed.get("cancelled_operations"),
                        "measured_phase_seconds": sealed.get("measured_phase_seconds"),
                        "http_p95_ms": sealed.get("http_p95_ms"),
                        "peak_active_workers": sealed.get("peak_active_workers"),
                        "postgres_connection_peak": sealed.get("postgres_connection_peak"),
                        "postgres_max_connections": sealed.get("postgres_max_connections"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "security":
            sealed = execute_security(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "security",
                        "evidence": "security.json",
                        "checksum": sealed.get("checksum"),
                        "local_security_status": sealed.get("local_security_status"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_soak_status": "not-run",
                        "completed_operations": sealed.get("completed_operations"),
                        "cancelled_operations": sealed.get("cancelled_operations"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "credential_exposure": sealed.get("credential_exposure"),
                        "data_corruption": sealed.get("data_corruption"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "penetration_test_proven": False,
                        "formal_review_proven": False,
                        "production_security_proven": False,
                        "tenant_isolation_proven": False,
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "identity-security":
            sealed = execute_identity(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "identity-security",
                        "evidence": "identity-security.json",
                        "checksum": sealed.get("checksum"),
                        "local_identity_status": sealed.get("local_identity_status"),
                        "local_security_status": "not-run",
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_soak_status": "not-run",
                        "completed_operations": sealed.get("completed_operations"),
                        "cancelled_operations": sealed.get("cancelled_operations"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "credential_exposure": sealed.get("credential_exposure"),
                        "data_corruption": sealed.get("data_corruption"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "penetration_test_proven": False,
                        "formal_review_proven": False,
                        "production_security_proven": False,
                        "tenant_isolation_proven": False,
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "isolation":
            sealed = execute_isolation(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "profile": "isolation",
                        "evidence": "isolation.json",
                        "checksum": sealed.get("checksum"),
                        "local_isolation_status": sealed.get("local_isolation_status"),
                        "local_security_status": "not-run",
                        "local_identity_status": "not-run",
                        "local_soak_status": "not-run",
                        "completed_operations": sealed.get("completed_operations"),
                        "cancelled_operations": sealed.get("cancelled_operations"),
                        "wall_time_seconds": sealed.get("wall_time_seconds"),
                        "cross_gym_leakage": sealed.get("cross_gym_leakage"),
                        "unexpected_errors": sealed.get("unexpected_errors"),
                        "credential_exposure": sealed.get("credential_exposure"),
                        "data_corruption": sealed.get("data_corruption"),
                        "caddy_metrics_observed": sealed.get("caddy_metrics_observed"),
                        "caddy_5xx_delta": sealed.get("caddy_5xx_delta"),
                        "penetration_test_proven": False,
                        "formal_review_proven": False,
                        "production_security_proven": False,
                        "tenant_isolation_proven": False,
                        "load_test_proven": False,
                        "soak_test_proven": False,
                        "production_capacity_proven": False,
                        "slo_proven": False,
                        "financial_reconciliation_proven": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "run":
            sealed = execute_run(repo, state_dir)
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "commands_completed": sealed.get("commands_completed"),
                        "checks": sealed.get("checks"),
                        **proof_flags(),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not args.confirmation_file:
            raise RehearsalError("cleanup token file is required")
        execute_cleanup(repo, state_dir, Path(args.confirmation_file))
        print(json.dumps({"cleaned": PROJECT, "evidence_retained": True}, sort_keys=True))
        return 0
    except RehearsalError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
