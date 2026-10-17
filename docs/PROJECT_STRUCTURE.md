# Project Structure

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`DATABASE.md`](./DATABASE.md), [`API.md`](./API.md), [`SECURITY.md`](./SECURITY.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

## 1. Repository strategy

A single repository coordinates API contracts, local development, documentation, and CI while keeping deployable applications independent.

```text
gym-management-platform/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── deploy-staging.yml
│       └── deploy-production.yml
├── docs/
│   ├── README.md                   # documentation index and authority map
│   ├── ARCHITECTURE.md
│   ├── DATABASE.md
│   ├── API.md
│   ├── SECURITY.md
│   ├── PROJECT_STRUCTURE.md
│   ├── DEVELOPMENT_PLAN.md
│   ├── HOW_IT_WORKS.md
│   ├── *.pdf                       # superseded reference material only
│   └── adr/
│       ├── 0001-single-vps-production-topology.md
│       ├── 0002-nextjs-bff-browser-authentication.md
│       └── 0003-reception-cash-session-authority.md
├── backend/
├── web/
├── mobile/
├── infra/
├── scripts/
├── docker-compose.yml
├── .env.example
├── .editorconfig
├── .gitignore
├── Makefile
└── README.md
```

Phase 0 is documentation-only. At the end of Phase 0, no repository scaffold, backend, web application, mobile application, infrastructure code, workflow, container, or application configuration is assumed to exist beyond the files under `/docs`. Every other path shown in this document is a target structure to be created only in its approved roadmap phase.

The seven Markdown contracts plus `docs/adr/` are authoritative. The PDFs under `docs/` are pre-Phase-0 reference input retained for history; they are indexed in [`README.md`](./README.md) and are superseded by the Markdown contracts wherever they disagree. No implementation decision may cite them as authority.

## 2. Backend

```text
backend/
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── local.py
│   │   ├── test.py
│   │   └── production.py
│   ├── api_router.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── common/
│   │   ├── api/
│   │   │   ├── exceptions.py
│   │   │   ├── pagination.py
│   │   │   └── responses.py
│   │   ├── db/
│   │   │   ├── models.py
│   │   │   └── tenant_context.py
│   │   ├── money.py
│   │   ├── time.py
│   │   └── validators.py
│   ├── tenants/
│   ├── users/
│   ├── members/
│   ├── memberships/
│   ├── checkins/
│   ├── payments/
│   ├── expenses/
│   ├── salaries/
│   ├── trainers/
│   ├── classes/
│   ├── inventory/
│   ├── sales/
│   ├── commissions/
│   ├── cashbook/
│   ├── receipts/
│   ├── reports/
│   ├── announcements/
│   ├── notifications/
│   ├── files/
│   └── audit/
├── requirements/
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
├── tests/
│   ├── factories/
│   ├── security/
│   ├── integration/
│   └── conftest.py
├── manage.py
├── pyproject.toml
├── Dockerfile
└── .env.example
```

### Domain app convention

The backend remains domain-based. `tenants`, `users`, `members`, `memberships`, `checkins`, `payments`, `expenses`, `salaries`, `trainers`, `classes`, `inventory`, `sales`, `commissions`, `cashbook`, `receipts`, `reports`, `announcements`, `notifications`, `files`, and `audit` are the approved domain apps. `common` is shared infrastructure for cross-cutting API, database-context, money, time, and validation primitives; it is not a catch-all business domain.

Each substantial app follows a predictable shape:

```text
apps/members/
├── api/
│   ├── serializers.py
│   ├── permissions.py
│   ├── views.py
│   └── urls.py
├── migrations/
├── admin.py
├── apps.py
├── models.py
├── selectors.py
├── services.py
├── validators.py
└── tests/
    ├── test_models.py
    ├── test_services.py
    ├── test_api.py
    ├── test_permissions.py
    └── test_tenant_isolation.py
```

Small apps need not contain empty layers. The rules are:

- `models.py`: persistence shape and local invariants;
- `serializers.py`: explicit HTTP input/output validation;
- `views.py`: HTTP orchestration only;
- `permissions.py`: reusable action/object authorization;
- `services.py`: transactional commands and business calculations;
- `selectors.py`: optimized, tenant-scoped reads;
- tests live beside the domain, with cross-domain security suites under `/tests`.

Complex financial calculations do not live in signals. Signals are limited to non-critical decoupled hooks; explicit services make transaction boundaries visible.

## 3. Next.js web

The web workspace uses Next.js 16.4.1 with the App Router, TypeScript 7.0.2 in strict mode, and Tailwind CSS. React Server Components (RSC) are the default; Client Components are explicit boundaries used only where browser interaction or local transient state requires them.

```text
web/
├── public/
├── src/
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── login/
│   │   │   └── select-gym/
│   │   ├── (tenant)/
│   │   │   ├── layout.tsx
│   │   │   ├── dashboard/
│   │   │   ├── members/
│   │   │   ├── memberships/
│   │   │   ├── check-ins/
│   │   │   ├── payments/
│   │   │   ├── expenses/
│   │   │   ├── approvals/
│   │   │   ├── salaries/
│   │   │   ├── cash-book/
│   │   │   ├── inventory/
│   │   │   │   ├── products/
│   │   │   │   ├── stock/
│   │   │   │   └── sales/
│   │   │   ├── pt/
│   │   │   │   ├── trainers/
│   │   │   │   ├── schedule/
│   │   │   │   ├── packages/
│   │   │   │   ├── sales/
│   │   │   │   └── commissions/
│   │   │   ├── classes/
│   │   │   ├── announcements/
│   │   │   ├── reports/
│   │   │   ├── staff/
│   │   │   └── settings/
│   │   ├── super-admin/
│   │   │   ├── layout.tsx
│   │   │   ├── dashboard/
│   │   │   ├── gyms/
│   │   │   ├── subscriptions/
│   │   │   └── audit-logs/
│   │   ├── api/
│   │   │   └── auth/               # same-origin browser BFF routes
│   │   ├── error.tsx
│   │   ├── not-found.tsx
│   │   └── layout.tsx
│   ├── components/
│   │   ├── ui/
│   │   ├── layout/
│   │   └── feedback/
│   ├── features/
│   │   ├── auth/
│   │   ├── members/
│   │   ├── memberships/
│   │   ├── finance/
│   │   ├── approvals/
│   │   ├── salaries/
│   │   ├── checkins/
│   │   ├── trainers/
│   │   ├── classes/
│   │   ├── pt/
│   │   ├── inventory/
│   │   ├── sales/
│   │   ├── commissions/
│   │   ├── reports/
│   │   ├── announcements/
│   │   └── staff/
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   ├── errors.ts
│   │   │   └── server.ts
│   │   ├── auth/
│   │   ├── env.ts
│   │   └── formatting/
│   ├── services/
│   ├── types/
│   │   └── api.generated.ts
│   ├── hooks/
│   └── utils/
├── tests/
├── next.config.ts
├── tsconfig.json
├── package.json
├── Dockerfile
└── .env.example
```

### Frontend boundaries

- Route components compose feature components.
- React Server Components are the default for routes, layouts, and initial data access where session and caching semantics allow.
- Client Components are used for forms, interactive tables, dialogs, and browser APIs.
- Next.js is the browser BFF. Only server-side route handlers read the host-scoped `HttpOnly`, `SameSite=Lax` authentication cookies; `Secure` is mandatory outside isolated `http://localhost` development. The multi-gym pre-session ticket uses a separate cookie with the same protections and a maximum 5-minute lifetime. Browser JavaScript never receives JWT/refresh/ticket values and never stores them in `localStorage` or `sessionStorage`.
- The `(auth)/select-gym` page renders only safe membership choices. It submits `gym_membership_id` to the same-origin auth route; the route handler supplies the protected ticket to Django, stores normal credentials only after atomic ticket consumption succeeds, and clears the ticket cookie on success or terminal failure. Tenant layouts and tenant-keyed caches are unavailable until Django has issued selected-gym credentials.
- Every cookie-mutating auth route applies the shared CSRF token/header and strict Origin/Referer guard. The server API client permits only one controlled refresh and one original-request retry on the recognized access-expiry response. Django remains authoritative for ticket issue/hash/state/attempts, membership bootstrap validation, token issuance, selected gym, refresh rotation/reuse revocation, logout, and authorization.
- Payment, expense, retail-sale, PT-sale, and salary forms create drafts and submit them; the manager approval queue is a separate feature boundary and never performs client-side posting, stock, cost, session-balance, or profit calculations.
- Salary period/line UI remains separate from PT commission UI. No fixed staff or PT amount is inferred in the frontend.
- PT sales, trainer workflows, and commission accrual/payout UI remain separate feature boundaries even when composed under the tenant `pt` route.
- Class/PT calendars, package/session balances, product inventory, retail sales, and announcement publishing each remain explicit feature boundaries with tenant-scoped API types.
- API functions are centralized; components do not construct ad hoc URLs.
- Generated or checked API types prevent contract drift.
- Formatting utilities receive currency and timezone explicitly.
- No Redux/global store is added without a demonstrated cross-route state requirement.
- Server data caching is tenant-aware and never keyed only by route or resource ID.

## 4. React Native mobile

```text
mobile/
├── src/
│   ├── app/
│   │   ├── navigation/
│   │   └── providers/
│   ├── features/
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── profile/
│   │   ├── membership/
│   │   ├── payments/
│   │   ├── checkins/
│   │   ├── schedule/
│   │   ├── service-packages/
│   │   ├── qr-scanner/
│   │   └── notifications/
│   ├── components/
│   │   └── ui/
│   ├── services/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── secure-storage/
│   │   └── notifications/
│   ├── types/
│   ├── utils/
│   └── config/
├── ios/
├── android/
├── tests/
├── app.json
├── tsconfig.json
├── package.json
└── .env.example
```

The framework choice between Expo and bare React Native is deferred to Phase 7 based on push notifications, camera-based onsite QR scanning, biometric/device integration, build, and team constraints. This is an architectural decision requiring approval; API contracts remain framework-neutral. Mobile has no tenant selector in ordinary member requests: the authenticated member session supplies the gym and member, and schedule, package, announcement, notification, and scan responses remain both gym- and self-scoped.

## 5. Infrastructure

```text
infra/
├── environments/
│   ├── development/
│   ├── staging/
│   └── production/
├── compose/
│   ├── docker-compose.prod.yml
│   ├── docker-compose.staging.yml
│   └── migration.yml
├── caddy/
│   └── Caddyfile
├── postgres/
│   ├── init/
│   ├── backup/
│   └── restore/
├── minio/
│   └── policies/
├── monitoring/
│   ├── healthchecks/
│   └── logrotate/
├── backup/
│   ├── database.sh
│   ├── wal-archive.sh
│   ├── objects.sh
│   └── verify.sh
├── scripts/
│   ├── deploy.sh
│   ├── rollback.sh
│   └── rebuild-vps.sh
├── cloudflare/
└── README.md
```

The Phase 8 baseline is one production VPS managed through reviewed Docker Compose and host/bootstrap scripts. Environment directories contain references and safe templates, never plaintext secrets. `staging/`, `docker-compose.staging.yml`, and `deploy-staging.yml` target local or ephemeral temporary infrastructure with separate credentials and volumes; they do not create a permanent staging stack on the production VPS. GitHub Container Registry stores immutable digest-pinned web/API images. GitHub Actions under `/.github/workflows` runs quality and security gates, publishes approved images to GHCR, then invokes a protected deployment that takes a backup checkpoint, runs the one-shot migration service with the migration role, performs health checks, and restarts services in controlled order. Database and MinIO volumes remain local to the VPS while encrypted copies and WAL archives leave the host through `infra/backup/`.

## 6. Tests

### Backend

```text
backend/tests/
├── security/
│   ├── test_rls_inventory.py
│   ├── test_connection_context.py
│   ├── test_cross_tenant_api.py
│   ├── test_jwt_security.py
│   ├── test_browser_bff_auth.py
│   ├── test_gym_selection_ticket.py
│   ├── test_mass_assignment.py
│   └── test_file_isolation.py
├── integration/
│   ├── test_membership_payment_flow.py
│   ├── test_cash_reconciliation_flow.py
│   ├── test_reception_cash_scope.py
│   ├── test_finance_approval_flow.py
│   ├── test_salary_flow.py
│   └── test_commission_flow.py
└── factories/
```

Domain unit/API tests remain in each app. Security tests use real PostgreSQL.

### Frontend and mobile

- feature/component behavior tests;
- accessibility tests for critical forms and navigation;
- API client contract/error tests;
- a small number of end-to-end critical paths;
- no snapshots used as a substitute for behavior assertions.

## 7. Configuration and dependency rules

- `.env.example` documents names and safe placeholders only.
- Runtime configuration is validated at startup.
- Secrets come from local ignored development files or production Docker secrets/root-readable encrypted host files; they never enter Compose files, images, logs, or source control.
- Dependencies are added only for a current requirement and reviewed for maintenance/security.
- Phase 1A pins Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3.
- Compatibility is verified by clean dependency installation, Django checks and migrations against PostgreSQL 18.6, the forced-RLS/transaction suite, DRF schema tests, and Next.js strict type/test/build checks on Node 24.20.0. Pins are changed only through an approved compatibility update.
- Web ESLint keeps ESLint core, React Hooks, Babel/TypeScript parsing, `no-debugger`, `no-var`, and `prefer-const`. Framework-specific Next.js lint rules are temporarily not loaded; the reason and the remaining type, build, and E2E gates are recorded in [`SECURITY.md`](./SECURITY.md).
- Formatting, lint, type check, tests, migration checks, and API schema checks run in CI.

## 8. Naming rules

- Python modules and JSON fields: `snake_case`.
- TypeScript variables/functions: `camelCase`; components/types: `PascalCase`.
- URLs use plural kebab-case resources.
- Permission codes use `module.action`.
- Database indexes and constraints have stable descriptive names.
- Use `Gym` in product/domain language and `gym_id` in ownership columns; avoid mixing `tenant_id` and `gym_id`.

## 9. Approved roadmap alignment

This target structure is introduced incrementally:

1. **Phase 1A — Foundation:** repository/tooling, Django and database foundation, authentication, tenant isolation, permissions, CI, and the minimal Next.js web shells. It includes no members, check-in, finance, PT, reports, or mobile implementation.
2. **Phase 1B — Administration:** Super Admin, gym management, staff, roles and permissions, and gym settings.
3. **Phase 2 — Membership:** members, plans, membership lifecycle, and related web features.
4. **Phase 3 — Check-in:** manual check-in/check-out, eligibility, rotating onsite QR challenges, and unattended authenticated mobile scanning.
5. **Phase 4 — Finance and inventory:** payment/expense/salary/retail drafts, product and stock ledgers, manager approval queue, receipts, cash book, and reconciliation.
6. **Phase 5 — PT and classes:** trainers, schedules, service packages, remaining-session ledger, class/PT bookings, attendance, submitted PT sales, manager approval, commission accruals, payouts, and their explicit web feature boundaries.
7. **Phase 6 — Reporting:** operational dashboards, exact owner attendance/expiry/pending-payment/revenue-source/product metrics, reports, and bounded exports.
8. **Phase 7 — Mobile:** the separate React Native member workspace, unattended onsite QR scan, PT/class schedule and remaining sessions, reminders, event/promotion announcements, and push delivery.
9. **Phase 8 — Hardening and production deployment:** security and quality hardening, single-VPS Docker Compose infrastructure, GHCR deployment workflows, bounded observability, encrypted offsite backups, restore validation, and production rollout.

Phase labels outside this sequence are stale and must not be used to justify creating target paths early.

## 10. Architectural decision records

Material changes are recorded under:

```text
docs/adr/
├── 0001-single-vps-production-topology.md
├── 0002-nextjs-bff-browser-authentication.md
└── 0003-reception-cash-session-authority.md
```

Each ADR states context, decision, alternatives, security impact, migration impact, and status. ADR 0002 fixes the browser BFF lifecycle; ADR 0003 fixes Reception's own-opened cash-session object scope while preserving denial of adjustment, approval, and posting. MFA is TOTP and is mandatory for platform administrators and Owners, with recent step-up, encrypted secrets, and replay protection; broader role-based MFA may be decided later. Email uses Django's configurable email backend, with non-delivery backends in test and end-to-end runs. Membership-expiry scheduling and delivery commands are implemented and set `X-Idempotency-Key`. Production must configure an approved transactional provider that honors that header before invitation, password-recovery, or expiry delivery is production-ready; vendor selection and that deployment configuration remain deferred. Jurisdiction remains a production-gate decision, device design remains Phase 3, and Expo versus bare React Native remains Phase 7.
