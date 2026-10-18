# Development Plan

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md).

## 1. Authority and delivery policy

This is the authoritative implementation roadmap. It must remain consistent with the complete Phase 0 document set:

1. [`ARCHITECTURE.md`](./ARCHITECTURE.md) — system boundaries, tenancy, deployment, reliability, observability, and scale;
2. [`DATABASE.md`](./DATABASE.md) — schema, ownership, constraints, RLS, transactions, and migrations;
3. [`API.md`](./API.md) — versioned HTTP contracts and phase delivery map;
4. [`SECURITY.md`](./SECURITY.md) — threat model, permission matrices, isolation, and release gates;
5. [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md) — repository, application, test, and infrastructure layout;
6. [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md) — sequence, dependencies, checks, and exit criteria;
7. [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md) — Burmese user journeys and operational explanations derived from the contracts above;
8. accepted records under [`adr/`](./adr/) — approved material decisions that refine the seven documents: [single-VPS production topology](./adr/0001-single-vps-production-topology.md), [Next.js BFF browser authentication](./adr/0002-nextjs-bff-browser-authentication.md), and [Reception cash-session authority](./adr/0003-reception-cash-session-authority.md).

Work proceeds one approved phase at a time. Phase 1 has two ordered subphases, 1A and 1B; both must pass their own gates. A phase or subphase may not advance while any required migration, test, format check, lint check, type check, schema check, security scan, build, smoke test, or documented manual check is failing. Flaky, skipped, muted, or waived checks are errors unless the waiver, owner, risk, and expiry are explicitly approved.

### Current phase status

Phase 0, Phase 1A, and Phase 1B are complete. The Phase 1B gate closed on 2026-10-17. Phase 2 and Phase 3 implementation is complete. Phase 4 Increments 1–6 are published (finance foundation, cash book, payments/receipts/daily sales, expense categories/approval, salary periods/lines, and product catalog/stock movements). Expense void, salary void, retail, and payment/receipt void/refund remain unpublished. The Phase 4 gate is not closed. Increment evidence lives in the repository README. Production expiry delivery remains gated on an approved transactional email provider that honors `X-Idempotency-Key`.

The 2026-10-17 closure is local CI-equivalent gate evidence. CI still enforces these check classes on push and pull request.

- Backend: `ruff` check and format, mypy on 73 files, migration drift, and OpenAPI validate plus diff passed. Pytest passed 102 non-PostgreSQL tests and 105 PostgreSQL tests (207).
- Web: generated types were stable; format, lint, and typecheck passed; Vitest passed 31 files and 162 tests; the Next.js build passed.
- Dependency audits passed. Playwright `platform-provision` and `tenant-settings` passed (2).
- Containerized gitleaks scanned 16 commits, about 3.63 MB, and found no leaks.

The phase sections below keep each phase's approved scope and exit criteria. Historical pins in those sections, including Next.js 16.3.3, stay as the baseline recorded when the phase was specified.

Every implementation increment must:

1. inspect the repository, authoritative documents, and migration history;
2. confirm dependencies, scope, security boundaries, and acceptance criteria;
3. implement the smallest complete vertical slice;
4. add unit, database, API, tenant/RLS, permission, financial, and concurrency tests as applicable;
5. run all phase checks against real PostgreSQL where database behavior matters;
6. review tenant isolation, permissions, audit, financial integrity, privacy, and operational impact;
7. update migrations, OpenAPI/schema, generated types, runbooks, and documentation in the same change;
8. summarize evidence, unresolved issues, and rollback considerations;
9. obtain approval before starting the next phase.

Any major architecture change requires approval before implementation. The proposal must state:

```text
Problem
Current approved approach
Proposed change and alternatives
Reason and evidence
Security, tenant, data, API, migration, cost, and operational impact
Rollback/compatibility plan
```

Material changes are recorded in an ADR. Safe defaults may resolve minor implementation details, but security, tenant, financial, regulatory, public-contract, migration, deployment-provider, or cross-application changes are never assumed.

Redis and Celery are explicitly deferred. They are not baseline dependencies in Phases 0–8 and may be introduced only after measured need, an approved architecture change, tenant-safe failure semantics, and operational ownership are documented.

## 2. Phase 0 — Approved design baseline

Phase 0 is documentation and approval only. It creates no application features, infrastructure, database objects, migrations, or runtime security controls.

### Dependencies

- Confirmed product scope and stakeholders for product, operations, finance, privacy, and security.
- Access to the seven Phase 0 documents and every accepted ADR listed above.
- Explicit tracking of unresolved decisions and assumptions.

### Scope and 15 required design deliverables

The seven documents together with accepted ADRs must define and cross-reference all of the following:

1. Product scope, actors, web/API/mobile boundaries, and out-of-scope behavior.
2. Repository strategy, project structure, domain boundaries, configuration, and dependency rules.
3. Multi-tenant ownership classification for every global, tenant-root, and tenant-owned domain.
4. Authoritative PostgreSQL entities, relationships, types, constraints, indexes, deletion, retention, and immutability rules.
5. Django scoping plus transaction-local tenant context, forced RLS, complete `USING`/`WITH CHECK` policies, pooled-connection safety, and separate runtime/migration roles.
6. Custom `User`, staff/member authentication, strict JWT validation, rotating refresh sessions, revocation, browser CSRF/cookie boundaries, mobile secure storage, and MFA requirements.
7. Platform and tenant RBAC, stable permission codes, role templates, custom-role anti-escalation, object restrictions, feature entitlements, and authorization-version invalidation.
8. Versioned `/api/v1/` resources, explicit serializers, safe envelopes/errors, pagination/filter limits, idempotency, concurrency, and generated-client compatibility.
9. Member, membership-plan, membership lifecycle, calendar expiry, member dashboard, and private-file behavior.
10. Check-in eligibility and checkout plus QR, NFC, and biometric/device abstraction boundaries that store no biometric templates in this system.
11. Payments, receipts, expenses, salary periods/lines, cash sessions/transactions/reconciliation, PT sales/commissions, pre-post manager approval, exact decimal rules, immutable posting, reversals, sequence allocation, and atomic workflows.
12. Web and React Native architecture, tenant-aware client data handling, accessibility, QR safety, notifications, and Expo-versus-bare decision criteria.
13. Threat model, audit/privacy/file controls, test strategy, CI/CD gates, dependency/container/secret scanning, and release security evidence.
14. Production topology, environment separation, secrets, observability, incident response, backup/PITR, restore drills, RPO/RTO approval, rollout, and rollback.
15. Performance budgets, connection capacity, bounded queries, load strategy, scaling path, deferred Redis/Celery criteria, open decisions, and deployment-cost analysis structure.

