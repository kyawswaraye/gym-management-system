# Gym Management Platform

Multi-tenant gym management SaaS under phased development.

Phase 1A (security foundation) and Phase 1B (Super Admin provisioning and gym
prerequisites) are complete. The Phase 1B gate closed on 2026-10-17 on the
local CI-equivalent evidence in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).
CI still enforces those check classes on push and pull request.

Phase 2 prerequisites are approved as the member, membership, expiry, file,
and dashboard policies already specified in the canonical contracts. Phase 2
publishes the MembershipPlan and Member list, create, detail, update, and
archive APIs, and the Membership sale, lifecycle, and expiry API.
Membership-expiry scheduling and delivery commands are implemented. Member
summary and history, notification HTTP routes, and payment endpoints remain
unpublished. Production expiry delivery remains gated on an approved
transactional provider that honors the idempotency header. Earlier persistence
for those areas stays outside the closed Phase 1B gate.

- Django REST Framework API for the published foundation and Phase 1B contracts;
- staff authentication, TOTP MFA, and rotating sessions;
- server-selected gym context;
- explicit Django tenant scoping and PostgreSQL forced RLS;
- permission and entitlement foundations;
- a Next.js browser BFF and application shell;
- platform administration, gym provisioning, owners, staff, roles, settings,
  and audit reads;
- automated security and contract checks.

The target product additionally includes membership management, attendance and
unattended onsite QR check-in, trainer/PT and classes with remaining sessions,
product inventory and retail sales, reports, announcements/reminders, and a
member mobile application. These remain incomplete until their
`docs/DEVELOPMENT_PLAN.md` phase exit criteria pass. Documentation describes
the approved target contract, not proof that a feature exists at runtime.

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

`make e2e` runs two Playwright Chromium journeys against the real Next.js pages,
the same-origin BFF, and the Django API on PostgreSQL. It uses only the
disposable `gym_platform_e2e` database. Unit tests do not install or launch a
browser.

Prerequisites are the local PostgreSQL roles from `make db-up`, Python 3.14.7,
Node.js 24.20.0, and `npm --prefix web ci`. The script installs Chromium through
Playwright, builds the web app, migrates as `gym_migration`, seeds MFA-backed
E2E identities, and serves Django as `gym_runtime` on `127.0.0.1:8010`. Next.js
listens on `localhost:3010`. Login still requires the BFF secret, password, and
a real TOTP code. Placeholder passwords and TOTP secrets live in `scripts/e2e-env.sh`
and can be overridden with `E2E_*` variables; they are not production secrets.
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

MembershipPlan and Member list, create, detail, update, and archive are
published. Membership list, create, detail, note update, cancel, suspend,
resume, and expiry buckets are published. Membership-expiry scheduling and
delivery commands are implemented. Member summary and history, notification
HTTP routes, payment, and receipt endpoints remain unpublished. Production
expiry delivery remains gated on an idempotency-capable transactional provider.
Check-in, finance, PT, reports, and
mobile routes stay excluded until their own phases.

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
