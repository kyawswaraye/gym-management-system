# Security Architecture

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`DATABASE.md`](./DATABASE.md), [`API.md`](./API.md), [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

## 1. Security objectives

The highest-priority property is tenant confidentiality and integrity: a gym must not read, modify, delete, reference, aggregate, infer, or download another gym's data.

Additional objectives:

- least privilege for platform, staff, members, services, and database roles;
- durable and accurate financial records;
- secure authentication and session lifecycle;
- private file handling;
- traceability of privileged and financial actions;
- safe failures without internal information disclosure.

The backend and database enforce security. Frontend controls are presentation only.

## 2. Threat model

Relevant threats include:

- IDOR and cross-tenant identifiers;
- forged or modified JWTs;
- stolen, replayed, brute-forced, or membership-substituted gym-selection tickets;
- stale privileges after role changes;
- privilege escalation through custom roles or mass assignment;
- unscoped list, search, aggregate, export, and relationship queries;
- pooled database connection tenant-context leakage;
- SQL injection and unsafe raw SQL;
- XSS, CSRF, credential theft, and brute force;
- signed URL reuse and cross-tenant file access;
- replayed payment or reconciliation requests;
- static, photographed, expired, replayed, or cross-gym check-in QR challenges;
- forged class/PT attendance, double booking, capacity races, or repeated session consumption;
- negative-stock races, forged product cost/profit, and cross-gym retail line substitution;
- announcement audience expansion, push-token reassignment, and cross-gym notification delivery;
- malicious uploads;
- accidental logging of secrets or personal data;
- excessive platform administrator access;
- compromised dependencies, containers, or CI credentials.

## 3. Layered tenant isolation

### Layer 1: authentication

JWT validation uses an explicit algorithm allowlist, issuer, audience, expiry, not-before, token type, signature key ID, and session state. Invalid context never falls back to an anonymous or default gym.

### Layer 2: principal-to-gym relationship

A staff access token identifies one active `GymMembership`; the API verifies that membership belongs to the user and gym and that the user, membership, gym, and subscription are active. A member access token identifies one `Member` linked directly to the authenticated global `User` by `Member.linked_user_id`; member principals do not derive gym context from `GymMembership`. The API verifies the selected member, linked user, member status, gym, session principal type, and authorization version.

After successful staff password/MFA checks, one active membership proceeds directly to a selected-gym session. Multiple active memberships produce only a high-entropy opaque `GymSelectionTicket` valid for exactly 5 minutes. The hash-only, single-use ticket binds the user to an immutable authoritative membership bootstrap list but grants no gym authority, creates no session/JWT, and establishes no tenant context. Selection revalidates the chosen active membership under an atomic ticket lock before any gym context can exist.

### Layer 3: Django query scoping

Every tenant selector and view starts from `filter(gym=request.gym)`. Object lookup uses the scoped queryset. Serializer relationships are scoped to the same gym. A cross-tenant ID normally produces `404`.

A reusable `TenantQuerySet` may improve consistency, but no implicit manager may hide security-critical behavior. Explicit request scope remains visible at API boundaries.

### Layer 4: database constraints

Non-null `gym_id`, composite unique constraints, and composite foreign keys prevent a child in Gym A from referencing a parent in Gym B. The API cannot update `gym_id`.

### Layer 5: PostgreSQL RLS

All tenant-owned tables enable and force RLS. Policies have `USING` and `WITH CHECK`. Missing context matches no tenant rows. The runtime role is not a table owner and has neither superuser nor `BYPASSRLS`.

`gym_migration` owns schema objects and is `NOBYPASSRLS NOINHERIT`. `gym_runtime` is the API role and is `NOBYPASSRLS NOINHERIT`. `gym_auth_bootstrap` is `NOLOGIN NOINHERIT BYPASSRLS` and cannot be assumed by runtime. It owns only the six `row_security=off` security-definer functions. Their `search_path` is `pg_catalog, public`. They contain no dynamic SQL. The role's table privileges are `SELECT` on exactly:

- `tenants_gym`
- `tenants_gym_membership`
- `tenants_staff_invitation`
- `tenants_staff_invitation_role`
- `tenants_role`
- `tenants_permission`
- `tenants_system_role_template`
- `tenants_system_role_template_permission`

`PUBLIC` has no execute privilege on those functions. Runtime may execute `app_staff_memberships()`, `app_owner_invitation(text)`, and `app_staff_invitation(text)`. `enforce_invitation_role_owner_boundary()`, `app_protect_owner_role_permissions()`, and `app_protect_membership_role_owner()` are triggers. They are not granted to runtime; PostgreSQL fires an existing trigger without a direct execute grant, and a direct call is denied. Runtime has no table-level `INSERT`, `UPDATE`, or `DELETE` on `users_user`. Its direct column update privilege is only `mfa_enabled`, `mfa_secret_encrypted`, `mfa_last_used_step`, and `updated_at`. Password, `is_superuser`, `is_platform_admin`, `is_active`, and the other identity columns are not directly updatable. Invitation identities are created only by `app_create_invited_identity(text, text, text, text)`, which inserts an active, non-platform, non-superuser, MFA-disabled row with an unusable password, a normalized lowercase email, and no login or MFA state. `app_guard_runtime_user_insert` rejects a direct runtime insert that omits or supplies a privileged flag, usable password, MFA or login state, or malformed normalized email. `app_accept_invitation_password(text, text, timestamptz)` performs the only runtime password write: unusable to one current `pbkdf2_sha256` hash, for a non-platform user, when the hash matches a same-gym pending unexpired invitation in the current gym context. `app_confirm_invited_profile(text, text, text, text, timestamptz)` may change only that invited user's name and phone under the same invitation. Those three functions keep row security on and are owned by the `users_user` table owner, not by `gym_auth_bootstrap`. `app_guard_runtime_user_update` rejects any other direct runtime column change.

### Layer 6: role and object permissions

Stable permission codes grant actions. Object rules further constrain trainer assignments, member self-service, record states, and high-risk financial commands.

### Layer 7: automated tests and deployment checks

Every resource receives two-gym CRUD, filter, aggregate, relationship, and file tests. CI inspects RLS policy and runtime role configuration against real PostgreSQL.

### Two mandatory, independent controls

Explicit Django tenant scoping and forced PostgreSQL RLS are independent mandatory controls. Every tenant API path, command, worker, report, export, and aggregate must pass both controls. Passing one control never compensates for a missing or disabled other control, and neither may be treated as a temporary substitute for the other.

## 4. PostgreSQL context safety

All tenant work runs inside `transaction.atomic()`. After entering that transaction and before the first tenant query, the application sets parameterized, transaction-local context in two ordered steps. Identity and request context come first; `app.gym_id` is installed only after global session validation has authoritatively selected the gym, so no tenant context exists while the selection itself is still unproven:

```sql
-- Step 1: immediately after BEGIN, before any tenant query.
SELECT set_config('app.user_id', :user_id, true),
       set_config('app.request_id', :request_id, true);

-- Step 2: only after global session, selected-gym, user, and gym lifecycle checks.
SELECT set_config('app.gym_id', :gym_id, true);
```

The same two-step order is mandatory in `ARCHITECTURE.md` section 6, the staff token validation sequence in `API.md` section 4, and the RLS contract in `DATABASE.md` section 6. Setting `app.gym_id` before the selection is validated is a fail-open defect, not an optimization.

Rules:

- assert an active transaction before setting context;
- perform every tenant read and write, including authorization lookup, aggregate, file-metadata lookup, and audit write, only after `set_config(..., true)` in that same `transaction.atomic()` scope;
- never use session-level `SET` or `set_config(..., false)`;
- do not run tenant queries in middleware before context exists;
- do not perform tenant queries from streaming iterators after transaction completion;
- initialize context separately in workers, scripts, commands, and tests;
- process cross-gym jobs as one gym transaction at a time;
- clear behavior is verified across commit, rollback, exceptions, nested transactions, and reused pooled connections.

Staff context is derived from an authoritative active `GymMembership` after global identity/session validation. Member context is bootstrapped differently: after global identity/session validation, an approved narrowly scoped database function/policy may read only the selected `Member` whose `linked_user_id` equals the authenticated `app.user_id`, derive that member's physical `gym_id`, and return no ordinary tenant business data. The transaction then sets `app.gym_id`, reloads the member through normal forced RLS, and applies both gym scope and immutable member-self scope. No general tenant query, authorization join, report, or file lookup may use the bootstrap path.

Platform administration cannot use the normal tenant role as an unrestricted bypass. Cross-platform aggregates use reviewed, narrowly scoped database functions/views or a dedicated service path with explicit platform permission and audit.

## 5. Authentication and sessions

### Passwords and MFA

- Use Django's supported password hashing with a production cost setting.
- Enforce minimum length and compromised-password checks.
- Do not impose composition rules that encourage predictable passwords.
- MFA is TOTP. It is mandatory for platform administrators and Owners. Protected owner operations require a recently verified MFA session. Secrets are stored encrypted, and authenticator codes are replay-protected. Broader role-based MFA may be added later as a hardening decision.
- Login and recovery responses do not reveal whether an account exists.

### JWT

- Prefer asymmetric signing with private keys supplied through Docker secrets or a root-readable encrypted host file (`0600`) that is never copied into an image or repository.
- Stable configurable non-secret identifiers are issuer `gym-platform-api`, staff/web audience `gym-platform`, and member audience `gym-platform-member`.
- Access lifetime target: 10 minutes.
- Refresh tokens are opaque or signed but always represented by a hashed server session.
- Refresh tokens rotate on every use; reuse revokes the family.
- Validate `iss`, `aud`, `alg`, `kid`, `exp`, `nbf`, `sub`, token type, session, gym membership, and authorization version.
- Never accept `alg=none`, algorithm confusion, or user-supplied verification keys.
- Do not log tokens.

### Browser storage

Next.js is the browser BFF. Browser JavaScript never receives a JWT or refresh token; Django remains authoritative for credentials, selected gym, issuance, rotation, session state, validation, revocation, logout, and authorization. The BFF stores credentials only in `HttpOnly`, host-scoped cookies with no `Domain` attribute, `SameSite=Lax`, and `Secure` outside isolated `http://localhost` development. Authentication tokens are prohibited in both `localStorage` and `sessionStorage`.

For a multi-membership login, the trusted BFF receives the plaintext gym-selection ticket once and stores it only in a separate cookie with the same host-scoped `HttpOnly`, `SameSite=Lax`, and production `Secure` requirements. Browser JavaScript receives only safe membership display choices and submits only `gym_membership_id`; it never receives the ticket. The BFF forwards the ticket with that choice, clears the ticket cookie on success or terminal failure, and never converts it into local/browser state. The cookie and server ticket expire in exactly 5 minutes.

Every BFF route that creates, rotates, selects, revokes, or otherwise mutates cookie-authenticated or ticket state requires a valid CSRF token/header pair and strict allowlisted `Origin` validation, with strict `Referer` validation only when `Origin` is unavailable. Missing or mismatched provenance fails closed. On the one recognized access-expiry response, the BFF may refresh once and retry the original request once; all other failures stop, so refresh cannot loop. Refresh rotation is atomic, reuse revokes the family, and logout invokes Django revocation before the BFF clears matching host/path cookies. Cookie clearing also occurs for an already-expired or revoked session but never substitutes for authoritative revocation.

Django `/api/v1/auth/` routes accept only the trusted BFF. The BFF sends a server-only shared secret in `X-Gym-BFF-Secret`; a missing, short, or mismatched secret fails closed with `BFF_REQUIRED`. The browser never receives this secret. The BFF strips inbound `gym_selection_ticket`, access, and refresh values from browser JSON and forwards a ticket only from its HttpOnly cookie. Production Django must listen on loopback (`127.0.0.1` or `localhost`) and must have a configured BFF secret; a public bind is rejected at startup.

These application checks are **not** network isolation. Production must place Django on a private Docker network with no published host port, expose only Caddy on 80/443, and keep PostgreSQL unpublished. The shared secret authenticates the BFF; infrastructure must still make Django unreachable from the internet. See [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md).

### Mobile storage

Store refresh credentials in iOS Keychain/Android Keystore-backed secure storage. Keep access tokens in memory where practical. Clear local data on logout or session revocation.

### Revocation

Password reset, user suspension, staff gym-membership suspension, role changes, member suspension/archive or identity-link changes, suspicious refresh reuse, and explicit logout revoke sessions or increment the selected `GymMembership.authz_version` or `Member.authz_version` as applicable.

## 6. Authorization model

Permissions are durable records identified by stable codes. A released code is never renamed or reused for a different meaning; deprecation creates a replacement code and an explicit migration. Authorization checks use permission codes plus object and state rules, not role-name conditionals scattered through views.

Matrix values are defaults. `Retired` means the stable code remains in the historical catalog, is inactive, and authorizes nothing:

- **Allow (A):** the protected role template includes the permission, subject to tenant scope, record-state rules, and normal validation.
- **Conditional (C):** allowed only under the object, workflow, delegation, or step-up conditions stated below.
- **Deny (D):** absent by default and denied unless an OWNER explicitly adds a permission that is in the role's delegable set. A hard-reserved permission remains denied and cannot be delegated.

### Platform permission matrix

Platform permissions use the `platform.*` namespace and are evaluated outside ordinary tenant-role permissions.

| Stable permission code          | SUPER_ADMIN | Scope and condition                                                                                                 |
| ------------------------------- | ----------: | ------------------------------------------------------------------------------------------------------------------- |
| `platform.dashboard.view`       |           A | View bounded platform operational summaries; not tenant business records.                                           |
| `platform.gyms.view`            |           A | View gym identity, lifecycle, and subscription metadata; not tenant business records.                               |
| `platform.gyms.create`          |           A | Create a gym through the audited provisioning workflow.                                                             |
| `platform.gyms.update`          |           A | Update gym identity and approved lifecycle metadata.                                                                |
| `platform.gyms.activate`        |           A | Activate an eligible gym with reason and policy checks.                                                             |
| `platform.gyms.suspend`         |           A | Suspend a gym with reason, impact checks, and audit.                                                                |
| `platform.owners.create`        |           C | Create/link the initial or an additional owner only within the audited gym-provisioning or approved owner workflow. |
| `platform.subscriptions.manage` |           A | Manage plans and tenant entitlements.                                                                               |
| `platform.settings.manage`      |     Retired | Historical catalog code. Inactive in Phase 1B, no approved HTTP route, and never a substitute for tenant `settings.manage`. |
| `platform.audit.view`           |           A | View platform audit events and support-access events.                                                               |

`platform.settings.manage` is retained for migration history and is not deleted. The Phase 1B retirement marks it inactive and does not reverse that row back to an active grant. Platform sessions load only active `platform.*` codes, so the retired code is omitted while its row still exists. No route may claim it. It must never authorize tenant `settings.manage` or stand in for dashboard, gym, subscription, or audit permissions. Tenant roles still cannot receive any `platform.*` code.

Platform status does not grant any tenant permission. Time-limited support and break-glass access are governed workflows, not additional stable permission codes: a support session receives only explicitly approved canonical tenant permissions, while refunds, voids, cash adjustments, commission payments, role changes, and OWNER-reserved controls remain denied unless incident policy separately approves each exact action.

### Tenant permission matrix

Tenant permissions use canonical `module.action` codes with no `tenant.` prefix. This matrix defines protected template defaults for tenant staff roles.

| Stable permission code     | OWNER | MANAGER | RECEPTIONIST | ACCOUNTANT | TRAINER |
| -------------------------- | ----: | ------: | -----------: | ---------: | ------: |
| `gym.view`                 |     A |       A |            A |          C |       C |
| `gym.update`               |     A |       C |            D |          D |       D |
| `members.view`             |     A |       A |            A |          C |       C |
| `members.create`           |     A |       A |            A |          D |       D |
| `members.update`           |     A |       A |            A |          D |       C |
| `members.archive`          |     A |       C |            D |          D |       D |
| `memberships.view`         |     A |       A |            A |          A |       C |
| `memberships.create`       |     A |       A |            A |          D |       D |
| `memberships.update`       |     A |       A |            A |          D |       D |
| `memberships.cancel`       |     A |       C |            C |          D |       D |
| `checkins.view`            |     A |       A |            A |          D |       C |
| `checkins.create`          |     A |       A |            A |          D |       C |
| `checkins.update`          |     A |       C |            C |          D |       D |
| `checkins.challenge.issue` |     A |       A |            C |          D |       C |
| `payments.view`            |     A |       A |            A |          A |       D |
| `payments.create`          |     A |       A |            A |          A |       D |
| `payments.update`          |     A |       A |            A |          A |       D |
| `payments.submit`          |     A |       A |            A |          A |       D |
| `payments.approve`         |     A |       A |            D |          D |       D |
| `payments.void`            |     A |       C |            D |          C |       D |
| `payments.refund`          |     A |       C |            D |          C |       D |
| `receipts.view`            |     A |       A |            A |          A |       D |
| `receipts.void`            |     A |       C |            D |          C |       D |
| `expenses.view`            |     A |       A |            C |          A |       D |
| `expenses.create`          |     A |       A |            C |          A |       D |
| `expenses.update`          |     A |       A |            C |          A |       D |
| `expenses.submit`          |     A |       A |            C |          A |       D |
| `expenses.approve`         |     A |       A |            D |          D |       D |
| `expenses.void`            |     A |       C |            D |          C |       D |
| `cashbook.view`            |     A |       A |            C |          A |       D |
| `cashbook.open`            |     A |       C |            C |          A |       D |
| `cashbook.reconcile`       |     A |       C |            C |          A |       D |
| `cashbook.adjust`          |     A |       C |            D |          C |       D |
| `inventory.view`           |     A |       A |            A |          A |       D |
| `inventory.manage`         |     A |       A |            C |          D |       D |
| `inventory.adjust`         |     A |       C |            D |          C |       D |
| `retail.view`              |     A |       A |            A |          A |       D |
| `retail.create`            |     A |       A |            A |          A |       D |
| `retail.update`            |     A |       A |            A |          A |       D |
| `retail.submit`            |     A |       A |            A |          A |       D |
| `retail.approve`           |     A |       A |            D |          D |       D |
| `retail.void`              |     A |       C |            D |          C |       D |
| `pt.assignments.view`      |     A |       A |            C |          D |       C |
| `pt.assignments.manage`    |     A |       A |            D |          D |       C |
| `pt.schedule.view`         |     A |       A |            A |          C |       C |
| `pt.schedule.manage`       |     A |       A |            C |          D |       C |
| `pt.sessions.adjust`       |     A |       C |            D |          D |       D |
| `pt.sales.view`            |     A |       A |            C |          A |       C |
| `pt.sales.create`          |     A |       A |            C |          A |       C |
| `pt.sales.update`          |     A |       A |            C |          A |       C |
| `pt.sales.submit`          |     A |       A |            C |          A |       C |
| `pt.sales.approve`         |     A |       A |            D |          D |       D |
| `pt.sales.void`            |     A |       C |            D |          C |       D |
| `pt.commissions.view`      |     A |       A |            D |          A |       C |
| `pt.commissions.pay`       |     A |       C |            D |          A |       D |
| `classes.view`             |     A |       A |            A |          C |       C |
| `classes.manage`           |     A |       A |            C |          D |       C |
| `classes.book`             |     A |       A |            A |          D |       C |
| `classes.attendance`       |     A |       A |            C |          D |       C |
| `salaries.view`            |     A |       A |            C |          A |       D |
| `salaries.create`          |     A |       A |            C |          A |       D |
| `salaries.update`          |     A |       A |            C |          A |       D |
| `salaries.submit`          |     A |       A |            C |          A |       D |
| `salaries.approve`         |     A |       A |            D |          D |       D |
| `salaries.void`            |     A |       C |            D |          C |       D |
| `reports.view`             |     A |       A |            C |          C |       C |
| `reports.financial`        |     A |       C |            D |          A |       D |
| `reports.export`           |     A |       C |            D |          C |       D |
| `staff.view`               |     A |       A |            D |          D |       D |
| `staff.manage`             |     A |       C |            D |          D |       D |
| `roles.view`               |     A |       A |            D |          C |       D |
| `roles.manage`             |     A |       C |            D |          D |       D |
| `settings.view`            |     A |       A |            D |          C |       D |
| `settings.manage`          |     A |       D |            D |          D |       D |
| `files.view`               |     A |       A |            C |          C |       C |
| `files.upload`             |     A |       A |            A |          A |       C |
| `files.delete`             |     A |       C |            D |          D |       D |
| `audit.view`               |     A |       C |            D |          C |       D |
| `notifications.view`       |     A |       A |            A |          A |       A |
| `notifications.manage`     |     A |       C |            D |          D |       D |
| `announcements.view`       |     A |       A |            A |          A |       A |
| `announcements.manage`     |     A |       A |            D |          D |       D |

High-risk conditional actions require the relevant permission plus valid record state, a reason, idempotency protection where applicable, and an audit event. Payment, expense, retail-sale, PT-sale, and salary approval is mandatory for every record before posting. The approver must be a different active Manager or Owner gym membership from both creator and submitter; permissions cannot authorize self-approval. A permission never overrides immutable posted records, stock/session capacity, tenant scope, object scope, or database constraints.

Role-specific conditional defaults are:

- MANAGER owns the standard approval queue for submitted payments, expenses, retail sales, PT sales, and salary periods. Approval is allowed only for records the manager did not create or submit, after revalidation of amounts, stock, evidence, source state, and any cash session. Other conditional financial, cancellation, correction, archive, export, deletion, staff, audit, and commission actions remain limited to OWNER-enabled thresholds. Staff management is limited to non-OWNER accounts and roles within the manager's authority.
- RECEPTIONIST may create, edit, and submit front-desk payment, expense, PT-sale, and salary drafts but cannot approve or post them. Reception's `cashbook.view`, `cashbook.open`, and `cashbook.reconcile` defaults are conditional: a Reception membership may view, operate, reconcile, and close only a same-gym session whose `opened_by_membership_id` equals that current `GymMembership`. Another Reception membership's session and every cross-gym session are denied. Ordinary operation means selecting that open session during draft preparation and allowing a later authorized posting service to append its automatic movement; it never permits a manual adjustment, approval, or posting. `cashbook.adjust` remains denied. Conditional expense and salary reads are limited to the receptionist's own drafts/submissions and minimum staff identity fields; posted salary totals, other staff payroll history, and protected category internals are hidden. PT views/sales are limited to the member transaction being served; operational reports exclude financial measures; file downloads require access to the related member or transaction.
- ACCOUNTANT conditional member access exposes only identity and contact fields needed for a financial workflow. The accountant may prepare and submit finance records but cannot approve them under the protected template; cash adjustments and corrections follow separation-of-duties rules. Operational reports, settings, files, and audit events are limited to finance-relevant scope.
- TRAINER conditional permissions are governed by the assignment and own-commission restrictions below. Trainer operational reports aggregate only records the trainer is authorized to see.

The five `*.approve` permissions for payments, expenses, retail sales, PT sales, and salaries are hard-reserved to the protected MANAGER and OWNER roles in Phase 0. They are not in the delegable set and cannot be added to ACCOUNTANT, RECEPTIONIST, TRAINER, or a custom role.

### Delegation and custom roles

The OWNER controls the tenant's delegable set and may add or remove delegable permissions from MANAGER and other custom roles. The MANAGER row is the standard operational template; `C` entries require their stated workflow conditions, while `D` entries are not available unless the OWNER has placed that exact permission in the MANAGER role's delegable set. Removing a permission takes effect through the authorization-version mechanism.

A caller using `roles.manage` may create or edit non-protected custom roles and assign them only when all of these are true:

1. the caller currently possesses every permission being granted;
2. every granted permission is within the tenant's OWNER-defined delegable set;
3. the caller is allowed to manage the target staff account and role;
4. the change does not modify a protected template, OWNER account, or reserved control.

The effective grant is therefore the intersection of the caller's current permissions and the delegable set; mass assignment, direct identifiers, stale tokens, or custom-role composition cannot expand it. The API revalidates the complete resulting role on every change, including removals and replacements, rather than checking only submitted additions.

`roles.manage` does not by itself authorize OWNER appointment/removal, protected template changes, security and billing settings, delegation-policy changes, or approval of platform support access; those remain OWNER-reserved object/workflow controls. A MANAGER cannot delegate these controls, create an OWNER-equivalent role, manage any OWNER, or grant any permission the MANAGER lacks.

A gym may have multiple active Owners with equal Owner login authority. Adding an Owner requires an active Owner or authorized Super Admin, recent MFA/step-up verification, the protected invitation workflow, and audit. Revoking or suspending an Owner requires a different active Owner or authorized platform workflow, a reason, row locking, authorization-version invalidation, and proof that another active Owner remains. The service locks the gym before counting active Owners, and a deferred database invariant prevents concurrent requests from leaving an active gym ownerless. General staff/role APIs cannot set `is_owner`, assign the protected OWNER role, or bypass these controls. Owner status is authorization only and must not be presented as evidence of equity, capital contribution, profit entitlement, or legal ownership.

### Object restrictions and entitlements

- Until Phase 5 assignments exist, trainer-only member and file mutation and read are denied. A staff membership whose only active role is TRAINER is not an assigned trainer, and the profile-photo routes return `404` for that caller. Phase 5 will limit a TRAINER with a conditional member, membership, check-in, PT, or file permission to members currently assigned to that trainer and to records belonging to those assignments. A trainer may update only explicitly trainer-editable fields.
- `pt.commissions.view` gives a TRAINER access only to that trainer's own commission records; it never permits viewing another trainer's rates, commissions, or payroll. `pt.commissions.pay` is always denied to TRAINER.
- A RECEPTIONIST's conditional salary scope permits only draft preparation/submission and the minimum staff identity needed to select a payee. Salary approval, posted salary history, aggregate salary reporting, and other creators' salary drafts are denied.
- A RECEPTIONIST's cash-session condition is exactly `CashSession.opened_by_membership_id = current GymMembership.id` after normal same-gym Django scope and forced RLS. It does not widen access based on role name, register code, shift, creator, or user identity shared across memberships. Close totals are always server-derived under lock, and a closed session cannot be changed or reopened.
- Member self-service endpoints are a separate principal type, not a tenant staff role. The verified `Member` linked to the authenticated global `User` supplies the authoritative gym and self identity; after approved bootstrap and normal RLS reload, every query must satisfy both `gym_id = authenticated_member.gym_id` and `member_id = authenticated_member.id`. A member principal never derives gym from `GymMembership`, and possession of another member or gym ID has no effect.
- A normal cross-tenant object lookup returns `404`, including nested resources, relationships, files, reports, and custom actions. Authentication failures remain `401`, and same-tenant permission failures may be `403`.
- Product-plan feature entitlements and RBAC answer different questions. An entitlement may disable a feature that RBAC would otherwise allow, but it cannot create, imply, or grant a permission. Access requires both an active entitlement and an authorization result of allow.

Authorization state is authoritative in PostgreSQL. Tokens carry an `authz_version` that is compared with current server state for protected requests. Redis is not assumed or required; any future cache is an optimization only, uses bounded lifetime/invalidation, and failure or staleness cannot grant access.

## 7. API defenses

- Explicit serializer fields prevent mass assignment of gym, actor, status, calculated values, sequence numbers, and permissions.
- DRF querysets are tenant-scoped for list, retrieve, update, delete, and custom actions.
- Object permissions are always called by custom actions.
- Filters and ordering fields are allowlisted.
- Cursor pagination, date range, upload, and export limits prevent unbounded work.
- Financial commands use idempotency keys and database transactions.
- SQL uses ORM or parameterized statements. Raw SQL receives security review.
- Error responses contain safe stable codes and request IDs, never traces or database messages.
- Cross-tenant probes can be rate-limited and alerted without confirming object existence.

## 8. Web and mobile defenses

### XSS

- React output escaping remains enabled.
- Avoid `dangerouslySetInnerHTML`; sanitize with a reviewed library only for an approved rich-text feature.
- Apply Content Security Policy, frame restrictions, MIME sniffing protection, Referrer Policy, and Permissions Policy.
- Uploaded HTML/SVG is not rendered inline from the application origin.

### CSRF and CORS

- Cookie authentication uses CSRF protection.
- CORS is a strict allowlist for production web origins and required headers/methods.
- CORS is not an authorization control.

### Client data

- Cache only data required for the current screen.
- Do not persist sensitive reports or tokens in browser storage.
- Navigation hiding mirrors permissions for usability only.
- QR codes contain short-lived opaque or signed references, not member personal data, permanent gym credentials, or authoritative admission decisions.
- Unattended check-in requires an authenticated same-gym member-self or authorized trainer principal plus an active onsite challenge. A static reusable poster QR is prohibited.
- Challenge verification and consumption are atomic with eligibility and duplicate-window checks. Expired, replayed, cross-gym, principal-substituted, and oversized/malformed scans fail without creating attendance.

## 9. File security

MinIO is private S3-compatible object storage. Its bucket policy denies anonymous reads, writes, and listing, and objects have no public ACL. Its container publishes no host port. Caddy is the only public ingress and reverse-proxies the configured public object origin to MinIO on the Docker network, preserving the public `Host`. That proxy forwards `OPTIONS`, presigned `POST`, and presigned `GET` for the object origin.

Phase 2 file HTTP behavior is implemented as specified in `API.md`. The only Phase 2 purpose is `PROFILE_PHOTO` for an existing same-gym member. The browser upload is a presigned POST whose policy binds the exact key, the exact declared content type (`image/jpeg`, `image/png`, or `image/webp`), `content-length-range` 1 through 5,242,880 bytes, and the exact `x-amz-algorithm`, `x-amz-credential`, and `x-amz-date` values returned in the form. `file`, `policy`, and `x-amz-signature` are the POST exemptions and are not conditions. The policy expires 300 seconds after intent. A presigned GET expires 60 seconds after issue. Server-side reads, the sanitized overwrite, and object deletion use the internal endpoint and server credentials.

The public object origin allows cross-origin `POST` and `GET` only from the configured staff web origins. `Access-Control-Allow-Origin` echoes one allowlisted origin, `Vary` includes `Origin`, and credentialed CORS is not enabled. The browser does not send cookies or `Authorization` to that origin.

Upload controls:

- create a tenant-scoped `PENDING` / `QUARANTINED` intent only after `files.upload`, `members.update`, object scope, and the `members` entitlement;
- use a random object key `{gym_id}/{random_uuid}`; the gym prefix is organization, not authorization;
- keep the asset quarantined until ClamAV accepts both the original object and the canonical re-encode, and a safe decode succeeds inside the 20,000,000-pixel ceiling;
- claim `SCANNING` with `scan_attempt_id` and `scan_started_at` in a short transaction, commit, and release the row locks before any MinIO, ClamAV, decode, or re-encode call; a claim younger than 120 seconds returns `409 FILE_SCAN_IN_PROGRESS` with `Retry-After` of 1 through 120 seconds;
- finalize `CLEAN`, `ERROR`, or `REJECTED` only when that same attempt token still matches and retention is still `QUARANTINED`; delete clears the attempt so a late worker cannot mark the asset clean, and a canonical object that worker already wrote is removed when the row is `DELETE_PENDING` or `DELETED` with no scan claim; a failed removal sets `DELETE_PENDING` with `deleted_at` null so retention retries, a successful removal finishes `DELETED`, and a current or newer claim is left in place without reviving scan state or detaching a newer photo;
- reject infected, malformed, mismatched, empty, oversize, and checksum-mismatched objects; strip metadata by re-encoding before `CLEAN` / `ACTIVE`;
- allow attachment and download only for `CLEAN` and `ACTIVE`;
- map same-gym owner mismatch and cross-tenant ids to `404`.

Download and delete controls:

- resolve RLS-protected metadata and the owning member before issuing a URL or changing retention;
- require `files.view` plus `members.view` for a download URL, and `files.delete` plus `members.update` for delete;
- sign a GET for this sanitized profile image only, with `inline; filename="profile-photo.jpg"` (`.png` or `.webp` for those types), the sanitized content type, and `private, no-store`; the filename is fixed and this inline disposition is not a general document mode;
- verify integrity with a server-credential `HEAD` before signing: `Content-Length` and the server-written `x-amz-meta-gms-sha256`, `x-amz-meta-gms-content-type`, and `x-amz-meta-gms-byte-size` headers must match the sanitized database values; a successful HEAD whose `Content-Length` is missing, non-integer, negative, or otherwise invalid is `409 FILE_INTEGRITY_FAILED`; a network or provider failure stays `503 FILE_STORE_FAILED`; ignore `Content-Type`, `ETag`, and every metadata header the original browser POST could have written;
- treat delete as retention metadata plus object removal, never as a database row delete; clear `scan_attempt_id` and `scan_started_at` before the object delete so a late worker cannot resurrect the asset; deleting an older asset does not clear a different `profile_photo_asset_id`;
- `expire_profile_photo_uploads` also claims `PROFILE_PHOTO` rows that are `CLEAN` and `ACTIVE`, at least 24 hours old, and not referenced by any same-gym `Member.profile_photo_asset_id`. It rechecks those conditions under the owning-member lock and the asset lock, sets `DELETE_PENDING` before the object delete, leaves `DELETE_PENDING` when that delete fails, and sets `DELETED` only after the object is gone. It never deletes a photo that is attached when the lock is taken.

ClamAV runs as a private container with no published host port. The API reaches it only on the Docker network. A scanner timeout or connection failure is retryable `ERROR`. An infected result is terminal `REJECTED`. Production MinIO-compatible storage and ClamAV credentials and endpoints are environment-provided and are absent from the repository, images, and logs. Local and end-to-end Compose use digest-pinned `docker.io/pgsty/silo` for the S3-compatible API. That image is a third-party MinIO fork, not official MinIO. Official MinIO images are not anonymously distributable, so production object-storage image and provider approval remains an unresolved deployment gate. End-to-end profile-photo upload through the fork is interoperability evidence only. Local Compose may set non-production placeholders; those values are not production secrets:

- `MINIO_ENDPOINT_URL` is the internal URL the API uses for read, overwrite, and delete.
- `MINIO_PUBLIC_URL` is the browser-facing HTTPS origin. Presigned POST and GET are signed for this host.
- `MINIO_SERVER_URL` is MinIO's own public-origin setting and equals `MINIO_PUBLIC_URL`.
- `MINIO_BUCKET` and `MINIO_REGION` select the private bucket and SigV4 region.
- `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are the signing secrets.
- `CLAMAV_HOST`, `CLAMAV_PORT`, and `CLAMAV_TIMEOUT_SECONDS` (15) configure the scanner.

Logs, audit payloads, and error bodies may include the file id, gym id, purpose, owner id, content type, byte size, checksum, and stable rejection code. They must not include object bytes, the presigned URL, POST fields, the signature, the scan attempt id, or either MinIO key. Audit rows are written in the same short transaction as the metadata change they record: `FILE_UPLOAD_INTENT`, `FILE_UPLOAD_COMPLETED`, `FILE_UPLOAD_REJECTED`, `FILE_UPLOAD_ERROR`, `FILE_DOWNLOAD_URL_ISSUED`, and `FILE_DELETE_REQUESTED`. None of those transactions stays open across a MinIO call, a ClamAV scan, or image decode. If that audit insert fails, the metadata transaction rolls back and no URL is returned.

The Phase 1B foundation stores `FileAsset` under forced RLS and grants the runtime role select, insert, and update only. Direct runtime delete remains denied. The Phase 2 profile-photo routes and ClamAV scanner are implemented. Production storage credentials, ClamAV deployment, and object-storage image approval remain gated. The local image is `docker.io/pgsty/silo`, not official MinIO.

Membership rate limits are 10 upload intents per 300 seconds, 20 completes per 300 seconds, 60 download URLs per 60 seconds, and 10 deletes per 300 seconds. The HTTP status is `429 RATE_LIMITED`.

## 10. Financial security

- Use `Decimal`/`DecimalField`, never binary floats.
- Backend services calculate totals, expiry, commission, expected cash, and differences.
- Posted records are immutable; correction actions require reason and linkage.
- Receipt sequences are allocated atomically and never client supplied.
- Raw card data and CVV are never stored.
- Payment provider callbacks require signature verification, timestamp tolerance, event deduplication, and idempotent processing.
- Reconciliation, refunds, voids, and manual cash adjustments require elevated capabilities and audit.
- Payments, expenses, retail sales, PT sales, and salary periods create no stock, receipt, settlement, commission, expense, or cash effect until a different authorized Manager or Owner approves them. Approval is the immutable posting boundary; rejected submissions retain audit evidence and no economic side effects.
- Database constraints assert valid ranges and tenant-consistent relationships.
- Retail clients cannot submit quantity on hand, unit-cost snapshots, cost of goods sold, gross profit, or dashboard totals. Approved sale posting locks stock, snapshots authoritative cost, prevents negative inventory, and creates stock/finance effects in one idempotent transaction.
- PT/class remaining sessions come from an append-only ledger. Booking and attendance lock capacity and balance state so one session cannot be consumed twice; corrections append reversals rather than rewriting history.

## 11. Audit and privacy

Audit events include actor, gym, action, module, target ID, safely redacted field changes, result, request ID, source IP, user agent, and timestamp. Platform support actions also record the effective tenant and impersonator.

Never log:

- passwords, refresh/access tokens, gym-selection ticket plaintext, MFA secrets;
- raw card or bank credentials;
- signed URLs;
- full unbounded request/response bodies;
- sensitive notes unless a specific redacted audit representation is approved.

Audit storage is append-only to the application. Critical events are exported to encrypted offsite retention with alerting so loss of the VPS does not erase all security evidence. Access to audit records itself is audited.

Privacy requirements include purpose limitation, retention schedules, controlled exports, deletion/anonymization workflows, and backup expiry. Applicable jurisdiction and retention periods must be confirmed before production.

Membership-expiry email is sent only to the member's current normalized email when the gym setting is enabled. `schedule_membership_expiry_emails` and `deliver_membership_expiry_emails` re-check tenant, active gym, memberships entitlement, expiry-email setting, membership and member eligibility, warning date, and the current address. They do not trust an address copied into a queued payload. A missing or removed address is a normal skip or cancellation and never falls back to phone or another member's contact details. Templates expose only the current member display name, snapshotted plan display name, expiry date, and necessary current gym contact or display fields. The plain-text message sets `X-Idempotency-Key` to the deterministic deduplication key and does not carry secrets. Provider failures store only `PROVIDER_UNAVAILABLE` or `DELIVERY_REJECTED`, never exception text or a destination address. Deterministic database deduplication and `select_for_update(skip_locked=True)` keep concurrent command runs and ordinary reruns from delivering one row twice. A crash after provider acceptance and before the status commit can still duplicate that send until the configured backend honors the idempotency header. Production transactional delivery remains blocked on that provider configuration. Notification HTTP routes stay unpublished. Audited configuration changes remain required for the gym setting.

Push reminders and event/promotion announcements require member consent and a current same-gym device binding. Publishing resolves a fixed allowlisted audience into explicit tenant-scoped recipients; request bodies cannot supply foreign recipient IDs. Push tokens are encrypted or stored as provider-safe references, never logged, and become ineligible after logout, consent withdrawal, member/gym suspension, or device revocation. Announcement content is size/type constrained and active media passes normal file security.

## 12. Infrastructure and secrets

- Docker secrets or root-readable encrypted environment files store database credentials, JWT private keys, provider secrets, backup keys, and MinIO signing credentials. Secrets never enter source control, container images, Compose files, logs, or shell history.
- The VPS firewall exposes only 80/443 publicly. SSH is key-only, rate-limited, restricted to approved administrators/source ranges where practical, and protected by fail2ban or equivalent controls.
- PostgreSQL and MinIO expose no public host ports and accept traffic only on explicit private Docker networks. Application, migration, backup, and administrative paths use separate least-privilege credentials.
- Production, staging, preview, and development data use separate credentials, databases, object buckets, and volumes. Permanent staging does not run on the production VPS in the budget baseline.
- Runtime, migration, reporting, and break-glass database roles are separate.
- Containers run as non-root, use minimal pinned base images, read-only filesystems where practical, dropped capabilities, health checks, resource limits, and only explicitly required private/public ports.
- The host uses a pinned supported Linux release, unattended security updates, disk encryption where the provider supports it, bounded log rotation, a monthly maintenance window, and monitored disk/RAM/CPU pressure.
- CI uses short-lived GHCR credentials and protected deployment environments. Production deploy access is narrowly scoped, audited, and cannot retrieve backup decryption material unnecessarily.
- Dependency, image, secret, and migration scans block severe findings.
- Database/WAL/object backups are encrypted before leaving the VPS, stored with an independent provider/credential boundary, monitored for freshness, retention-protected where supported, and subject to the same access expectations as primary data.
- Continuous offsite WAL archiving targets RPO ≤ 15 minutes; the replacement-VPS rebuild and restore runbook targets RTO ≤ 4 hours. These are planning targets until demonstrated by a pre-production drill and quarterly restore drills.

## 13. Rate limits and abuse controls

Separate throttles cover:

- login, refresh, password reset, MFA, gym-selection-ticket issue, and gym selection;
- ordinary authenticated reads;
- mutations;
- file upload intents, complete retries, download URL generation, and deletes, at the Phase 2 profile-photo limits in section 9;
- searches and report queries;
- exports and platform administration.

Cloudflare edge controls and Caddy request limits complement application-level user/gym/session throttles. The API trusts forwarding headers only from the private reverse-proxy path. Resource limits and graceful overload protect PostgreSQL and the shared VPS from one endpoint exhausting the host.

Gym-selection throttles combine safe account/user, source, and internal ticket identifiers or non-reversible hash fingerprints. They never use or emit ticket plaintext. Every issue, success, expiry, replay, invalid choice, attempt exhaustion, revocation, and rate-limit outcome is security-audited with request/user/internal ticket identifiers and redacted metadata; no gym authority is implied by an audit row.

## 14. Failure behavior

- Missing tenant context: reject or return no tenant rows.
- Invalid or conflicting token gym: `401`/`403`, never choose a default.
- Expired, consumed, revoked, user-mismatched, substituted, or attempt-exhausted gym-selection ticket: issue no JWT/session, install no tenant context, revoke on terminal failure, clear the BFF ticket cookie, and return a safe authentication error.
- Cross-tenant object: `404`.
- Permission/cache failure: deny sensitive operations.
- Disabled RLS or privileged runtime role: deployment health/security check fails.
- Background job without tenant context: fail without processing.
- Signed URL failure: do not expose a bucket URL.
- Audit pipeline failure: persist in the authoritative local append-only audit table; high-risk actions follow a documented fail-closed policy. An outbox remains deferred until measured asynchronous need.
- Secret or key loading failure: service remains unhealthy.

The application never silently switches to a privileged database credential.

## 15. Security test suite

### Tenant isolation

For every tenant resource and HTTP action:

- Gym A cannot access Gym B by path ID.
- Cross-gym IDs in request bodies and serializer relationships fail.
- Lists, filters, search, annotations, joins, counts, sums, dashboard cards, reports, and exports contain only active-gym data, including empty and adversarial filter cases.
- Bulk operations, if introduced, authorize every item.
- Insert/update cannot set or change `gym_id`.
- Two sequential gym requests on one reused pooled connection remain isolated after success, rollback, exception, and nested transaction paths.
- Concurrent requests for different gyms on pooled connections never exchange context.
- Every tenant query fails closed when attempted before `set_config(..., true)` or outside its `transaction.atomic()` scope.
- Missing/malformed RLS settings expose no rows.
- Cross-tenant probes return the documented `404` without timing or aggregate-count disclosure.

### Authorization

- Every matrix cell has allowed, conditional, and denied endpoint tests using stable permission codes.
- Receptionist cannot access owner financial reports by default.
- Receptionist can submit only authorized front-desk finance drafts, cannot approve any finance record, and cannot view salary records outside the restricted own-draft scope.
- Manager approval fails for a record created or submitted by that same manager membership, including concurrent role or actor changes.
- Phase 5 trainer access is assignment-scoped: a trainer sees only assigned members, assignment-related files, and the trainer's own private commission. Until those assignments exist, trainer-only member and file mutation and read are denied.
- Member self endpoints require both the authenticated gym and authenticated member; changing either path/body identifier fails.
- Member/trainer QR scan tests cover expired/replayed challenges, static-token rejection, Gym A challenge with Gym B principal, principal substitution, concurrent scans, duplicate windows, and no PII or permanent secret in the QR payload.
- PT/class tests cover cross-gym trainer/member/package/occurrence IDs, schedule overlap, capacity races, duplicate attendance, no-show policy, session-ledger correction, and concurrent last-session consumption.
- Inventory tests cover cross-gym product/category/sale IDs, client-forged price/cost/profit/stock fields, concurrent last-item sales, negative stock, duplicate posting, return/void reversal, and dashboard reconciliation.
- Implemented membership-expiry email tests cover warning-day boundaries, disabled setting and entitlement, inactive, cancelled, and renewed memberships, archived and cancelled members, missing, removed, and changed email, cross-gym isolation, safe current template data, malformed payload cancellation, redacted provider failure, zero-send, retry limits, duplicate scheduler and delivery runs, and two concurrent delivery workers.
- Future notification tests still need to cover cross-gym announcement audiences, revoked or reassigned devices, consent withdrawal, unsafe content or media, and recipient-count non-disclosure.
- Custom role create, update, replacement, and assignment cannot grant permissions absent from the caller or outside the delegable set.
- MANAGER cannot modify protected templates, OWNER accounts, OWNER-reserved controls, or construct an equivalent custom role.
- Multiple Owners can authenticate independently within the same gym; duplicate invitation/link attempts are idempotent, and no ordinary staff or role mutation can create an Owner.
- Concurrent Owner revoke/suspend/role-removal requests cannot leave an active gym without at least one active Owner; self-revocation and attempts by non-Owners fail.
- Suspended memberships, role removals, delegation changes, and stale `authz_version` fail without relying on Redis.
- Feature entitlement changes cannot add RBAC permissions, and permissions cannot bypass a disabled entitlement.
- Object permissions execute on custom actions, nested routes, bulk actions, exports, and files.

### Authentication and API

- JWT signature, issuer, audience, algorithm, expiry, type, and modified claim tests.
- Refresh rotation, replay, revocation, and logout tests.
- Multi-gym login tests prove password/MFA success precedes ticket issue; one active membership bypasses selection; multiple memberships produce a hash-only high-entropy ticket with exact 5-minute expiry; and zero memberships issue neither ticket nor session.
- Selection tests cover immutable bootstrap-list binding, safe membership choices, current membership revalidation, user mismatch, altered/substituted IDs, expiry boundaries, replay, consumed/revoked state, bounded invalid attempts, terminal revocation, concurrent requests yielding at most one session, and no JWT claims or tenant context before success.
- Unauthenticated access and rate limiting.
- CSRF tests cover every browser cookie mutation, missing/incorrect tokens, and Origin/Referer validation; strict CORS tests verify denied origins and credential behavior.
- Browser tests prove login/select-gym/refresh responses expose no JWT, refresh token, or gym-selection ticket to JavaScript; ticket and auth cookies are host-scoped `HttpOnly`/`SameSite=Lax` and `Secure` outside isolated localhost; the ticket cookie expires within 5 minutes and clears on success/terminal failure; browser storage remains credential-free; one access-expiry response causes at most one refresh/retry; refresh reuse revokes the family; and logout both invokes authoritative revocation and clears cookies.
- Audit/rate-limit tests prove ticket events are bounded and attributable without plaintext in database rows, logs, traces, errors, audit payloads, or throttle keys.
- Mass-assignment attempts cover gym, actor, owner, assignment, totals, status, permissions, roles, approval fields, commission recipient, and receipt/sequence numbers on create, update, bulk, and nested serializers.
- SQL/ORM injection payloads cover search, filters, ordering, report parameters, exports, filenames, and every reviewed raw-SQL boundary.
- Stored, reflected, and DOM XSS payloads cover displayed text, reports, exports, filenames, and uploaded active content; CSP behavior is tested.
- Privilege-escalation tests cover forged role/permission fields, stale authorization, OWNER reservation, support sessions, and platform-to-tenant boundary crossing.

### Files and finance

- Cross-gym and non-self-member file metadata, upload intent, download, delete, and presigned POST or GET requests are denied without object existence disclosure. Until Phase 5 assignments exist, trainer-only member and file mutation and read are denied: upload intent, complete, and download-url return `404` for a trainer-only caller, and an assigned trainer is not allowed. Same-gym owner mismatch is `404`. Missing `files.upload`, `files.view`, `files.delete`, `members.update`, or `members.view` is `403`.
- Tests cover the 300-second POST policy and 60-second GET, exact content type and `content-length-range`, declared versus sanitized checksums, type and size mismatch, the 5,242,880-byte and 20,000,000-pixel ceilings, decompression bombs, malformed and infected objects, quarantine until both scans and the canonical re-encode succeed, a committed `SCANNING` claim before any MinIO or ClamAV call, `409 FILE_SCAN_IN_PROGRESS` with `Retry-After` while a claim is younger than 120 seconds, stale reclaim, finalize that requires the same `scan_attempt_id` and `QUARANTINED` retention, a late worker discarding its result after delete or reclaim, a canonical PUT that races a committed delete and is removed, a failed removal that the retention command retries to `DELETED`, and a current or newer claim that is not deleted, crash recovery after a canonical overwrite and before finalize for a claim the worker still owns, `HEAD` integrity using the three server-written metadata headers, delete that clears the attempt and retains the database row, an inline profile-photo content disposition, and retention that removes an unattached clean profile photo older than 24 hours while leaving an attached photo in place.
- Exact decimal rounding, commission split, membership expiry, expected cash, and immutable-posting rules are tested.
- Reception cash tests allow view/operation/reconcile/close for the current membership's own-opened session; deny another Reception membership's session and every cross-gym session; deny every finance approval/posting and manual adjustment; verify close totals are calculated by the server under lock; and verify closed sessions and their transactions remain immutable.
- Duplicate and concurrent payment, expense, PT-sale, and salary submit/approve/reject commands, callbacks, voids, refunds, cashbook open/reconcile/adjust, commission pay, and receipt allocation produce one valid financial result.
- Rejected finance submissions produce no receipt, source settlement, commission, salary expense, or cash transaction; approval after rejection or after draft mutation fails.
- Idempotency keys are actor/tenant/operation bound; replay with altered payload, tenant, object, or actor fails and concurrent identical requests return the same committed result.
- Failed and retried transactions do not double-post ledgers, reuse sequence numbers incorrectly, or leave partial audit/financial state.

### Database and deployment

Security tests run against real PostgreSQL, not SQLite. CI asserts RLS policy coverage, forced RLS, constraints, indexes, runtime grants, and absence of `BYPASSRLS`.

Deployment checks also assert that PostgreSQL/MinIO are not publicly reachable, containers run with approved users/capabilities/limits, production secrets are absent from images and Compose output, offsite backup/WAL age is within policy, and a temporary-stack restore reproduces roles, forced RLS, finance totals, and object references.

## 16. Phase 0 design status

Phase 0 is design and documentation only. This document defines required behavior but does not prove that endpoints, database policies, roles, or tests implement it. During Phase 0, permission codes and matrix semantics must be reviewed with product, finance, privacy, and security owners; unresolved cells or workflows are explicit design blockers.

Phase 1A is foundation only and does not implement members, check-in, finance, PT, reports, or mobile features. Its approved pins are Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update for the critical October 2026 Next.js advisories. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. Compatibility must be proven by the real PostgreSQL security suite, Django/DRF checks, and Next.js/TypeScript build tests.

Phase 1B closed its gate on 2026-10-17 under the local CI-equivalent evidence in `DEVELOPMENT_PLAN.md`. Published OpenAPI includes the Phase 1B administration, staff, role, settings, and audit routes, plus MembershipPlan and Member list, create, detail, update, and archive, Membership sale, lifecycle, and expiry routes, the member summary, and nested member payment history. Membership-expiry scheduling and delivery commands are implemented. Nested check-in history, notification HTTP routes, and general payment and receipt endpoints remain unpublished. Phase 2 implementation is complete. Production expiry delivery remains gated on an idempotency-capable transactional provider. Production object-storage image and provider approval remains unresolved. The local `docker.io/pgsty/silo` image is a third-party fork, not official MinIO. `memberships.create` authorizes a discount from zero through the plan list price.

Web lint does not load `@next/eslint-plugin-next`. Releases 16.4.1 and 16.4.2 of that plugin still depend on `fast-glob@3.3.1`, which resolves unfixed high-severity `braces@3.0.3`; npm publishes no later `braces` 3.x release. The independent fork `eslint-plugin-nextjs@1.1.4` depends on `fast-glob@3.3.3` and the same `braces@3.0.3` chain, so it cannot restore the Next.js rules without reintroducing the finding. Older Next-oriented plugins are unmaintained and are not equivalent replacements. ESLint still enforces core JavaScript rules, React Hooks recommended rules, Babel/TypeScript parsing, `no-debugger`, `no-var`, and `prefer-const`. Framework behavior stays gated by strict TypeScript, the Next.js production build, Vitest, and Playwright E2E. The high-severity dependency audit, including development dependencies, is not waived.

Secret scanning uses gitleaks. `.gitleaksignore` lists only global `file:rule:line` fingerprints for documented fixtures: the public RFC 6238 SHA-1 test vector, disposable E2E placeholders, and idempotency-key fixture UUIDs. It does not exclude source directories or real credentials.

No tenant-data feature may pass the Phase 0 security gate until its data ownership, stable permissions, object restrictions, entitlement dependency, Django scoping, forced-RLS policy, transaction context, audit events, and test cases are documented and approved. No documentation status may be represented as a runtime security control.

## 17. Security release gate

Before production:

1. Phase 0 threat model, stable permission matrix, delegation rules, and OWNER-reserved controls are approved, and implementation traceability maps every protected endpoint/action to them;
2. all critical tenant isolation tests pass against production-like pooling;
3. no critical/high unresolved dependency or image findings;
4. secret scanning clean;
5. replacement-VPS/isolated-Compose restore drill completed from encrypted offsite database/WAL/object backups within RPO ≤ 15 minutes and RTO ≤ 4 hours, with quarterly drills scheduled and owned;
6. key rotation and incident runbooks tested;
7. audit and security alerts reach responders;
8. penetration test covers IDOR, tenant isolation, privilege escalation, JWT, files, and financial workflows;
9. retention, privacy, and terms requirements approved;
10. break-glass access tested and audited;
11. CI proves both explicit Django scoping and forced PostgreSQL RLS independently fail closed;
12. all tenant work is verified to run inside `transaction.atomic()` after transaction-local `set_config(..., true)`;
13. authorization tests cover every matrix default, custom-role anti-escalation, object restriction, stale authorization, and entitlement/RBAC separation;
14. replay and concurrency tests pass for finance approval, payments, receipts, expenses, salaries, cashbook, refunds, voids, PT sales/commissions, and provider callbacks;
15. firewall, SSH, private Docker networks, container limits, disk/log rotation, external uptime, backup freshness, and offsite-restore controls pass the single-VPS production checklist.

The single-VPS production topology that sections 12 and 15 harden — including its accepted single point of failure and its mandatory encrypted offsite backup and restore-drill controls — is accepted in [ADR 0001](./adr/0001-single-vps-production-topology.md). The browser boundary is accepted in [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md), and Reception cash-session object authority is accepted in [ADR 0003](./adr/0003-reception-cash-session-authority.md). MFA is TOTP and is mandatory for platform administrators and Owners, with recent step-up, encrypted secrets, and replay protection; broader role-based MFA may be decided later. Email uses Django's configurable email backend, with non-delivery backends in test and end-to-end runs. Production must configure an approved transactional provider before invitation, password-recovery, or Phase 2 expiry delivery is production-ready; vendor selection and that deployment configuration remain deferred. Jurisdiction is required before production, device design remains Phase 3, and Expo versus bare React Native remains Phase 7.
