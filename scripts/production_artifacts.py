"""Offline checks for the Phase 8 increment 1 production artifacts.

These checks fail closed on mutable image references, published private ports,
and a missing migration/runtime split. They do not pull images, contact
providers, or prove a deployment.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

DIGEST_NAMES = (
    "API_IMAGE",
    "WEB_IMAGE",
    "POSTGRES_IMAGE",
    "CADDY_IMAGE",
    "CLAMAV_IMAGE",
    "PROMETHEUS_IMAGE",
    "ALERTMANAGER_IMAGE",
    "GRAFANA_IMAGE",
    "NODE_EXPORTER_IMAGE",
    "BLACKBOX_EXPORTER_IMAGE",
    "POSTGRES_EXPORTER_IMAGE",
)
SECRET_PLACEHOLDERS = {
    "POSTGRES_ADMIN_PASSWORD",
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
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
}
EMPTY_OK = {"MINIO_SESSION_TOKEN"}
EXPECTED_VALUES = {
    "PUBLIC_HOST": "app.example.com",
    "ACME_EMAIL": "ops@example.com",
    "APP_ORIGINS": "https://app.example.com",
    "APP_PUBLIC_URL": "https://app.example.com",
    "GOOGLE_OAUTH_CLIENT_IDS": (
        "603619909582-t5ociaflk5ck54ttm1atbk42oguvj4c1.apps.googleusercontent.com,"
        "603619909582-sqq7junqbia6qckn33t4cvbui2j1aeqo.apps.googleusercontent.com"
    ),
    "DEFAULT_FROM_EMAIL": "Gym <no-reply@example.com>",
    "POSTGRES_DB": "gym_platform",
    "POSTGRES_ADMIN_USER": "gym_admin",
    "MIGRATION_DB_USER": "gym_migration",
    "RUNTIME_DB_USER": "gym_runtime",
    "JWT_KEY_ID": "replace-with-key-id",
    "FCM_PROJECT_ID": "replace-with-project-id",
    "MINIO_ENDPOINT_URL": "https://s3.ap-southeast-1.amazonaws.com",
    "MINIO_PUBLIC_URL": "https://s3.ap-southeast-1.amazonaws.com",
    "MINIO_BUCKET": "replace-with-approved-bucket",
    "OBJECT_STORE_BUCKET_ALLOWLIST": "replace-with-approved-bucket",
    "OBJECT_STORE_ORIGIN_ALLOWLIST": "https://s3.ap-southeast-1.amazonaws.com",
    "BACKUP_S3_ENDPOINT": "https://backup-storage.example",
    "BACKUP_S3_REGION": "replace-with-approved-backup-region",
    "BACKUP_S3_BUCKET": "replace-with-approved-backup-bucket",
    "BACKUP_S3_URI_STYLE": "host",
    "BACKUP_REPO_PATH": "/gym-platform",
    "BACKUP_STANZA": "gym",
    "BACKUP_REPO_CIPHER_PASS_FILE": (
        "../environments/production/backup-repo-cipher-pass.example"
    ),
    "BACKUP_S3_ACCESS_KEY_FILE": "../environments/production/backup-s3-access-key.example",
    "BACKUP_S3_SECRET_KEY_FILE": "../environments/production/backup-s3-secret-key.example",
    "MONITOR_DB_PASSWORD_FILE": "../environments/production/monitor-db-password.example",
    "GRAFANA_ADMIN_PASSWORD_FILE": (
        "../environments/production/grafana-admin-password.example"
    ),
    "ALERT_DELIVERY_MODE": "resend-email",
    "ALERT_EMAIL_FROM": "alerts@example.com",
    "ALERT_EMAIL_TO": "ops@example.com",
    "ALERT_SMTP_PASSWORD_FILE": (
        "../environments/production/alert-smtp-password.example"
    ),
}
PLACEHOLDER = "replace-from-secret-store"
PRIVATE_SERVICES = (
    "web",
    "api",
    "postgres",
    "clamav",
    "migrate",
    "backup-full",
    "backup-diff",
    "backup-check",
    "backup-bootstrap",
    "prometheus",
    "alertmanager",
    "grafana",
    "node-exporter",
    "blackbox",
    "postgres-exporter",
)
STEADY_SERVICES = (
    "caddy",
    "web",
    "api",
    "postgres",
    "clamav",
    "prometheus",
    "alertmanager",
    "grafana",
    "node-exporter",
    "blackbox",
    "postgres-exporter",
)
OBSERVE_SERVICES = (
    "prometheus",
    "alertmanager",
    "grafana",
    "node-exporter",
    "blackbox",
    "postgres-exporter",
)
CORE_SERVICES = ("caddy", "web", "api", "postgres", "clamav", "migrate")
MEMBER_API_PATTERNS = (
    "/api/v1/member-auth",
    "/api/v1/member-auth/*",
    "/api/v1/member",
    "/api/v1/member/*",
)
MIGRATE_ENVIRONMENT = {
    "DJANGO_SETTINGS_MODULE": "config.settings.migration",
    "DJANGO_SECRET_KEY": "${DJANGO_SECRET_KEY:?Set DJANGO_SECRET_KEY}",
    "DATABASE_ROLE_MODE": "MIGRATION",
    "DB_NAME": "${POSTGRES_DB:?Set POSTGRES_DB}",
    "DB_HOST": "postgres",
    "DB_PORT": "5432",
    "DB_CONN_MAX_AGE": "0",
    "MIGRATION_DB_USER": "${MIGRATION_DB_USER:?Set MIGRATION_DB_USER}",
    "MIGRATION_DB_PASSWORD": "${MIGRATION_DB_PASSWORD:?Set MIGRATION_DB_PASSWORD}",
}
SERVICE_NETWORKS = {
    "caddy": {"edge", "app", "probe", "observe"},
    "web": {"edge", "app", "probe"},
    "api": {"app", "data", "scan", "probe"},
    "postgres": {"data", "backup"},
    "clamav": {"scan"},
    "migrate": {"data"},
    "backup-full": {"backup"},
    "backup-diff": {"backup"},
    "backup-check": {"backup"},
    "backup-bootstrap": {"backup"},
    "prometheus": {"observe"},
    "alertmanager": {"observe"},
    "grafana": {"observe"},
    "node-exporter": {"observe"},
    "blackbox": {"probe", "observe"},
    "postgres-exporter": {"data", "observe"},
}
CAP_DROP_SERVICES = (
    "caddy",
    "web",
    "api",
    "migrate",
    "prometheus",
    "alertmanager",
    "grafana",
    "node-exporter",
    "blackbox",
    "postgres-exporter",
)
CONNECTION_RESERVE = 8
MONITOR_CONNECTIONS = 1
STEADY_MEMORY_CEILING_MB = 5888
STEADY_CPU_CEILING = 3.25
HOST_MEMORY_MB = 8192
MEMORY_RESERVE_MB = 2048
SECRET_MARKERS = ("AKIA", "-----BEGIN", "re_", "xox", "ghp_", "github_pat_", "eyJ")
VARIABLE_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::\?[^}]*)?\}")
SECRET_ARG_RE = re.compile(
    r"ARG\s+([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|PEPPER|KEY)[A-Z0-9_]*)"
)


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the restricted mapping style used by the production Compose file."""
    lines: list[tuple[int, str]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise ValueError(f"tabs are not allowed on line {line_no}")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((line_no, raw))
    value, index = _parse_node(lines, 0, 0)
    if index != len(lines):
        raise ValueError(f"unparsed content at line {lines[index][0]}")
    if not isinstance(value, dict):
        raise ValueError("root must be a mapping")
    return value


def lint_image_reference(value: str) -> list[str]:
    if not value or value == PLACEHOLDER:
        return ["image digest is missing"]
    if re.search(r"(^|[/])latest($|@)", value) or value.endswith(":latest"):
        return [f"mutable image reference: {value}"]
    if "@sha256:" not in value:
        return [f"image is not digest pinned: {value}"]
    name, digest = value.rsplit("@", 1)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return [f"image digest is not 64 lowercase hex characters: {value}"]
    parts = name.split("/")
    if ":" in parts[-1]:
        return [f"image reference includes a tag: {value}"]
    if len(parts) > 1 and ":" in parts[0]:
        port = parts[0].rsplit(":", 1)[1]
        if not port.isdigit():
            return [f"image reference includes a tag: {value}"]
    return []


def lint_compose_source(text: str) -> list[str]:
    errors: list[str] = []
    if ":-" in text:
        errors.append(
            "Compose defaults are not allowed; a default can hide a mutable tag"
        )
    if re.search(r"\blatest\b", text):
        errors.append("Compose source contains a mutable latest reference")
    for banned in ("pgsty", "silo", "redis", "celery", "kubernetes"):
        if banned in text.lower():
            errors.append(f"Compose source contains unsupported {banned}")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("image:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if not re.fullmatch(r"\$\{[A-Z0-9_]+:\?[^}]+\}\Z", value):
            errors.append(f"image must be a required digest variable: {stripped}")
    if "DJANGO_LISTEN_HOST" in text:
        errors.append("DJANGO_LISTEN_HOST is not a production bind control")
    return errors


def lint_compose_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    services = document.get("services")
    if not isinstance(services, dict):
        return ["Compose services mapping is missing"]
    expected = {"caddy", *PRIVATE_SERVICES}
    if set(services) != expected:
        errors.append(f"services must be {sorted(expected)}")
        return errors
    networks = document.get("networks")
    if not isinstance(networks, dict) or set(networks) != {
        "edge",
        "app",
        "data",
        "scan",
        "backup",
        "probe",
        "observe",
    }:
        errors.append(
            "networks must be edge, app, data, scan, backup, probe, and observe"
        )
    else:
        for name, internal in (
            ("data", "true"),
            ("scan", "true"),
            ("probe", "true"),
            ("observe", "true"),
            ("edge", "false"),
            ("app", "false"),
            ("backup", "false"),
        ):
            spec = networks.get(name)
            if not isinstance(spec, dict) or spec.get("internal") != internal:
                errors.append(f"{name} internal must be {internal}")
    volumes = document.get("volumes")
    if not isinstance(volumes, dict):
        errors.append("persistent volumes are missing")
    else:
        for name in (
            "postgres_data",
            "postgres_socket",
            "pgbackrest_spool",
            "pgbackrest_log",
            "clamav_data",
            "caddy_data",
            "caddy_config",
            "backup_metrics",
            "prometheus_data",
            "alertmanager_data",
            "grafana_data",
        ):
            if name not in volumes:
                errors.append(f"missing volume {name}")
        if any("minio" in name.lower() for name in volumes):
            errors.append("production volumes must not include object storage")

    memory_total = 0
    cpu_total = 0.0
    for name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"{name} is not a mapping")
            continue
        errors.extend(_lint_service(name, service))
        if name in STEADY_SERVICES:
            memory_total += _memory_mb(service.get("mem_limit"), name, errors)
            cpu_total += _cpu(service.get("cpus"), name, errors)
    if memory_total > STEADY_MEMORY_CEILING_MB:
        errors.append(
            f"steady memory {memory_total}m exceeds {STEADY_MEMORY_CEILING_MB}m"
        )
    if round(cpu_total, 2) > STEADY_CPU_CEILING:
        errors.append(f"steady CPU {cpu_total} exceeds {STEADY_CPU_CEILING}")
    if memory_total + MEMORY_RESERVE_MB > HOST_MEMORY_MB:
        errors.append(
            f"steady memory {memory_total}m leaves less than {MEMORY_RESERVE_MB}m "
            "for the OS and backup"
        )
    errors.extend(_lint_roles(services))
    errors.extend(_lint_connections(services))
    return errors


