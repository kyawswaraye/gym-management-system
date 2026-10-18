# Gym Management Platform

Multi-tenant gym management SaaS under phased development.

Phase 1A (security foundation) and Phase 1B (Super Admin provisioning and gym
prerequisites) are complete. The Phase 1B gate closed on 2026-10-17 on the
local CI-equivalent evidence in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).
CI still enforces those check classes on push and pull request.

Phase 2 implementation is complete as of 2026-10-17. Staff can manage
members, membership plans, and memberships inside one gym, including the full
member profile, private profile photos, the member summary, nested payment
history, expiry buckets, and membership-expiry scheduling and delivery
commands. `docs/DEVELOPMENT_PLAN.md` remains the immutable phase specification
and was not edited for this closure. Manual check-in and check-out, including
nested member check-in history and the member summary attendance facts, are
implemented. Staff can issue a rotating onsite QR challenge, and an
authenticated member can redeem it at `POST /api/v1/member/check-ins/scan/`.
The generic staff scan path is not published. An owner can enroll, rotate,
and revoke a display device, and that device can issue its own rotating
challenge. An owner can enroll an NFC or biometric reader, and that reader
can record attendance only through the device ingestion route. No
vendor-specific adapter is implemented, and no biometric template, raw
capture, or vendor secret is stored. Vendor-specific integration remains
optional. Trainer scan remains a Phase 5 assignment-scoped extension and is
not implemented. Public member identity linking, multi-member selection, the
React Native app, and the broader Phase 7 mobile APIs remain deferred and are
not prerequisites for the already-linked member QR path.

Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008.
Phase 3 closure evidence: backend checks passed; 225 non-PostgreSQL unit
tests passed; the full PostgreSQL security and integration suite passed 171
tests before the new check-in plan test, and that focused plan test passed
separately; web format, lint, typecheck, build, and 263 component and unit
tests passed earlier, and focused static checks passed after the end-to-end
spec change; a clean end-to-end reset passed 4 Playwright specs, including
member authentication, a rotating onsite challenge, member QR scan, staff
front desk, history, and summary, then member logout and refresh replay
denial. The peak query-plan baseline passed and is documented in
`docs/DATABASE.md`. It is not a production load test.

Phase 4 increment 1, the finance foundation under ADR 0009, is implemented.
`docs/DEVELOPMENT_PLAN.md` section 15 item 15 is resolved there. The increment
adds exact decimal money, encrypted idempotency claims, transactional receipt
sequences, and the `expense_categories.manage` permission. No payment,
receipt, expense, inventory, retail, or cash HTTP route is published.
Increment 1 gate evidence: backend ruff, format, and mypy passed and
migrations are in sync; 252 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 189 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI regeneration did not change the published
contract.
Phase 4 increment 2 publishes the cash-book API: open, manual adjustment,
reconcile/close, and the session and transaction lists. A membership that
holds Reception can view, use, and close only a session it opened, even when
it also holds another role, and it cannot adjust. Until an Owner-configurable
policy exists, only the protected Owner role may adjust. Movements are
append-only. The cash-book staff UI is deferred to increment 9. Notification HTTP routes stay
unpublished for later phases.
Increment 2 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 255 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 194 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
six cash routes and the command `429` responses. A second schema export
matched that file. The local `make check` contract step still reports those
files because this increment is not committed.
Phase 4 increment 3 publishes payment drafts, submit, approve, reject, cancel,
receipt list, detail, and download, and daily sales. Void, refund, and receipt
void stay unpublished. Approval requires a different active protected Manager
or Owner, and a cash approval must use an open session. Legacy posted cash
rows are marked `legacy_unlinked_cash` instead of receiving an invented cash
movement. The payment staff UI is deferred to increment 9.
Increment 3 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 258 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 216 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
payment, receipt, and daily-sales routes. A second schema export matched the
working tree. The local `make check` contract step still reports those files
because this increment is not committed.
Phase 4 increment 4 publishes expense-category list/create/update and expense
list/create/detail/update/submit/approve/reject/cancel. Expense void, salaries,
inventory, retail, and payment/receipt void/refund stay unpublished. Protected
system categories stay immutable. Custom category changes require
`expense_categories.manage`. Cash approval posts one `OUT` expense movement on
an open same-gym session. The expense staff UI is deferred to increment 9.
Increment 4 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 263 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 224 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
expense-category and expense command routes. A second schema export matched the
working tree. The local `make check` contract step still reports those files
because this increment is not committed.
`is_pt_client` stays false until Phase 5 derives it from an active trainer
assignment.

Production deployment is not complete. These gates remain open:

- an approved transactional email provider that honors `X-Idempotency-Key`,
  before invitation, password-recovery, or membership-expiry delivery is
  production-ready;
- production object-storage image and provider approval. Official MinIO images
  are not anonymously distributable. Local and end-to-end Compose use the
  digest-pinned third-party image `docker.io/pgsty/silo`, a MinIO-compatible
  fork. That image is not official MinIO, and the end-to-end profile-photo
  journey is interoperability evidence only;
- production object-storage and ClamAV credentials and deployment. Those values
  are not in the repository.

- Django REST Framework API for the published foundation and Phase 1B contracts;
- staff authentication, TOTP MFA, and rotating sessions;
- server-selected gym context;
- explicit Django tenant scoping and PostgreSQL forced RLS;
- permission and entitlement foundations;
- a Next.js browser BFF and application shell;
- platform administration, gym provisioning, owners, staff, roles, settings,
  and audit reads;
- automated security and contract checks.

