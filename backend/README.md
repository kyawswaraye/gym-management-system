# Django backend

This directory contains the gate-closed Phase 1A identity, tenancy,
authorization, session, audit, RLS, and API foundation, and the gate-closed
Phase 1B platform provisioning, gym lifecycle, owner and staff administration,
roles, settings, and audit reads. Phase 1B closed on 2026-10-17 on the local
CI-equivalent evidence in `docs/DEVELOPMENT_PLAN.md`. CI still enforces those
check classes on push and pull request.

Member, membership, payment, receipt, and notification tables exist from
earlier work. MembershipPlan list, create, detail, update, and archive are
published. Member list, create, detail, update, and archive are published.
Membership list, create, detail, note update, cancel, suspend, resume, and
expiry buckets are published. Membership-expiry scheduling and delivery
commands are implemented. The member summary API is published. Nested member
payment and check-in history, notification HTTP routes, and payment and
receipt HTTP endpoints remain unpublished. Production expiry delivery remains
gated on an idempotency-capable transactional provider. Phase 2 stays open
until its development-plan exit criteria pass.

## Runtime

- Python 3.14.7 (the local verification host currently provides 3.13.7)
- Django 5.2.17
- Django REST Framework 3.18.0
- PostgreSQL 18.6 for database/security verification

Create a Python 3.14.7 virtual environment and install
`requirements/development.txt`. Production installs `requirements/production.txt`.

## Database roles and migrations

Provision these roles before applying migrations:

- `gym_migration`: schema owner/migration identity; `NOBYPASSRLS NOINHERIT`; not used by the API.
- `gym_runtime`: `NOSUPERUSER NOBYPASSRLS NOINHERIT`; never owns schema objects.
- `gym_auth_bootstrap`: `NOLOGIN NOINHERIT BYPASSRLS`. Owns only the
  `row_security=off` security-definer functions, with `search_path` locked to
  `pg_catalog, public`. Runtime cannot assume this role. The role has `SELECT`
  only on the tables those functions read and has no write privilege. `PUBLIC`
  cannot execute them. Runtime can execute `app_staff_memberships()`,
  `app_owner_invitation(text)`, and `app_staff_invitation(text)`. The integrity
  triggers are not granted to runtime.
- Runtime has no table-level `INSERT` or `UPDATE` on `users_user`. It can update
  only `mfa_enabled`, `mfa_secret_encrypted`, `mfa_last_used_step`, and
  `updated_at`. Invitation identities and the one acceptance password
  transition go through locked security-definer functions.

Run migrations with `make migrate` from the repository root, then run the API
with `DATABASE_ROLE_MODE=runtime` on loopback only. All `/api/v1/auth/` routes
require the BFF shared secret. Migrations fail if required roles are missing.
PostgreSQL-only tests use `make backend-test-pg` (`RUN_POSTGRESQL_TESTS=1` and
`TEST_DB_USER=gym_admin`). Runtime RLS proofs `SET LOCAL ROLE gym_runtime`.

Terminal gym-selection tickets are retained for 30 days. A privileged maintenance
database identity can inspect and purge eligible rows in bounded batches:

```text
python manage.py purge_gym_selection_tickets --dry-run
python manage.py purge_gym_selection_tickets --batch-size=1000
```

The runtime database role has no `DELETE` grant for this maintenance path.

Browser smoke coverage is started from the repository root with `make e2e`.
`python manage.py seed_e2e` runs only when `E2E_SEED=1`,
`DJANGO_SETTINGS_MODULE=config.settings.e2e`, and the connected database is
exactly `gym_platform_e2e`. It deletes prior `E2E*` gyms and `e2e-*@example.test`
users in that database, then creates one MFA-enabled platform admin and one
MFA-enabled owner gym. It does not change production authentication.

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

- Password recovery endpoints return `501 PROVIDER_NOT_CONFIGURED`. Django's
  configurable `EMAIL_BACKEND` is the delivery abstraction. Tests and E2E use
  non-delivery backends. `schedule_membership_expiry_emails` and
  `deliver_membership_expiry_emails` are implemented. The delivery command
  locks one due row at a time with `skip_locked`, commits that outcome after
  the provider call, and sets `X-Idempotency-Key`. Concurrent runs and ordinary
  reruns do not deliver one row twice. A crash after provider acceptance and
  before commit can still duplicate that send until the backend honors the
  header. Production must configure an approved transactional provider that
  does so before invitation, password-recovery, or expiry delivery is
  production-ready. Vendor selection remains deferred. Notification HTTP routes
  stay unpublished.