def caddy_upstream(path: str) -> str:
    """Classify one public path the way the member allowlist must."""

    for pattern in MEMBER_API_PATTERNS:
        prefix = pattern[:-1] if pattern.endswith("*") else None
        if prefix is not None and path.startswith(prefix):
            return "api"
        if path == pattern:
            return "api"
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return "reject"
    return "web"


def lint_caddy(text: str) -> list[str]:
    errors: list[str] = []
    if _matcher_paths(text, "member") != list(MEMBER_API_PATTERNS):
        errors.append(
            "Caddy member proxy must allow only /api/v1/member-auth and /api/v1/member"
        )
    if _matcher_paths(text, "blocked") != ["/api/v1", "/api/v1/*"]:
        errors.append("Caddy must reject every other /api/v1 route")
    if text.count("reverse_proxy api:8000") != 1:
        errors.append("Caddy must proxy Django exactly once")
    member_at = text.find("handle @member {")
    proxy_at = text.find("reverse_proxy api:8000")
    blocked_at = text.find("handle @blocked {")
    if member_at < 0 or proxy_at < member_at or blocked_at < proxy_at:
        errors.append("Django proxy must stay inside the member allowlist")
    if "reverse_proxy web:3000" not in text:
        errors.append("Caddy must reverse proxy browser routes to Next.js")
    if re.search(r"reverse_proxy\s+(postgres|clamav)\b", text):
        errors.append("Caddy must not proxy PostgreSQL or ClamAV")
    if 'respond "Not found" 404' not in text:
        errors.append("Caddy must reject blocked API routes")
    if "admin off" not in text:
        errors.append("Caddy admin listener must be off")
    for marker in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "nosniff",
        "DENY",
    ):
        if marker not in text:
            errors.append(f"Caddy is missing {marker}")
    if "minio" in text.lower() or re.search(r"reverse_proxy\s+\S*s3", text.lower()):
        errors.append("Caddy must not proxy object storage")
    if "admin off" not in text or re.search(r"(?m)^\s*admin\s+(?!off\b)", text):
        errors.append("Caddy admin listener must stay off")
    public = text.split("{$PUBLIC_HOST}", 1)
    if len(public) != 2 or re.search(r"(?m)^\s*metrics\b", public[1]):
        errors.append("Caddy must not serve metrics on the public site")
    if "http://:2020" not in text or "per_host" in text:
        errors.append("Caddy metrics must stay on the private :2020 listener")
    for header in (
        "request>headers>Authorization delete",
        "request>headers>Cookie delete",
        "request>headers>X-Gym-Bff-Secret delete",
        "resp_headers>Set-Cookie delete",
    ):
        if header not in text:
            errors.append(f"Caddy access log must omit {header.split('>')[-1]}")
    if "request>uri replace" not in text or r"\?" not in text:
        errors.append("Caddy access log must strip the query string")
    if "output stdout" not in text or "wrap json" not in text:
        errors.append("Caddy access log must be JSON on stdout")
    if "{gym_request_id}" not in text:
        errors.append("Caddy must forward one normalized request ID")
    if r"\{1,128\}" in text:
        errors.append("Caddy request ID quantifier must be valid RE2")
    capture = "\n\t\t~^([A-Za-z0-9._:-]{1,128})$ $1\n"
    if capture not in text:
        errors.append("Caddy request ID matcher must capture 1-128 characters into $1")
    if "{http.request.header.X-Request-Id}" not in text:
        errors.append(
            "Caddy request ID source must use the canonical header placeholder"
        )
    return errors