### Migrations and data

- None. Schema examples are contracts, not executed migrations.
- The model and RLS inventory in `DATABASE.md` must account for every planned tenant table.
- Seed catalogs, provisioning data, test fixtures, and migration-role behavior must be specified before implementation.

### Required reviews and checks

- Cross-document phase names, model names, endpoint delivery annotations, permission codes, and ownership rules agree.
- Every accepted ADR is referenced by the affected documents, and Phase 0 reviews prove that its decision, risks, budget, and migration path remain consistent with the core contracts.
- Every tenant resource has documented Django scope, RLS policy, permission/object rules, audit behavior, and two-gym tests.
- Financial formulas, immutable states, reversal paths, locks, idempotency, and concurrency invariants agree across database, API, architecture, and security documents.
- Backup, restore, observability, load, deployment, rollback, and scale responsibilities are documented.
- All unresolved decisions have an owner, due phase, and explicit blocking status.

### Exit criteria

- All 15 design deliverables are covered, and the seven documents plus every accepted ADR are internally consistent.
- Product, finance, privacy, security, and operational assumptions are approved or explicitly marked as blockers.
- No critical tenancy, permission, financial, API, migration, backup, or deployment question is implicit.
- Phase 1A scope is approved.
- Git use is approved, but Phase 0 documentation closure does not initialize Git or create repository metadata; that separate action occurs only after the documentation work is complete.

## 3. Phase 1 — Foundation

### Phase 1A — Security foundation

#### Dependencies

- Phase 0 exit criteria and Phase 1A approval.
- Approved pins verified for compatibility: Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.3.3 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2.
- Approved local PostgreSQL strategy and the Next.js BFF browser authentication boundary in ADR 0002.

#### Scope

- Initialize the repository under the separately approved post-documentation Git step if Git is absent; add README, ignore rules, safe environment examples, and repeatable development commands.
- Create the Django/DRF project, settings split, domain skeleton, `/api/v1/`, response envelopes, exception mapping, request IDs, pagination, health checks, and environment validation.
- Create the custom `User` before the first migration.
- Implement `Gym`, `GymMembership`, `Role`, `Permission`, role/permission joins, protected role templates, global `GymSelectionTicket`, `RefreshSession`, foundational `AuditLog`, and only the support entities required by their approved contracts.
- Implement staff authentication with stable configurable non-secret identifiers: issuer `gym-platform-api`, staff/web audience `gym-platform`, and member audience `gym-platform-member`. Implement login, a 10-minute access-token target, refresh rotation/replay-family revocation, logout/logout-all, current user, gym membership list, and selected-gym token exchange without adding claims beyond the API contract.
- After successful password/MFA checks, one active membership proceeds directly. Multiple active memberships use the ADR 0002 bridge: Django issues a high-entropy opaque ticket valid for exactly 5 minutes, stores only its cryptographic hash with user/immutable bootstrap list/state/bounded attempts, and grants no gym authority or tenant context until atomic selection consumes it. The trusted BFF keeps plaintext only in a host-scoped `HttpOnly`, `SameSite=Lax` cookie (`Secure` outside isolated localhost), exposes only safe choices to browser JavaScript, and clears the ticket on success or terminal failure.
- Implement stable permission evaluation, endpoint/action permission classes, object-check hooks, entitlement boundary, and authorization-version invalidation.
- Establish one transaction per tenant unit of work; set parameterized transaction-local gym/user/request context before tenant queries.
- Enable and force RLS with complete policies. Use separate non-owner runtime and migration roles; runtime has no superuser or `BYPASSRLS`.
- Enforce explicit Django gym scoping, server-owned `gym_id`, tenant-scoped relationships, and same-gym composite constraints.
- Add CI/CD foundation for backend and web checks; deployment is not yet production release.
- Create the Next.js App Router shell with strict TypeScript, Tailwind, auth, tenant, and super-admin layouts, typed API client boundary, safe error handling, and no business pages. Next.js is the browser BFF: browser JavaScript never receives JWT/refresh values; server routes use host-scoped `HttpOnly`, `SameSite=Lax` cookies that are `Secure` outside isolated localhost; browser storage is token-free; every cookie mutation requires CSRF token/header plus strict Origin/Referer validation; only one controlled refresh/retry is allowed; and logout combines Django revocation with cookie clearing.
- Provide Docker/local PostgreSQL and isolated test configuration. Do not add Redis or Celery.
- Do not implement members, check-in, finance, PT, reports, or mobile in Phase 1A.

#### Migrations and data

- Initial custom-user migration must precede dependent models.
- Reversible schema migrations for foundation tables, including the global hash-only `GymSelectionTicket` with exact 5-minute expiry, single-use/terminal-state/attempt constraints, 30-day terminal retention support, indexes, UUID/timestamp conventions, composite constraints, RLS functions/policies, and role grants.
- Data migrations seed immutable permission catalog and versioned protected role templates.
- Migration and runtime identities are verified independently; migrations must not silently run as the runtime owner.

#### Required tests

- Authentication: strict JWT claims/signature/algorithm/key and exact issuer/audiences; login states; direct one-membership login; multi-membership ticket issue only after password/MFA success; exact 5-minute expiry; high entropy and hash-only persistence; immutable bootstrap-list/user binding; current-membership revalidation; no pre-selection JWT/session/tenant context; single-use atomic consumption; concurrent selection/replay; bounded attempts and terminal revocation; 30-day cleanup eligibility; 10-minute access target; refresh rotation/replay family revocation; authoritative logout; BFF non-exposure of tokens/tickets; safe membership choices; host-scoped cookie attributes and clearing; no browser-storage persistence; CSRF plus Origin/Referer checks on every cookie mutation; one-refresh/one-retry limit; dedicated ticket rate limits; and plaintext-free audit/log evidence.
- Tenant/RLS: two-gym CRUD and relationship rejection; missing/malformed context; `USING` and `WITH CHECK`; server-owned gym; commit, rollback, exception, nested transaction, pooled reuse, and concurrent different-gym requests.
- Permission: default role allow/deny baselines, stale `authz_version`, protected templates, custom-role anti-escalation primitives, and platform-versus-tenant separation.
- Financial: no financial feature is implemented; tests prove foundation APIs cannot mass-assign financial/status/calculated fields and super admin acquires no tenant financial capability.
- Concurrency: refresh replay, concurrent role changes/version increments, duplicate seed safety, and tenant-context isolation under concurrent requests.
- Frontend: strict trusted-BFF-versus-browser API types, auth/error behavior, safe multi-gym choice rendering, ticket-cookie clearing, tenant-aware cache keys only after selection, layout guards, accessibility smoke tests, and no token/ticket persistence in browser storage.