- MFA is TOTP and is mandatory for platform administrators and Owners.
  MFA-enabled users do not receive a session at password login. They receive a
  short-lived challenge and must complete `POST /api/v1/auth/mfa/challenge/`
  with a TOTP code. Secrets are encrypted, and codes are replay-protected.
  Owner invitation and revocation require a recently verified MFA session.
  Broader role-based MFA may be required later as a hardening decision.
- A user with multiple eligible gyms receives a 5-minute, single-use
  `GymSelectionTicket`. The BFF stores the plaintext ticket in an HttpOnly
  cookie; `/auth/select-gym/` consumes it atomically and then issues the
  selected-gym session. Browser JavaScript never receives the ticket.
- Member authentication is configuration-only. MembershipPlan and Member list,
  create, detail, update, and archive are published, as are membership sale,
  lifecycle, and expiry routes and the member summary. Nested member payment
  and check-in history, notification, and payment routes stay out of the
  published OpenAPI contract until their own increments.
- Platform gym provisioning, lifecycle, owner invitation, platform audit reads,
  tenant audit reads, staff invitation/acceptance, staff profiles, staff
  activate/suspend, and tenant role/permission management are in place.
  Check-in, cash, PT, notification HTTP routes, and mobile are not complete.
  Membership-expiry email commands are implemented and remain gated on the
  production email provider described above.
  Tenant `FileAsset` metadata, checks, and forced RLS are in place. File HTTP
  routes, signed URLs, and object-storage integration are not.
- `GET /api/v1/audit-logs/` requires a staff access token and active `audit.view`.
  A platform token is rejected. An active owner receives every same-gym tenant
  audit row (`scope=GYM` in the collection metadata). Every other caller
  receives only rows whose actor membership is their own (`scope=SELF`).
  Responses omit gym, global actor identity, IP address, user agent, record
  timestamps, and credential-like values. There is no gym selector and no
  update or delete route.
- `POST /api/v1/admin/gyms/` treats omitted `owner.send_invitation` as true.
  The initial Owner membership stays ACTIVE. The acceptance link is emailed
  synchronously inside the provisioning transaction, and only the token hash
  is stored. A successful idempotent replay does not send another invitation.
  `send_invitation=false` is allowed only when that email already belongs to
  an eligible user with a usable password; otherwise the request returns
  `409 OWNER_CREDENTIAL_REQUIRED` and rolls back. Email delivery failure
  rolls back the same transaction. There is no invitation outbox or Celery task.
  After that invitation expires, `POST /api/v1/admin/gyms/{gym_id}/owners/`
  can issue a new credential link for the same active Owner while the password
  remains unusable. A still-pending invitation is returned unchanged. Tenant
  owners cannot reinvite an existing active Owner.
- The tenant delegable set is the active protected MANAGER role's permissions
  minus the five hard-reserved approval codes and `settings.manage`. There is
  no separate delegable-set endpoint. Owners change that set by replacing
  MANAGER permissions and must keep the hard-reserved approvals. Custom roles
  cannot receive those approvals or `settings.manage`. Published OpenAPI
  includes role create, update, archive, permission replacement, the permission
  catalog, and membership-role assignment, plus MembershipPlan, Member list,
  create, detail, update, and archive, membership sale and lifecycle, and the
  member summary. It leaves nested member payment and check-in history,
  notification, and payment routes unpublished.
  `profile_photo_asset_id` remains a nullable identifier with no foreign key to
  `FileAsset`. The staff API does not accept or return it. Attaching it later
  requires a same-gym asset whose scan status is CLEAN.
- `platform.settings.manage` remains in the historical permission catalog and is
  marked inactive in Phase 1B. It has no HTTP route, is omitted from platform
  sessions while inactive, and never authorizes tenant `settings.manage`.
  The retirement migration does not reverse it into an active grant.
