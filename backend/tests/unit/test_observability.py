from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.common.observability.authority import note_observability_authority
from apps.common.observability.logging import JsonLogFormatter
from apps.common.observability.redaction import redact_value
from apps.common.observability.request_log import (
    RequestLogMiddleware,
    normalized_route,
    request_log_level,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

observability = importlib.import_module("observability")
production_artifacts = importlib.import_module("production_artifacts")

ACTOR = uuid.UUID("11111111-1111-4111-8111-111111111111")
GYM = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _backup():
    path = ROOT / "infra" / "postgres" / "backup_policy.py"
    spec = importlib.util.spec_from_file_location("backup_policy_metrics", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("backup policy module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose():
    return production_artifacts.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )


def _rules() -> str:
    return (ROOT / "infra/observability/prometheus/rules/gym-baseline.yml").read_text(
        encoding="utf-8"
    )


def test_repository_observability_baseline_passes() -> None:
    assert observability.lint_observability(ROOT, _compose()) == []


def test_public_monitoring_port_fails() -> None:
    document = _compose()
    document["services"]["prometheus"]["ports"] = ["9090:9090"]
    errors = production_artifacts.lint_compose_document(document)
    assert any("prometheus" in error and "port" in error for error in errors)


def test_privileged_socket_and_host_modes_fail() -> None:
    document = {"services": {"api": {"depends_on": {"prometheus": {}}}}}
    errors = observability.lint_compose_observability(
        "privileged: true\ndocker.sock\nnetwork_mode: host\npid: host\n",
        document,
    )
    assert any("privileged" in error for error in errors)
    assert any("docker.sock" in error for error in errors)
    assert any("network_mode" in error for error in errors)
    assert any("pid" in error for error in errors)
    assert any("api must not wait for monitoring" in error for error in errors)


def test_broad_database_credential_and_writable_mount_fail() -> None:
    document = {
        "services": {
            "postgres-exporter": {
                "command": ["--datasource.pass-file=/run/secrets/monitor_db_password"],
                "environment": {
                    "DATA_SOURCE_USER": "gym_admin",
                    "DATA_SOURCE_PASS": "secret",
                    "DATA_SOURCE_PASS_FILE": "/run/secrets/monitor_db_password",
                },
                "secrets": ["monitor_db_password"],
            },
            "node-exporter": {"volumes": ["/:/host/root"]},
        }
    }
    errors = observability.lint_compose_observability("", document)
    assert any("broad database credential" in error for error in errors)
    assert any("unsupported v0.20 CLI flag" in error for error in errors)
    assert any("writable host mount" in error for error in errors)


def test_high_cardinality_weak_alert_and_false_claim_fail() -> None:
    rules = _rules().replace("for: 5m", "for: 1m", 1)
    rules += "          request_id: req_secret\n"
    errors = observability.lint_rules_text(rules)
    assert any("too short" in error for error in errors)
    assert any("high-cardinality" in error for error in errors)
    assert observability.lint_claim_text("The SLO is approved.")
    assert observability.lint_claim_text("alert delivery is proven")
    drifted = _rules().replace("expr: vector(0)", "expr: vector(1)", 1)
    assert any("vector(0)" in error for error in observability.lint_rules_text(drifted))


def test_unbounded_retention_fails() -> None:
    errors = observability.lint_compose_observability(
        "",
        {
            "services": {
                "prometheus": {
                    "command": ["--config.file=/etc/prometheus/prometheus.yml"],
                    "logging": {"options": {"max-size": "100m", "max-file": "9"}},
                }
            }
        },
    )
    assert any("retention" in error for error in errors)
    assert any("unbounded" in error for error in errors)


def test_recursive_redaction_drops_secrets_and_control_characters() -> None:
    redacted = redact_value(
        {
            "request_id": "req_keep",
            "authorization": "Bearer eyJhbGciOi.secretvalue",
            "cookie": "session=secret",
            "nested": {
                "access_token": "access-secret",
                "gym_selection_ticket": "ticket-secret",
                "mfa_challenge_token": "mfa-secret",
                "reset_token": "reset-secret",
                "invitation_token": "invite-secret",
                "bff_shared_secret": "bff-secret",
                "X-Gym-BFF-Secret": "header-secret",
                "provider_api_key": "provider-secret",
                "body": {"password": "body-secret"},
            },
            "note": "x-amz-signature=abc123&token=raw\n\x00tail",
        }
    )
    rendered = json.dumps(redacted)
    assert redacted["request_id"] == "req_keep"
    for secret in (
        "secretvalue",
        "session=secret",
        "access-secret",
        "ticket-secret",
        "mfa-secret",
        "reset-secret",
        "invite-secret",
        "bff-secret",
        "header-secret",
        "provider-secret",
        "body-secret",
        "abc123",
        "raw",
    ):
        assert secret not in rendered
    assert "\n" not in rendered
    assert "\x00" not in rendered


def test_formatter_is_one_json_line_and_audit_stays_distinct() -> None:
    record = logging.LogRecord(
        "apps.audit",
        logging.INFO,
        "",
        0,
        "Bearer eyJhbGciOi.secretvalue",
        (),
        None,
    )
    line = JsonLogFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["logger"] == "apps.audit"
    assert payload["logger"] != "gym.http"
    assert "secretvalue" not in line


@override_settings(STRUCTURED_REQUEST_LOG=True)
def test_request_completion_uses_route_and_expected_severity(caplog) -> None:
    request = RequestFactory().get("/api/v1/health/?token=rawsecret&email=a@b.test")
    request.resolver_match = SimpleNamespace(route="api/v1/health/")
    request.request_id = "req_test"
    request.observability_actor_id = "not-a-uuid"
    middleware = RequestLogMiddleware(lambda _request: HttpResponse(status=404))
    with caplog.at_level(logging.DEBUG, logger="gym.http"):
        middleware(request)
    record = caplog.records[-1]
    assert record.name == "gym.http"
    assert record.route == "api/v1/health/"
    assert record.levelno == logging.INFO
    assert not hasattr(record, "actor_id")
    line = JsonLogFormatter().format(record)
    assert "rawsecret" not in line
    assert "a@b.test" not in line
    assert request_log_level(429) == logging.WARNING
    assert request_log_level(400) == logging.WARNING
    assert request_log_level(500) == logging.ERROR
    assert request_log_level(401) == logging.INFO
    assert normalized_route(request) == "api/v1/health/"


def test_authority_ids_are_copied_only_when_supplied() -> None:
    class Request:
        def __init__(self) -> None:
            self._request = SimpleNamespace()

    request = Request()
    note_observability_authority(request, actor_id=ACTOR, gym_id=GYM)
    assert request.observability_actor_id == str(ACTOR)
    assert request._request.observability_gym_id == str(GYM)
    platform = Request()
    note_observability_authority(platform, actor_id=ACTOR, gym_id=None)
    assert platform.observability_actor_id == str(ACTOR)
    assert not hasattr(platform, "observability_gym_id")


def test_backup_metrics_reject_injection_and_keep_failed_status(tmp_path, monkeypatch) -> None:
    policy = _backup()
    injected = policy.render_backup_metrics(
        {
            "result": 'ok"}\ngym_backup_injected 1',
            "errors": ["token=rawsecret"],
            "labels": {"user_id": "person"},
            "backup_label": "secret-label",
            "rpo_proven": True,
            "rto_proven": True,
            "production_proof": True,
            "backup_age_seconds": True,
            "wal_age_seconds": -1,
        }
    )
    assert 'gym_backup_result{result="rejected"} 1' in injected
    assert "gym_backup_injected" not in injected
    assert "rawsecret" not in injected
    assert "person" not in injected
    assert "secret-label" not in injected
    assert "gym_backup_rpo_proven 0" in injected
    assert "gym_backup_rto_proven 0" in injected
    assert "gym_backup_production_proof 0" in injected
    assert "gym_backup_age_seconds" not in injected
    assert "gym_backup_wal_age_seconds" not in injected

    destination = tmp_path / "backup.prom"
    monkeypatch.setenv("BACKUP_METRICS_PATH", str(destination))
    busy = policy.terminal_status("busy")
    assert policy.emit_status(busy) == 75
    assert 'result="busy"' in destination.read_text(encoding="ascii")
    stale = {
        "result": "stale",
        "errors": ["backup is old"],
        "rpo_proven": False,
        "rto_proven": False,
    }
    assert policy.emit_status(stale) == 1
    rendered = destination.read_text(encoding="ascii")
    assert 'result="stale"' in rendered
    assert "backup is old" not in rendered

    monkeypatch.setenv("BACKUP_METRICS_PATH", str(tmp_path / "other.prom"))
    policy.publish_backup_metrics({"result": "failed"})
    assert not (tmp_path / "other.prom").exists()
    assert 'result="stale"' in destination.read_text(encoding="ascii")


def test_node_exporter_does_not_wait_for_postgres() -> None:
    document = _compose()
    assert "depends_on" not in document["services"]["node-exporter"]
    document["services"]["node-exporter"]["depends_on"] = {"postgres": {}}
    errors = observability.lint_compose_observability("", document)
    assert any("host metrics" in error for error in errors)


def test_caddy_request_id_uses_re2_capture() -> None:
    text = (ROOT / "infra/caddy/Caddyfile").read_text(encoding="utf-8")
    assert r"\{1,128\}" not in text
    assert "~^([A-Za-z0-9._:-]{1,128})$ $1" in text
    escaped = text.replace(
        "~^([A-Za-z0-9._:-]{1,128})$ $1",
        r"~^[A-Za-z0-9._:-]\{1,128\}$ {http.request.header.X-Request-ID}",
    )
    errors = production_artifacts.lint_caddy(escaped)
    assert any("valid RE2" in error for error in errors)


def test_exporter_database_comes_from_postgres_db() -> None:
    document = _compose()
    uri = document["services"]["postgres-exporter"]["environment"]["DATA_SOURCE_URI"]
    assert uri == observability.EXPORTER_DATA_SOURCE_URI
    assert "@" not in uri
    document["services"]["postgres-exporter"]["environment"]["DATA_SOURCE_URI"] = (
        "postgres:5432/gym_platform?sslmode=disable"
    )
    errors = observability.lint_compose_observability("", document)
    assert any("POSTGRES_DB" in error for error in errors)


def test_blackbox_server_name_gate_is_explicit() -> None:
    config = (ROOT / "infra/observability/blackbox/blackbox.yml").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/phase8-increment-4.md").read_text(encoding="utf-8")
    assert observability.lint_blackbox_tls(config, "app.example.com", runbook) == []
    drifted = config.replace(
        "server_name: app.example.com",
        "server_name: other.example.com",
    )
    errors = observability.lint_blackbox_tls(drifted, "app.example.com", runbook)
    assert any("example PUBLIC_HOST" in error for error in errors)
    errors = observability.lint_blackbox_tls(
        config,
        "app.example.com",
        "The probe stays private and verification stays enabled.",
    )
    assert any("not acknowledged" in error for error in errors)
    disabled = config.replace("insecure_skip_verify: false", "insecure_skip_verify: true")
    errors = observability.lint_blackbox_tls(disabled, "app.example.com", runbook)
    assert any("verification" in error for error in errors)


def test_caddy_rules_use_status_coded_counter() -> None:
    text = _rules()
    assert "caddy_http_requests_total" not in text
    assert 'caddy_http_request_duration_seconds_count{code=~"5.."}' in text
    assert 'caddy_http_request_duration_seconds_count{code="429"}' in text


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def test_init_script_keeps_passwords_out_of_psql_argv(tmp_path) -> None:
    secrets = (
        "admin secret value",
        "mig'ration secret",
        "run time\\secret",
        'mon"itor:secret',
    )
    script = ROOT / "infra/postgres/init/001-create-roles.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv"
    sql_path = tmp_path / "sql"
    fake = bin_dir / "psql"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > "
        + repr(str(argv_path))
        + "\ncat > "
        + repr(str(sql_path))
        + "\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    secret = tmp_path / "monitor"
    secret.write_text(secrets[3] + "\n", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "POSTGRES_USER": "gym_admin",
        "POSTGRES_DB": "gym_platform",
        "POSTGRES_PASSWORD": secrets[0],
        "POSTGRES_MIGRATION_PASSWORD": secrets[1],
        "POSTGRES_RUNTIME_PASSWORD": secrets[2],
        "GYM_MONITOR_PASSWORD_FILE": str(secret),
    }
    completed = subprocess.run(
        [str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    argv = argv_path.read_text(encoding="utf-8")
    sql = sql_path.read_text(encoding="utf-8")
    visible = "\n".join((argv, sql, completed.stdout, completed.stderr))
    leaked = _contains_any(visible, secrets)
    assert leaked is False, "psql argv contains a database secret"
    assert "--set" not in argv
    assert "--username" in argv
    assert "--dbname" in argv
    assert r"\getenv migration_password POSTGRES_MIGRATION_PASSWORD" in sql
    assert r"\getenv runtime_password POSTGRES_RUNTIME_PASSWORD" in sql
    assert r"\getenv monitor_password GYM_MONITOR_PASSWORD" in sql
    assert ":'migration_password'" in sql
    assert ":'monitor_password'" in sql


def test_init_script_skips_monitor_without_secret_and_creates_it_with_one(tmp_path) -> None:
    script = ROOT / "infra/postgres/init/001-create-roles.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorded = tmp_path / "sql"
    fake = bin_dir / "psql"
    fake.write_text(
        "#!/bin/sh\ncat > " + repr(str(recorded)) + "\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "POSTGRES_USER": "gym_admin",
        "POSTGRES_DB": "gym_platform",
        "POSTGRES_PASSWORD": "admin-secret",
        "POSTGRES_MIGRATION_PASSWORD": "migration-secret",
        "POSTGRES_RUNTIME_PASSWORD": "runtime-secret",
        "GYM_MONITOR_PASSWORD_FILE": str(tmp_path / "missing"),
    }
    skipped = subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False)
    assert skipped.returncode == 0, skipped.stderr
    skipped_sql = recorded.read_text(encoding="utf-8")
    assert "gym_migration" in skipped_sql
    assert "gym_runtime" in skipped_sql
    assert "gym_monitor" not in skipped_sql

    secret = tmp_path / "monitor"
    secret.write_text("monitor-secret\n", encoding="utf-8")
    env["GYM_MONITOR_PASSWORD_FILE"] = str(secret)
    created = subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False)
    assert created.returncode == 0, created.stderr
    created_sql = recorded.read_text(encoding="utf-8")
    assert "CREATE ROLE gym_monitor" in created_sql
    assert "GRANT pg_monitor TO gym_monitor" in created_sql
    assert "monitor-secret" not in created_sql

    secret.write_text("admin-secret\n", encoding="utf-8")
    refused = subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False)
    assert refused.returncode == 1
    assert "must differ" in refused.stderr


def test_alertmanager_sink_check_fails_closed(tmp_path) -> None:
    script = ROOT / "infra/observability/alertmanager/entrypoint.sh"
    expected = tmp_path / "expected"
    sink = tmp_path / "sink"
    expected.write_text(
        "http://127.0.0.1:9/gym-observability-non-production-sink\n",
        encoding="utf-8",
    )
    sink.write_text("http://example.invalid/live\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "started"
    fake = bin_dir / "alertmanager"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + repr(str(marker)) + "\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "GYM_ALERT_SINK_FILE": str(sink),
        "GYM_ALERT_SINK_EXPECTED_FILE": str(expected),
    }
    mismatched = subprocess.run(
        ["/bin/sh", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatched.returncode == 1
    assert "delivery is not claimed" in mismatched.stderr
    assert not marker.exists()

    sink.write_text(expected.read_text(encoding="utf-8"), encoding="utf-8")
    matched = subprocess.run(
        ["/bin/sh", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert matched.returncode == 0, matched.stderr
    started = marker.read_text(encoding="utf-8")
    assert "--config.file=/etc/alertmanager/alertmanager.yml" in started
    assert "--cluster.listen-address=" in started
    assert "example.invalid" not in started

    bare = subprocess.run(
        ["/bin/sh", str(script)],
        env={"PATH": str(bin_dir), "GYM_ALERT_SINK_FILE": str(sink)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert bare.returncode == 1
    assert "missing tr or alertmanager" in bare.stderr


def test_synthetic_canary_does_not_open_a_socket() -> None:
    source = (ROOT / "scripts/observability_canary.py").read_text(encoding="utf-8")
    module = (ROOT / "scripts/observability.py").read_text(encoding="utf-8")
    for name in ("import socket", "urllib", "urlopen", "http.client"):
        assert name not in source
        assert name not in module
    output = observability.synthetic_canary(ROOT)
    assert output.endswith("alert_delivery=not_claimed\n")
    payload = json.loads(output.splitlines()[0])
    assert payload["url"].endswith("/gym-observability-non-production-sink")
    assert payload["receiver"] == "non-production-sink"