#### Checks

- Backend format, lint, type, migration drift/conflict, full PostgreSQL 18.6 tests, RLS inventory, API schema compatibility, dependency and secret scans on Python 3.14.7, Django 5.2.17, and DRF 3.18.0.
- Web format/lint, TypeScript 7.0.2 strict type check, tests, Next.js 16.3.3 build on Node.js 24.20.0, dependency scan, and environment validation.
- Clean setup from documented commands and safe failure with missing secrets or privileged database configuration.

#### Exit criteria

- Authentication and both direct/single-membership and ticketed/multi-membership gym selection work end to end through the API and web shell, with no tenant context before selection.
- Explicit Django scope and forced PostgreSQL RLS independently fail closed.
- Runtime role and pooled-connection isolation are proven.
- All required checks pass with no unapplied or unexpected migration.
- No Phase 1B provisioning or tenant administration feature is represented as complete.
- Phase 1A evidence and open issues are approved.

### Phase 1B — Required Super Admin provisioning and gym prerequisites

#### Dependencies

- Phase 1A exit criteria.
- Approved platform permission/MFA policy, provisioning fields, subscription/entitlement defaults, invitation boundary, and owner-protection rules.

#### Scope

- Implement separate Super Admin authentication/authorization surface, MFA-ready protected layout, dashboard, gym list/search/detail, lifecycle/subscription management, and platform audit views.
- Implement the required idempotent, transactional gym-and-owner provisioning command: create gym; safely create/link owner identity; create owner `GymMembership`; copy protected roles/permissions; assign OWNER; seed feature entitlements, the protected `RENT`, `UTILITIES`, `INTERNET`, `STAFF_SALARY`, `PT_COMMISSION`, `CLEANING_SUPPLIES`, `MAINTENANCE`, `MARKETING`, `WATER_DRINKS`, and `OTHER` expense categories, tenant sequences, approval policy, and required defaults; append audit evidence.
- Implement the protected co-Owner invitation, listing, login, and revocation workflow. A gym may have multiple equal-authority Owners, but owner lifecycle operations must retain at least one active Owner and must not represent shares or profit-distribution percentages.
- Implement activation/suspension behavior without granting Super Admin ordinary tenant data or financial mutation access.
- Implement gym profile/settings, validated timezone/currency display plumbing, staff invitation/acceptance, `StaffProfile`, staff lifecycle, custom roles, permission replacement, membership-role assignment, delegation checks, and authorization-version increments.
- Deliver prerequisite Super Admin, staff, role, and settings web flows.
- Keep membership plans and member business features in Phase 2.

#### Migrations and data

- Add `StaffProfile`, invitation/supporting security records, tenant settings/entitlements, provisioning idempotency, platform audit records, and required composite constraints/RLS policies.
- Add idempotent seed/version migrations for permissions, protected templates, the canonical protected expense-category catalog, feature defaults, approval policy, and tenant sequences.
- Existing-gym backfills must be resumable, auditable, tenant-batched, and verified; no partial tenant is exposed.

#### Required tests

- Tenant/RLS: staff, roles, settings, files, and joins cannot cross gyms; provisioning produces complete same-gym relationships; suspended gyms fail closed.
- Permission: platform-only routes; Super Admin has no implicit `tenant.*`; owner-reserved controls; non-owner anti-escalation; protected templates; delegable intersection; stale-token invalidation; custom actions call object checks.
- Financial: provisioning seeds finance prerequisites without posting economic records; currency/timezone defaults are authoritative; no platform path mutates tenant finance.
- Concurrency: duplicate provisioning/idempotency keys yield one gym and one initial Owner; duplicate co-Owner invitations/link attempts are idempotent; concurrent Owner revoke/suspend/role-removal cannot leave an active gym ownerless; concurrent role replacement/version increments are safe; invitation acceptance is single-use; partial failures roll back all provisioning rows.
- Audit/privacy: every platform, lifecycle, support, role, and settings action records redacted evidence; account-existence and invitation secrets do not leak.

#### Checks

- Phase 1A checks plus provisioning rollback inspection, seed idempotency, migration/backfill verification, OpenAPI/generated-type compatibility, Super Admin and tenant-settings build/E2E smoke paths.

#### Exit criteria

- An authorized Super Admin can provision exactly one complete gym with its required initial Owner atomically and safely retry the request.
- Multiple Owners can log in independently with equal Owner authority. An active Owner can add another Owner through the protected workflow, while no request or race can remove the last active Owner.
- Owners can configure gym settings, invite staff, and manage allowed roles/permissions.
- Suspended gyms and stale permissions fail closed; platform and tenant authority remain separated.
- All checks pass and Phase 2 prerequisites are approved.

**Gate status:** Closed on 2026-10-17 under the local CI-equivalent evidence in Current phase status. Phase 2 prerequisites are approved.

## 4. Phase 2 — Members and memberships

### Dependencies

- Phase 1B exit criteria. Satisfied by the 2026-10-17 gate closure.
- Approved member numbering, membership overlap/cancellation policy, calendar-expiry rules, profile-file limits, and member-dashboard contract. Satisfied by the policies already specified in the canonical contracts. The first implementation increment publishes the MembershipPlan list, create, detail, update, and archive API. Member list, create, detail, update, and archive are now published. Membership list, create, detail, note update, cancel, suspend, resume, and expiry buckets are now published. The member summary is now published. Nested member payment history is now published. Membership-expiry scheduling and delivery commands are implemented. Nested check-in history, notification HTTP routes, and general payment and receipt endpoints remain unpublished. Phase 2 is not complete. Production expiry delivery remains gated on an idempotency-capable transactional email provider.

### Scope

- Implement `Member` CRUD/archive, search, profile, private files, summary, histories, and the read-only `is_pt_client` badge/filter derived from an active `TrainerAssignment`.
- Implement `MembershipPlan` CRUD/archive and immutable plan snapshots on sale.
- Implement `Membership` create, suspend/resume/cancel, exact amount calculation, overlap prevention, and calendar-aware expiry.
- Implement expiry buckets: expired, today, within 3, 7, and 30 days; expiry processing remains synchronous/bounded or platform-scheduled without Celery.
- Implement membership-expiry email at the configured warning days. Create and deliver durable, deduplicated notifications only when the feature is enabled and the member has a current nonblank email; skip members without email and never fall back to SMS/phone. Use bounded scheduled Django commands and Django's configured email backend, without Celery. Tests and end-to-end runs use non-delivery backends. Production must configure an approved transactional provider before this delivery is production-ready. Vendor selection and that deployment configuration remain deferred.
- Implement tenant staff web pages and the API-backed member dashboard/summary for profile, current membership, days remaining, PT-client badge, payment summary, and check-in summary. Before Phase 5 assignment data exists the badge is false; React Native remains Phase 7.

