#!/usr/bin/env bash
# Create the disposable E2E database, migrate as gym_migration, seed, then
# serve the API as gym_runtime on loopback. Playwright starts this script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
E2E_ENV_ROOT="${ROOT}"
# shellcheck disable=SC1091
source "${ROOT}/scripts/e2e-env.sh"
e2e_prepare_env

if [[ "${DB_NAME}" != "gym_platform_e2e" ]]; then
  echo "Refusing to prepare a database other than gym_platform_e2e." >&2
  exit 1
fi

PYTHON="${E2E_PYTHON:-}"
if [[ -z "${PYTHON}" && -x "${ROOT}/backend/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/backend/.venv/bin/python"
fi
if [[ -z "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

mkdir -p "${ROOT}/tmp/e2e"
umask 077
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out "${ROOT}/tmp/e2e/jwt-private.pem"
openssl rsa -pubout -in "${ROOT}/tmp/e2e/jwt-private.pem" \
  -out "${ROOT}/tmp/e2e/jwt-public.pem" >/dev/null
chmod 600 "${ROOT}/tmp/e2e/jwt-private.pem" "${ROOT}/tmp/e2e/jwt-public.pem"

pem_line() {
  awk 'NF { printf "%s\\n", $0 }' "$1"
}

export JWT_KEY_ID="e2e-local-key"
export JWT_PRIVATE_KEY="$(pem_line "${ROOT}/tmp/e2e/jwt-private.pem")"
JWT_PUBLIC_PEM="$(pem_line "${ROOT}/tmp/e2e/jwt-public.pem")"
export JWT_PUBLIC_KEYS="${JWT_KEY_ID}=${JWT_PUBLIC_PEM}"

psql_admin() {
  if command -v psql >/dev/null 2>&1; then
    PGPASSWORD="${POSTGRES_ADMIN_PASSWORD}" psql \
      -h "${POSTGRES_HOST}" \
      -p "${POSTGRES_PORT}" \
      -U "${POSTGRES_ADMIN_USER}" \
      -v ON_ERROR_STOP=1 \
      "$@"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker compose --project-directory "${ROOT}" exec -T postgres \
      psql -U "${POSTGRES_ADMIN_USER}" -v ON_ERROR_STOP=1 "$@"
    return
  fi
  echo "psql or docker is required to prepare gym_platform_e2e." >&2
  exit 1
}

role_exists="$(psql_admin -d postgres -tA -c "SELECT 1 FROM pg_roles WHERE rolname = 'gym_migration'")" || exit 1
if [[ "${role_exists//[[:space:]]/}" != "1" ]]; then
  echo "Database roles are missing. Start PostgreSQL with make db-up or the CI role setup first." >&2
  exit 1
fi

if [[ "${E2E_RESET:-0}" == "1" ]]; then
  psql_admin -d postgres <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'gym_platform_e2e' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS gym_platform_e2e;
SQL
fi

database_exists="$(psql_admin -d postgres -tA -c "SELECT 1 FROM pg_database WHERE datname = 'gym_platform_e2e'")" || exit 1
if [[ "${database_exists//[[:space:]]/}" != "1" ]]; then
  psql_admin -d postgres -c "CREATE DATABASE gym_platform_e2e OWNER gym_migration"
fi

psql_admin -d postgres <<'SQL'
GRANT gym_auth_bootstrap TO gym_migration;
GRANT CONNECT ON DATABASE gym_platform_e2e TO gym_runtime, gym_migration;
SQL

psql_admin -d gym_platform_e2e <<'SQL'
ALTER SCHEMA public OWNER TO gym_migration;
GRANT USAGE ON SCHEMA public TO gym_runtime, gym_migration;
SQL

cd "${ROOT}/backend"
export DATABASE_ROLE_MODE="MIGRATION"
unset PGOPTIONS
"${PYTHON}" manage.py migrate --noinput
# Test-only: Django runserver checks django_migrations before serving.
# gym_runtime does not own schema and receives no other privilege here.
psql_admin -d gym_platform_e2e <<'SQL'
GRANT SELECT ON django_migrations TO gym_runtime;
SQL
E2E_SEED=1 "${PYTHON}" manage.py seed_e2e
export DATABASE_ROLE_MODE="runtime"
exec "${PYTHON}" manage.py runserver "127.0.0.1:${E2E_API_PORT}" --noreload