Phase 3 attendance and authenticated member-self QR check-in are implemented.
Trainer and PT work, classes with remaining sessions, product inventory and
retail sales, reports, announcements and reminders, and the member mobile
application remain incomplete until their `docs/DEVELOPMENT_PLAN.md` phase
exit criteria pass. Documentation describes the approved target contract, not
proof that a later phase exists at runtime.

## Approved runtimes

- Python 3.14.7
- Django 5.2.17 LTS
- Django REST Framework 3.18.0
- Node.js 24.20.0 LTS
- Next.js 16.4.1 Active LTS
- PostgreSQL 18.6
- TypeScript 7.0.2

Use the exact versions in `.python-version`, `.nvmrc`, dependency locks, CI,
and container definitions. Next.js 16.4.1 is an approved security
compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable
historical baseline and still records Next.js 16.3.3.

## Local setup

1. Copy `.env.example` to `.env` and replace every placeholder.
2. Generate an RSA key pair under the ignored `secrets/` directory:

   ```sh
   mkdir -p secrets
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
     -out secrets/jwt-private.pem
   openssl rsa -pubout -in secrets/jwt-private.pem \
     -out secrets/jwt-public.pem
   chmod 600 secrets/jwt-private.pem secrets/jwt-public.pem
   ```

3. Start PostgreSQL 18.6 with `make db-up`.
4. Create a Python 3.14.7 virtualenv, then `make migrate`.
5. Verify RLS and ticket flows with `make backend-test-pg`.
6. Install the web workspace with Node.js 24.x (`24.20.0` in CI).

Django authentication routes accept only the trusted Next.js BFF. Production
Django listens on loopback and requires `BFF_SHARED_SECRET`.

`Secure` authentication cookies are mandatory outside isolated localhost
development. Browser JavaScript never receives authentication tokens.

## Browser smoke tests

`make e2e` runs the Playwright Chromium journeys against the real Next.js pages,
the same-origin BFF, and the Django API on PostgreSQL. The member journey also
uploads a profile photo through live MinIO, ClamAV, and the Caddy object proxy,
then checks that member in and out and shows the rotating desk QR. It does not
redeem a scan.
It uses only the disposable `gym_platform_e2e` database. Unit tests do not
install or launch a browser.

Prerequisites are the local PostgreSQL roles from `make db-up`, Docker with
Compose v2, Python 3.14.7, Node.js 24.20.0, and `npm --prefix web ci`. The
script installs Chromium through Playwright, starts a separate `gym-e2e-files`
compose project for MinIO, ClamAV, and the object proxy, builds the web app,
migrates as `gym_migration`, seeds MFA-backed E2E identities, and serves Django
as `gym_runtime` on `127.0.0.1:8010`. Next.js listens on `localhost:3010`.
Login still requires the BFF secret, password, and a real TOTP code. Placeholder
passwords, TOTP secrets, and the local MinIO password live in `scripts/e2e-env.sh`
and can be overridden with `E2E_*` variables; they are not production secrets.
The file-service containers are removed when the command exits. Their volumes
stay so ClamAV does not download signatures again, and the application Postgres
volume is not touched.
Migrations run as `gym_migration` with ordinary `manage.py migrate`. After
migrate, only `gym_platform_e2e` grants `gym_runtime` `SELECT` on
`django_migrations` so Django's runserver migration check can read that table.
Application table and function privileges come from the migrations.

```sh
make e2e
```

Set `E2E_RESET=1` to drop and recreate `gym_platform_e2e` before migrating.
Screenshots, traces, and video are off. A failure log can include the obvious
E2E placeholder password or a one-time authenticator code when the failure is
on the sign-in form.

## API contract

The published contract is committed at `contracts/openapi.v1.json`. It includes
the Phase 1A foundation and the gate-closed Phase 1B routes: platform
administration, gym provisioning and lifecycle, owners, staff, settings, audit
reads, role create, update, and archive, permission replacement, the permission
catalog, and membership-role assignment. Generated TypeScript definitions are
committed at `web/src/types/api.generated.ts`. Run `make openapi-check` with
the approved Python and Node versions to regenerate both artifacts and fail on
contract drift.

Phase 2 routes are published: MembershipPlan and Member list, create, detail,
update, and archive; Membership list, create, detail, note update, cancel,
suspend, resume, and expiry buckets; the member summary; nested member payment
history; and profile-photo upload, complete, download-url, and delete.
Membership-expiry scheduling and delivery commands are implemented. Manual
check-in list, detail, create, and check-out, nested member check-in
history, and staff check-in challenge issuance are published. Member login, refresh,
and logout are published for one already linked member. The scan route
stays unpublished.
Notification HTTP routes and general payment and receipt endpoints remain
unpublished. Production expiry delivery remains gated on an
idempotency-capable transactional provider. Production object-storage image
approval remains gated: Compose uses digest-pinned `docker.io/pgsty/silo`, a
third-party MinIO-compatible fork, not official MinIO. Finance, PT, reports,
and mobile routes stay excluded until their own phases.

## Documentation

Start at the documentation index, [`docs/README.md`](docs/README.md). It lists the
seven authoritative contracts and states which document owns each rule.

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — boundaries, tenancy, deployment
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, constraints, RLS, transactions
- [`docs/API.md`](docs/API.md) — versioned HTTP contracts and required permissions
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model, permission matrices, gates
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) — repository layout
- [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md) — phases, exit criteria, open decisions
- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — Burmese operational walkthrough

Accepted material decisions are recorded under [`docs/adr/`](docs/adr/).