### Migrations and data

- Add `Member`, `MembershipPlan`, `Membership`, member-owned `FileAsset` usage, the limited membership-expiry `Notification` queue, composite foreign keys, exclusions, tenant-leading indexes, forced RLS, notification deduplication, and immutability/snapshot constraints.
- Seed no fake production members. Backfills or imports require approved mapping, validation, tenant batching, dry run, and reconciliation.

### Required tests

- Tenant/RLS: two-gym CRUD/list/filter/search/relationship/file matrix; overlapping human identifiers; cross-tenant path/body/filter/file IDs return safe failures; the derived PT badge cannot observe another gym's assignments.
- Permission: every member/membership action and discount/cancellation condition; trainer and member-self access are not prematurely granted.
- Financial: exact `Decimal` list/discount/final amounts and currency; clients cannot set totals/payment state; no payment is posted in this phase.
- Concurrency: simultaneous membership create/renew/cancel cannot overlap or corrupt snapshots; archive/update conflicts are deterministic.
- Date/UI: month/year/leap/calendar edges in gym timezone, expiry buckets, dashboard accuracy, bounded queries, accessibility, and API contract behavior.
- Expiry email: configured warning-day boundaries, missing/removed/changed email, disabled setting, inactive/cancelled/renewed membership, cross-gym isolation, safe template data, provider failure/retry limits, and duplicate scheduler/delivery runs producing at most one email per deterministic key.

### Checks

- Standard CI gates, migration/RLS inventory, constraint tests, API schema/generated types, query-count and query-plan review for member search/dashboard, file-security tests, web build and E2E member lifecycle.

### Exit criteria

- Staff can manage members, plans, memberships, expiry, and the member dashboard within one gym only; eligible members with email receive one deduplicated expiry warning per configured day, while members without email are skipped.
- Historical plan/price snapshots and calendar expiry remain correct.
- All permission, tenant/RLS, financial-boundary, concurrency, file, and UI checks pass.

## 5. Phase 3 — Check-in and device abstraction

### Dependencies

- Phase 2 exit criteria.
- Approved eligibility, duplicate window, backdating/correction, checkout, onsite display/device identity, rotating challenge lifetime/use bounds, and attended/unattended QR policies.

### Scope

- Implement `CheckIn`, manual check-in/check-out, eligibility snapshots, duplicate handling, corrections, search/history, and front-desk web flow.
- Implement both front-desk QR and unattended mobile scan: an authenticated same-gym member or authorized trainer scans a short-lived rotating onsite gym challenge, and the server validates principal scope, challenge gym/expiry/replay, eligibility, and duplicate rules atomically without personal data or permanent secrets in the payload.
- Define scoped device/service credential and ingestion interfaces for NFC and biometric devices.
- Store only check-in evidence and bounded source metadata. Do not store biometric templates, raw biometric captures, or vendor secrets.
- Vendor-specific NFC/biometric integration is optional behind the approved abstraction and cannot weaken manual/QR controls.

### Migrations and data

- Add `CheckIn`, `CheckInChallenge`, approved display-device metadata/credential records, challenge hashes/state/use bounds, idempotency indexes, same-gym composite FKs, tenant-leading indexes, and forced RLS.
- Device enrollment/import requires tenant ownership, key rotation, revocation, and audit records.

### Required tests

- Tenant/RLS: cross-gym member/membership/device/check-in access, list/filter/aggregate isolation, missing context, and pooled concurrent requests.
- Permission: create/view/correct/backdate/checkout actions, trainer object scope where enabled, and device credential scope.
- Financial: check-in never mutates payment state or accepts financial totals; eligibility uses authoritative membership state.
- Concurrency: simultaneous check-ins, repeated QR/NFC/device events, idempotency, duplicate-window locking, and checkout races create one valid result.
- Security/device: expired/replayed/modified/photographed-old or cross-gym QR challenge, static-token rejection, principal substitution, concurrent scans, no PII/permanent-secret payload, revoked display device, malformed/oversized metadata, biometric-data rejection, rate limits, and audit.

### Checks

- Standard gates, real-PostgreSQL lock tests, RLS inventory, device/QR threat review, query/load baseline for peak check-in, web build, and critical E2E manual/QR flows.

### Exit criteria

- Manual and approved QR check-in paths are reliable, idempotent, tenant-safe, and auditable.
- NFC/biometric vendors can integrate only through the approved abstraction with no biometric template stored.
- Peak baseline, concurrency, permission, tenant/RLS, and security checks pass.

## 6. Phase 4 — Core finance, inventory, retail sales, approvals, salaries, receipts, expenses, and cash

### Dependencies

- Phase 2 exit criteria; Phase 3 is required only where finance screens link check-in history.
- Approved tax/receipt, rounding, refund/void, manager approval, salary-period, cash-register, reconciliation, retention, and financial permission policies.

### Scope

- Implement `Payment`, `Receipt`, `ExpenseCategory`, `Expense`, `SalaryPeriod`, `StaffSalaryLine`, `ProductCategory`, `Product`, `StockMovement`, `RetailSale`, `RetailSaleLine`, `CashSession`, `CashTransaction`, `TenantSequence`, and finance `IdempotencyRecord` workflows.
- Deliver membership/other non-PT payment and expense `DRAFT → PENDING_APPROVAL → POSTED/REJECTED` workflows, daily sales, atomic receipt allocation/rendering, void/refund/reversal, private expense evidence, salary periods with manually entered per-period lines, cash open/manual adjustment/automatic movement/reconciliation/close, and finance web flows.
- Deliver per-gym product catalog, SKU/barcode, purchase/opening/adjustment/return stock movements, low-stock view, multi-line retail sale, immutable unit-price/unit-cost snapshots, item quantity/revenue/gross-profit selectors, and retail web flows. Retail approval atomically posts payment/receipt/cash and stock deduction; concurrent sales cannot make stock negative.
- Reception and authorized finance staff may create/edit/submit Payment, Expense, and Salary drafts. Every record requires approval by a different Manager or Owner before posting; approval atomically creates all receipt, settlement, protected salary-expense, and cash effects. Rejection creates none of those effects. Reception may view/open/operate/reconcile/close only the same-gym CashSession whose `opened_by_membership_id` equals the current Reception `GymMembership`; it cannot manually adjust, approve, or post.
- Posted records are immutable; corrections are linked records with actor, reason, and time.
- Every financial command uses exact `Decimal`, authoritative server calculations, one transaction, permissions, same-gym validation, idempotency, locks, audit, and cash/receipt side effects.
- PT sales, trainer assignments, and PT commissions are explicitly excluded and reserved for Phase 5.

