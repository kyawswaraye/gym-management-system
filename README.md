# Gym Management Platform

Phase 1A security foundation for a multi-tenant gym management SaaS.

The current implementation scope is intentionally limited to:

- Django REST Framework API foundations;
- staff authentication and rotating sessions;
- server-selected gym context;
- explicit Django tenant scoping and PostgreSQL forced RLS;
- permission and entitlement foundations;
- a Next.js browser BFF and application shell;
- automated security and contract checks.

Members, check-ins, finance, PT, reports, production infrastructure, and the
member mobile application are not part of Phase 1A.

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

## Documentation

The architecture contracts are under [`docs/`](docs/). Accepted material
decisions are recorded under [`docs/adr/`](docs/adr/).
