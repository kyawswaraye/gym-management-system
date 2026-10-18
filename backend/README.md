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
payment history is published. The member and membership browser workspaces
follow those cursor pages, and the membership workspace can browse one expiry
bucket at a time. Manual check-in list, detail, create, check-out, nested
member check-in history, and staff check-in challenge issuance are published.
Member login, refresh, and logout are published for one already linked member.
Member-self QR redemption is published at `POST /api/v1/member/check-ins/scan/`.
Display-device list, enroll, rotate, and revoke are published, as is
`POST /api/v1/device/check-in-challenges/`. NFC and biometric ingestion is published at `POST /api/v1/device/check-ins/`.
The generic staff scan path, trainer scan, notification HTTP routes, and
Payment draft, submit, approve, reject, cancel, receipt list, detail, download, and daily sales are published. Void, refund, and receipt void remain unpublished. Cash-session
list, detail, open, adjustment, and reconcile, plus the cash-transaction
list, are published. The cash-book staff UI is deferred to increment 9. Expense-category and expense
approval routes are published; expense void remains unpublished. Salary-period
list, detail, create, update, line replacement, submit, approve, reject, and
cancel are published; salary void remains unpublished. Product-category
list and create, product list, detail, create, and update, low-stock, and
stock-movement list and adjustment are published; category update and retail
remain unpublished. Trainer scan
waits for Phase 5 assignments. Profile-photo
upload, complete, download-url, and delete are published. Phase 2
implementation is complete. `docs/DEVELOPMENT_PLAN.md` was not edited for that
closure. Production MinIO-compatible storage and ClamAV credentials stay in
the environment and are not repository secrets. Local and end-to-end Compose
use digest-pinned `docker.io/pgsty/silo`, a third-party MinIO-compatible fork,
not official MinIO. Production object-storage image and provider approval
remains open because official MinIO images are not anonymously distributable.
Production expiry delivery remains gated on an idempotency-capable
transactional provider.

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
That command also starts loopback MinIO, ClamAV, and the object proxy for the
Phase 2 member-lifecycle journey.
`python manage.py seed_e2e` runs only when `E2E_SEED=1`,
`DJANGO_SETTINGS_MODULE=config.settings.e2e`, and the connected database is
exactly `gym_platform_e2e`. It deletes prior `E2E*` gyms and `e2e-*@example.test`
users in that database, then creates one MFA-enabled platform admin, one
MFA-enabled owner gym, and one already-linked member with an eligible
membership. The member password is `E2E_MEMBER_PASSWORD`. Member MFA stays
off because member login has no challenge contract. The command does not
change production authentication.

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
- Member login, refresh, and logout are published for a user who already has
  exactly one active linked member. Tokens are returned in the JSON body for a
  future mobile client and are not browser cookies. Zero or several active
  links fail closed with the generic credential error. There is no member
  selector and no public linking route. MFA-enabled identities cannot use this
  login until a member challenge exists. MembershipPlan and Member list,
  create, detail, update, and archive are published, as are membership sale,
  lifecycle, and expiry routes, the member summary, and nested member payment
  history. Manual check-in routes, nested check-in history, staff
  check-in challenge issuance, display and integration device credentials,
  device challenge issuance, device ingestion, and member QR redemption are
  published. The approved QR path is authenticated member-self scan. Trainer
  scan remains a Phase 5 assignment-scoped extension and is not implemented.
  No vendor-specific adapter is implemented, and no biometric template, raw
  capture, or vendor secret is stored. Vendor-specific integration remains
  optional. Public linking, multi-member selection, the React Native app, and
  broader Phase 7 mobile APIs remain deferred and are not prerequisites for
  the already-linked member QR path. Phase 3 implementation is complete as of
  2026-10-18 under ADRs 0004–0008. The closure evidence is in the repository
  README. The generic staff scan path and notification routes stay unpublished.
  Payment and receipt routes are published in Increment 3.
  Phase 4 increment 1 is the finance foundation under ADR 0009: exact money,
  encrypted idempotency, receipt sequences, and `expense_categories.manage`.
  Increment 3 publishes payment, receipt, and daily-sales routes on that foundation.
  Increment 4 publishes expense-category and expense approval routes. Void, refund,
  receipt void, and expense void remain unpublished.
  Increment 5 publishes salary-period and salary-line routes. Salary void
  remains unpublished.
  Increment 6 publishes product-category list/create, product
  list/create/detail/update, low-stock, stock-movement list, and manual
  adjustment. Category update, retail, and void/refund routes remain
  unpublished. Catalog and stock commands use the finance entitlement.
- Platform gym provisioning, lifecycle, owner invitation, platform audit reads,
  tenant audit reads, staff invitation/acceptance, staff profiles, staff
  activate/suspend, and tenant role/permission management are in place.
  PT, notification HTTP routes, and the member mobile app are not complete.
  Membership-expiry email commands are implemented and remain gated on the
  production email provider described above.
  Tenant `FileAsset` metadata, checks, and forced RLS are in place. Profile-photo
  upload intent, complete, download URL, and delete routes are published.
  Pillow 13.0.0 re-encodes those images. MinIO and ClamAV stay private and are
  configured with environment variables, never repository secrets.
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
  member summary, nested member payment history, manual check-in routes,
  staff check-in challenge issuance, member login, refresh, and logout, and
  member QR redemption, and display-device enrollment, rotation, revocation,
  and device challenge issuance, Increment 3 payment, receipt, and daily-sales
  routes, Increment 4 expense-category and expense approval routes, Increment 5
  salary-period routes, and Increment 6 product-category, product, low-stock,
  and stock-movement routes. It leaves
  the generic staff scan
  path, trainer scan, notification, member profile, payment void, refund, receipt
  void, expense void, salary void, category update, and retail routes unpublished.
  `profile_photo_asset_id` remains a nullable identifier with no foreign key to
  `FileAsset`. The staff API does not accept or return it. Attaching it later
  requires a same-gym asset whose scan status is CLEAN.
- `platform.settings.manage` remains in the historical permission catalog and is
  marked inactive in Phase 1B. It has no HTTP route, is omitted from platform
  sessions while inactive, and never authorizes tenant `settings.manage`.
  The retirement migration does not reverse it into an active grant.
