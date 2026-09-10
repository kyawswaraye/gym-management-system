#!/usr/bin/env bash
set -euo pipefail

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=db_name="$POSTGRES_DB" \
  --set=migration_password="$POSTGRES_MIGRATION_PASSWORD" \
  --set=runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<'SQL'
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
