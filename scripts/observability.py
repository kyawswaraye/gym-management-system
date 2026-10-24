"""Offline checks for the Phase 8 increment 4 observability baseline.

These checks do not scrape a live target, deliver an alert, or prove an SLO.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SINK = "http://127.0.0.1:9/gym-observability-non-production-sink"
PLACEHOLDER = "replace-from-secret-store"
OBSERVE_SERVICES = (
    "prometheus",
    "alertmanager",
    "grafana",
    "node-exporter",
    "blackbox",
    "postgres-exporter",
)
CORE_SERVICES = ("caddy", "web", "api", "postgres", "clamav", "migrate")
PROBE_TARGETS = {
    "http://web:3000/login",
    "http://api:8000/api/v1/health/",
    "caddy:443",
}
HOST_MOUNTS = {
    "/proc:/host/proc:ro",
    "/sys:/host/sys:ro",
    "/:/host/root:ro",
}
PANEL_TITLES = {
    "Availability",
    "Latency",
    "HTTP 5xx",
    "HTTP 429",
    "Host CPU",
    "Host memory",
    "Host disk",
    "PostgreSQL",
    "Backup",
    "Deploy and restore evidence",
    "Certificate expiry",
}
SEVERITIES = {"page", "ticket", "info"}
FORBIDDEN_LABELS = (
    "request_id",
    "tenant",
    "tenant_id",
    "user_id",
    "member_id",
    "gym_id",
    "email",
    "token",
    "query",
    "path",
    "object_key",
)
EXPORTER_DATA_SOURCE_URI = "postgres:5432/${POSTGRES_DB:?Set POSTGRES_DB}?sslmode=disable"
BLACKBOX_TLS_ACK = (
    "tls_connect server_name is only the example PUBLIC_HOST. "
    "Render it to the deployed PUBLIC_HOST before start. "
    "This is a pre-deployment rendered-config gate."
)
FALSE_CLAIM = re.compile(
    r"(?i)\bslos?\s+(?:is|are)\s+(?:met|approved|achieved|satisfied)\b"
    r"|\balert delivery\s+(?:is|was)\s+(?:live|proven|delivered|working)\b"
    r"|\bdelivery\s+proven\b"
    r"|\brpo\s+proven\b"
    r"|\brto\s+proven\b"
)
_DURATION = re.compile(r"\A(\d+)(m|h)\Z")


def lint_observability(repo: Path, document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    root = repo / "infra" / "observability"
    required = (
        root / "prometheus" / "prometheus.yml",
        root / "prometheus" / "rules" / "gym-baseline.yml",
        root / "alertmanager" / "alertmanager.yml",
        root / "alertmanager" / "entrypoint.sh",
        root / "alertmanager" / "non-production-sink.url",
        root / "blackbox" / "blackbox.yml",
        root / "postgres" / "queries.yaml",
        root / "grafana" / "dashboards" / "gym-baseline.json",
        repo / "docs" / "runbooks" / "phase8-increment-4.md",
        repo / "scripts" / "bootstrap_monitor_role.py",
        repo / "scripts" / "observability_canary.py",
    )
    for path in required:
        if not path.is_file():
            errors.append(f"observability file is missing: {path.name}")
    if list((root).rglob("Dockerfile")):
        errors.append("observability must not add a custom image")
    release = repo / ".github" / "workflows" / "image-release.yml"
    if release.is_file() and "infra/observability" in release.read_text(encoding="utf-8"):
        errors.append("release workflow must not build an observability image")
    compose = repo / "infra" / "compose" / "docker-compose.prod.yml"
    compose_text = compose.read_text(encoding="utf-8") if compose.is_file() else ""
    errors.extend(lint_compose_observability(compose_text, document))
    if (root / "prometheus" / "prometheus.yml").is_file():
        errors.extend(
            lint_prometheus_text(
                (root / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
            )
        )
    if (root / "prometheus" / "rules" / "gym-baseline.yml").is_file():
        errors.extend(
            lint_rules_text(
                (root / "prometheus" / "rules" / "gym-baseline.yml").read_text(encoding="utf-8")
            )
        )
    if (root / "alertmanager" / "alertmanager.yml").is_file():
        errors.extend(
            lint_alertmanager_text(
                (root / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8")
            )
        )
    entry = root / "alertmanager" / "entrypoint.sh"
    if entry.is_file():
        script = entry.read_text(encoding="utf-8")
        required = (
            "resend-email",
            "non-production",
            "smtp.resend.com:587",
            "smtp_auth_password_file:",
            "/run/secrets/alert_smtp_password",
            "delivery is not claimed",
        )
        if any(item not in script for item in required):
            errors.append("alertmanager production email gate is incomplete")
        if SINK not in (
            root / "alertmanager" / "non-production-sink.url"
        ).read_text(encoding="utf-8"):
            errors.append("alertmanager non-production sink drifted")
    dashboard = root / "grafana" / "dashboards" / "gym-baseline.json"
    if dashboard.is_file():
        errors.extend(lint_dashboard_text(dashboard.read_text(encoding="utf-8")))
    queries = root / "postgres" / "queries.yaml"
    if queries.is_file():
        errors.extend(lint_queries_text(queries.read_text(encoding="utf-8")))
    blackbox = root / "blackbox" / "blackbox.yml"
    runbook_path = repo / "docs" / "runbooks" / "phase8-increment-4.md"
    if blackbox.is_file() and runbook_path.is_file():
        errors.extend(
            lint_blackbox_tls(
                blackbox.read_text(encoding="utf-8"),
                _example_public_host(repo),
                runbook_path.read_text(encoding="utf-8"),
            )
        )
    errors.extend(_lint_secret_files(repo))
    errors.extend(_lint_monitor_bootstrap(repo))
    runbook = repo / "docs" / "runbooks" / "phase8-increment-4.md"
    if runbook.is_file():
        errors.extend(lint_claim_text(runbook.read_text(encoding="utf-8")))
    return errors


def lint_claim_text(text: str) -> list[str]:
    if FALSE_CLAIM.search(text):
        return ["observability text claims a proven SLO, delivery, RPO, or RTO"]
    return []


def _example_public_host(repo: Path) -> str:
    path = repo / "infra" / "environments" / "production" / "compose.env.example"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("PUBLIC_HOST="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def lint_blackbox_tls(config: str, example_public_host: str, runbook: str) -> list[str]:
    errors: list[str] = []
    if re.search(r"(?m)^\s*insecure_skip_verify:\s*true\s*$", config):
        errors.append("blackbox must not disable certificate verification")
    if "insecure_skip_verify: false" not in config:
        errors.append("blackbox TLS verification must stay enabled")
    names = re.findall(r"(?m)^\s*server_name:\s*(\S+)\s*$", config)
    if len(names) != 1 or names[0] != example_public_host or not example_public_host:
        errors.append("blackbox server_name does not match the example PUBLIC_HOST")
    if BLACKBOX_TLS_ACK not in re.sub(r"\s+", " ", runbook):
        errors.append("blackbox PUBLIC_HOST mismatch is not acknowledged")
    if re.search(r"(?i)server_name follows PUBLIC_HOST", runbook):
        errors.append("blackbox server_name is not a static PUBLIC_HOST binding")
    return errors


def lint_compose_observability(text: str, document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()
    for banned in ("docker.sock", "privileged:", "network_mode:", "pid:"):
        if banned in lowered:
            errors.append(f"observability compose contains {banned.strip(':')}")
    services = document.get("services")
    if not isinstance(services, dict):
        return [*errors, "observability compose services are missing"]
    for name in CORE_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        depends = service.get("depends_on", {})
        names = set(depends) if isinstance(depends, dict) else set(depends or [])
        if names & set(OBSERVE_SERVICES):
            errors.append(f"{name} must not wait for monitoring")
    errors.extend(_lint_exporter(services.get("postgres-exporter")))
    errors.extend(_lint_node_exporter(services.get("node-exporter")))
    errors.extend(_lint_prometheus_service(services.get("prometheus")))
    errors.extend(_lint_grafana(services.get("grafana")))
    errors.extend(_lint_alertmanager(services.get("alertmanager")))
    errors.extend(_lint_retention(services))
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        for item in service.get("volumes") or []:
            rendered = str(item)
            source = rendered.split(":", 1)[0]
            if source.startswith("/") and not rendered.endswith(":ro"):
                errors.append(f"{name} has a writable host mount")
            if "docker.sock" in rendered:
                errors.append(f"{name} mounts the Docker socket")
    return errors


def lint_prometheus_text(text: str) -> list[str]:
    errors = lint_claim_text(text)
    if re.search(r"(?m)^\s*remote_write:", text) or re.search(r"(?m)^\s*remote_read:", text):
        errors.append("Prometheus must not remote-write or remote-read")
    probes = set(re.findall(r"(?m)^\s+- (https?://\S+|caddy:443)\s*$", text))
    if probes != PROBE_TARGETS:
        errors.append("blackbox targets drifted from the private allowlist")
    for label in FORBIDDEN_LABELS:
        if re.search(rf"target_label:\s*{label}\b", text) or re.search(rf"\b{label}\s*=", text):
            errors.append(f"Prometheus label is too specific: {label}")
    if "?" in text:
        errors.append("Prometheus scrape targets must not include a query string")
    return errors


def lint_rules_text(text: str) -> list[str]:
    errors = lint_claim_text(text)
    if "vector(1)" in text or text.count("expr: vector(0)") != 2:
        errors.append("deploy and restore evidence must stay vector(0)")
    alerts = re.split(r"(?m)^\s*- alert:\s*", text)[1:]
    if len(alerts) < 15:
        errors.append("alert catalog is missing a required signal")
    for alert in alerts:
        name = alert.splitlines()[0].strip()
        matched = re.search(r"(?m)^\s*for:\s*(\S+)\s*$", alert)
        if matched is None:
            errors.append(f"{name} is missing a bounded for")
            continue
        minutes = _minutes(matched.group(1))
        if minutes is None or minutes < 2 or minutes > 24 * 60:
            errors.append(f"{name} for duration is unbounded or too short")
        severity = re.search(r"(?m)^\s*severity:\s*(\S+)\s*$", alert)
        if severity is None or severity.group(1) not in SEVERITIES:
            errors.append(f"{name} severity is missing")
        if "runbook: docs/runbooks/phase8-increment-4.md" not in alert:
            errors.append(f"{name} is missing its runbook label")
        if "threshold: provisional" not in alert:
            errors.append(f"{name} must mark its threshold provisional")
        if "{{" in alert or "}}" in alert:
            errors.append(f"{name} annotation can expand a sensitive label")
        for label in FORBIDDEN_LABELS:
            if re.search(rf"(?m)^\s*{label}:", alert):
                errors.append(f"{name} uses a high-cardinality label")
    return errors


def lint_alertmanager_text(text: str) -> list[str]:
    errors = lint_claim_text(text)
    if text.count(SINK) != 1:
        errors.append("Alertmanager must route only to the non-production sink")
    if "http://" in text.replace(SINK, "") or "https://" in text:
        errors.append("Alertmanager contains another endpoint")
    grouped = re.search(r"group_by:\n(?:\s+- .+\n)+", text)
    if grouped is None:
        errors.append("Alertmanager group_by is missing")
    else:
        labels = re.findall(r"- (\S+)", grouped.group(0))
        if labels != ["alertname", "severity"]:
            errors.append("Alertmanager group_by is too specific")
    return errors


def lint_dashboard_text(text: str) -> list[str]:
    errors = lint_claim_text(text)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return [*errors, f"dashboard JSON is invalid: {exc}"]
    if not isinstance(document, dict):
        return [*errors, "dashboard JSON must be an object"]
    titles = {panel.get("title") for panel in document.get("panels", []) if isinstance(panel, dict)}
    if not PANEL_TITLES <= titles:
        errors.append("dashboard is missing a required panel")
    for label in (*FORBIDDEN_LABELS, "Bearer", "AKIA", "password="):
        if label in text:
            errors.append(f"dashboard contains {label}")
    return errors


def lint_queries_text(text: str) -> list[str]:
    errors = lint_claim_text(text)
    if re.search(r"(?m)^\s*-?\s*labels:\s*$", text) or re.search(
        r"(?m)^\s*usage:\s*LABEL\s*$", text
    ):
        errors.append("postgres queries must not add labels")
    for label in FORBIDDEN_LABELS:
        if label == "query":
            continue
        if re.search(rf"\b{label}\b", text):
            errors.append(f"postgres queries mention {label}")
    if "pg_stat_activity" not in text or "last_archived_wal" in text:
        errors.append("postgres queries drifted from the numeric baseline")
    return errors


def synthetic_canary(repo: Path) -> str:
    """Build one alert payload locally. This function does not open a socket."""

    errors = lint_observability(repo, _empty_document(repo))
    route_errors = [
        error
        for error in errors
        if "Alertmanager" in error or "live sink" in error or "SLO" in error
    ]
    if route_errors:
        raise RuntimeError("; ".join(route_errors))
    payload = {
        "labels": {
            "alertname": "GymObservabilityCanary",
            "severity": "info",
            "runbook": "docs/runbooks/phase8-increment-4.md",
            "threshold": "provisional",
        },
        "annotations": {
            "summary": "Synthetic canary. Alert delivery is not claimed.",
        },
        "receiver": "non-production-sink",
        "url": SINK,
    }
    return json.dumps(payload, sort_keys=True) + "\nalert_delivery=not_claimed\n"


def _empty_document(repo: Path) -> dict[str, Any]:
    scripts = str(repo / "scripts")
    import sys

    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from production_artifacts import parse_simple_yaml

    path = repo / "infra" / "compose" / "docker-compose.prod.yml"
    parsed = parse_simple_yaml(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _minutes(value: str) -> int | None:
    matched = _DURATION.fullmatch(value)
    if matched is None:
        return None
    amount = int(matched.group(1))
    return amount if matched.group(2) == "m" else amount * 60


def _lint_exporter(service: object) -> list[str]:
    if not isinstance(service, dict):
        return ["postgres-exporter is missing"]
    command = " ".join(str(item) for item in service.get("command", []))
    environment = service.get("environment")
    errors: list[str] = []
    if not isinstance(environment, dict):
        return ["postgres-exporter environment is missing"]
    required = (
        "--config.file=/etc/postgres_exporter/postgres_exporter.yml",
        "--disable-default-metrics",
        "--extend.query-path=/etc/postgres_exporter/queries.yaml",
        "--no-collector.stat_activity",
        "--no-collector.stat_statements",
        "--no-collector.settings",
        "--no-collector.stat_user_tables",
        "--no-collector.roles",
    )
    for item in required:
        if item not in command:
            errors.append(f"postgres-exporter is missing {item}")
    tokens = command.split()
    if any(token == "--config.file=" or token.startswith("--datasource.") for token in tokens):
        errors.append("postgres-exporter uses an unsupported v0.20 CLI flag")
    if environment.get("DATA_SOURCE_USER") != "gym_monitor":
        errors.append("postgres-exporter must log in as gym_monitor")
    if environment.get("DATA_SOURCE_PASS_FILE") != "/run/secrets/monitor_db_password":
        errors.append("postgres-exporter password must come from its secret file")
    uri = str(environment.get("DATA_SOURCE_URI", ""))
    if uri != EXPORTER_DATA_SOURCE_URI or "@" in uri or "://" in uri:
        errors.append("postgres-exporter URI must use POSTGRES_DB and must not embed credentials")
    if "DATA_SOURCE_PASS" in environment or "DATA_SOURCE_NAME" in environment:
        errors.append("postgres-exporter reuses a broad database credential")
    for banned in (
        "gym_admin",
        "gym_runtime",
        "gym_migration",
        "RUNTIME_DB_PASSWORD",
        "MIGRATION_DB_PASSWORD",
        "POSTGRES_ADMIN_PASSWORD",
    ):
        if banned in command or banned in json.dumps(environment):
            errors.append("postgres-exporter reuses a broad database credential")
    if service.get("secrets") != ["monitor_db_password"]:
        errors.append("postgres-exporter must mount only the monitor password")
    return errors


def _lint_node_exporter(service: object) -> list[str]:
    if not isinstance(service, dict):
        return ["node-exporter is missing"]
    volumes = [str(item) for item in service.get("volumes", [])]
    expected = {*HOST_MOUNTS, "backup_metrics:/textfile:ro"}
    if set(volumes) != expected:
        return ["node-exporter mounts drifted from the read-only baseline"]
    command = " ".join(str(item) for item in service.get("command", []))
    if "--collector.disable-defaults" not in command or "--collector.textfile" not in command:
        return ["node-exporter collectors drifted"]
    if service.get("depends_on"):
        return ["node-exporter must keep serving host metrics when PostgreSQL is down"]
    return []


def _lint_prometheus_service(service: object) -> list[str]:
    if not isinstance(service, dict):
        return ["prometheus is missing"]
    command = " ".join(str(item) for item in service.get("command", []))
    errors: list[str] = []
    if "--storage.tsdb.retention.time=15d" not in command:
        errors.append("Prometheus retention time is unbounded")
    if "--storage.tsdb.retention.size=512MB" not in command:
        errors.append("Prometheus retention size is unbounded")
    if "--web.enable-admin-api" in command or "--web.enable-lifecycle" in command:
        errors.append("Prometheus admin API must stay off")
    if service.get("depends_on"):
        errors.append("Prometheus must not gate itself on another service")
    return errors


def _lint_alertmanager(service: object) -> list[str]:
    if not isinstance(service, dict):
        return ["alertmanager is missing"]
    errors: list[str] = []
    if service.get("entrypoint") != ["/bin/sh", "/etc/alertmanager/entrypoint.sh"]:
        errors.append("alertmanager must run the sink check with the image shell")
    health = service.get("healthcheck")
    test = health.get("test") if isinstance(health, dict) else None
    if test != ["CMD", "/bin/alertmanager", "--version"]:
        errors.append("alertmanager healthcheck must call /bin/alertmanager")
    environment = service.get("environment")
    expected_environment = {
        "GYM_ALERT_DELIVERY_MODE": "${ALERT_DELIVERY_MODE:?Set ALERT_DELIVERY_MODE}",
        "GYM_ALERT_EMAIL_FROM": "${ALERT_EMAIL_FROM:?Set ALERT_EMAIL_FROM}",
        "GYM_ALERT_EMAIL_TO": "${ALERT_EMAIL_TO:?Set ALERT_EMAIL_TO}",
    }
    if environment != expected_environment:
        errors.append("alertmanager delivery environment drifted")
    if service.get("secrets") != ["alert_smtp_password"]:
        errors.append("alertmanager must mount only the SMTP password secret")
    return errors


def _lint_grafana(service: object) -> list[str]:
    if not isinstance(service, dict):
        return ["grafana is missing"]
    environment = service.get("environment")
    if not isinstance(environment, dict):
        return ["grafana environment is missing"]
    errors: list[str] = []
    if environment.get("GF_AUTH_ANONYMOUS_ENABLED") != "false":
        errors.append("Grafana anonymous access must stay off")
    if environment.get("GF_USERS_ALLOW_SIGN_UP") != "false":
        errors.append("Grafana sign-up must stay off")
    password = str(environment.get("GF_SECURITY_ADMIN_PASSWORD__FILE", ""))
    if password != "/run/secrets/grafana_admin_password":
        errors.append("Grafana admin password must come from its secret file")
    if "GF_SECURITY_ADMIN_PASSWORD" in environment:
        errors.append("Grafana admin password must not be an environment value")
    health = service.get("healthcheck")
    test = health.get("test") if isinstance(health, dict) else None
    if test != ["CMD", "/usr/bin/curl", "-fsS", "http://127.0.0.1:3000/api/health"]:
        errors.append("Grafana healthcheck must call /usr/bin/curl")
    return errors


def _lint_retention(services: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        logging = service.get("logging")
        options = logging.get("options") if isinstance(logging, dict) else None
        if not isinstance(options, dict):
            errors.append(f"{name} log retention is missing")
            continue
        size = str(options.get("max-size", ""))
        files = str(options.get("max-file", ""))
        if not re.fullmatch(r"(?:[1-9]|10)m", size) or not re.fullmatch(r"[1-5]", files):
            errors.append(f"{name} log retention is unbounded")
    return errors


def _lint_secret_files(repo: Path) -> list[str]:
    root = repo / "infra" / "environments" / "production"
    errors: list[str] = []
    expected = {
        "monitor-db-password.example": PLACEHOLDER + "\n",
        "grafana-admin-password.example": PLACEHOLDER + "\n",
        "alert-smtp-password.example": PLACEHOLDER + "\n",
    }
    seen: set[Path] = set()
    for name, content in expected.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            errors.append(f"observability secret example is missing: {name}")
            continue
        resolved = path.resolve()
        if resolved in seen:
            errors.append("observability secret examples must be separate files")
        seen.add(resolved)
        if path.read_text(encoding="utf-8") != content:
            errors.append(f"{name} drifted")
    return errors


def _lint_monitor_bootstrap(repo: Path) -> list[str]:
    init = repo / "infra" / "postgres" / "init" / "001-create-roles.sh"
    script = repo / "scripts" / "bootstrap_monitor_role.py"
    errors: list[str] = []
    if not init.is_file() or not script.is_file():
        return ["monitor role bootstrap is missing"]
    init_text = init.read_text(encoding="utf-8")
    script_text = script.read_text(encoding="utf-8")
    for text, name in ((init_text, "init"), (script_text, "bootstrap")):
        if "existing volume" not in text.lower() and "existing volumes" not in text.lower():
            errors.append(f"{name} must say it does not update an existing volume by itself")
        grant = "GRANT pg_monitor TO gym_monitor" in text or "GRANT pg_monitor TO {ROLE}" in text
        if not grant:
            errors.append(f"{name} must grant only pg_monitor")
        if "NOBYPASSRLS" not in text:
            errors.append(f"{name} must keep gym_monitor from bypassing row security")
    if "docker-entrypoint-initdb.d runs only when the data directory is empty" not in init_text:
        errors.append("init script must not pretend to alter an existing volume")
    if "skips gym_monitor" not in init_text:
        errors.append("init script must skip gym_monitor when its password file is absent")
    if (
        'monitor_file="${GYM_MONITOR_PASSWORD_FILE:-/run/secrets/monitor_db_password}"'
        not in init_text
    ):
        errors.append("init script must default to the production monitor secret file")
    if "--set=" in init_text:
        errors.append("init script must not pass passwords on the psql command line")
    for marker in (
        r"\getenv migration_password POSTGRES_MIGRATION_PASSWORD",
        r"\getenv runtime_password POSTGRES_RUNTIME_PASSWORD",
        r"\getenv monitor_password GYM_MONITOR_PASSWORD",
        ":'migration_password'",
        ":'runtime_password'",
        ":'monitor_password'",
    ):
        if marker not in init_text:
            errors.append("init script must load passwords with psql getenv")
    local = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    if "monitor_db_password" in local or "./infra/postgres/init" not in local:
        errors.append("local Compose must mount init scripts without the monitor secret")
    rehearsal = repo / "infra" / "compose" / "docker-compose.backup-rehearsal.yml"
    if rehearsal.is_file() and "monitor_db_password" in rehearsal.read_text(encoding="utf-8"):
        errors.append("backup rehearsal must not require the monitor password")
    prod = repo / "infra" / "compose" / "docker-compose.prod.yml"
    if prod.is_file():
        prod_text = prod.read_text(encoding="utf-8")
        if "GYM_MONITOR_PASSWORD_FILE" in prod_text:
            errors.append("production must use the mounted monitor secret, not an override")
        if "monitor_db_password" not in prod_text:
            errors.append("production Compose must mount the monitor password")
        init_mount = (
            "../postgres/init/001-create-roles.sh:"
            "/docker-entrypoint-initdb.d/001-create-roles.sh:ro"
        )
        if init_mount not in prod_text:
            errors.append("production Compose must mount the role init script")
    config = repo / "infra" / "observability" / "postgres" / "postgres_exporter.yml"
    if config.is_file():
        body = config.read_text(encoding="utf-8")
        if "auth_modules: {}" not in body or "password:" in body.lower():
            errors.append("postgres exporter config must not carry a password")
    rules = repo / "infra" / "observability" / "prometheus" / "rules" / "gym-baseline.yml"
    if rules.is_file():
        rule_text = rules.read_text(encoding="utf-8")
        if "caddy_http_requests_total" in rule_text:
            errors.append("Caddy rules must use the duration counter that has a code label")
        if "caddy_http_request_duration_seconds_count" not in rule_text:
            errors.append("Caddy rules are missing the status-coded request counter")
    return errors
