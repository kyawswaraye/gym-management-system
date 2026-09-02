# Phase 1A Django backend

This directory contains only the Phase 1A identity, tenancy, authorization, session,
audit, RLS, and API foundation.

## Runtime

- Python 3.14.7 (the local verification host currently provides 3.13.7)
- Django 5.2.17
- Django REST Framework 3.18.0
- PostgreSQL 18.6 for database/security verification

Create a Python 3.14.7 virtual environment and install
`requirements/development.txt`. Production installs `requirements/production.txt`.

## Database roles and migrations

Provision these roles before applying migrations:

- `gym_migration`: schema owner/migration identity; not used by the API.
- `gym_runtime`: `NOSUPERUSER NOBYPASSRLS NOINHERIT`; never owns schema objects.
- `gym_auth_bootstrap`: `NOLOGIN BYPASSRLS`; owns only the narrowly granted
  `app_staff_memberships()` security-definer function. The runtime role can execute
  the function but cannot assume this role.

Run migrations with `make migrate` from the repository root, then run the API
with `DATABASE_ROLE_MODE=runtime` on loopback only. All `/api/v1/auth/` routes
require the BFF shared secret. Migrations fail if required roles are missing.
PostgreSQL-only tests use `make backend-test-pg` (`RUN_POSTGRESQL_TESTS=1` and
`TEST_DB_USER=gym_admin`). Runtime RLS proofs `SET LOCAL ROLE gym_runtime`.

## Checks

```text
ruff format --check .
ruff check .
mypy apps config
python manage.py check
python manage.py makemigrations --check --dry-run
pytest -m "not postgresql"
RUN_POSTGRESQL_TESTS=1 pytest -m postgresql
```

## Deliberate limitations

- Password recovery and MFA challenge endpoints return
  `501 PROVIDER_NOT_CONFIGURED`; no provider or secret format is invented.
- MFA-enabled users fail closed at login until the Phase 1B provider decision.
- A user with multiple eligible gyms receives a 5-minute, single-use
  `GymSelectionTicket`. The BFF stores the plaintext ticket in an HttpOnly
  cookie; `/auth/select-gym/` consumes it atomically and then issues the
  selected-gym session. Browser JavaScript never receives the ticket.
- Member authentication is configuration-only in Phase 1A; member models/routes
  belong to later phases.
- No Phase 1B provisioning/admin API and no member, staff-profile, check-in,
  finance, receipt, cash, PT, report, notification, file, or mobile model exists.