### Migrations and data

- Add all listed models, approval actor/state constraints, append-only/immutability enforcement, sequence allocator, product/stock/sale-line cost snapshots, exact revenue/cost/gross-profit checks, same-gym composite FKs, tenant-leading indexes, forced RLS, and restricted runtime grants.
- Seed the canonical protected expense categories and tenant sequences idempotently for existing gyms. Salary posting may use only `STAFF_SALARY`; commission payout may use only `PT_COMMISSION`.
- Financial imports/backfills require reconciliation totals, immutable source references, dry run, rollback, and finance approval.

### Required tests

- Tenant/RLS: two-gym matrix for all finance, product, stock, and retail-sale resources, relationships, files, lists, totals, exports, and custom actions; runtime cannot bypass RLS.
- Permission: create/update/submit/view/approve/void/refund/reconcile/adjust separation; Reception's own-opened cash session allows view/operation/reconcile/close while another Reception session and every cross-gym session are denied; Reception cannot adjust, approve, or post; creator/submitter cannot self-approve; high-risk reasons, Super Admin denial, restricted salary visibility, and mass-assignment rejection.
- Financial: exact decimal/rounding/currency; immutable posts; valid approval/rejection/void/refund/reversal links; rejected submissions have no economic or stock effects; salary expense linkage; refundable limits; receipt snapshots; cash in/out; retail line revenue/cost/gross-profit snapshots; stock movement reconciliation; expected/actual/difference formulas calculated by the server under lock; immutable closed sessions; audit completeness.
- Concurrency: duplicate idempotency keys, simultaneous submit/approve/reject, payment/refund/void/expense/salary/retail approval, concurrent last-item sale and stock adjustment, deterministic product locking, atomic receipt sequences, one open cash session, transaction-versus-close races, reconciliation races, deadlock retry bounds, rollback with no partial side effects.
- Files/UI: private receipt evidence, safe generated receipts, timezone business-day boundaries, accessibility, and end-to-end payment-to-reconciliation workflows.

### Checks

- Standard gates plus database trigger/grant inspection, sequence stress test, financial reconciliation suite, production-like pooling, bounded query plans, dependency/file scans, schema/generated types, web build, and finance E2E smoke.

### Exit criteria

- Core non-PT finance, salaries, and retail inventory reconcile exactly from posted, non-void source records through sale lines, stock movements, receipts, expenses, and cash.
- No financial record can post without approval by a different Manager or Owner.
- Retry, race, rollback, refund, void, and close paths preserve one valid immutable history.
- No PT model, endpoint, calculation, or UI is represented as Phase 4 scope.
- All checks pass and finance/security evidence is approved.

## 7. Phase 5 — Personal training, classes, schedules, and remaining sessions

### Dependencies

- Phase 4 exit criteria.
- Approved trainer eligibility/object scope, assignment overlap, PT/class package validity and session-consumption rules, booking/cancellation/no-show/capacity policy, schedule timezone/recurrence rules, PT sale/payment approval, commission snapshot/accrual/payout/reversal, and separation-of-duties policies.

### Scope

- Implement `TrainerAssignment`, trainer views, assignment lifecycle, and assigned-member restrictions.
- Implement `ServicePackage`, `MemberServicePackage`, append-only `ServiceSessionLedger`, `PTBooking`, `ClassType`, `ClassSchedule`, `ClassOccurrence`, and `ClassBooking`; expose gym-local trainer/class calendars and authoritative granted/used/remaining session counts.
- Enforce trainer/member/location conflict prevention, class capacity, eligible package/balance checks, attendance/no-show consumption, cancellation policy, and reversal-based corrections under concurrency.
- Implement PT-sale draft/submission with approval by a different Manager or Owner and optional atomic payment/receipt/cash effects through Phase 4 services.
- Implement `PTCommission` accrual, exact trainer/gym split, partial/full payout, linked expense/cash effects, outstanding balance, reversal, and trainer-own views.
- Deliver staff and trainer web workflows; do not duplicate finance posting logic.

### Migrations and data

- Add assignment, service-package/balance-ledger, PT/class scheduling/booking, PT sale, and commission models with snapshots, capacity/balance constraints, exclusions, exact checks, same-gym composite FKs, tenant-leading indexes, forced RLS, immutable states, and restricted grants.
- Existing trainer data migration requires explicit staff-profile mapping and reconciliation of all opening commission balances.

### Required tests

- Tenant/RLS: assignments, packages, session ledgers, PT/class schedules/bookings, sales, payments, receipts, commissions, expenses, and trainer views cannot cross gyms.
- Permission/object scope: trainer sees only assigned members and own commissions; payout is denied; Reception can submit but cannot approve; creator/submitter cannot self-approve; assignment/sale/pay permissions and protected financial actions follow the matrix.
- Financial: exact percentage snapshot, `ROUND_HALF_UP`, gym share, paid/outstanding totals, atomic linked payment/receipt/expense/cash records, and valid reversal ordering.
- Concurrency: overlapping assignments/bookings, class capacity races, concurrent last-session consumption, duplicate attendance/no-show/correction, duplicate PT submit/approve/reject, one commission per approved sale, concurrent partial payouts, payout-versus-void, idempotent retries, and rollback without partial finance.
- UI/API: assignment/package date boundaries, trainer and member gym-local calendars, remaining-session accuracy, class capacity, trainer dashboard privacy, safe errors, accessibility, and end-to-end PT/class booking plus PT sale-to-payout reconciliation.

### Checks

- Standard gates, financial reconciliation and lock tests, RLS/permission inventory, schema/generated types, query plans for assigned-member/commission lists, web build, and PT E2E.

### Exit criteria

- Assignments, PT sales, and commissions are tenant-safe, permission-safe, exactly calculated, and fully reconcilable.
- Trainers cannot access unassigned members, other trainers' private commissions, or payout controls.
- All checks pass and Phase 6 aggregation policy is approved.

## 8. Phase 6 — Dashboards and reports

### Dependencies

- Phases 2–5 exit criteria for the source domains included in each metric.
- Approved posted-only metric definitions, source-status policy, timezone cutoffs, permission split, date/row limits, export behavior, and performance budgets.

### Scope