def _matcher_paths(text: str, name: str) -> list[str]:
    prefix = f"@{name} path "
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].split()
    return []


def _lint_secret_args(text: str) -> list[str]:
    return [
        f"image build arg looks like a secret: {arg}"
        for arg in SECRET_ARG_RE.findall(text)
        if arg != "NEXT_PUBLIC_OBJECT_ORIGINS"
    ]


def lint_backend_dockerfile(text: str) -> list[str]:
    errors: list[str] = []
    if not text.startswith("FROM python:3.14.7-slim\n"):
        errors.append("API image must start from python:3.14.7-slim")
    if "requirements/production.txt" not in text:
        errors.append("API image must install requirements/production.txt")
    if "USER api" not in text:
        errors.append("API image must run as the api user")
    if "COPY . " in text or "COPY . ." in text:
        errors.append("API image must copy explicit source paths")
    errors.extend(_lint_secret_args(text))
    return errors


def lint_web_dockerfile(text: str) -> list[str]:
    errors: list[str] = []
    if text.count("FROM node:24.20.0-slim") < 3:
        errors.append("web image must be a three-stage node:24.20.0-slim build")
    if "USER web" not in text or ".next/standalone" not in text:
        errors.append("web image must run the standalone server as web")
    errors.extend(_lint_secret_args(text))
    if "NEXT_PUBLIC_ALLOW_LOCAL_OBJECT_HTTP" in text:
        errors.append("production web build must not allow local object HTTP")
    return errors


