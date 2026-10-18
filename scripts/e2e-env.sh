#!/usr/bin/env bash
# Shared loopback E2E environment. Source this file; do not execute it.
# Application values are obvious placeholders. Database passwords come from
# the existing environment or .env and are never given defaults here.

if [[ -z "${E2E_ENV_ROOT:-}" ]]; then
  E2E_ENV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

e2e_load_database_env() {
  local env_file="${E2E_ENV_ROOT}/.env"
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
  fi

  export POSTGRES_HOST="${POSTGRES_HOST:-${DB_HOST:-localhost}}"
  export POSTGRES_PORT="${POSTGRES_PORT:-${DB_PORT:-5432}}"
  export POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-${POSTGRES_USER:-gym_admin}}"
  export POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-${POSTGRES_PASSWORD:-${TEST_DB_PASSWORD:-}}}"
  export MIGRATION_DB_USER="${MIGRATION_DB_USER:-${POSTGRES_MIGRATION_USER:-gym_migration}}"
  export MIGRATION_DB_PASSWORD="${MIGRATION_DB_PASSWORD:-${POSTGRES_MIGRATION_PASSWORD:-}}"
  export RUNTIME_DB_USER="${RUNTIME_DB_USER:-${POSTGRES_RUNTIME_USER:-gym_runtime}}"
  export RUNTIME_DB_PASSWORD="${RUNTIME_DB_PASSWORD:-${POSTGRES_RUNTIME_PASSWORD:-}}"
  export DB_HOST="${POSTGRES_HOST}"
  export DB_PORT="${POSTGRES_PORT}"
  export DB_NAME="gym_platform_e2e"

  if [[ -z "${POSTGRES_ADMIN_PASSWORD}" || -z "${MIGRATION_DB_PASSWORD}" || -z "${RUNTIME_DB_PASSWORD}" ]]; then
    echo "E2E needs POSTGRES_ADMIN_PASSWORD, MIGRATION_DB_PASSWORD, and RUNTIME_DB_PASSWORD." >&2
    echo "Set them in the environment or .env. They are database passwords, not application secrets." >&2
    return 1
  fi
}

e2e_apply_app_env() {
  export E2E_WEB_PORT="${E2E_WEB_PORT:-3010}"
  export E2E_API_PORT="${E2E_API_PORT:-8010}"
  export DJANGO_SETTINGS_MODULE="config.settings.e2e"
  export DJANGO_SECRET_KEY="${E2E_DJANGO_SECRET_KEY:-e2e-django-secret-key-placeholder-not-for-production-use}"
  export DJANGO_DEBUG="false"
  export ALLOWED_HOSTS="localhost,127.0.0.1"
  export DJANGO_LISTEN_HOST="127.0.0.1"
  export BFF_SHARED_SECRET="${E2E_BFF_SHARED_SECRET:-e2e-bff-shared-secret-placeholder-32}"
  export E2E_BFF_SHARED_SECRET="${BFF_SHARED_SECRET}"
  export MFA_ENCRYPTION_KEY="${E2E_MFA_ENCRYPTION_KEY:-e2e-mfa-encryption-key-placeholder}"
  export FINANCE_IDEMPOTENCY_ENCRYPTION_KEY="${E2E_FINANCE_IDEMPOTENCY_ENCRYPTION_KEY:-e2e-finance-idempotency-key-placeholder}"
  export MFA_TOKEN_PEPPER="${E2E_MFA_TOKEN_PEPPER:-e2e-mfa-token-pepper-placeholder}"
  export REFRESH_TOKEN_PEPPER="${E2E_REFRESH_TOKEN_PEPPER:-e2e-refresh-pepper-placeholder-value}"
  export INVITATION_TOKEN_PEPPER="${E2E_INVITATION_TOKEN_PEPPER:-e2e-invitation-pepper-placeholder}"
  export E2E_PLATFORM_PASSWORD="${E2E_PLATFORM_PASSWORD:-e2e-platform-password}"
  export E2E_PLATFORM_TOTP_SECRET="${E2E_PLATFORM_TOTP_SECRET:-E2EPLATFORMTOTPPLACEHOLDERAAAAAA}"
  export E2E_OWNER_PASSWORD="${E2E_OWNER_PASSWORD:-e2e-owner-password}"
  export E2E_OWNER_TOTP_SECRET="${E2E_OWNER_TOTP_SECRET:-E2EOWNERTOTPPLACEHOLDERAAAAAAAAA}"
  export E2E_MEMBER_PASSWORD="${E2E_MEMBER_PASSWORD:-e2e-member-password}"
  export E2E_MANAGER_PASSWORD="${E2E_MANAGER_PASSWORD:-e2e-manager-password}"
  export E2E_MANAGER_TOTP_SECRET="${E2E_MANAGER_TOTP_SECRET:-E2EMANAGERTOTPPLACEHOLDERAAAAAAA}"
  export JWT_ISSUER="${JWT_ISSUER:-gym-platform-api}"
  export JWT_STAFF_AUDIENCE="${JWT_STAFF_AUDIENCE:-gym-platform}"
  export JWT_MEMBER_AUDIENCE="${JWT_MEMBER_AUDIENCE:-gym-platform-member}"
  export APP_PUBLIC_URL="http://localhost:${E2E_WEB_PORT}"
  export DJANGO_API_URL="http://127.0.0.1:${E2E_API_PORT}"
  export APP_ORIGINS="http://localhost:${E2E_WEB_PORT}"
  export ACCESS_EXPIRED_CODE="${ACCESS_EXPIRED_CODE:-ACCESS_TOKEN_EXPIRED}"
  export ALLOW_INSECURE_LOCALHOST_COOKIES="true"
  export NEXT_PUBLIC_APP_URL="http://localhost:${E2E_WEB_PORT}"
  export PYTHONUNBUFFERED="1"
}

e2e_apply_file_env() {
  local python_bin exports
  python_bin="$(command -v python3)"
  local -a args=(
    --web-port "${E2E_WEB_PORT}"
    --proxy-port "${E2E_OBJECT_PROXY_PORT:-18088}"
    --minio-port "${E2E_MINIO_PORT:-19000}"
    --clamav-port "${E2E_CLAMAV_PORT:-13310}"
    --access-key "${E2E_MINIO_ACCESS_KEY:-local-dev-only}"
    --secret-key "${E2E_MINIO_SECRET_KEY:-local-dev-only-change}"
  )
  if [[ -n "${E2E_FILES_LOCALHOST_RESOLVES:-}" ]]; then
    args+=(--files-localhost-resolves "${E2E_FILES_LOCALHOST_RESOLVES}")
  fi
  exports="$("${python_bin}" "${E2E_ENV_ROOT}/backend/apps/tenants/e2e_runtime.py" "${args[@]}")"
  # shellcheck disable=SC1090
  eval "${exports}"
}

e2e_prepare_env() {
  e2e_load_database_env
  e2e_apply_app_env
  e2e_apply_file_env
}