- Implement gym operational, owner financial, inventory, and trainer-scoped dashboards. The owner contract includes active/new/expired and 3/7/30-day expiring members; total and unique check-ins; currently checked-in members; overdue unpaid/partial member count and amount; pending approvals; ranked new-member/renewal/PT/class/product/other posted net revenue; top product items by quantity/revenue/gross profit; expenses by protected category; staff salary and PT commission expense; total expenses; and net profit.
- Implement daily, monthly, cash-book, profit/loss, membership/expiry, check-in/current-occupancy, PT/class schedule/session, and product/stock/sales/gross-profit reports from authoritative records.
- Use bounded filters, stable pagination, server-calculated totals, explicit timezone/currency, and tenant-leading selectors.
- Keep exports synchronous and bounded initially. Background exports, Redis, Celery, caches, aggregates, replicas, or warehouses require measured evidence and approval.

### Migrations and data

- Prefer no mutable report tables. Add only measured indexes or approved tenant/date aggregates with reproducible rebuild and reconciliation rules.
- Any backfill is tenant-batched, resumable, forced-RLS safe, and reconciled to source records.

### Required tests

- Tenant/RLS: all counts, sums, joins, annotations, filters, empty/adversarial ranges, dashboard cards, and exports exclude other gyms.
- Permission/object scope: operational versus financial reports, receptionist denial, owner/accountant rules, trainer-assigned-only aggregates, and export permissions.
- Financial: posted/void/refund/reversal policy and all dashboard/report formulas reconcile exactly to Phase 4/5 source records; revenue ranking is explicitly distinct from product gross-profit ranking; drafts, pending submissions, and rejections affect only `pending_approvals`, never revenue, stock sold, expense, or profit.
- Concurrency: reports remain transactionally coherent during posting/refund/close/payout; duplicate export requests and cache/aggregate refresh, if approved, are safe.
- Performance/UI: timezone boundaries, bounded ranges, realistic volumes, query plans, N+1 limits, accessibility, loading/error states, and downloadable output safety.

### Checks

- Standard gates, formula reconciliation, RLS aggregate matrix, explain-plan review, performance-budget tests, schema/generated types, web build, and dashboard/report E2E.

### Exit criteria

- Every displayed total has an approved definition and reconciles to tenant-scoped source records.
- Permission and object scopes apply to rows, totals, exports, and cached/derived data equally.
- Performance budgets are met without unapproved Redis/Celery or weakened consistency.

## 9. Phase 7 — React Native member application

### Dependencies

- Stable member APIs from Phases 2–6.
- Approved ADR choosing Expo or bare React Native after evaluating QR, notifications, native modules, device/biometric interaction, build ownership, and store delivery.
- Approved mobile token, secure-storage, privacy, accessibility, supported-device, and release policies.

### Scope

- Create the separate TypeScript React Native application and environment/build documentation.
- Implement member-specific login/refresh/logout, secure credential storage, profile, member dashboard, current membership/days remaining, payment/check-in histories, PT/class calendar and remaining sessions, onsite QR scanning for unattended check-in, event/promotion announcements, membership-expiry push reminders, notification consent/read state, and safe offline/loading/error behavior.
- Every member API enforces both tenant and immutable member-self scope; staff roles are never inherited.
- Device biometric unlock may protect local credentials through platform APIs, but the application does not collect or store biometric templates.

### Migrations and data

- Add only approved member-auth/session, `Announcement`, notification, device-registration, or push-token records, each with ownership, audience/consent, retention, revocation, RLS, and privacy controls.
- No local mobile database becomes a system of record; migrations clear or transform cached sensitive data safely.

### Required tests

- Tenant/RLS/self: changing gym/member/path/body IDs cannot expose another member; lists, files, schedule/packages, QR challenges, announcements, and notifications remain gym-and-self scoped.
- Permission/auth: member token audience/type, refresh/revocation, suspended member/gym, staff-token rejection, and profile field allowlists.
- Financial: payment history and totals are read-only and match authoritative server records; no client-side recalculation is trusted.
- Concurrency: refresh races, repeated notification reads/delivery, QR challenge rotation/replay/parallel scan, multi-device logout/revocation, announcement publication deduplication, and cache invalidation.
- Mobile: Keychain/Keystore storage, logout cleanup, no sensitive logs/screenshots where controlled, offline/error/loading states, accessibility, supported devices, deep-link safety, and Android/iOS builds.

### Checks

- Standard API gates plus mobile format/lint/strict types/tests, dependency/secret scan, signed test builds, device smoke tests, store-policy review, and E2E member journeys.

### Exit criteria

- Approved Android/iOS builds provide only self-scoped member features with secure session handling.
- QR and local biometric use expose no personal or biometric template data.
- All server, mobile, privacy, accessibility, and release checks pass.

## 10. Phase 8 — Hardening, operations, and production deployment

### Dependencies

- Phases 1–7 exit criteria for all features included in the release.
- Approved production topology, domains, environments, infrastructure-as-code approach, data jurisdiction, privacy/retention, RPO/RTO, SLOs, performance budgets, incident ownership, and launch plan.
- Independent security review/penetration-test scope and production change approval.

### Scope

- Complete security hardening: IDOR/cross-tenant, privilege escalation, mass assignment, JWT/session, CSRF/CORS/XSS/injection, rate limits, files, dependencies, containers, secrets, and break-glass/support access.
- Run production-like load and soak tests for authentication, peak check-in, dashboards/reports, financial writes, receipt allocation, cash close, and PT payout.
- Implement structured logs, request/trace correlation, metrics, tracing, error reporting, audit export, dashboards, alerts, SLOs, on-call routing, and sensitive-data redaction.
- Provision the budget production topology: Cloudflare, one hardened VPS, Caddy, Docker Compose, Next.js, Django/Gunicorn, PostgreSQL with forced RLS, private MinIO, GHCR, offsite encrypted backup storage, and protected CI/CD environments.
- Configure host firewall and SSH hardening, private Docker networks, separate database roles, forced-RLS health/security checks, fixed worker/container limits within the VPS connection and memory budget, WAL-based recovery, object versioning/sync, key rotation, and least privilege.
- Implement controlled migration jobs, immutable artifacts, staged rollout, smoke/isolation tests, rollback, incident, restore, key-rotation, and break-glass runbooks.
- Complete an isolated restore drill and validate RLS, roles, constraints, financial reconciliation, and file references before launch.

### Migrations and data

- Production migrations use expand/migrate/contract where destructive or lock-sensitive, with rehearsal, duration/lock evidence, backup checkpoint, data verification, and rollback/forward-fix plan.
- Migration identity applies schema; runtime never owns objects. Seed/backfill jobs are idempotent, resumable, tenant-batched, audited, and reconciled.
- Production data is never copied to lower environments without approved minimization/anonymization.

