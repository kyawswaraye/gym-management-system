from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.common.production import (
    require_dedicated_reset_pepper,
    require_https_endpoint,
    require_private_clamav_host,
    require_push_backend,
    require_resend_api_key,
    require_resend_backend,
)

ROOT = Path(__file__).resolve().parents[3]


def load():
    path = ROOT / "scripts" / "production_artifacts.py"
    spec = importlib.util.spec_from_file_location("production_artifacts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("production artifact module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_artifacts_pass_static_checks() -> None:
    errors = load().lint_repository(ROOT)
    assert errors == []


def test_digest_reference_rejects_tags_and_short_hashes() -> None:
    module = load()
    assert module.lint_image_reference("postgres:18.6")
    assert module.lint_image_reference("postgres:18.6@sha256:" + "ab" * 32)
    assert module.lint_image_reference("postgres@sha256:" + "ab" * 31)
    assert module.lint_image_reference("repo:latest") == ["mutable image reference: repo:latest"]
    assert module.lint_image_reference("postgres@sha256:" + "a" * 64) == []


def test_compose_source_rejects_mutable_defaults() -> None:
    module = load()
    errors = module.lint_compose_source("image: ${API_IMAGE:-postgres:18.6}\n")
    assert any("default" in error or "digest" in error for error in errors)


def test_private_host_port_fails_closed() -> None:
    module = load()
    document = module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    document["services"]["postgres"]["ports"] = ["5432:5432"]
    errors = module.lint_compose_document(document)
    assert any("postgres" in error and "port" in error for error in errors)


def test_shared_database_role_fails_closed() -> None:
    module = load()
    document = module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    document["services"]["api"]["environment"]["MIGRATION_DB_PASSWORD"] = "present"
    errors = module.lint_compose_document(document)
    assert any("migration" in error for error in errors)


def test_offline_django_env_is_not_a_live_secret() -> None:
    module = load()
    env = module.offline_environment()
    assert "-----BEGIN" not in "".join(env.values())
    require_resend_backend(env["EMAIL_BACKEND"])
    require_resend_api_key(env["RESEND_API_KEY"])
    require_https_endpoint(env["MINIO_ENDPOINT_URL"], name="MINIO_ENDPOINT_URL")
    require_private_clamav_host(env["CLAMAV_HOST"])
    require_dedicated_reset_pepper(
        reset_pepper=env["PASSWORD_RESET_TOKEN_PEPPER"],
        other_secrets={
            "REFRESH_TOKEN_PEPPER": env["REFRESH_TOKEN_PEPPER"],
            "MFA_TOKEN_PEPPER": env["MFA_TOKEN_PEPPER"],
            "MFA_ENCRYPTION_KEY": env["MFA_ENCRYPTION_KEY"],
            "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": env["FINANCE_IDEMPOTENCY_ENCRYPTION_KEY"],
            "INVITATION_TOKEN_PEPPER": env["INVITATION_TOKEN_PEPPER"],
        },
    )
    require_push_backend(
        backend=env["PUSH_BACKEND"],
        project_id=env["FCM_PROJECT_ID"],
        service_account_json=env["FCM_SERVICE_ACCOUNT_JSON"],
        encryption_key=env["PUSH_TOKEN_ENCRYPTION_KEY"],
        fingerprint_pepper=env["PUSH_TOKEN_FINGERPRINT_PEPPER"],
        other_secrets={
            "MFA_ENCRYPTION_KEY": env["MFA_ENCRYPTION_KEY"],
            "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": env["FINANCE_IDEMPOTENCY_ENCRYPTION_KEY"],
            "PASSWORD_RESET_TOKEN_PEPPER": env["PASSWORD_RESET_TOKEN_PEPPER"],
        },
    )
    with pytest.raises(ImproperlyConfigured):
        require_private_clamav_host("0.0.0.0")


def test_caddy_allowlist_rejects_staff_and_arbitrary_api() -> None:
    module = load()
    assert module.caddy_upstream("/api/v1/member-auth/login/") == "api"
    assert module.caddy_upstream("/api/v1/member/check-ins/scan/") == "api"
    assert module.caddy_upstream("/api/v1/auth/login/") == "reject"
    assert module.caddy_upstream("/api/v1/members/") == "reject"
    assert module.caddy_upstream("/api/v1/memberships/") == "reject"
    assert module.caddy_upstream("/api/v1/notifications/") == "reject"
    assert module.caddy_upstream("/login") == "web"
    text = (ROOT / "infra/caddy/Caddyfile").read_text(encoding="utf-8")
    assert module.lint_caddy(text) == []
    broadened = text.replace(
        "/api/v1/member-auth /api/v1/member-auth/* /api/v1/member /api/v1/member/*",
        "/api/v1/*",
    )
    assert module.lint_caddy(broadened)
    staff = text.replace(
        "@member path /api/v1/member-auth /api/v1/member-auth/* /api/v1/member /api/v1/member/*",
        "@member path /api/v1/auth /api/v1/auth/*",
    )
    assert module.lint_caddy(staff)


def test_database_passwords_have_one_canonical_name() -> None:
    module = load()
    document = module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    postgres = document["services"]["postgres"]["environment"]
    assert postgres["POSTGRES_MIGRATION_PASSWORD"] == (
        "${MIGRATION_DB_PASSWORD:?Set MIGRATION_DB_PASSWORD}"
    )
    assert postgres["POSTGRES_RUNTIME_PASSWORD"] == (
        "${RUNTIME_DB_PASSWORD:?Set RUNTIME_DB_PASSWORD}"
    )
    example = (ROOT / "infra/environments/production/compose.env.example").read_text(
        encoding="utf-8"
    )
    assert "POSTGRES_MIGRATION_PASSWORD=" not in example
    assert "POSTGRES_RUNTIME_PASSWORD=" not in example


def test_migrate_service_receives_no_provider_secrets() -> None:
    module = load()
    document = module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    migrate = document["services"]["migrate"]["environment"]
    assert "JWT_PRIVATE_KEY" not in migrate
    assert "RESEND_API_KEY" not in migrate
    assert "FCM_SERVICE_ACCOUNT_JSON" not in migrate
    assert "MINIO_SECRET_KEY" not in migrate
    assert "BFF_SHARED_SECRET" not in migrate
    assert document["networks"]["scan"]["internal"] == "true"
    assert document["services"]["caddy"]["networks"] == ["edge", "app", "probe", "observe"]
    assert "ports" not in document["services"]["api"]
    migrate["JWT_PRIVATE_KEY"] = "${JWT_PRIVATE_KEY:?Set JWT_PRIVATE_KEY}"
    errors = module.lint_compose_document(document)
    assert any("migrate environment" in error for error in errors)