def lint_env_example(text: str, compose_text: str) -> list[str]:
    errors: list[str] = []
    values = _load_env(text)
    required = set(VARIABLE_RE.findall(compose_text))
    classified = (
        set(DIGEST_NAMES) | SECRET_PLACEHOLDERS | EMPTY_OK | set(EXPECTED_VALUES)
    )
    if required - classified:
        errors.append(
            f"unclassified Compose variables: {sorted(required - classified)}"
        )
    for name in required:
        if name not in values:
            errors.append(f"compose env example is missing {name}")
    for name, value in values.items():
        if name not in classified:
            errors.append(f"unclassified env example value: {name}")
        if any(marker in value for marker in SECRET_MARKERS):
            errors.append(f"{name} looks like a live secret")
        if name in DIGEST_NAMES:
            errors.extend(f"{name}: {item}" for item in lint_image_reference(value))
        elif name in SECRET_PLACEHOLDERS and value != PLACEHOLDER:
            errors.append(f"{name} must stay {PLACEHOLDER} in the committed example")
        elif name in EXPECTED_VALUES and value != EXPECTED_VALUES[name]:
            errors.append(f"{name} drifted from the documented non-secret example")
        elif name in EMPTY_OK and value != "":
            errors.append(f"{name} must be empty in the committed example")
        if (
            ":?" in compose_text
            and name in required
            and value == ""
            and name not in EMPTY_OK
        ):
            errors.append(f"{name} is required and empty")
    for name in required:
        if ":?" in _variable_usage(compose_text, name) and values.get(name, "") == "":
            errors.append(f"{name} is required by Compose and empty in the example")
    return errors