### Required tests

- Tenant/RLS: complete two-gym resource/action/aggregate/file/export matrix under production-like pooling; metadata inventory; runtime grants; missing context; rollback/error/reuse/concurrent gym isolation.
- Permission/security: every matrix default and conditional path, custom-role escalation, stale authorization, platform/tenant boundary, member self-scope, support/break-glass audit, attack suite, and penetration-test remediation.
- Financial: full exact reconciliation, immutable-state enforcement, idempotency, receipt uniqueness, refunds/voids, cash close, PT payout, rollback, and restore integrity.
- Concurrency/load: realistic duplicate/race/deadlock tests, connection and VPS CPU/RAM/disk saturation, fixed worker/container budget, load/soak thresholds, graceful overload, deployment during traffic, and no cross-tenant context leakage.
- Operations: backup freshness alerts, point-in-time and full restore, key rotation, secret failure, alert delivery, audit retention, rollback, disaster/incident exercises, smoke tests, and mobile/web release checks.

### Checks and deployment sequence

1. All CI, security, schema, migration, build, dependency, image, and secret gates pass.
2. Provision and validate an isolated local/temporary-VPS staging Compose stack with separate credentials and volumes.
3. Rehearse migrations with the migration identity and verify runtime grants/RLS.
4. Publish digest-pinned web/API images to GHCR and deploy the release candidate through the protected Compose workflow.
5. Run smoke, isolation, financial reconciliation, load, and restore tests.
6. Resolve all critical/high findings; explicitly approve any lower-risk acceptance.
7. Approve the production change and confirmed rollback point.
8. Take the pre-deploy backup checkpoint, apply migrations, restart services in controlled order, and monitor SLOs, logs, alerts, VPS resources, database connections, and core workflows.
9. Stop or roll back on errors; do not advance rollout stages while any release gate is failing.
10. Complete launch verification and retain rollback/incident readiness.

### Exit criteria

- No unresolved critical/high security, tenant-isolation, data-integrity, financial, backup, or availability defect.
- Performance budgets and database connection limits are met under production-like load.
- Penetration/security review findings are resolved or explicitly risk-accepted by authorized owners.
- Restore, rollback, incident, key-rotation, alerting, and break-glass exercises pass.
- Production deployment is observable, reproducible, least-privileged, and approved.

## 11. Cross-phase test and quality strategy

### Test pyramid and required evidence

- Unit: calculations, validators, permission decisions, state transitions, and date logic.
- Database/model: constraints, composite relationships, RLS, immutability, sequences, locks, and migration behavior.
- API: explicit contracts, errors, serializers, filters, pagination, idempotency, authorization, and mass assignment.
- Integration: membership, check-in, payment, receipt, expense, cash, PT, commission, report, file, and audit workflows.
- Security: two-gym matrix, member self-scope, platform/tenant separation, attack cases, and production-like pooling.
- Frontend/mobile: typed clients, forms, permission-aware presentation, accessibility, offline/error behavior, and critical E2E journeys.
- Performance: realistic PostgreSQL volumes, query plans, connection budgets, load, soak, and graceful degradation.

Money tests assert exact `Decimal` values. Local-date tests set an explicit gym timezone. RLS, locking, exclusion, and PostgreSQL constraint tests never use SQLite. Factories require an explicit gym for tenant records; security fixtures use at least Gym A and Gym B with overlapping human-readable identifiers.

### CI quality gates

Every pull request must pass all applicable formatting, lint, Python/TypeScript type, migration drift/conflict, unit, API, permission, tenant/RLS, financial, concurrency, schema compatibility, generated type, build, dependency, image, and secret checks. Main-branch deployment additionally requires staging migration, smoke, isolation, reconciliation, observability, and rollback readiness.

No failed, flaky, skipped, or missing required test permits phase advancement.

## 12. Backup, recovery, and continuity

- PostgreSQL writes daily encrypted base/logical backups and continuous WAL archives to independent offsite storage. Backups kept only on the VPS do not satisfy recovery policy.
- Private MinIO objects use approved versioning/lifecycle and encrypted offsite synchronization with reference and checksum validation.
- Backup age, WAL archive, object-sync, encryption, capacity, and failure alerts are production requirements.
- Quarterly restore drills rebuild an isolated temporary Compose stack or replacement VPS from source configuration, immutable GHCR images, and offsite backups.
- A restore validates database roles, forced RLS, constraints, tenant isolation, financial reconciliation, audit evidence, and object references before exposure.
- Initial planning targets remain RPO ≤ 15 minutes and RTO ≤ 4 hours; they are restore targets rather than high-availability commitments and must be proven before launch.

## 13. Scalability path

### Near term

- Tenant-leading indexes, cursor pagination, bounded reports/exports, query-plan review, N+1 prevention, stateless API, private object storage, and strict disk/log retention.
- Explicit VPS worker/memory budget and PostgreSQL connection limit with migration, backup, and operations reserve.
- Load tests before scaling configuration changes.

### Evidence-driven later options

- Redis only for a measured cache, rate-limit, or distributed-lock requirement with tenant-safe keys, invalidation, outage behavior, and no authorization fail-open.
- Celery plus transactional outbox only for measured asynchronous notifications, scans, exports, or scheduled work with one gym context per transaction, idempotency, retries, and operational ownership.
- Reproducible tenant/date aggregates, validated read replicas, and time partitioning for high-volume check-in/audit/transaction data.
- Tenant-safe analytics warehouse, regional/data-residency deployment, or dedicated databases only behind stable domain/API contracts.
- Service extraction only around demonstrated ownership, reliability, or scaling boundaries.

No scaling change may weaken tenant isolation, permission enforcement, financial semantics, auditability, restore capability, or consistency without explicit approval.

## 14. Phase 0 deployment cost planning estimate

**Estimate date:** 2026-08-15. **Currency and period:** USD per month. **Target:** one budget-production VPS in a low-latency Southeast Asia region.

This is a planning budget, not a vendor quote. Actual prices vary by provider, region, tax, bandwidth, storage growth, exchange rate, and available promotions. Provider selection must meet the approved CPU/RAM/NVMe, backup, data-jurisdiction, and support requirements rather than choosing price alone.

### Approved monthly target

