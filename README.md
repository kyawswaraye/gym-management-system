# Gym Management Platform

Multi-tenant gym management SaaS under phased development.

The repository contains the Phase 1A security foundation plus an initial,
incomplete slice of member, membership, payment, and owner-dashboard
operations. Passing tests for that slice does not mean the full roadmap is
implemented.

- Django REST Framework API foundations;
- staff authentication and rotating sessions;
- server-selected gym context;
- explicit Django tenant scoping and PostgreSQL forced RLS;
- permission and entitlement foundations;
- a Next.js browser BFF and application shell;
- initial tenant-scoped member, membership, payment, and dashboard paths;
- automated security and contract checks.

The target product additionally includes administration, attendance and
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
- Next.js 16.3.3 Active LTS
- PostgreSQL 18.6
- TypeScript 7.0.2

Use the exact versions in `.python-version`, `.nvmrc`, dependency locks, CI,
and container definitions.

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

## API contract

The implemented Phase 1A foundation contract is committed at
`contracts/openapi.v1.json`. Its generated TypeScript definitions are committed
at `web/src/types/api.generated.ts`. Run `make openapi-check` with the approved
Python and Node versions to regenerate both artifacts and fail on contract drift.
Later-phase business routes remain excluded until their contracts and exit
criteria are approved.

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