def lint_repository(repo: Path) -> list[str]:
    compose_path = repo / "infra/compose/docker-compose.prod.yml"
    env_path = repo / "infra/environments/production/compose.env.example"
    errors: list[str] = []
    compose_text = compose_path.read_text(encoding="utf-8")
    errors.extend(lint_compose_source(compose_text))
    try:
        document = parse_simple_yaml(compose_text)
    except ValueError as exc:
        return [*errors, f"Compose parser: {exc}"]
    errors.extend(lint_compose_document(document))
    errors.extend(
        lint_caddy((repo / "infra/caddy/Caddyfile").read_text(encoding="utf-8"))
    )
    errors.extend(
        lint_backend_dockerfile(
            (repo / "backend/Dockerfile").read_text(encoding="utf-8")
        )
    )
    errors.extend(
        lint_web_dockerfile((repo / "web/Dockerfile").read_text(encoding="utf-8"))
    )
    errors.extend(lint_env_example(env_path.read_text(encoding="utf-8"), compose_text))
    errors.extend(_lint_supporting_files(repo))
    errors.extend(_lint_release(repo))
    errors.extend(_lint_backup(repo, document if isinstance(document, dict) else {}))
    errors.extend(
        _lint_observability(repo, document if isinstance(document, dict) else {})
    )
    errors.extend(_lint_deployment(repo))
    errors.extend(_lint_staging(repo))
    return errors


def _lint_backup(repo: Path, document: dict[str, Any]) -> list[str]:
    path = repo / "infra" / "postgres"
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    from backup_policy import lint_backup_baseline

    return lint_backup_baseline(repo, document)