| Cost area                                     | Planning range | Included assumption                                                                                |
| --------------------------------------------- | -------------: | -------------------------------------------------------------------------------------------------- |
| Production VPS                                |      About $50 | 4–8 vCPU, 8–16 GiB RAM, 80–160 GiB NVMe, Linux, Docker Compose                                     |
| Encrypted offsite backup/object storage       |      About $10 | PostgreSQL base/logical backup, WAL archive, MinIO object copy, bounded retention                  |
| Edge, domain, monitoring, and email allowance |      About $20 | Cloudflare free baseline, domain allocation, external uptime/alerting, limited transactional email |
| **Estimated monthly production cost**         |  **About $80** | One production VPS; no high availability or permanent hosted staging                               |

The target initially supports a small rollout, planned around up to 10 gyms, approximately 1,000 monthly active users, bounded file retention, and up to roughly one million API requests per month. These are planning limits, not guarantees; production load tests and monitoring determine the actual safe capacity.

### Cost and availability trade-off

- Next.js, Django, PostgreSQL, and MinIO share one VPS. Host or datacenter failure can take the full service offline.
- The budget buys restore capability, not automatic failover. Offsite backups, WAL archiving, monitored backup freshness, a documented rebuild script, and quarterly restore drills are mandatory.
- A permanent remote staging environment, managed database, second application node, load balancer, and hot standby are outside the approximately $80 baseline.
- Capacity alerts trigger vertical VPS resizing first. Sustained resource pressure, repeated SLO failure, more than 10 active gyms, database/storage growth beyond tested headroom, or restore requirements that exceed RTO trigger review of a separate/managed PostgreSQL service or multi-node architecture.

### Capacity, recovery, and approval conditions

- Initial connection budget: `API workers × maximum connections per worker + migration/operations reserve < PostgreSQL max_connections`. Worker count and memory limits must leave explicit headroom for PostgreSQL, MinIO, backup, and the operating system.
- RPO ≤ 15 minutes requires continuous offsite WAL archiving; RTO ≤ 4 hours requires a tested replacement-VPS build and restore runbook. If drills do not meet either target, the target or budget must change before launch.
- The selected VPS region/provider, data jurisdiction, measured latency, backup-provider independence, saved monthly quote, and upgrade pricing are production approval evidence.
- Redis and Celery remain deferred and are not included. Cost alone does not authorize weakening tenant isolation, backups, financial integrity, or security gates.

### Exclusions

The target excludes taxes, premium support, high availability, a permanent hosted staging server, SMS/push/payment-provider fees, penetration tests and remediation, mobile app-store/build fees, legal/compliance services, engineering/operations staffing, temporary incident-replacement servers, and future multi-node architecture. Large email volume, file egress, or backup growth can increase the approximately $80 estimate and requires approval before purchase.

## 15. Open decisions and approval register

Track each decision with owner, due phase, options, security/data/migration/cost impact, status, and approval reference. Current decisions include:

1. **Resolved for Phase 0:** Phase 1A is foundation only and implements no members, check-in, finance, PT, reports, or mobile features. JWT identifiers are stable configurable non-secrets: issuer `gym-platform-api`, staff/web audience `gym-platform`, and member audience `gym-platform-member`.
2. **Resolved for Phase 0:** Runtime pins are Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.3.3 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Phase 1A compatibility checks remain required evidence, not an open choice.
3. **Resolved for Phase 0:** Next.js is the browser BFF under [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md); Django remains authoritative and browser JavaScript never receives JWT/refresh values or the 5-minute single-use multi-gym selection ticket. The ticket grants no authority before atomic membership selection.
4. **Resolved behavioral contract; vendor deployment deferred:** Email uses Django's configurable email backend. The approved behavior is the existing invitation, password-recovery, and Phase 2 expiry contract: synchronous invitation delivery inside the provisioning transaction with rollback on delivery failure, recovery responses that do not reveal whether an account exists, expiry mail only when the setting is enabled and the member has a current nonblank email, no SMS fallback, deduplicated delivery, and bounded retries. Tests and end-to-end runs use non-delivery backends. Production must configure an approved transactional provider before invitation, password-recovery, or Phase 2 expiry delivery is production-ready. Vendor selection and that deployment configuration remain deferred. File-scan and non-email notification providers remain deferred with the unpublished file and notification routes.
5. **Resolved current MFA policy:** MFA is TOTP. It is mandatory for platform administrators and Owners. Protected owner operations require a recently verified MFA session. Secrets are encrypted, and authenticator codes are replay-protected. Broader role-based MFA may be required later as a hardening decision.
6. **Deferred, nonblocking until Phase 1A:** Local-development and single-VPS production runtime/migration/reporting database role strategy; any future managed database requires a new approved architecture decision.
7. **Resolved for Phase 0:** Every Payment, Expense, Retail Sale, PT Sale, and Salary record uses mandatory `DRAFT → PENDING_APPROVAL → POSTED/REJECTED`; a different MANAGER or OWNER approves and approval is the posting boundary. Approval permissions are hard-reserved to those protected roles. Threshold-based auto-posting and self-approval are not allowed. Future policy changes require an approved migration and security review.
8. **Deferred but blocking before production:** Jurisdiction, tax/receipt behavior, privacy, retention, RPO/RTO approval evidence, and data residency.
9. **Deferred, nonblocking until Phase 7:** Expo versus bare React Native.
10. **Resolved baseline:** Cloudflare edge plus single-VPS Docker Compose production topology with Caddy, PostgreSQL forced RLS, private MinIO, GHCR deployment, encrypted offsite backup, and bounded observability. Provider/region and final sizing remain Phase 8 evidence.
11. **Resolved baseline:** estimated production infrastructure cost is **about $80/month** with the documented single-point-of-failure trade-off and mandatory restore controls. A managed database or high-availability topology requires a new approved budget.
12. **Resolved for Phase 0:** Under [ADR 0003](./adr/0003-reception-cash-session-authority.md), Reception has conditional `cashbook.view/open/reconcile` only for a session opened by the current Reception `GymMembership`; `cashbook.adjust` and all finance approval/posting remain denied.
13. **Deferred, nonblocking until Phase 3:** Exact integration credential/device model and vendor policy.
14. **Resolved for Phase 1B:** `platform.settings.manage` has no HTTP routes. The Phase 1B retirement marks the catalog row inactive, omits it from platform sessions, and does not authorize tenant `settings.manage`. Reactivation requires a new approved decision.
15. **Resolved for Phase 4:** Custom `ExpenseCategory` create/update uses `expense_categories.manage`, recorded in [ADR 0009](./adr/0009-finance-foundation-defaults.md). OWNER is allowed, MANAGER and ACCOUNTANT are conditional, and RECEPTIONIST and TRAINER are denied. The protected `system_key` catalog stays immutable. This code is never satisfied by a broader finance permission.

A blocker remains a blocker until explicitly resolved. Major architecture changes and progression to every next phase require approval.
