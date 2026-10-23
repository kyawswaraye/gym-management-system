#!/usr/bin/env bash
set -euo pipefail

# docker-entrypoint-initdb.d runs only when the data directory is empty.
# It does not create or alter gym_monitor on an existing volume.
# Existing databases use scripts/bootstrap_monitor_role.py.
# Local Compose and the backup rehearsal do not mount the monitor password.
# This script skips gym_monitor when that file is absent so those fresh
# databases still initialize. Production Compose mounts the file, so a fresh
# production volume creates gym_monitor.
# PostgreSQL 18 psql reads passwords with \getenv. They are not argv.

monitor_file="${GYM_MONITOR_PASSWORD_FILE:-/run/secrets/monitor_db_password}"
if [ -z "${POSTGRES_USER+x}" ] || [ -z "${POSTGRES_DB+x}" ] \
  || [ -z "${POSTGRES_PASSWORD+x}" ] || [ -z "${POSTGRES_MIGRATION_PASSWORD+x}" ] \
  || [ -z "${POSTGRES_RUNTIME_PASSWORD+x}" ]; then
  echo "postgres role passwords are required" >&2
  exit 1
fi

create_monitor=0
monitor_password=""
if [ -f "$monitor_file" ]; then
  create_monitor=1
  monitor_password=$(tr -d '\r' < "$monitor_file")
  monitor_password=${monitor_password%$'\n'}
  if [ -z "$monitor_password" ] || [ "$monitor_password" = "replace-from-secret-store" ]; then
    echo "gym_monitor password placeholder is refused" >&2
    exit 1
  fi
  case "$monitor_password" in
    *$'\n'*|*$'\r'*)
      echo "gym_monitor password must be one line" >&2
      exit 1
      ;;
  esac
  if [ "$monitor_password" = "${POSTGRES_PASSWORD}" ] \
    || [ "$monitor_password" = "${POSTGRES_MIGRATION_PASSWORD}" ] \
    || [ "$monitor_password" = "${POSTGRES_RUNTIME_PASSWORD}" ]; then
    echo "gym_monitor password must differ from admin, migration, and runtime" >&2
    exit 1
  fi
fi

psql_args=(
  psql
  --username "$POSTGRES_USER"
  --dbname "$POSTGRES_DB"
)
if [ "$create_monitor" = 1 ]; then
  GYM_MONITOR_PASSWORD="$monitor_password" "${psql_args[@]}" <<'SQL'
\set ON_ERROR_STOP on
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv runtime_password POSTGRES_RUNTIME_PASSWORD
\getenv db_name POSTGRES_DB
\getenv monitor_password GYM_MONITOR_PASSWORD
CREATE ROLE gym_migration LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE gym_migration PASSWORD :'migration_password';

CREATE ROLE gym_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE gym_runtime PASSWORD :'runtime_password';

CREATE ROLE gym_auth_bootstrap NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
GRANT gym_auth_bootstrap TO gym_migration;

CREATE ROLE gym_monitor LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE gym_monitor PASSWORD :'monitor_password';
GRANT CONNECT ON DATABASE :"db_name" TO gym_monitor;
GRANT pg_monitor TO gym_monitor;

ALTER DATABASE :"db_name" OWNER TO gym_migration;
ALTER SCHEMA public OWNER TO gym_migration;
GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
GRANT CONNECT ON DATABASE :"db_name" TO gym_runtime;
GRANT USAGE ON SCHEMA public TO gym_runtime;
SQL
else
  "${psql_args[@]}" <<'SQL'
\set ON_ERROR_STOP on
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv runtime_password POSTGRES_RUNTIME_PASSWORD
\getenv db_name POSTGRES_DB
CREATE ROLE gym_migration LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE gym_migration PASSWORD :'migration_password';

CREATE ROLE gym_runtime LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE gym_runtime PASSWORD :'runtime_password';

CREATE ROLE gym_auth_bootstrap NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
GRANT gym_auth_bootstrap TO gym_migration;

ALTER DATABASE :"db_name" OWNER TO gym_migration;
ALTER SCHEMA public OWNER TO gym_migration;
GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
GRANT CONNECT ON DATABASE :"db_name" TO gym_runtime;
GRANT USAGE ON SCHEMA public TO gym_runtime;
SQL
fi
unset monitor_password