def _lint_observability(repo: Path, document: dict[str, Any]) -> list[str]:
    scripts = str(repo / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from observability import lint_observability

    return lint_observability(repo, document)


def _lint_release(repo: Path) -> list[str]:
    scripts = str(repo / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from release_supply_chain import lint_repository_release

    return lint_repository_release(repo)


def _lint_staging(repo: Path) -> list[str]:
    scripts = str(repo / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from staging_rehearsal import lint_repository as lint_staging_rehearsal

    return lint_staging_rehearsal(repo)


def _lint_deployment(repo: Path) -> list[str]:
    scripts = str(repo / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from deployment_control import lint_repository_deployment

    return lint_repository_deployment(repo)


def offline_environment() -> dict[str, str]:
    """Format-valid settings for Django's deployment check. Not live credentials."""

    def marker(name: str) -> str:
        return f"offline-{name}-not-a-live-secret-value-0001"

    return {
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_SECRET_KEY": marker("django-secret-key"),
        "ALLOWED_HOSTS": "app.example.com,api,127.0.0.1",
        "DATABASE_ROLE_MODE": "runtime",
        "DB_NAME": "gym_platform",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_CONN_MAX_AGE": "0",
        "RUNTIME_DB_USER": "gym_runtime",
        "RUNTIME_DB_PASSWORD": marker("runtime-db"),
        "JWT_ISSUER": "gym-platform-api",
        "JWT_STAFF_AUDIENCE": "gym-platform",
        "JWT_MEMBER_AUDIENCE": "gym-platform-member",
        "JWT_KEY_ID": "offline-key",
        "JWT_PRIVATE_KEY": marker("jwt-private"),
        "JWT_PUBLIC_KEYS": "offline-key=offline-public-material",
        "REFRESH_TOKEN_PEPPER": marker("refresh-pepper"),
        "MFA_TOKEN_PEPPER": marker("mfa-pepper"),
        "MFA_ENCRYPTION_KEY": marker("mfa-encryption"),
        "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": marker("finance-key"),
        "INVITATION_TOKEN_PEPPER": marker("invitation-pepper"),
        "PASSWORD_RESET_TOKEN_PEPPER": marker("reset-pepper"),
        "PASSWORD_RESET_TTL_SECONDS": "1800",
        "BFF_SHARED_SECRET": marker("bff-secret"),
        "GOOGLE_OAUTH_CLIENT_IDS": (
            "603619909582-t5ociaflk5ck54ttm1atbk42oguvj4c1.apps.googleusercontent.com,"
            "603619909582-sqq7junqbia6qckn33t4cvbui2j1aeqo.apps.googleusercontent.com"
        ),
        "APP_PUBLIC_URL": "https://app.example.com",
        "EMAIL_BACKEND": "apps.common.mail.resend.EmailBackend",
        "DEFAULT_FROM_EMAIL": "Gym <no-reply@example.com>",
        "RESEND_API_KEY": marker("resend"),
        "PUSH_BACKEND": "fcm",
        "FCM_PROJECT_ID": "offline-project",
        "FCM_SERVICE_ACCOUNT_JSON": marker("fcm-json"),
        "PUSH_TOKEN_ENCRYPTION_KEY": marker("push-encryption"),
        "PUSH_TOKEN_FINGERPRINT_PEPPER": marker("push-fingerprint"),
        "MINIO_ENDPOINT_URL": "https://s3.ap-southeast-1.amazonaws.com",
        "MINIO_PUBLIC_URL": "https://s3.ap-southeast-1.amazonaws.com",
        "MINIO_BUCKET": "offline-bucket",
        "MINIO_REGION": "ap-southeast-1",
        "MINIO_ACCESS_KEY": marker("s3-access"),
        "MINIO_SECRET_KEY": marker("s3-secret"),
        "MINIO_SESSION_TOKEN": "",
        "MINIO_ADDRESSING_STYLE": "virtual",
        "OBJECT_STORE_BUCKET_ALLOWLIST": "offline-bucket",
        "OBJECT_STORE_ORIGIN_ALLOWLIST": "https://s3.ap-southeast-1.amazonaws.com",
        "CLAMAV_HOST": "clamav",
        "CLAMAV_PORT": "3310",
    }


def run_django_deploy_check(repo: Path) -> list[str]:
    backend = repo / "backend"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(offline_environment())
    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "check",
            "--deploy",
            "--tag",
            "security",
            "--fail-level",
            "WARNING",
        ],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return []
    detail = (result.stderr or result.stdout).strip()
    return [f"Django deployment check failed:\n{detail}"]


def run_compose_config(repo: Path) -> list[str]:
    if shutil.which("docker") is None:
        return ["docker is not available"]
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(repo / "infra/compose"),
        "--env-file",
        str(repo / "infra/environments/production/compose.env.example"),
        "-f",
        str(repo / "infra/compose/docker-compose.prod.yml"),
        "config",
        "--quiet",
    ]
    result = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return []
    detail = (result.stderr or result.stdout or "docker compose config failed").strip()
    return [f"docker compose config failed:\n{detail}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--require-django", action="store_true")
    parser.add_argument("--require-docker", action="store_true")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    errors = lint_repository(repo)
    if not args.static_only:
        if args.require_django or _django_importable():
            errors.extend(run_django_deploy_check(repo))
        elif args.require_django:
            errors.append("Django is not importable")
        docker_errors = run_compose_config(repo)
        if docker_errors == ["docker is not available"] and not args.require_docker:
            print(
                "docker compose config skipped; docker is not available",
                file=sys.stderr,
            )
        else:
            errors.extend(docker_errors)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Production artifact checks passed. This is not a live deployment.")
    return 0


def _django_importable() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import django"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _lint_service(name: str, service: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if service.get("networks") != _network_list(name):
        errors.append(f"{name} networks must be {sorted(SERVICE_NETWORKS[name])}")
    if name in PRIVATE_SERVICES and "ports" in service:
        errors.append(f"{name} must not publish a host port")
    if name == "caddy":
        if service.get("ports") != ["80:80", "443:443"]:
            errors.append("caddy may publish only 80:80 and 443:443")
    if service.get("read_only") != "true":
        errors.append(f"{name} must be read-only")
    if "no-new-privileges:true" not in _as_list(service.get("security_opt")):
        errors.append(f"{name} must set no-new-privileges")
    if name in CAP_DROP_SERVICES and "ALL" not in _as_list(service.get("cap_drop")):
        errors.append(f"{name} must drop all capabilities")
    if name == "caddy" and "NET_BIND_SERVICE" not in _as_list(service.get("cap_add")):
        errors.append("caddy needs NET_BIND_SERVICE after dropping capabilities")
    if name in {"api", "web", "migrate"} and service.get("user") != "10001:10001":
        errors.append(f"{name} must run as uid 10001")
    if (
        name != "migrate"
        and not name.startswith("backup-")
        and "healthcheck" not in service
    ):
        errors.append(f"{name} is missing a healthcheck")
    logging = service.get("logging")
    options = logging.get("options") if isinstance(logging, dict) else None
    if not isinstance(options, dict) or "max-size" not in options:
        errors.append(f"{name} logs must be size-capped")
    if "mem_limit" not in service or "cpus" not in service:
        errors.append(f"{name} is missing a CPU or memory limit")
    return errors


def _lint_roles(services: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    api = services["api"]["environment"]
    migrate = services["migrate"]["environment"]
    postgres = services["postgres"]["environment"]
    web = services["web"]["environment"]
    if not isinstance(api, dict) or not isinstance(migrate, dict):
        return ["api and migrate environments must be mappings"]
    if api.get("DATABASE_ROLE_MODE") != "runtime":
        errors.append("api must use the runtime database role")
    if migrate.get("DATABASE_ROLE_MODE") != "MIGRATION":
        errors.append("migrate must use the migration database role")
    if "MIGRATION_DB_PASSWORD" in api or "POSTGRES_ADMIN_PASSWORD" in api:
        errors.append("api must not receive migration or admin database secrets")
    if "RUNTIME_DB_PASSWORD" in migrate or "POSTGRES_ADMIN_PASSWORD" in migrate:
        errors.append("migrate must not receive runtime or admin database secrets")
    if migrate != MIGRATE_ENVIRONMENT:
        errors.append(
            "migrate environment must contain only the migration identity and Django secret"
        )
    if api.get("ALLOWED_HOSTS") != "${PUBLIC_HOST:?Set PUBLIC_HOST},api,127.0.0.1":
        errors.append(
            "api must accept the public host plus private health and BFF hosts"
        )
    if "DJANGO_LISTEN_HOST" in api or "DJANGO_LISTEN_HOST" in migrate:
        errors.append("DJANGO_LISTEN_HOST is not a production bind control")
    if api.get("RUNTIME_DB_USER") != "${RUNTIME_DB_USER:?Set RUNTIME_DB_USER}":
        errors.append("api runtime user must come from RUNTIME_DB_USER")
    if (
        migrate.get("MIGRATION_DB_USER")
        != "${MIGRATION_DB_USER:?Set MIGRATION_DB_USER}"
    ):
        errors.append("migrate user must come from MIGRATION_DB_USER")
    if services["migrate"].get("profiles") != ["migrate"]:
        errors.append("migrate must be the one-shot migrate profile")
    if services["migrate"].get("restart") != "no":
        errors.append("migrate must not restart")
    command = services["migrate"].get("command")
    if command != ["python", "manage.py", "migrate", "--noinput"]:
        errors.append("migrate command must be manage.py migrate --noinput")
    if web.get("DJANGO_API_URL") != "http://api:8000":
        errors.append("web must call the private API service")
    if "ALLOW_INSECURE_LOCALHOST_COOKIES" in web:
        errors.append("production web must not allow insecure cookies")
    joined = " ".join(services["postgres"].get("command", []))
    if "max_connections=40" not in joined:
        errors.append("postgres must set max_connections=40")
    if "JWT_PRIVATE_KEY" in postgres or "BFF_SHARED_SECRET" in postgres:
        errors.append("postgres must not receive application secrets")
    if postgres.get("POSTGRES_MIGRATION_PASSWORD") != (
        "${MIGRATION_DB_PASSWORD:?Set MIGRATION_DB_PASSWORD}"
    ):
        errors.append(
            "postgres must initialize gym_migration from MIGRATION_DB_PASSWORD"
        )
    if postgres.get("POSTGRES_RUNTIME_PASSWORD") != (
        "${RUNTIME_DB_PASSWORD:?Set RUNTIME_DB_PASSWORD}"
    ):
        errors.append("postgres must initialize gym_runtime from RUNTIME_DB_PASSWORD")
    return errors


def _lint_connections(services: dict[str, Any]) -> list[str]:
    api = services["api"]["environment"]
    if api.get("WEB_CONCURRENCY") != "2" or api.get("DB_CONN_MAX_AGE") != "60":
        return ["api connection budget must stay at 2 workers and CONN_MAX_AGE 60"]
    joined = " ".join(services["postgres"].get("command", []))
    match = re.search(r"max_connections=(\d+)", joined)
    if match is None:
        return ["postgres max_connections is missing"]
    maximum = int(match.group(1))
    if "max_wal_senders=3" not in joined:
        return ["postgres max_wal_senders must stay at 3 for backup"]
    if 2 + MONITOR_CONNECTIONS + CONNECTION_RESERVE + 3 >= maximum:
        return [
            "worker connections, WAL senders, and the reserve must fit max_connections"
        ]
    return []


def _lint_supporting_files(repo: Path) -> list[str]:
    errors: list[str] = []
    gunicorn = (repo / "backend/gunicorn.conf.py").read_text(encoding="utf-8")
    if 'worker_class = "sync"' not in gunicorn or "threads = 1" not in gunicorn:
        errors.append("Gunicorn must stay sync with one thread")
    if "workers > 4" not in gunicorn:
        errors.append("Gunicorn must reject WEB_CONCURRENCY above 4")
    if "accesslog = None" not in gunicorn:
        errors.append("Gunicorn must not log the raw request target")
    production = (repo / "backend/requirements/production.txt").read_text(
        encoding="utf-8"
    )
    for pin in ("gunicorn==26.2.0", "drf-spectacular==0.30.0"):
        if pin not in production:
            errors.append(f"production requirements missing {pin}")
    settings = (repo / "backend/config/settings/production.py").read_text(
        encoding="utf-8"
    )
    if "SECURE_SSL_REDIRECT = True" not in settings:
        errors.append("production TLS redirect must stay enabled")
    if "SECURE_PROXY_SSL_HEADER" not in settings:
        errors.append(
            "production settings must declare the private forwarded-proto header"
        )
    if "django.middleware.csrf.CsrfViewMiddleware" not in settings:
        errors.append("production settings must keep Django CSRF middleware")
    if "XFrameOptionsMiddleware" not in settings:
        errors.append("production settings must keep the clickjacking middleware")
    if "DJANGO_LISTEN_HOST" in settings:
        errors.append(
            "production settings must not treat DJANGO_LISTEN_HOST as a bind control"
        )
    if "STRUCTURED_REQUEST_LOG = True" not in settings:
        errors.append("production request logs must be enabled")
    if "JsonLogFormatter" not in settings or '"gym.http"' not in settings:
        errors.append("production logs must use the JSON request formatter")
    if '"django.request"' not in settings or '"CRITICAL"' not in settings:
        errors.append("Django's raw request logger must stay quiet")
    migration = (repo / "backend/config/settings/migration.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "DATABASE_ROLE_MODE",
        "MIGRATION",
        "gym_migration",
        "MIGRATION_DB_PASSWORD",
        "DJANGO_SECRET_KEY",
    ):
        if required not in migration:
            errors.append(f"migration settings missing {required}")
    if "require_resend_api_key" in migration or "JWT_PRIVATE_KEY" in migration:
        errors.append("migration settings must not require provider secrets")
    next_config = (repo / "web/next.config.ts").read_text(encoding="utf-8")
    if 'output: "standalone"' not in next_config:
        errors.append("Next.js must build a standalone server")
    api_template = (repo / "backend/.env.production.example").read_text(
        encoding="utf-8"
    )
    for name in (
        "MINIO_ENDPOINT_URL",
        "RESEND_API_KEY",
        "CLAMAV_HOST",
        "DATABASE_ROLE_MODE",
    ):
        if name not in api_template:
            errors.append(f"API production template is missing {name}")
    if any(marker in api_template for marker in SECRET_MARKERS):
        errors.append("API production template contains a live-secret marker")
    return errors


def _network_list(name: str) -> list[str]:
    order = ["edge", "app", "data", "scan", "backup", "probe", "observe"]
    return [item for item in order if item in SERVICE_NETWORKS[name]]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _memory_mb(value: Any, name: str, errors: list[str]) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d+m", value):
        errors.append(f"{name} mem_limit must be an explicit megabyte value")
        return 0
    return int(value[:-1])


def _cpu(value: Any, name: str, errors: list[str]) -> float:
    if not isinstance(value, str):
        errors.append(f"{name} cpus must be an explicit string")
        return 0.0
    try:
        return float(value)
    except ValueError:
        errors.append(f"{name} cpus must be numeric")
        return 0.0


def _load_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def _variable_usage(text: str, name: str) -> str:
    match = re.search(rf"\$\{{{name}(?::\?[^}}]*)?\}}", text)
    return match.group(0) if match else ""


def _parse_node(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines) or _indent(lines[index][1]) < indent:
        return {}, index
    if lines[index][1].strip().startswith("- "):
        return _parse_list(lines, index, _indent(lines[index][1]))
    return _parse_map(lines, index, _indent(lines[index][1]))


def _parse_map(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_no, raw = lines[index]
        current = _indent(raw)
        if current < indent:
            break
        if current != indent:
            raise ValueError(f"unexpected indent on line {line_no}")
        content = raw.strip()
        if content.startswith("- "):
            raise ValueError(f"list item inside a mapping on line {line_no}")
        key, raw_value = _split_key(content, line_no)
        if raw_value is None:
            index += 1
            if index >= len(lines) or _indent(lines[index][1]) <= indent:
                result[key] = {}
                continue
            nested, index = _parse_node(lines, index, indent + 2)
            result[key] = nested
        else:
            result[key] = _scalar(raw_value)
            index += 1
    return result, index


def _parse_list(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_no, raw = lines[index]
        if _indent(raw) < indent:
            break
        if _indent(raw) != indent or not raw.strip().startswith("- "):
            break
        item = raw.strip()[2:]
        if _looks_like_key(item):
            key, raw_value = _split_key(item, line_no)
            mapping: dict[str, Any] = {}
            child_indent = indent + 2
            if raw_value is None:
                index += 1
                nested, index = _parse_node(lines, index, child_indent)
                mapping[key] = nested
            else:
                mapping[key] = _scalar(raw_value)
                index += 1
                while index < len(lines):
                    next_no, next_raw = lines[index]
                    if _indent(next_raw) != child_indent or next_raw.strip().startswith(
                        "- "
                    ):
                        break
                    child_key, child_value = _split_key(next_raw.strip(), next_no)
                    if child_value is None:
                        index += 1
                        nested, index = _parse_node(lines, index, child_indent + 2)
                        mapping[child_key] = nested
                    else:
                        mapping[child_key] = _scalar(child_value)
                        index += 1
            result.append(mapping)
        else:
            result.append(_scalar(item))
            index += 1
    return result, index


def _looks_like_key(content: str) -> bool:
    return ": " in content or content.endswith(":")


def _split_key(content: str, line_no: int) -> tuple[str, str | None]:
    if ": " in content:
        key, value = content.split(": ", 1)
        return key.strip(), value.strip()
    if content.endswith(":"):
        return content[:-1].strip(), None
    raise ValueError(f"expected key on line {line_no}")


def _scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


if __name__ == "__main__":
    sys.exit(main())
