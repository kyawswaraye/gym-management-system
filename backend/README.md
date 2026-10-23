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
Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`.
The generic staff scan path and notification HTTP routes stay unpublished.
Display-device list, enroll, rotate, and revoke are published, as is
`POST /api/v1/device/check-in-challenges/`. NFC and biometric ingestion is published at `POST /api/v1/device/check-ins/`.
Payment draft, submit, approve, reject, cancel, void, refund, receipt list, detail, download, receipt void, and daily sales are published. Salary-period void and full retail-sale void are published in Increment 8B. Increment 8C publishes partial retail returns. Cash-session
list, detail, open, adjustment, and reconcile, plus the cash-transaction
list, are published. The cash-book, payment, expense, salary, inventory, and retail staff pages are delivered in increment 9. Increment 8C publishes partial retail returns. Expense-category and expense
approval routes are published. Manual expense void is published in Increment 8A. Salary-period
list, detail, create, update, line replacement, submit, approve, reject, and
cancel, and void are published. Increment 8C publishes partial retail returns. Product-category
list and create, product list, detail, create, and update, low-stock, and
stock-movement list and adjustment are published; category update remains
unpublished. Full retail-sale void is published in Increment 8B. Trainer scan is published
in Phase 5. Phase 5 Increment 1 publishes the trainer
directory and trainer-assignment list, create, detail, end, and cancel routes.
The member PT badge and protected-Trainer access to member, membership,
check-in, and profile-photo data are derived from effective active
assignments. Increment 2 publishes PT service-package list and create. Package
currency and actors are server-owned, definitions are immutable, and Class
packages remain closed until the ClassType catalog exists. Increment 3
publishes PT-sale draft list, create, detail, update, and cancellation with
assignment scope, commission previews, and no economic side effects. Increment
4 publishes an optional sale-owned PT payment draft, submit, and Manager/Owner
separate-actor approve and reject. Approval atomically posts the optional
payment, receipt, and cash movement and exactly one immutable ACCRUED
commission. Approval with no linked payment remains valid and creates no
receipt or cash movement. Generic payment commands cannot mutate a sale-owned
payment. Increment 5 publishes commission list, detail, and record-payment, including
partial payout, the protected commission expense, and optional cash out.
Payout reversal is an internal helper for a future sale void and has no public
route. Sale void, member package grants, the session ledger, bookings, classes,
and schedules were unpublished in Increment 5. Later increments publish those
routes, class types, schedules, occurrences, class and PT bookings, and
trainer scan. The staff and trainer pages and browser E2E are published.
Increments 1–5 did not by themselves close Phase 5. Production deployment is not
complete. Increments 1–3 historical gate evidence is in the repository README.
Increments 1–4 historical Increment 4 gate evidence: backend Ruff and format
passed; mypy passed on 179 source files; migration drift was clean;
non-PostgreSQL 321 passed and 269 were deselected; real PostgreSQL 269 passed
and 321 were deselected; web format, lint, typecheck, and build passed using
Node 24.20.0; Vitest passed 53 files and 296 tests; OpenAPI spectacular
validation with `--fail-on-warn` passed and the fresh output matched the
committed `contracts/openapi.v1.json`; IDE lints and `git diff --check` were
clean. No E2E was run because Increment 4 is backend/API only and no new web
workflow was published. Phase 5 remained open after Increment 4.
Phase 5 code and test gate is complete as of 2026-10-19. Full code scope is
complete: backend PT, classes, and trainer scan; staff and trainer web; and
browser E2E. Linked-member self-service remains Phase 7. This is code and test
gate closure, not production deployment. [ADR 0010](../docs/adr/0010-production-provider-readiness.md)
live Resend, AWS, and ClamAV provider proofs remain open. The numbers below are
the Phase 5 gate, not a Phase 6 or Phase 7 claim. Mobile implementation,
production readiness, and provider closure are not part of the Phase 5 claim.
Phase 7 now adds the member, announcement, device, and FCM routes and tables,
plus the separate Android/iOS app. The 2026-10-21 local code and test gate is
recorded in the repository README. Live Firebase and APNs credentials, release
signing, store submission, and a production device proof remain open. That
closure is not production deployment.
`make backend-check`: Ruff passed; format 312 files; mypy 199 source files;
migration drift none. `make backend-test`: 334 passed, 298 deselected. Final
serial `make backend-test-pg`: 298 passed, 334 deselected, 398.50s. Node
24.20.0 web format, lint, typecheck, and build passed; the build has 29
routes; Vitest 61 files / 326 tests. A fresh `spectacular --validate
--fail-on-warn` temporary export byte-matched `contracts/openapi.v1.json`;
generated types are current. A clean full `make e2e` passed 16 tests in 5.2m,
including 10 Phase 5 browser tests (assignments/catalogs; Reception submit +
separate Owner approval cash sale; grant+commission; two payouts; consumed
void rollback + untouched refund/reconciliation; class
schedule/book/attendance/no-show/cutoff; trainer commission scope and QR scan
assigned/unassigned/generic-route absence). A formal security review reported
no medium-or-higher finding in its reviewed slice but was partial, and that
partial review is not full Phase 5 coverage. A targeted GPT-5.6 Phase 5 audit
found and fixed trainer finance bypasses, premature attendance and no-show,
payout date bounds, and trainer scan throttling. Final targeted verification
found no remaining medium-or-higher issue in the audited Phase 5 scope. IDE
lints and `git diff --check` were clean.
Phase 6 Increment 1 publishes canonical `GET /api/v1/dashboard/` from
`apps/reports/` with no models or migrations. `reports.view` returns the
operational block. `reports.financial` adds the full financial block; otherwise
`financial` is null. Trainer finance closure remains. Presets are `today`,
`this_week`, `this_month`, `last_month`, and `this_year`, plus a custom
inclusive range of at most 366 days, with the gym timezone and currency on the
response. Posted ledger correction-date semantics, six mutually exclusive
revenue buckets including `CLASS`, pending approvals, ten zero-filled protected
categories, total expenses, and net profit are included. Overdue balance is
membership-only. Product snapshots are net of a full void and partial returns,
and top products are bounded to 10. Phase 6 Increment 2 publishes
`GET /api/v1/reports/daily/?date=YYYY-MM-DD` and
`GET /api/v1/reports/monthly/?year=YYYY&month=M` from `apps/reports/urls.py`,
and the tenant `/reports` page. Both require `reports.financial` and the
`reports` entitlement. Trainer finance closure remains. Daily is one row.
Monthly zero-fills every calendar date. Totals include range-distinct
attendees, salary and PT subtotals, and the ten protected categories. Six
revenue buckets include `CLASS`. A 1-day report and a 31-day report each use
10 queries. There are still no models, migrations, report tables, caches,
Redis, or Celery. Phase 6 Increment 3 publishes `GET /api/v1/reports/cash-book/` and
`GET /api/v1/reports/profit-loss/` with the same permission and entitlement.
Ranges are ordered, inclusive, and at most 366 days. Cash book uses two grouped
queries. Profit/loss reuses the Increment 2 formulas in six queries. Net profit
stays total revenue minus total expenses. Increment 3 is
gate-clean as of 2026-10-19. Gate evidence is in
`docs/DEVELOPMENT_PLAN.md`. Phase 6 Increment 4 publishes
`GET /api/v1/reports/membership-expiry/` and `GET /api/v1/reports/check-ins/`
for `reports.view` and the `reports` entitlement. `reports.financial` is not
required, and the responses do not include money. Trainer-only callers stay
closed. Membership expiry uses the current ACTIVE, SUSPENDED, or EXPIRED
status in 2 queries. Check-ins use gym-local windows in 4 queries.
Increment 4 is gate-clean as of 2026-10-20. Phase 6 Increment 5 publishes
`GET /api/v1/reports/pt-sessions/` and `GET /api/v1/reports/classes/` for
`reports.view` and the `reports` entitlement. `reports.financial` and the `pt`
entitlement are not required, and the responses do not include money. PT
sessions use the gym-local start date and current status in 2 queries. Class
rows use the occurrence local date, current statuses, and capacity summed on
scheduled and completed occurrences in 4 queries. At the Increment 5 close,
product, stock, and inventory reports, trainer-scoped dashboards and reports,
and exports remained unpublished.
At the Increment 5 close, Phase 6 was not complete. Increment 5 is gate-clean as of 2026-10-20. Gate evidence is in
`docs/DEVELOPMENT_PLAN.md`. Phase 6 Increment 6 is gate-clean as of 2026-10-20. It publishes
`GET /api/v1/reports/product-sales/`, `GET /api/v1/reports/stock-movements/`,
and `GET /api/v1/reports/inventory/`. Product sales requires `reports.financial`
and uses 8 queries over immutable line snapshots. Stock movements require
`reports.view` and use 1 grouped query. Inventory requires `reports.view` and
uses 3 queries for the current movement sum. At the Increment 6 close,
trainer-scoped dashboards and reports, and exports, remained unpublished.
Phase 6 Increment 7 is independently verified and gate-clean as of 2026-10-20.
It publishes `GET /api/v1/trainer/dashboard/` and
`GET /api/v1/trainer/reports/sessions/`, and `/pt/dashboard`, for protected
TRAINER callers with `reports.view` plus the `reports` and `pt` entitlements.
Both reads are own-profile only and include no money. The dashboard selector
uses 3 queries and the session-report selector uses 2. After authorization,
the dashboard handler uses 4 queries and the session handler uses 2. At that
close, report exports remained unpublished. Phase 6 Increment 8 is
independently verified and gate-clean as of 2026-10-20. It publishes
`GET /api/v1/reports/{kind}/export/` as an immediate CSV download for the
eleven gym reports, reuses the existing selector query counts, and does not
add export jobs, migrations, caches, Redis, or Celery. Trainer dashboard and
session reports are not exportable. Phase 6 implementation is complete and its
local CI-equivalent gate is closed. Final gate evidence—including the clean backend,
web, OpenAPI/types, full E2E, and `git diff --check` results—is in
`docs/DEVELOPMENT_PLAN.md`. Production deployment is not complete, and the
[ADR 0010](../docs/adr/0010-production-provider-readiness.md)
live Resend, AWS, and ClamAV proofs remain open.
Profile-photo
upload, complete, download-url, and delete are published. Phase 2
implementation is complete. `docs/DEVELOPMENT_PLAN.md` was not edited for that
closure. [ADR 0010](../docs/adr/0010-production-provider-readiness.md) selects AWS S3 in `ap-southeast-1` and Resend. The Resend client, password recovery, AWS virtual-hosted addressing, and ClamAV production fragment are implemented. Live credentials and provider proofs stay outside the repository. Local and end-to-end Compose
use digest-pinned `docker.io/pgsty/silo`, a test-only MinIO-compatible fork,
not official MinIO and not the production store. Production MinIO is no longer planned.
Live Resend, AWS, and ClamAV proofs remain open. Legal retention stays draft-only.

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

Run migrations with `make migrate` from the repository root, then run the local
API with `DATABASE_ROLE_MODE=runtime` bound to loopback. Production isolation
is a container-private bind, no API host port, and the Caddy member-route
allowlist. All `/api/v1/auth/` routes require the BFF shared secret. Migrations
fail if required roles are missing.
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
exactly `gym_platform_e2e`. The same guard wraps the temporary disable of the
append-only audit and owner-role cleanup triggers. Finance, cash, receipt,
salary, inventory, and idempotency protections stay enabled. The repeatable
browser reset drops and recreates `gym_platform_e2e` before migrate; prefix
deletion is not a substitute once those protected rows exist. If a protected
row blocks deletion, the original database error is raised. The command then
creates one MFA-enabled platform admin, one MFA-enabled owner gym, one
MFA-enabled protected Manager, and one already-linked member with an eligible
membership. The manager is invited and accepted through the staff workflow.
That manager then creates the catalog item and opening stock, submits a card
retail sale, and the owner approves it. The member password is
`E2E_MEMBER_PASSWORD` and the manager password is `E2E_MANAGER_PASSWORD`.
Member MFA stays off because member login has no challenge contract. The
command does not change production authentication and does not disable finance,
stock, or retail immutability triggers.

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

- Password recovery is implemented under [ADR 0010](../docs/adr/0010-production-provider-readiness.md): global user scope for platform, staff, and linked-member identities; BFF routes; forgot constant `200`; reset `204`; a 30-minute high-entropy hash-only single-use token under `PASSWORD_RESET_TOKEN_PEPPER`; one live token per user; page `/reset-password?token=`; existing password validators and a different password; revocation of every refresh session, gym-selection ticket, and MFA challenge; an `authz_version` bump on every linked gym membership and member; MFA left enabled; login required afterward. Provider failure stays private. Django's
  configurable `EMAIL_BACKEND` is still the delivery abstraction. Production uses
  `apps.common.mail.resend.EmailBackend`. Tests and E2E use
  non-delivery backends. `schedule_membership_expiry_emails` and
  `deliver_membership_expiry_emails` are implemented. The delivery command
  locks one due row at a time with `skip_locked`, commits that outcome after
  the provider call, and sets `X-Idempotency-Key` as an email header. The
  Resend backend translates that header to `Idempotency-Key`. Concurrent runs and ordinary
  reruns do not deliver one row twice. A crash after provider acceptance and
  before commit can still duplicate that send until Resend honors
  `Idempotency-Key` (1–256 characters, retained 24 hours). The live domain,
  API key, and idempotency proof are absent. Notification HTTP routes
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
  scan is published at `POST /api/v1/trainer/check-ins/scan/`.
  No vendor-specific adapter is implemented, and no biometric template, raw
  capture, or vendor secret is stored. Vendor-specific integration remains
  optional. Public linking, multi-member selection, and member MFA remain
  deferred. The broader member-self APIs and React Native app are implemented
  in Phase 7. Phase 3 implementation is complete as of
  2026-10-18 under ADRs 0004–0008. The closure evidence is in the repository
  README. The generic staff scan path and notification routes stay unpublished.
  Payment and receipt routes are published in Increment 3.
  Phase 4 increment 1 is the finance foundation under ADR 0009: exact money,
  encrypted idempotency, receipt sequences, and `expense_categories.manage`.
  Increment 3 publishes payment, receipt, and daily-sales routes on that foundation.
  Increment 4 publishes expense-category and expense approval routes.
  Increment 8A publishes payment void, payment refund, receipt void, and
  manual expense void. Increment 8B publishes salary-period void and full
  retail-sale void. A cash correction names an open destination session.
  Increment 8C publishes partial retail returns.
  Increment 5 publishes salary-period and salary-line routes.
  Increment 6 publishes product-category list/create, product
  list/create/detail/update, low-stock, stock-movement list, and manual
  adjustment. Category update remains unpublished. Payment and manual expense
  corrections are Increment 8A.
  Catalog and stock commands use the finance entitlement.
  Increment 7 publishes retail-sale list/create/detail/update and
  submit/approve/reject/cancel. Increment 8B publishes full retail void.
  Increment 8C later publishes `POST /api/v1/retail-sales/{id}/return/`.
  The retail staff page exposes that return for a posted sale. Approval posts stock, a product payment, a receipt,
  and a cash movement in one transaction.
- Platform gym provisioning, lifecycle, owner invitation, platform audit reads,
  tenant audit reads, staff invitation/acceptance, staff profiles, staff
  activate/suspend, and tenant role/permission management are in place.
  PT, notification HTTP routes, and the member mobile app are not complete.
  Membership-expiry email commands are implemented and remain gated on the
  production email provider described above.
  Tenant `FileAsset` metadata, checks, and forced RLS are in place. Profile-photo
  upload intent, complete, download URL, and delete routes are published.
  Pillow 13.0.0 re-encodes those images. Local and end-to-end storage is the private test fork. Production storage is AWS S3 when the live bucket exists. ClamAV stays on the private network.
  Credentials are environment variables, never repository secrets, and the production values are not set.
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
  salary-period routes, Increment 6 product-category, product, low-stock,
  and stock-movement routes, Increment 7 retail-sale routes, Increment 8A
  payment void, payment refund, receipt void, and expense void, and Increment
  8B salary-period void and full retail-sale void, and the Increment 8C
  `POST /api/v1/retail-sales/{id}/return/` route. The retail staff page exposes
  that return. It leaves the generic staff scan path,
  notification, member profile, and category update unpublished.
  Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`.
  `profile_photo_asset_id` remains a nullable identifier with no foreign key to
  `FileAsset`. The staff API does not accept or return it. Attaching it later
  requires a same-gym asset whose scan status is CLEAN.
- `platform.settings.manage` remains in the historical permission catalog and is
  marked inactive in Phase 1B. It has no HTTP route, is omitted from platform
  sessions while inactive, and never authorizes tenant `settings.manage`.
  The retirement migration does not reverse it into an active grant.
- Phase 8 increment 13 adds an opt-in local identity-security characterization
  on the disposable staging rehearsal. The record is
  `docs/runbooks/phase8-increment-13.md`. It does not prove a penetration test,
  a formal security review, production security, tenant isolation, or a live
  provider. Phase 8 increment 14 adds an opt-in local tenant-isolation
  characterization. The record is `docs/runbooks/phase8-increment-14.md`. It
  does not prove independent tenant isolation, a penetration test, a formal
  security review, or production proof. Phase 8 is not complete.
