# Phase 0 Authoritative Database Design

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`API.md`](./API.md), [`SECURITY.md`](./SECURITY.md), [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

This document is the Phase 0 schema contract. PostgreSQL is authoritative; Django models, migrations, services, APIs, tests, and fixtures must conform to it. Reports and dashboards are query/service outputs, never mutable report tables in Phase 0.

The migrated schema includes the Phase 1A foundation (identity, tenancy, authorization, sessions, audit, and RLS), the Phase 1B provisioning, settings, entitlement, staff, platform-audit, MFA enrollment and challenge, and `files_file_asset` tables, and the earlier `Member`, `MembershipPlan`, `Membership`, `Payment`, `Receipt`, and `Notification` tables. Manual `CheckIn` rows are migrated in `members_check_in`. Staff-issued `CheckInChallenge` rows are migrated in `members_check_in_challenge`. The finance idempotency table and receipt-sequence protections from Phase 4 increment 1 are migrated. Display and reader devices are migrated. Cash sessions, cash transactions, expenses, salary periods, and salary lines are migrated. Product categories, products, and stock movements are migrated. The remaining retail, PT, class, and announcement tables remain contract-only until their phases. Cash, payment, receipt, daily-sales, expense-category, expense, salary-period, product-category, product, low-stock, and stock-movement HTTP routes are published; payment void/refund, receipt void, expense void, salary void, category update, and retail routes remain unpublished. The MembershipPlan HTTP API, the Member list, create, detail, update, and archive API, the Membership sale, lifecycle, and expiry API, and the member summary API are published. Membership-expiry scheduling and delivery commands are implemented. The summary reads existing Member, Membership, and Payment rows with a fixed query budget and does not add a table. Nested member payment history reads the same Payment and Receipt rows with `select_related('receipt')` and a fixed query count. It does not add a table. Migration `0009_payment_member_history_index` replaces `payment_gym_member_idx` with `payment_gym_member_hist_idx` on `(gym_id, member_id, received_at DESC, id DESC)`, matching the history order so `LIMIT` can stop without sorting every payment for the member. Nested check-in history, notification, and general payment and receipt HTTP endpoints remain unpublished.

## 1. Non-negotiable conventions

- Every primary key is a server-generated UUID (`UUIDField`, preferably UUIDv7 when the selected library is fixed; otherwise UUIDv4). No business identifier is a primary key.
- Every tenant-owned table contains its own immutable `gym_id UUID NOT NULL`, even when the gym is derivable through another foreign key.
- Every tenant-owned table has `UNIQUE (gym_id, id)`. Every tenant-to-tenant relationship is a composite foreign key on `(gym_id, referenced_id)` to the parent's `(gym_id, id)`.
- Every table has `created_at TIMESTAMPTZ NOT NULL` and `updated_at TIMESTAMPTZ NOT NULL`. Append-only tables set both to the insertion instant and reject later updates. `created_by` is required where a human or authenticated service initiates the record and is nullable only for documented bootstrap/system jobs.
- Instants are stored as `TIMESTAMPTZ` in UTC. Business dates are stored as `DATE`, derived in the gym's IANA timezone. Gym timezone defaults to `Asia/Yangon`; normalize legacy `Asia/Rangoon` input to `Asia/Yangon`.
- Money is `NUMERIC(18,2)` / Django `DecimalField(max_digits=18, decimal_places=2)`. Application calculations use Python `Decimal`, never binary float. Currency is an ISO 4217 `CHAR(3)`, default `THB`.
- Unless a later legal requirement overrides it, every monetary result is quantized once at the currency quantum (`Decimal("0.01")`) with `ROUND_HALF_UP`. Persisted formula inputs and rounded outputs are retained.
- Percentage snapshots are `NUMERIC(7,4)` and constrained to `0 <= value <= 100`. Rates are calculated at higher precision and only the monetary result is quantized.
- Enums are database `CHECK` constraints (or PostgreSQL enums only if migrations can safely evolve them). Normalized text is generated/stored or maintained by the service and checked nonblank where required.
- All foreign keys declare deletion behavior. Tenant data is never physically cascaded from `Gym`.
- Posted finance is immutable: business fields cannot be updated or deleted. Corrections use `VOID`, `CANCEL`, `REFUND`, or explicit reversal rows and preserve the original.

Common tenant columns, included explicitly by this contract on every tenant table, are:

```text
id UUID PRIMARY KEY
gym_id UUID NOT NULL REFERENCES tenants_gym(id) ON DELETE RESTRICT
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
UNIQUE (gym_id, id)
```

Where listed below, `created_by_membership_id` forms a composite FK with `gym_id` to `GymMembership`; `updated_by_membership_id` is used on mutable master data when accountability is required.

## 2. Scope, ownership, and RLS inventory

### Platform-global tables (no `gym_id`, no tenant RLS)

| Table                          | Purpose and access                                                                     |
| ------------------------------ | -------------------------------------------------------------------------------------- |
| `User`                         | Global login identity; authentication and platform administration only                 |
| `Gym`                          | Tenant root; platform administration and trusted tenant-resolution path                |
| `Permission`                   | Stable immutable authorization catalog                                                 |
| `SystemRoleTemplate`           | Immutable global role definitions used only for provisioning                           |
| `SystemRoleTemplatePermission` | Global template-to-permission mapping                                                  |
| `Feature`                      | Stable global feature catalog                                                          |
| `SubscriptionPlan`             | Global commercial plan catalog                                                         |
| `SubscriptionPlanFeature`      | Global plan-to-feature defaults                                                        |
| `GymSelectionTicket`           | Global pre-session control-plane state for multi-membership staff login                |
| `RefreshSession`               | Global control-plane token/session security state for explicit staff/member principals |
| `PlatformAuditLog`             | Global append-only platform administration and security audit evidence                 |

Global tables are not made tenant-readable merely because they lack RLS. Runtime grants expose only the rows/columns required by authentication, provisioning, or platform administration.

### Tenant-owned tables (all require `gym_id`, forced RLS)

`GymSettings`, `GymMembership`, `StaffInvitation`, `StaffInvitationRole`, `Role`, `RolePermission`, `GymMembershipRole`, `GymFeatureEntitlement`, `StaffProfile`, `Member`, `MembershipPlan`, `Membership`, `CheckIn`, `CheckInChallenge`, `Payment`, `Receipt`, `ExpenseCategory`, `Expense`, `SalaryPeriod`, `StaffSalaryLine`, `TrainerAssignment`, `ServicePackage`, `MemberServicePackage`, `ServiceSessionLedger`, `ClassType`, `ClassSchedule`, `ClassOccurrence`, `ClassBooking`, `PTBooking`, `PTSale`, `PTCommission`, `ProductCategory`, `Product`, `StockMovement`, `RetailSale`, `RetailSaleLine`, `RetailReturn`, `RetailReturnLine`, `CashSession`, `CashTransaction`, `TenantSequence`, `Announcement`, `MemberDevice`, `FileAsset`, `Notification`, `AuditLog`, and `IdempotencyRecord`.

No table has mixed global/tenant scope. In particular, `Role.gym_id` is never nullable.

## 3. Role provisioning and authorization

`Permission` is a stable global catalog: `id`, unique immutable `code`, `module`, `description`, `risk_level` (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), `is_active`, and timestamps. Permission codes are data migration constants, not tenant-editable records.

`SystemRoleTemplate` is global and immutable after release: `id`, unique immutable `key`, `name`, `description`, `version INTEGER > 0`, `is_active`, and timestamps. `SystemRoleTemplatePermission` has `id`, `system_role_template_id`, `permission_id`, timestamps, and `UNIQUE (system_role_template_id, permission_id)`; both FKs use `RESTRICT`.

At gym provisioning, each active template is copied into a tenant `Role`; its permission mappings are copied into tenant `RolePermission` rows in the same transaction. Later template changes never silently change existing gyms. An explicit, audited upgrade command may diff and apply a newer template version.

The complete canonical stable permission catalog is below. A listed code is not deleted when it is retired; `is_active` records whether it can still authorize. Tenant permission codes intentionally have no `tenant.` prefix. Feature-entitlement codes remain a separate catalog and never substitute for permissions.

```text
platform.dashboard.view
platform.gyms.view
platform.gyms.create
platform.gyms.update
platform.gyms.activate
platform.gyms.suspend
platform.owners.create
platform.subscriptions.manage
platform.settings.manage
platform.audit.view

gym.view
gym.update
members.view
members.create
members.update
members.archive
memberships.view
memberships.create
memberships.update
memberships.cancel
checkins.view
checkins.create
checkins.update
checkins.challenge.issue
payments.view
payments.create
payments.update
payments.submit
payments.approve
payments.void
payments.refund
receipts.view
receipts.void
expenses.view
expenses.create
expenses.update
expenses.submit
expenses.approve
expenses.void
expense_categories.manage
cashbook.view
cashbook.open
cashbook.reconcile
cashbook.adjust
inventory.view
inventory.manage
inventory.adjust
retail.view
retail.create
retail.update
retail.submit
retail.approve
retail.void
pt.assignments.view
pt.assignments.manage
pt.schedule.view
pt.schedule.manage
pt.sessions.adjust
pt.sales.view
pt.sales.create
pt.sales.update
pt.sales.submit
pt.sales.approve
pt.sales.void
pt.commissions.view
pt.commissions.pay
classes.view
classes.manage
classes.book
classes.attendance
salaries.view
salaries.create
salaries.update
salaries.submit
salaries.approve
salaries.void
reports.view
reports.financial
reports.export
staff.view
staff.manage
roles.view
roles.manage
settings.view
settings.manage
files.view
files.upload
files.delete
audit.view
notifications.view
notifications.manage
announcements.view
announcements.manage
```

`expense_categories.manage` is not part of migration `0003`. Migration `0025_finance_permission_catalog` adds it with the Phase 4 inventory and retail codes that were documented but missing. `retail.approve` stays the migration `0016` row and is not inserted again. The new code authorizes custom expense-category create and update only. It is not implied by any `expenses.*` code. See [ADR 0009](./adr/0009-finance-foundation-defaults.md).

`platform.settings.manage` stays in this historical catalog and is not removed from migration `0003`. Migration `0020_retire_platform_settings_manage` marks that existing row inactive and records that it has no approved HTTP route. The reverse is a no-op, so reactivation requires an approved decision. The inactive row must not be assigned to a tenant role and must never authorize tenant `settings.manage`. Platform sessions load only active `platform.*` permissions, so the retired code is absent from a newly authenticated session while the row remains.

## 4. Entity definitions

All tenant entities below include the common tenant columns from section 1.

### User (global)

Fields: `id`, `email`, `normalized_email`, `password_hash`, `first_name`, `last_name`, `phone`, `is_active`, `is_platform_admin`, `mfa_enabled`, `mfa_secret_encrypted` (nullable), `last_login_at`, `password_changed_at`, `created_at`, `updated_at`.

Constraints: `UNIQUE (normalized_email)`; normalized email is nonblank; MFA secret is present iff MFA is enabled. This is the custom Django user model from migration one. It contains no gym profile, role, or tenant status. Deletion is normally anonymization plus deactivation; referenced users are `RESTRICT`/`SET NULL` as specified, never cascaded.

### Gym (global tenant root)

Fields: `id`, unique immutable `code`, `name`, `legal_name`, `status` (`PENDING`, `ACTIVE`, `SUSPENDED`, `CLOSED`), `email`, `phone`, structured address fields, `timezone`, `currency`, `subscription_plan_id`, `subscription_status` (`TRIAL`, `ACTIVE`, `PAST_DUE`, `CANCELLED`), `subscription_starts_at`, `subscription_ends_at`, `created_at`, `updated_at`.

Constraints: normalized `code` and `name` are nonblank; timezone is a supported IANA zone; currency is supported and defaults to `THB`; subscription end follows start. `subscription_plan_id` is a global FK with `RESTRICT`. Gyms are never physically deleted after provisioning; closing or suspension preserves all data.

### GymSettings

`GymSettings` is the one-to-one, tenant-owned persistence target for `/api/v1/settings/`. Identity, lifecycle, timezone, currency, subscription, and address remain typed fields on global tenant root `Gym`; operational settings are not stored in an unbounded JSON blob.

Fields: common tenant columns; `week_starts_on SMALLINT` (`0..6`); `allow_manual_member_numbers BOOLEAN`; `member_number_prefix VARCHAR(20)`; `receipt_number_prefix VARCHAR(20)`; `check_in_duplicate_window_seconds INTEGER`; `check_in_challenge_ttl_seconds INTEGER`; `check_in_challenge_max_uses INTEGER`; `membership_expiry_warning_days SMALLINT[]`; `membership_expiry_email_enabled BOOLEAN NOT NULL DEFAULT TRUE`; `membership_expiry_push_enabled BOOLEAN NOT NULL DEFAULT FALSE`; `class_cancellation_cutoff_minutes INTEGER`; `class_no_show_consumes_session BOOLEAN NOT NULL DEFAULT TRUE`; `require_separate_finance_approval BOOLEAN NOT NULL DEFAULT TRUE`; `created_by_membership_id`; `updated_by_membership_id`.

Constraints: `UNIQUE (gym_id)`; prefixes contain only approved uppercase letters, digits, and hyphen and are bounded; duplicate window is `0..86400`; challenge lifetime and use bounds follow the approved short-lived policy; warning-day values are unique and each is `0..365`; cancellation cutoff is nonnegative and bounded; actor fields use same-gym composite FKs. The initial warning-day default is `[0, 3, 7, 30]`. When expiry email is enabled, the scheduled expiry job creates email notifications only for members whose normalized `Member.email` is nonblank; absence of email is a normal skip and never falls back to phone/SMS. Push delivery additionally requires member consent and an active device. All payments, expenses, retail sales, PT sales, and salary periods require approval by a different authorized Manager or Owner in Phase 0; the approval setting records that policy but cannot be disabled without a later approved policy migration and security review. Changes are audited. New settings require typed migrations and explicit API fields rather than arbitrary client-defined keys.

### GymMembership

Fields: common tenant columns; `user_id UUID NOT NULL`; `status` (`INVITED`, `ACTIVE`, `SUSPENDED`, `REVOKED`); `is_owner BOOLEAN`; `authz_version INTEGER NOT NULL DEFAULT 1`; `joined_date DATE`; `invited_at`, `activated_at`, `revoked_at`; `created_by_membership_id` nullable only for the first provisioning owner.

Constraints: `UNIQUE (gym_id, user_id)`; multiple memberships may have `is_owner = TRUE` in the same gym; `authz_version > 0`; status timestamps must agree with status. `user_id` references global `User ON DELETE RESTRICT`. Changing `gym_id` or `user_id` is forbidden. Revocation is a state transition, not deletion. An active gym must always retain at least one `ACTIVE` owner. Every owner add, activate, suspend, revoke, or OWNER-role change locks the `Gym` row, revalidates this invariant in the same transaction, and is protected by a deferred database constraint trigger so concurrent requests cannot remove the last active owner. Indexes include `(gym_id, status)` and a partial `(gym_id, user_id) WHERE is_owner AND status = 'ACTIVE'` for owner lookup; this latter index is intentionally non-unique.

`is_owner` represents Owner login authority only. Phase 0 stores no equity percentage, capital contribution, profit-distribution ratio, or legal-ownership claim. A future share-accounting feature must add separate effective-dated ownership-allocation records and migrations rather than changing the meaning of `is_owner` or rewriting historical financial records.

### GymSelectionTicket (global)

`GymSelectionTicket` is the Phase 1A pre-session control-plane bridge used only after successful staff password/MFA verification yields more than one active `GymMembership`. It is not tenant-owned, carries no gym authority, is never read through tenant repositories, and never establishes `app.gym_id`.

Fields: `id`; `user_id`; immutable unique `ticket_hash`; immutable `eligible_gym_membership_ids UUID[]`; `issued_at`; `expires_at`; nullable `consumed_at`, `revoked_at`; nullable `selected_gym_membership_id`; `attempt_count INTEGER NOT NULL DEFAULT 0`; `max_attempts INTEGER NOT NULL`; nullable `revocation_reason`; safe bounded `ip_address`, `user_agent`, and `request_id`; `created_at`, `updated_at`.

Constraints: `user_id` references global `User ON DELETE RESTRICT`; ticket plaintext is high entropy, is returned once, and is never stored; `ticket_hash` uses the approved keyed/slow-verification-safe cryptographic construction and is globally unique and immutable; `eligible_gym_membership_ids` is an immutable, duplicate-free, deterministically ordered bootstrap snapshot containing at least two IDs; `expires_at = issued_at + INTERVAL '5 minutes'`; `max_attempts` is a small deployment constant fixed by policy and constrained to `1..10`; `0 <= attempt_count <= max_attempts`; consumed and revoked states are mutually exclusive; `selected_gym_membership_id` is present only when consumed and must occur in the bootstrap array; consumed/revoked timestamps cannot precede issue. A ticket cannot be extended, reset, transferred, or reused.

Selection runs in one transaction with `SELECT ... FOR UPDATE`. Django verifies the hash, user binding, live state, expiry, and attempts; verifies the submitted `gym_membership_id` is in the immutable bootstrap list; and revalidates that exact membership is currently active and belongs to the same user through the narrowly scoped authentication bootstrap path. Success atomically marks the ticket consumed and creates the normal selected-gym `RefreshSession`/credentials. Invalid attempts increment under the same lock. Expiry, replay, user mismatch, list substitution, inactive membership, or attempt exhaustion produces no session; terminal failure sets `revoked_at` and a safe reason. No JWT claim or nullable preselected gym/session row is introduced.

Indexes include unique `(ticket_hash)`, `(user_id, expires_at)`, and `(expires_at)` for bounded cleanup. Plaintext never appears in logs, audit payloads, traces, rate-limit keys, or database fields. Terminal/expired rows are retained for 30 days for security investigation and abuse correlation, then hard-deleted by a privileged maintenance role; durable audit events retain only the internal ticket ID/hash fingerprint and safe outcome, never plaintext.

### StaffInvitation

Fields: common tenant columns; `email`, `normalized_email`; `token_hash`; `inviter_membership_id`; `expires_at`; nullable `accepted_at`, `accepted_by_user_id`, `revoked_at`, `revoked_by_membership_id`, `revocation_reason`; `status` (`PENDING`, `ACCEPTED`, `REVOKED`, `EXPIRED`).

`StaffInvitationRole` fields: common tenant columns; `staff_invitation_id`; `role_id`. Constraints: `UNIQUE (gym_id, staff_invitation_id, role_id)` and composite FKs to both tenant parents. At least one role is required before an invitation can be issued; role rows become immutable once issued, preserving the invited-role snapshot used to create normalized `GymMembershipRole` rows on acceptance.

`StaffInvitation` constraints: `token_hash` is globally unique, stores only a cryptographic hash of a high-entropy single-use token, and cannot change; email is nonblank; inviter and revoker use composite same-gym membership FKs; expiry follows creation; exactly the timestamps required by status are present; acceptance is allowed once, before expiry, and atomically consumes the token while creating/linking the global user and activating the selected gym membership and roles. `accepted_by_user_id` is a global `User` FK with `RESTRICT`. Pending invitations have partial uniqueness on `(gym_id, normalized_email)` so concurrent active invitations cannot be issued for the same gym/email. Revocation/expiry/acceptance are terminal; records are retained for audit.

### Role, RolePermission, GymMembershipRole

`Role` fields: common tenant columns; `key`, `name`, `description`, `is_active`; nullable global `source_template_id`; nullable `source_template_version`; `created_by_membership_id`. Constraints: `UNIQUE (gym_id, key)` and active normalized-name uniqueness; source ID and version are both null or both present; source template FK uses `RESTRICT`.

`RolePermission` fields: common tenant columns; `role_id`; global `permission_id`; `created_by_membership_id`. Constraints: `UNIQUE (gym_id, role_id, permission_id)`; composite FK `(gym_id, role_id)`; global permission FK `RESTRICT`.

`GymMembershipRole` fields: common tenant columns; `gym_membership_id`; `role_id`; `created_by_membership_id`. Constraints: `UNIQUE (gym_id, gym_membership_id, role_id)` and composite FKs for both tenant parents.

Tenant roles are archived/deactivated, not deleted while assigned or referenced. Assignment changes increment `GymMembership.authz_version` transactionally.

### Feature entitlement catalogs

`Feature` fields: global `id`, unique immutable `code`, `name`, `description`, `created_at`, `updated_at`.

`SubscriptionPlan` fields: global `id`, unique immutable `code`, `name`, `status` (`ACTIVE`, `RETIRED`), `price`, `currency`, `billing_interval`, timestamps. `SubscriptionPlanFeature`: global `id`, `subscription_plan_id`, `feature_id`, `enabled`, nullable `limit_value INTEGER`, timestamps, and unique plan/feature pair.

`GymFeatureEntitlement` fields: common tenant columns; global `feature_id`; `enabled`; nullable `limit_value`; `source` (`PLAN`, `OVERRIDE`); nullable `effective_from`, `effective_until`; `created_by_membership_id`. Constraints: `UNIQUE (gym_id, feature_id)`; nonnegative limit; end after start. Effective entitlement is this tenant snapshot/override, not a live mutable join to plan defaults. Changes are audited.

### StaffProfile

Fields: common tenant columns; `gym_membership_id`; `staff_code`; `display_name`; `phone`; `joined_date`; `status` (`ACTIVE`, `ON_LEAVE`, `TERMINATED`, `ARCHIVED`); nullable `profile_photo_asset_id`; `default_commission_percent`; nullable `employment_title`, `terminated_date`, `archived_at`; `created_by_membership_id`, `updated_by_membership_id`.

Constraints: `UNIQUE (gym_id, staff_code)` and `UNIQUE (gym_id, gym_membership_id)`; commission percent in `0..100`; termination date is required only for terminated status and is not before joined date; all tenant FKs are composite. Archive instead of delete after operational use.

### Member

Fields: common tenant columns; `member_number`; `first_name`, `last_name`, `normalized_full_name`; nullable normalized `phone`, `email`, `gender`, `date_of_birth`; structured address; `join_date`; emergency contact name/phone; `notes`; nullable `profile_photo_asset_id`; nullable global `linked_user_id`; `authz_version INTEGER NOT NULL DEFAULT 1`; `status` (`ACTIVE`, `EXPIRED`, `SUSPENDED`, `CANCELLED`, `ARCHIVED`); nullable `archived_at`; `created_by_membership_id`, `updated_by_membership_id`.

Constraints: `UNIQUE (gym_id, member_number)`; partial `UNIQUE (gym_id, linked_user_id) WHERE linked_user_id IS NOT NULL`; `authz_version > 0`; names nonblank; date of birth is no later than `join_date`; archived status and timestamp agree. Composite file FK; global user FK `RESTRICT`. Linking/unlinking an identity, changing self-service authorization, or suspending/archiving the member increments `authz_version` transactionally and revokes affected sessions. Indexes: `(gym_id, status, member_number)`, `(gym_id, linked_user_id) WHERE linked_user_id IS NOT NULL`, `(gym_id, normalized_full_name)`, `(gym_id, phone)`, `(gym_id, email)`, `(gym_id, join_date DESC)`.

`is_pt_client` is a read-only selector/API value, not a persisted or client-writable `Member` field. It is true only when a `TrainerAssignment` with `status = ACTIVE` exists for the member on the gym-local as-of date (`effective_from <= as_of` and `effective_until IS NULL OR effective_until >= as_of`). Historical PT sales do not keep the badge true after the active assignment ends.

### MembershipPlan

Fields: common tenant columns; `code`, `name`, `normalized_name`, `description`; `duration_value INTEGER`; `duration_unit` (`DAY`, `WEEK`, `MONTH`, `YEAR`); `price`; `currency`; `is_active`; nullable `archived_at`; `created_by_membership_id`, `updated_by_membership_id`.

Constraints: `UNIQUE (gym_id, code)`; partial unique active `(gym_id, normalized_name)`; duration positive; price nonnegative; currency equals gym currency in Phase 0. Existing plans are archived, not deleted after sale.

### Membership

Fields: common tenant columns; `member_id`, `plan_id`; immutable snapshots `plan_code`, `plan_name`, `duration_value`, `duration_unit`; `start_date`, `expiry_date`; `list_price`, `discount_amount`, `final_amount`, `currency`; `payment_status` (`UNPAID`, `PARTIAL`, `PAID`, `PARTIALLY_REFUNDED`, `REFUNDED`); lifecycle `status` (`PENDING`, `ACTIVE`, `EXPIRED`, `SUSPENDED`, `CANCELLED`); optional non-financial `note`; nullable `cancelled_at`, `cancelled_by_membership_id`, `cancellation_reason`; `created_by_membership_id`.

Constraints: composite member, plan, creator, and canceller FKs; `expiry_date >= start_date`; all amounts nonnegative; `discount_amount <= list_price`; exact check `final_amount = list_price - discount_amount`; currency is three uppercase letters; duration is positive and uses `DAY`, `WEEK`, `MONTH`, or `YEAR`; note is at most 4000 characters and the cancellation reason is at most 500; status and cancellation fields agree. A cancelled row has a timestamp, same-gym canceller, and nonblank reason. Every other status has none of those. Expiry is calculated once with calendar-aware inclusive arithmetic in the gym timezone. Sale snapshots, the creator, and cancellation evidence after the transition to `CANCELLED` are immutable at the database. Lifecycle status, payment status, and the note may change only through approved service paths. No payment is posted by the sale path. Overlap protection is an exclusion constraint on `(gym_id, member_id, daterange(start_date, expiry_date, '[]'))` where status is `PENDING`, `ACTIVE`, or `SUSPENDED`. Indexes lead with `gym_id`, including member/status, status/created time, plan, creator, canceller, and expiry.

### CheckIn

Fields: common tenant columns; `member_id`; nullable `membership_id`; `check_in_at`; nullable `check_out_at`; `method` (`MANUAL`, `QR`, `NFC`, `BIOMETRIC`); nullable `recorded_by_membership_id`; nullable `device_id`; nullable `source_metadata JSONB`; nullable `idempotency_key`; optional `note` of at most 500 characters; immutable `eligibility_snapshot JSONB`; nullable `created_by_membership_id`.

The manual increment stores this table as `members_check_in` in the members app. `note` and `eligibility_snapshot` are part of the runtime contract because the API requires an optional note and an explicit snapshot. `device_id` is a nullable same-gym foreign key to `members_check_in_device`. Manual and member QR rows leave it null. NFC and biometric rows set it and leave both membership actors null. `source_metadata` stays null. Visit status is derived, not stored: `OPEN` while `check_out_at` is null, otherwise `CHECKED_OUT`.

Constraints: composite same-gym FKs to member, membership, recorder, and creator. The creator column is nullable so a member-self QR row does not store a member id in a membership actor column. `MANUAL` requires both actor columns. `QR` requires both actor columns, `device_id`, and `source_metadata` to be null. `NFC` and `BIOMETRIC` require both actor columns and `source_metadata` to be null and `device_id` to reference a same-gym device whose kind equals the method. Checkout is after check-in. `UNIQUE (gym_id, idempotency_key)` when nonnull. Source JSON is an object of at most 2048 octets and contains no biometric template key. The snapshot allows only the recorded eligibility fields. Identity fields are immutable. `check_out_at` may be set once from null. Indexes lead with `gym_id`, including `(gym_id, member_id, check_in_at DESC, id DESC)` and a partial open-visit index. Forced RLS uses `app_current_gym_id()`. `gym_runtime` receives `SELECT`, `INSERT`, and `UPDATE`. `DELETE` is not granted. Duplicate-window policy is enforced in the service under a member lock, not by unreliable timestamp rounding. Explicit checkout times use the same 24-hour backdate and 60-second future bound as check-in. Member scan does not accept a client time. Device ingestion also uses server time and stores a SHA-256 idempotency digest rather than the external event id. A PostgreSQL query-plan baseline, not a production load test, loads 4 open same-gym visits, then 300 older checked-out visits in that gym and 300 visits in another gym, plus 300 challenges and 300 display credentials in each gym. The front-desk open list stays at 11 statements and the member history list at 12, both before and after the extra rows, under a ceiling of 14. The open list uses `checkin_open_member_idx` and sorts the open set. Member history uses `checkin_gym_member_hist_idx`. Challenge lookup by hash uses `checkin_chal_hash_uniq` and filters with `app_current_gym_id()`. `app_check_in_device` looks up `credential_selector` through `checkin_dev_selector_uniq` with row security off. That measurement is the Phase 3 query-plan baseline, not a production load test. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. The closure evidence is in the repository README: 225 non-PostgreSQL unit tests, 171 full PostgreSQL tests plus this focused plan test, the earlier web build and 263 component and unit tests, and 4 passing Playwright specs.

### CheckInChallenge

Fields: common tenant columns; cryptographic `challenge_hash`; `display_location`; `issued_at`; `expires_at`; nullable `consumed_at`; bounded `use_count`, `max_uses`; nullable `issued_by_membership_id`, `display_device_id`; `status` (`ACTIVE`, `CONSUMED`, `EXPIRED`, `REVOKED`).

The table is `members_check_in_challenge`. The plaintext is not a column. `challenge_hash` is the SHA-256 hex digest and is globally unique. Expiry is 30 to 600 seconds after issue. A staff row sets both membership actor columns and leaves `display_device_id` null. A display row does the opposite. `(gym_id, display_device_id)` references a `DISPLAY` row of `members_check_in_device`. Status `ACTIVE` has no `consumed_at` and `use_count < max_uses`. `CONSUMED` requires `consumed_at` and `use_count = max_uses`. `EXPIRED` and `REVOKED` have no `consumed_at` and `use_count < max_uses`. Identity fields are immutable. From `ACTIVE`, a revocation or expiry may keep `use_count` and a null `consumed_at`. A redemption may add exactly one to `use_count` while the row stays `ACTIVE` and `consumed_at` stays null, until `use_count` reaches `max_uses`. That final update sets `CONSUMED` and `consumed_at` together. Terminal states cannot change. Forced RLS uses `app_current_gym_id()`. `gym_runtime` receives `SELECT`, `INSERT`, and `UPDATE`. `DELETE` is not granted. Issuer and creator use same-gym composite foreign keys. Staff issuance locks gym settings, then unbound challenges ordered by id. Device issuance locks gym settings, then that device, then that device's active challenges ordered by id. Member scan locks this row before the member and membership. It does not lock gym settings or a device. A failed scan does not mark a row expired or consumed. A challenge from Gym A cannot create, count, or infer attendance in Gym B. Trainer scan remains a Phase 5 assignment extension.

### CheckInDevice

Fields: common tenant columns; `label`; `location`; immutable `kind` (`DISPLAY`, `NFC`, `BIOMETRIC`); `status` (`ACTIVE`, `REVOKED`); globally unique `credential_selector`; `credential_hash`; `credential_version`; nullable `rotated_at` and `rotated_by_membership_id`; nullable `revoked_at` and `revoked_by_membership_id`; `created_by_membership_id`.

The table is `members_check_in_device`. `kind` is `DISPLAY`, `NFC`, or `BIOMETRIC` and is immutable. The selector matches `^[A-Za-z0-9_-]{16,64}$` and is not secret. The hash is a `pbkdf2_sha256` Django password hash and is not equal to the selector. Version 1 has no rotation evidence. Version 2 or higher has both rotation fields. `ACTIVE` has no revocation evidence. `REVOKED` has both revocation fields. Label, location, selector, kind, creator, and creation time are immutable. Rotation, while `ACTIVE`, increments the version by one and replaces the hash. Revocation does not change the hash or version. The plaintext credential is not a column, and the table has no biometric or vendor-secret column. Creator, rotator, and revoker use same-gym composite foreign keys. Forced RLS uses `app_current_gym_id()`. `gym_runtime` receives `SELECT`, `INSERT`, and `UPDATE`. `DELETE` is not granted. `gym_auth_bootstrap` receives column `SELECT` on `id`, `gym_id`, `credential_selector`, `status`, `credential_version`, and `kind` only, used by `app_check_in_device(selector)`. That function does not return the hash. An index leads with `gym_id`. A check-in device must match the visit method. A challenge device must be `DISPLAY`. See [ADR 0007](./adr/0007-display-device-credentials.md) and [ADR 0008](./adr/0008-device-ingestion.md).

### Payment

Fields: common tenant columns; nullable `member_id`, `membership_id`, `member_service_package_id`, `pt_sale_id`, `retail_sale_id`; `purpose` (`MEMBERSHIP`, `PT`, `CLASS`, `PRODUCT`, `OTHER`); `entry_type` (`CHARGE`, `REFUND`, `REVERSAL`); `description`; positive `amount`; `currency`; `method` (`CASH`, `BANK_TRANSFER`, `CARD`, `MOBILE_PAYMENT`, `OTHER`); `business_date`; `received_at`; nullable external `reference_number`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `VOID`, `CANCELLED`); nullable `original_payment_id`, `reversal_payment_id`, `reason`, `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `voided_at`, `cancelled_at`, `cancelled_by_membership_id`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`.

Increment 3 hardens the existing `members_payment` and `members_receipt` tables in place. It does not add `member_service_package_id`, `pt_sale_id`, or `retail_sale_id` until those sources exist. New charges are only `MEMBERSHIP` or `OTHER`. Posted cash rows created before the cash book keep `legacy_unlinked_cash = true` and no fabricated cash movement; runtime inserts cannot set that flag, and runtime inserts must start as drafts. `MEMBERSHIP` requires that member's membership, `OTHER` has no membership, and payment currency matches the gym. `created_with_reception_scope` and `submitted_with_reception_scope` are checked against the protected Reception assignment of the creator at insert and the submitter at submit, and only for `app_current_gym_id()`. A different or missing gym is rejected. A supplied flag that disagrees is rejected, and the flags stay immutable afterward. A voided charge and its posted reversal store reciprocal links. The reversal must match the original gym, amount, currency, member, membership, and purpose. A posted cash charge needs one `IN` movement; a posted cash refund or reversal needs one `OUT` movement of the same amount. `legacy_unlinked_cash` remains the exception for a cash row that predates the cash book and has no movement. Increment 8A lets `gym_runtime` move a posted charge to `VOID` only in the same committed transaction as a matching posted reversal, void evidence, a voided original receipt, and a linked open-session cash out when the original has a cash movement. A raw update that lacks that reversal is rejected. A session setting does not authorize the transition. `legacy_unlinked_cash` reversals must not invent a movement. Membership obligation counts posted and void charges, then subtracts posted refunds and reversals. Receipt numbers for new posts use `RECEIPT:<business-date year>`. Legacy receipts use deterministic `LEGACY:<year>` keys. Migrations `0015`, `0016`, and `0017` expand, reconcile, then constrain. Reconciliation fails with counts instead of inventing lifecycle evidence.

Constraints: all tenant FKs are composite; amount is greater than zero; currency equals gym currency; `CHARGE` has no original. `REFUND` requires an original posted charge. `REVERSAL` requires an original charge that is posted or void, and that voided charge points back at the reversal. An entry cannot reference itself; one reversal per original; cash method requires a generated cash transaction when posted. `DRAFT` is creator-editable; submission freezes business fields; `PENDING_APPROVAL` has submitter evidence; `POSTED` has a different approver/poster and approval evidence with `posted_by_membership_id = approved_by_membership_id`; `REJECTED` has rejection actor/time/reason and no receipt, settlement, or cash effect; an unsubmitted draft may become `CANCELLED` only with cancellation actor/time/reason. The creator or submitter cannot approve or reject the same record. A posted charge may become `VOID` only with void actor, time, and reason plus one posted `REVERSAL` that matches the charge, including method. Customer refunds are posted `REFUND` rows. The sum of posted refunds and the full reversal cannot exceed the charge. A deferred constraint enforces that cap at commit. Public payment void and refund still reject purpose `PRODUCT`. A partial retail return may post a `PRODUCT` `REFUND` against the original charge while that charge and its original receipt stay posted and issued. The refund cannot commit unless a retail return points at it. A full void still uses one `REVERSAL` and is rejected once any refund has consumed part of the charge. Raw card/PIN data is never stored.

### Receipt

Fields: common tenant columns; `payment_id`; `receipt_number`; `sequence_key`, `sequence_value BIGINT`, `sequence_year`; `issued_at`; immutable snapshots of gym code/name/address, member number/name, description, amount, currency, method, business date, and issuer name; `status` (`ISSUED`, `VOID`, `REPLACED`); nullable `voided_at`, `voided_by_membership_id`, `void_reason`, `replacement_receipt_id`, `rendered_file_asset_id`; `created_by_membership_id`.

Constraints: `UNIQUE (gym_id, receipt_number)` and `UNIQUE (gym_id, sequence_key, sequence_value)`; one partial unique issued receipt per payment; receipt requires a posted payment; amount/currency equal payment; void/replacement fields agree with state; replacement points to a receipt in the same gym. Receipt numbers are never reused, changed, or deleted. Committing a payment as `POSTED` still requires exactly one issued receipt. A later document-only void of that receipt does not change the payment and does not reimpose an issued receipt.

### ExpenseCategory and Expense

`ExpenseCategory` fields: common tenant columns; `code`, `name`, `normalized_name`, nullable immutable `system_key`; `display_order`; `is_active`; nullable `archived_at`; `created_by_membership_id`, `updated_by_membership_id`. Constraints: unique `(gym_id, code)`, unique `(gym_id, normalized_name)`, and nonnegative display order. Seed categories are copied into each gym; they are not mixed-scope rows.

The canonical protected seed catalog, copied idempotently at provisioning and backfilled per gym, is:

| `system_key` / code | Display name                   | Posting rule                             |
| ------------------- | ------------------------------ | ---------------------------------------- |
| `RENT`              | Rent                           | Manual approved expense                  |
| `UTILITIES`         | Electricity / water            | Manual approved expense                  |
| `INTERNET`          | Internet                       | Manual approved expense                  |
| `STAFF_SALARY`      | Staff salary                   | Salary service only                      |
| `PT_COMMISSION`     | PT commission                  | Commission payout service only           |
| `CLEANING_SUPPLIES` | Cleaning supplies              | Manual approved expense                  |
| `MAINTENANCE`       | Maintenance / equipment repair | Manual approved expense                  |
| `MARKETING`         | Marketing                      | Manual approved expense                  |
| `WATER_DRINKS`      | Water / drinks                 | Manual approved expense                  |
| `OTHER`             | Other expenses                 | Manual approved expense with description |

Protected `system_key` and code values are immutable and unique per gym. Once `system_key` is set, the display name, display order, and active state are immutable, and the row cannot be archived or deleted. A row that already used the protected code may keep its existing display name when the seed attaches `system_key`. Custom categories may be renamed and archived. Clients cannot manually post into `STAFF_SALARY` or `PT_COMMISSION`.

`Expense` fields: common tenant columns; `category_id`; `business_date`; `description`; positive `amount`; `currency`; `payment_method`; nullable `receipt_file_asset_id`; optional `policy_note`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `VOID`, `CANCELLED`); nullable `reversal_expense_id`, `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `voided_at`, `cancelled_at`, `cancelled_by_membership_id`, `reason`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`; nullable cash session/transaction links; immutable `created_with_reception_scope` and `submitted_with_reception_scope` matching the payment provenance rule.

Increment 4 migrates `expenses_expense` with forced RLS, composite same-gym FKs, runtime `SELECT/INSERT/UPDATE` (no `DELETE`), insert-as-draft, posted immutability, and a deferred check that a posted cash expense has exactly one matching `OUT` movement on one open same-gym session. Reception-origin create or submit provenance must match that session's opener. A non-cash expense cannot keep a cash link. Increment 8A lets `gym_runtime` void a posted expense only together with one posted reversal linked by `original_expense_id` and `reversal_expense_id`, void actor, time, and reason, and an open-session cash in when the original movement exists. A raw posted-to-void update without that reversal is rejected. The public route still refuses `STAFF_SALARY` and `PT_COMMISSION`; the database transition stays available to the salary service. Salary-period void remains reserved to the migration role. Protected `system_key` category deletes and post-seed edits are rejected. Migration `expenses.0003_expense_posting_commit_current_row` makes that deferred check read the expense row the transaction will commit. A salary approval can insert the draft, submit it, record the cash movement, and post inside one transaction. A row that is still unposted when the transaction commits cannot keep cash evidence.

Constraints: composite category/file/actor FKs; amount positive; currency equals gym currency; an unsubmitted draft may become `CANCELLED` only with cancellation actor/time/reason; submission freezes business fields; approval requires a different actor from creator/submitter, sets `posted_by_membership_id = approved_by_membership_id`, and atomically posts the expense; rejection requires a different actor, records actor/time/reason, and creates no cash movement. A posted expense is voided only with an equal and opposite linked reversal expense; one reversal per original. A posted cash expense atomically creates its cash transaction.

### SalaryPeriod and StaffSalaryLine

`SalaryPeriod` fields: common tenant columns; `period_start`, `period_end`; `business_date`; `description`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `CANCELLED`, `VOID`); nullable `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `cancelled_at`, `cancelled_by_membership_id`, `voided_at`, `reason`; immutable `created_with_reception_scope` and `submitted_with_reception_scope`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`.

`StaffSalaryLine` fields: common tenant columns; `salary_period_id`; `staff_profile_id`; positive manually entered `amount`; `currency`; `payment_method` (`CASH`, `BANK_TRANSFER`, `CARD`, `MOBILE_PAYMENT`, `OTHER`); nullable `external_reference`, `cash_session_id`, `expense_id`; `description`; `created_by_membership_id`, `updated_by_membership_id`.

Constraints: periods use gym-local dates with `period_end >= period_start`; `UNIQUE (gym_id, salary_period_id, staff_profile_id)`; all tenant FKs are composite; amount is positive and currency equals gym currency. Lines are editable only while their period is `DRAFT`; no fixed salary or PT amount is inferred from `StaffProfile`. An unsubmitted draft may become `CANCELLED` with actor/time/reason. Submission freezes the period and all lines. Approval requires a different actor from creator/submitter, sets `posted_by_membership_id = approved_by_membership_id`, and atomically posts one immutable `Expense` per line under protected `STAFF_SALARY`, plus a cash transaction for each cash line, then links `expense_id`. Rejection also requires a different actor and has no expense/cash effect. Voiding a posted period reverses every linked salary expense and cash movement while preserving the original period and lines.

Increment 5 migrates `salaries_salary_period` and `salaries_staff_salary_line` for existing gyms without inserting salary rows. `salaries.0001` depends on `expenses.0003_expense_posting_commit_current_row`, so a targeted salary migrate cannot stop before the committed-row expense check. Both tables have forced RLS, composite same-gym foreign keys, and tenant-leading indexes. Runtime may `SELECT`, `INSERT`, and `UPDATE` a period and may not `DELETE` it. Runtime may `SELECT`, `INSERT`, `UPDATE`, and `DELETE` a line; the delete trigger allows that only for an unlinked line whose period is still `DRAFT`, which is how draft line replacement removes a payee. Runtime inserts start as `DRAFT`. Runtime cannot void a `POSTED` period. `created_with_reception_scope` and `submitted_with_reception_scope` snapshot the protected Reception assignment at create and submit and then stay immutable, using the same provenance rule as `Expense`. A posted cash line must use an open same-gym session for that currency and business date, and a Reception-origin period must use the originating membership's till. A non-cash line cannot keep a cash link. A deferred check requires exactly one posted `STAFF_SALARY` expense per line before the period commit. Salary void stays unpublished.

### TrainerAssignment

Fields: common tenant columns; `trainer_staff_profile_id`; `member_id`; `effective_from`, nullable `effective_until`; `status` (`ACTIVE`, `ENDED`, `CANCELLED`); nullable `ended_at`, `reason`; `created_by_membership_id`.

Constraints: composite trainer/member/creator FKs; end is not before start; status fields agree; exclusion constraint prevents overlapping active date ranges for the same `(gym_id, trainer_staff_profile_id, member_id)`. End/cancel instead of deleting an effective assignment.

### Service packages, PT bookings, and classes

`ServicePackage` fields: common tenant columns; `type` (`PT`, `CLASS`); `code`, `name`; positive `session_count`; `validity_value`, `validity_unit`; `price`, `currency`; nullable `class_type_id`; `is_active`; actors and timestamps. `MemberServicePackage` snapshots package identity, session grant, validity, amount/currency, member, optional sale/payment, and status.

`ServiceSessionLedger` fields: common tenant columns; `member_service_package_id`; signed `session_delta`; `reason` (`GRANT`, `ATTENDED`, `POLICY_NO_SHOW`, `CORRECTION`, `REFUND`, `EXPIRY`); nullable `pt_booking_id`, `class_booking_id`, `reverses_entry_id`; occurred time and actor. Entries are append-only. The authoritative remaining count is the bounded sum of ledger deltas and cannot fall below zero or exceed the granted total after corrections.

`ClassType` stores tenant-defined class identity and default duration/capacity. `ClassSchedule` stores recurrence, trainer, location, local start/end rules, capacity, and active range. `ClassOccurrence` is a concrete gym-local start/end instance with trainer, location, capacity, and status. `ClassBooking` links one member and optional service package to one occurrence with status (`BOOKED`, `ATTENDED`, `NO_SHOW`, `CANCELLED`). `PTBooking` links trainer, member, optional service package, start/end, and the same lifecycle states.

Constraints: all references are composite same-gym FKs; active trainer/member/package are required; occurrence/booking end follows start; unique active booking per member/occurrence; bookings cannot exceed locked occurrence capacity; exclusion constraints prevent overlapping active PT bookings for a trainer or member and overlapping class occurrences for an exclusive location. Attendance/no-show consumption and authorized corrections append exactly one idempotent session-ledger effect.

### PTSale and PTCommission

`PTSale` fields: common tenant columns; `member_id`, `trainer_staff_profile_id`; `service_date`; `description`; `gross_fee`; `currency`; `commission_percent_snapshot`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `CANCELLED`, `VOID`); nullable `payment_id`, `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `cancelled_at`, `cancelled_by_membership_id`, `reversal_pt_sale_id`, `reason`; `created_by_membership_id`, `updated_by_membership_id`.

`PTCommission` fields: common tenant columns; `pt_sale_id`; `trainer_staff_profile_id`; `gross_fee_snapshot`, `commission_percent_snapshot`, `commission_amount`, `gym_share`, `paid_amount`, `outstanding_amount`, `currency`; `status` (`ACCRUED`, `PARTIALLY_PAID`, `PAID`, `VOID`); nullable `paid_at`, `commission_expense_id`, `reversal_commission_id`; `created_by_membership_id`.

Constraints: one commission per sale via `UNIQUE (gym_id, pt_sale_id)`; all tenant FKs composite; gross fee positive; percent in `0..100`; all money nonnegative; exact checks `commission_amount = ROUND_HALF_UP(gross_fee_snapshot * percent / 100)`, `gym_share = gross_fee_snapshot - commission_amount`, and `outstanding_amount = commission_amount - paid_amount`; paid amount cannot exceed commission. An unsubmitted draft may become `CANCELLED` with actor/time/reason. Submission freezes sale and optional payment inputs. Approval requires a different actor from creator/submitter and atomically posts the sale, optional payment/receipt/cash effects, and exactly one commission. Rejection also requires a different actor and creates none of those effects. Paying commission locks the commission, posts the linked `PT_COMMISSION` expense, and updates paid/outstanding in one transaction. Voiding a posted sale requires reversal of payment and commission; paid commission cannot be voided until its payout expense is reversed.

### Product inventory and retail sales

`ProductCategory` fields: common tenant columns; unique code/name within the gym; active/archive state and actors. `Product` fields: common tenant columns; category; unique gym-local SKU and optional barcode; name; unit; sale price; current/default unit cost; low-stock threshold; active/archive state and actors.

`StockMovement` fields: common tenant columns; product; signed quantity; type (`OPENING`, `PURCHASE`, `SALE`, `SALE_RETURN`, `DAMAGE`, `LOSS`, `ADJUSTMENT`, `CORRECTION`); immutable unit-cost snapshot where applicable; nullable retail-sale-line and reversal references; required reason for manual loss/adjustment/correction; business date and actor. Movements are append-only and quantity on hand is their sum.

`RetailSale` fields: common tenant columns; optional member; business date; status (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `CANCELLED`, `VOID`); totals; currency; nullable payment/receipt and approval/correction evidence. `RetailSaleLine` fields: common tenant columns; sale; product; positive quantity; immutable SKU/name/unit-price/unit-cost snapshots; discount; line revenue, cost of goods sold, and gross profit.

Constraints: all tenant FKs are composite; SKU and barcode uniqueness are per gym; quantities and prices are exact decimals. Quantity times money rounds half up to minor units before the line formulas: `line_revenue = round(quantity * unit_price, 2) - discount`, `cost_of_goods_sold = round(quantity * unit_cost_snapshot, 2)`, and `gross_profit = line_revenue - cost_of_goods_sold`. Posting locks every product in deterministic order, rejects negative resulting stock unless an explicit future backorder policy is approved, creates stock movements and payment/receipt/cash effects atomically, and is idempotent. Posted sale facts and cost snapshots are immutable; returns/voids create linked reversing movements and financial records. Returns and voids are not published in Increment 7.

Increment 6 migrates `inventory_product_category`, `inventory_product`, and `inventory_stock_movement` for existing gyms without inserting catalog rows. `inventory.0001` depends on `tenants.0025_finance_permission_catalog`. `inventory.0002` adds composite same-gym foreign keys, forced RLS, complete policies, and least-privilege grants. Runtime may `SELECT` and `INSERT` a category and may not `UPDATE` or `DELETE` it. Runtime may `SELECT`, `INSERT`, and `UPDATE` a product and may not `DELETE` it. Runtime may `SELECT` and `INSERT` a movement and may not `UPDATE` or `DELETE` it. Category code and creator, and product gym, id, timestamps, and creator, are immutable. Category code matches `^[A-Z][A-Z0-9_]{0,31}$`. The stored normalized name is the uppercased collapsed display name and is unique per gym. SKU matches `^[A-Z0-9][A-Z0-9_-]{0,31}$`. Unit matches `^[A-Z][A-Z0-9_]{0,15}$`. An optional barcode is uppercase letters and digits, 1–64 characters, and unique per gym when present. Prices and costs are nonnegative `NUMERIC(18,2)`. Quantities and the low-stock threshold are nonnegative `NUMERIC(18,3)` except a movement quantity, which is signed, nonzero, and still three decimal places. Extra quantity places are rejected rather than rounded. There is no per-product currency column.

Movements are append-only. Allowed types are `OPENING`, `PURCHASE`, `SALE`, `SALE_RETURN`, `DAMAGE`, `LOSS`, `ADJUSTMENT`, and `CORRECTION`. `OPENING` and `PURCHASE` are positive. `DAMAGE` and `LOSS` are negative. `SALE` is negative. `ADJUSTMENT` and `CORRECTION` are signed and nonzero. `LOSS`, `ADJUSTMENT`, and `CORRECTION` require a nonblank reason. The public adjustment command also requires an explanation for every manual type and still rejects `SALE`, `SALE_RETURN`, a reversal, a unit cost, and a retail-line id. A full retail void appends one positive `SALE_RETURN` per line. It requires the same product, the sold quantity, the original unit-cost snapshot, and `reverses_movement_id` pointing at that line's `SALE`. A partial return appends one positive `SALE_RETURN` for the returned quantity. It uses the same product, the sold unit-cost snapshot, and the same original `SALE`. Cumulative `SALE_RETURN` quantity for a line cannot exceed the quantity sold. The one-return-per-line and one-reversal-per-movement unique indexes are removed so repeat partial returns can share that original `SALE`. A deferred stock trigger still requires a voided sale to have exactly one full-quantity return and no `RetailReturn` line, and a posted sale's return movement to be linked from return evidence. The manual stock route still rejects `SALE_RETURN` and a reversal link. A `SALE` row requires a same-gym `retail_sale_line_id`, quantity equal to the negative of that line, the line's product, and the line's unit-cost snapshot. At most one `SALE` movement exists per line. Each movement stores `unit_cost_snapshot` copied from the locked product's `current_unit_cost`. Adjustment does not change that master cost and does not compute a weighted average. An `AFTER INSERT` trigger locks the product and rejects a negative sum. The service locks the product before that check as well. A deferred trigger requires the parent sale to be `POSTED` before a `SALE` movement can commit.

Increment 7 migrates `sales_retail_sale` and `sales_retail_sale_line` for existing gyms without inserting sale rows. The graph is acyclic: `sales.0001` depends on `inventory.0002` and `members.0017`; `members.0018` and `inventory.0003` depend on `sales.0001`; `sales.0002` depends on `members.0018` and `inventory.0003`. `members.0018` adds nullable `members_payment.retail_sale_id`, a same-gym foreign key, and one payment per sale. `inventory.0003` adds nullable `inventory_stock_movement.retail_sale_line_id` and the sale evidence check. Sale and line rows have `gym_id NOT NULL`, `UNIQUE (gym_id, id)`, composite same-gym foreign keys, tenant-leading indexes, enabled and forced RLS, and complete policies. Runtime may `SELECT`, `INSERT`, and `UPDATE` a sale and may not `DELETE` it. Runtime may `SELECT`, `INSERT`, `UPDATE`, and `DELETE` a line so a draft can replace its lines. A delete trigger still allows that only while the parent is `DRAFT`. Hard delete of a sale is rejected. Inserts start as `DRAFT`. Transitions are `DRAFT` to `PENDING_APPROVAL` or `CANCELLED`, and `PENDING_APPROVAL` to `POSTED` or `REJECTED`. Runtime cannot move `POSTED` to `VOID`. Rejected and cancelled rows cannot gain a payment, receipt, or sale movement. Posted rows require exactly one matching `PRODUCT` payment, one issued receipt, and one `SALE` movement per line. A cash posted sale requires one matching `IN` payment cash transaction; a non-cash posted sale cannot have cash links. Reception provenance flags are immutable and are set from the protected Reception role at create and submit.

Line quantity is positive `NUMERIC(18,3)`. Discount, snapshots, and line money are `NUMERIC(18,2)`. A database check requires `line_revenue = round(quantity * unit_price_snapshot, 2) - discount`, `cost_of_goods_sold = round(quantity * unit_cost_snapshot, 2)`, and `gross_profit = line_revenue - cost_of_goods_sold`. PostgreSQL `round(numeric, 2)` is half away from zero, the same `ROUND_HALF_UP` rule the service uses for nonnegative quantity times money. Discount cannot be negative or greater than the pre-discount extension. A deferred totals trigger requires 1 to 100 lines and header sums equal to those lines before commit. One product appears once per sale.

`RetailReturn` and `RetailReturnLine` are immutable evidence for a partial return. `sales.0004_retail_return_foundation` creates `sales_retail_return` and `sales_retail_return_line`. It depends on `sales.0003_retail_sale_void`, `inventory.0004_sale_return_runtime`, and `members.0020_retail_product_reversal`. `inventory.0005_retail_return_foundation` depends on that sales migration and relaxes the full-return stock guards. `members.0021_retail_product_refund` depends on it and allows a `PRODUCT` `REFUND` only when the posted sale and a return row match. The graph stays acyclic: the stock and payment migrations follow the return tables, and the return tables do not depend on those later guards.

`RetailReturn` stores the sale, correction `business_date`, nonblank reason of at most 2000 characters, currency, original payment method, refund, discount, cost, and gross profit, the actor, and nullable payment, receipt, and cash-session links. `RetailReturnLine` stores the return, the sale line, a positive quantity, the allocated discount, refund revenue, cost, and gross profit, the actor, and exactly one stock movement. Header gross profit is refund minus cost. Line gross profit is refund revenue minus cost. A refund above zero requires the payment and receipt; a zero refund has neither. Cash requires the destination session only when the method is `CASH` and the refund is above zero. Both tables have `gym_id NOT NULL`, `UNIQUE (gym_id, id)`, composite same-gym foreign keys, tenant-leading indexes, enabled and forced RLS, and select, insert, update, and delete policies. Runtime may `SELECT` and `INSERT` only. Update and delete triggers reject every change. Inserts require the parent sale to be `POSTED`, the correction date to be on or after the sale date, and the method and currency to match the sale. A deferred trigger requires 1 to 100 lines, header sums equal to those lines, and each line's money to equal the cumulative `ROUND_HALF_UP` slice described in [API.md](./API.md). The final quantity of a line receives the exact remainder. Returned quantity cannot exceed the sale line. The linked movement is a `SALE_RETURN` for that quantity and date, created by the same actor, reversing the line's original `SALE`. A positive refund must be one posted `PRODUCT` `REFUND` of the original charge for the header amount, with one issued correction receipt. The original charge and its receipt stay posted and issued. Cash requires one `OUT` payment movement in the named open session, with `reversal_of` null. A returned sale cannot move from `POSTED` to `VOID`. A full void still cannot commit while a return row exists.

### CashSession and CashTransaction

`CashSession` fields: common tenant columns; `business_date`; `register_code` (uppercase, 1–32 characters, `[A-Z0-9_-]+`, with no separate register catalog); `opening_cash`; derived `cash_sales`, `cash_expenses`, `manual_net`, `expected_closing`; nullable `actual_closing`, `difference`, `difference_reason`, `closed_at`, `closed_by_membership_id`; `status` (`OPEN`, `CLOSED`, `VOID`); `opened_by_membership_id`; `created_by_membership_id`.

Constraints: opening cash and actual closing are nonnegative. `cash_sales` and `cash_expenses` may be negative when a later open session receives a correcting out payment or in expense. Signed `manual_net` and `difference` stay signed; one partial unique open session per `(gym_id, register_code)`; exact `expected_closing = opening_cash + cash_sales - cash_expenses + manual_net`; when closed, actual and difference are required and `difference = actual_closing - expected_closing`; nonzero difference requires a reason.

`CashTransaction` fields: common tenant columns; `cash_session_id`; `direction` (`IN`, `OUT`); `source_type` (`PAYMENT`, `EXPENSE`, `COMMISSION`, `MANUAL`, `REVERSAL`); nullable `source_id`; positive `amount`; `currency`; `occurred_at`; `business_date`; `description`; nullable `reversal_transaction_id`, `reason`; `created_by_membership_id`.

Constraints: composite session/actor/reversal FKs; amount positive; currency equals gym/session currency; source uniqueness `(gym_id, source_type, source_id)` when source is nonnull; one reversal per transaction. Cash transactions are append-only. Manual entries require a reason and elevated permission. Closed sessions accept no transactions; correction is a transaction in a new open session referencing the original, never reopening/editing a closed session.

### TenantSequence

Fields: common tenant columns; `sequence_key` (for example `RECEIPT:2026`); `prefix`; `next_value BIGINT`; `padding SMALLINT`; `created_by_membership_id` nullable only for provisioning.

Constraints: `UNIQUE (gym_id, sequence_key)`; next value positive; padding in `1..18`; prefix format validated. Receipt allocation locks this row with `SELECT ... FOR UPDATE` inside the caller's transaction, consumes `next_value`, increments it by exactly one, and inserts the receipt in that same transaction. A trigger rejects every other column change. `gym_runtime` may update only `next_value` and `updated_at`, and cannot delete the row. Rollback restores `next_value`:

```text
{gym.code}-{year}-{sequence:06d}
W1-2026-000001
```

Transaction rollback does not consume a number; once committed, a number is never reused, including after void. Do not use `MAX(receipt_number) + 1`.

### FileAsset

Fields: common tenant columns; opaque `object_key`; `original_name`; detected `content_type`; `byte_size BIGINT`; `sha256`; `purpose`; `scan_status` (`PENDING`, `SCANNING`, `CLEAN`, `REJECTED`, `ERROR`); nullable `owner_type`, `owner_id`; `uploaded_by_membership_id`; `retention_status` (`ACTIVE`, `QUARANTINED`, `DELETE_PENDING`, `DELETED`); nullable `deleted_at`. The Phase 2 migration adds nullable `scan_attempt_id` (UUID) and `scan_started_at` (`timestamptz`). `SCANNING` fits the existing `varchar(8)` `scan_status` column.

Constraints: `UNIQUE (gym_id, object_key)`; byte size nonnegative; SHA-256 format valid; composite uploader FK; owner is an opaque association and must be service-validated under the same gym. Object keys include the gym UUID plus a random UUID so they remain globally collision-resistant without a non-tenant-leading database index. The bucket is private. Physical deletion follows retention policy; metadata remains when needed for audit/finance.

Phase 1B created `files_file_asset` with `UNIQUE (gym_id, id)`, the checks above, a database check that `object_key` is `{gym_id}/{random_uuid}`, forced RLS using `app_current_gym_id()` for select, insert, update, and delete, and runtime grants for `SELECT`, `INSERT`, and `UPDATE` only. Runtime `DELETE` is not granted. The Phase 1B `fileasset_scan_status_ck` allowed only `PENDING`, `CLEAN`, `REJECTED`, and `ERROR`, and that migration had no claim columns. Migration `0003_profile_photo_contract` is applied and extends that check. `deleted_at` is required exactly when `retention_status` is `DELETED`; `DELETE_PENDING` and the other non-deleted states keep `deleted_at` null. `owner_type`/`owner_id` stay an opaque pair with no foreign key. `StaffProfile.profile_photo_asset_id` is not a cross-app foreign key: the staff API does not accept or return it, and a future staff-photo service must prove the asset is same-gym and `CLEAN` before attaching it. Phase 2 implements upload, download, presigned POST, and the storage adapter. Production storage and ClamAV credentials remain gated and are not stored in the repository. Local Compose uses digest-pinned `docker.io/pgsty/silo`, a third-party MinIO-compatible fork and not official MinIO. Production object-storage image approval remains unresolved.

The Phase 2 profile-photo contract adds those columns and checks in migration `0003_profile_photo_contract`. It does not change the Phase 1B grants. New nullable columns are `upload_sha256` (64 lowercase hex characters when present), `upload_byte_size`, `upload_content_type`, `rejection_code` (1–64 characters matching `^[A-Z0-9_]+$` when present), `scan_attempt_id`, and `scan_started_at`. The migration extends `fileasset_scan_status_ck` to allow `SCANNING` and adds `fileasset_scan_claim_ck` for every row:

- `scan_status = SCANNING` exactly when `scan_attempt_id` and `scan_started_at` are both non-null;
- a `SCANNING` row has `retention_status = QUARANTINED`;
- every other scan status has both claim columns null.

For `purpose = PROFILE_PHOTO` the check `fileasset_profile_photo_ck` requires all of the following:

- `owner_type = MEMBER` and `owner_id` is not null;
- `upload_sha256`, `upload_byte_size`, and `upload_content_type` are present;
- `upload_content_type` and `content_type` are each one of `image/jpeg`, `image/png`, and `image/webp`;
- `upload_byte_size` and `byte_size` are each from 1 through 5,242,880;
- `retention_status = ACTIVE` only when `scan_status = CLEAN`;
- `rejection_code` is present exactly when `scan_status = REJECTED`;
- when `scan_status = CLEAN`, `content_type = upload_content_type`.

`sha256`, `byte_size`, and `content_type` describe the object currently named by `object_key`. At intent they equal the upload declaration. After a successful sanitize they become the canonical object's values, while `upload_*` remains the original declaration. Other purposes are not accepted by the Phase 2 HTTP contract and are not forced into the profile-photo check. Phase 4 increment 4 accepts purpose `EXPENSE_RECEIPT` on the same private pipeline: image-only, at most 5 MiB, `owner_type=GYM`, `owner_id` the gym ([ADR 0009](./adr/0009-finance-foundation-defaults.md)). `fileasset_expense_receipt_ck` mirrors the profile-photo image and size bounds, and also requires `owner_id = gym_id`, without changing `PROFILE_PHOTO` rules. Quarantine expiry and delete-pending object removal include this purpose. A clean unattached expense receipt is not retired on the profile-photo 24-hour schedule. An immutability trigger rejects updates to `gym_id`, `object_key`, `original_name`, `purpose`, `owner_type`, `owner_id`, `uploaded_by_membership_id`, `upload_sha256`, `upload_byte_size`, and `upload_content_type`. Scan, retention, the current checksum columns, `rejection_code`, `deleted_at`, `scan_attempt_id`, and `scan_started_at` change only through the transitions in `API.md`.

A profile-photo intent inserts `PENDING` and `QUARANTINED`, with both claim columns null, even though the column default for retention is `ACTIVE`. Complete claims `SCANNING` only for the short transaction described in `API.md`, then commits before MinIO, ClamAV, decode, or re-encode. Finalize writes `CLEAN`, `ERROR`, or `REJECTED` only when `scan_attempt_id` still matches and retention is still `QUARANTINED`, and that write clears the claim. Delete clears the claim in the same update that leaves `SCANNING`, so a late worker cannot satisfy the finalize predicate. Only `CLEAN` and `ACTIVE` may be attached or downloaded. `Member.profile_photo_asset_id` remains a same-gym composite foreign key without cascading a file-row delete. Deleting a photo nulls that pointer when it matches, then moves retention to `DELETE_PENDING` or `DELETED`. The file row stays. A canonical object written after that delete is removed again. When that cleanup delete fails, the row is set back to `DELETE_PENDING` with `deleted_at` null so object removal is retried; when it succeeds, the row stays or becomes `DELETED`. That cleanup does not change scan status and does not clear a different `profile_photo_asset_id`. A current or newer scan claim keeps its object. Rows still `QUARANTINED` 24 hours after `created_at` become `REJECTED` with `rejection_code = FILE_UPLOAD_EXPIRED` when they are `PENDING` or `ERROR`, or when they are `SCANNING` and `scan_started_at` is at least 120 seconds before the database timestamp. A `SCANNING` claim younger than 120 seconds stays. Those rejected rows then follow object removal. `REJECTED` rows that are still `QUARANTINED` are included in that object-removal retry. The same command also retires a `PROFILE_PHOTO` row that is `CLEAN` and `ACTIVE`, whose `created_at` is at least 24 hours before the database clock, and whose id is not equal to any same-gym `Member.profile_photo_asset_id`. It locks the owning member and the asset, rechecks that the row is still that unattached clean photo, and only then sets `DELETE_PENDING` with `deleted_at` null. It does not change the member pointer. After that transaction commits it deletes the object. Success sets `DELETED` and `deleted_at` while the row is still `DELETE_PENDING`. Failure leaves `DELETE_PENDING` with `deleted_at` null for a later retry. A photo that is attached, younger than 24 hours, or no longer `CLEAN` and `ACTIVE` under the lock stays as it is. Object removal runs after the status transaction commits. This migration and the file routes are implemented. Production credential and deployment configuration remains gated.

The migration leaves every non-`PROFILE_PHOTO` row unchanged. For an existing `PROFILE_PHOTO` row it copies the current checksum, size, and content type into `upload_*` when those values already meet the bounds, and it keeps an existing owner pair. Every existing row receives null `scan_attempt_id` and `scan_started_at` and keeps its current scan status, so `fileasset_scan_claim_ck` holds without creating a `SCANNING` row. A `PROFILE_PHOTO` row with a disallowed type, a size outside 1 through 5,242,880, or a missing owner fails the migration closed.

### Notification

The migrated `notifications_notification` table is the membership-expiry email queue. Fields: common tenant columns; `recipient_member_id`; `membership_id`; `type = MEMBERSHIP_EXPIRY`; `channel = EMAIL`; `template_key`; `template_version`; size-limited safe `payload JSONB`; `scheduled_at`; nullable `sent_at`, `failed_at`, and `next_attempt_at`; `status` (`PENDING`, `SENT`, `FAILED`, `CANCELLED`); blank `provider_id`; `deduplication_key`; `attempt_count` from 0 through 5; `last_error_code`; nullable `created_by_membership_id` for system-generated notices. The row does not store a destination address or raw provider exception text. Later in-app, SMS, push, announcement, and device fields below remain contract-only.

Constraints: `UNIQUE (gym_id, id)`; `UNIQUE (gym_id, channel, deduplication_key)`; same-gym member and membership foreign keys; forced RLS from migration `0002`; runtime `SELECT`, `INSERT`, and `UPDATE` only. Pending rows require `next_attempt_at` and `attempt_count < 5`. Sent rows require `sent_at`, at least one attempt, and a blank error code. Failed rows require `failed_at`, at least one attempt, and a redacted error code (`PROVIDER_UNAVAILABLE` or `DELIVERY_REJECTED`). Cancelled rows have no sent, failed, or next-attempt timestamp and a blank error code. `provider_id` stays a short token without an address. A partial index on `(gym_id, next_attempt_at, id) WHERE status = 'PENDING'` serves due work. `notification_expiry_contract_ck` binds the type, email channel, `membership_expiry` template version 1, the deduplication key, and the payload object. Allowed payload keys are `member_name`, `plan_name`, `expiry_date`, `warning_day`, and `gym_name`, plus optional `gym_email` and `gym_phone`. Destination keys such as `email`, `member_email`, `to`, and `destination` are rejected. Migration `0003` repairs every existing pending, sent, failed, and cancelled row under that gym's tenant context before the new checks. It bounds attempts, fills a missing terminal timestamp from the stored sent, failed, or scheduled time, keeps a stable error code or stores `DELIVERY_REJECTED`, clears an unsafe `provider_id`, and drops blank or invalid optional contacts. It does not invent a destination address.

Membership-expiry email uses that type, channel, and versioned template, with the member as recipient. `schedule_membership_expiry_emails` and `deliver_membership_expiry_emails` are both implemented. Before creation and again before delivery, the service verifies that the selected gym is active, the memberships entitlement and expiry-email setting are enabled, the member and membership are active, the configured warning day matches the gym-local date, and `Member.email` remains a nonblank normalized address. The destination is resolved from that current address at send time. It is not accepted from the scheduled payload. The payload contains only the member display name captured at schedule time, the snapshotted plan name, the expiry date, the warning day, and gym display or contact fields required by the template. Rendering uses the current member display name, the membership's snapshotted plan name, the expiry date, and the current gym display and contact fields. The deduplication key is deterministic per `(membership_id, expiry_date, warning_day, template_version)`. Repeated scheduler runs do not create a second row, and repeated or concurrent delivery runs do not send one pending row twice under normal operation. A member without email produces no row. A removed address or a row that is no longer eligible becomes `CANCELLED` with no provider call and no SMS or phone fallback. Provider failure increments `attempt_count` once, stores only the redacted code, and either sets `next_attempt_at` with bounded exponential backoff or marks `FAILED` at five attempts. Delivery selects due pending rows below that stored maximum. When `attempt_count` has already reached the command's `--max-attempts`, that row is marked `FAILED` without a provider call and without another increment, and a stored stable error code is kept. Each delivery transaction locks one due row and commits its outcome after the provider call. That deduplicates command concurrency and reruns. A crash after the provider accepts the message and before commit can still leave the row pending; closing that window requires a provider that honors the `X-Idempotency-Key` header. Production readiness remains blocked on that provider configuration. Notification HTTP routes stay unpublished.

`Announcement` fields: common tenant columns; `type` (`EVENT`, `PROMOTION`, `GENERAL`); title; size-limited body; optional start/end and media file; audience definition from a fixed allowlist; status (`DRAFT`, `SCHEDULED`, `PUBLISHED`, `CANCELLED`, `EXPIRED`); publisher and audit timestamps. Publishing resolves only same-gym recipients and creates deduplicated notifications; audience filters never accept arbitrary cross-tenant IDs.

`MemberDevice` fields: common tenant columns; member; device identifier; platform; encrypted/provider-safe push token reference; notification consent; last-seen time; revoked time and reason. Constraints: composite member FK, gym/member/device uniqueness, and one active ownership binding per token fingerprint. Logout, member/gym suspension, unlinking, or consent withdrawal revokes delivery eligibility.

### AuditLog

Fields: common tenant columns; nullable global `actor_user_id`; nullable tenant `actor_membership_id`, `impersonator_membership_id`; `action`, `module`, `target_type`, `target_id UUID`; redacted `old_values JSONB`, `new_values JSONB`; `request_id`; nullable `ip_address`, `user_agent`; `outcome` (`SUCCESS`, `DENIED`, `ERROR`); `occurred_at`.

Constraints: composite membership FKs; append-only trigger rejects `UPDATE` and `DELETE`; runtime grants permit insert/select only as authorized. Tenant audit pages use `(gym_id, occurred_at DESC, id DESC)` so a gym-scoped keyset does not sort the table. Target lookup keeps `(gym_id, target_type, target_id, occurred_at DESC)`. Platform-only events belong in `PlatformAuditLog`, never in this tenant table with a nullable `gym_id`.

### RefreshSession (global)

Fields: `id`; `principal_type` (`STAFF`, `MEMBER`); `user_id`; immutable `selected_gym_id`; nullable `gym_membership_id`; nullable `member_id`; unique `token_hash`; `family_id UUID`; `issued_at`, `expires_at`; nullable `rotated_at`, `revoked_at`, `replaced_by_session_id`; `revocation_reason`; `ip_address`, `user_agent`, `device_id`; `created_at`, `updated_at`.

Constraints: exactly one selected principal context is present: `STAFF` requires `gym_membership_id IS NOT NULL AND member_id IS NULL`, while `MEMBER` requires `member_id IS NOT NULL AND gym_membership_id IS NULL`; expiry follows issue; revoked/rotated fields agree; replacement remains in the same family, user, principal type, and selected context. The composite foreign keys `(selected_gym_id, gym_membership_id)` and `(selected_gym_id, member_id)` protect physical gym selection. `refresh_replacement_context_trg` rejects a replacement outside that family and context. Token plaintext is never stored. Creation, refresh, and validation additionally verify authoritatively that a staff membership's `user_id`, or a member's `linked_user_id`, equals session `user_id`, that the selected principal is active, and that its current authorization version matches the token. Indexes include `(user_id, revoked_at, expires_at)`, `(family_id)`, `(selected_gym_id, gym_membership_id)` where present, and `(selected_gym_id, member_id)` where `member_id` is present.

`RefreshSession` is global control-plane security state, not tenant business data. It is never queried through tenant repositories, reports, exports, or ordinary tenant APIs; only the authentication/session service receives narrow access. Sessions are revoked and then retention-purged; user deletion is `RESTRICT` until purge/anonymization.

For browser sessions, Next.js is only the BFF credential boundary; Django remains the sole authority that inserts, rotates, links, revokes, and validates `RefreshSession`. Browser JavaScript never receives token values. The staff/web issuer and audience (`gym-platform-api`, `gym-platform`) and member audience (`gym-platform-member`) are stable startup-validated configuration identifiers, not secrets and not additional database columns or JWT claims.

### PlatformAuditLog (global)

Fields: `id`; `actor_user_id`; `action`; `module`; nullable `target_gym_id`, `target_type`, `target_id`; nullable `effective_user_id`, `effective_gym_id`; redacted `old_values JSONB`, `new_values JSONB`; `request_id`; nullable `ip_address`, `user_agent`; `outcome` (`SUCCESS`, `DENIED`, `ERROR`); `occurred_at`; `created_at`, `updated_at`.

Constraints: append-only trigger rejects `UPDATE` and `DELETE`; runtime grants allow insertion only through the platform audit service and bounded reads only with `platform.audit.view`; actor/effective user and gym FKs use `RESTRICT`; redaction and payload-size limits apply. Required indexes include `(occurred_at DESC)`, `(actor_user_id, occurred_at DESC)`, `(target_gym_id, occurred_at DESC)`, and `(request_id)`. This table has no tenant RLS and is separate from tenant `AuditLog`.

### CashSession and CashTransaction

Phase 4 increment 2 migrates `cashbook_cash_session` and `cashbook_cash_transaction`. `VOID` exists on the session status constraint, and no increment 2 command creates or transitions to it.

`CashSession` stores the gym, business date, uppercase register code (`^[A-Z0-9_-]{1,32}$`), currency copied from the gym, nonnegative opening cash, recomputed cash sales and cash expenses (negative when a correction lands on this session), signed manual net, expected closing, nullable actual closing and difference, difference reason, `OPEN`/`CLOSED`/`VOID`, the opening membership, and nullable closing membership and time. One `OPEN` session is allowed per gym and register. Expected closing is `opening_cash + cash_sales - cash_expenses + manual_net`. Difference is `actual_closing - expected_closing`. An open row has no closing evidence. A closed row requires it, and a nonzero difference requires a nonblank reason. Gym, register, business date, currency, opening cash, and opener are immutable after insert. Totals change only while the session is open, including the update that closes it. Closed and void rows are immutable. Runtime grants are `SELECT`, `INSERT`, and `UPDATE`, not `DELETE`.

`CashTransaction` is permanently append-only. It stores direction `IN`/`OUT`, source type `PAYMENT`/`EXPENSE`/`COMMISSION`/`MANUAL`/`REVERSAL`, nullable source id, positive amount, currency, business date, server `occurred_at`, description, reason, nullable reversal link, and the creating membership. Manual rows require a reason and no source. Reversal rows require a reversal link and a reason. Currency and business date must match the session. Runtime grants are `SELECT` and `INSERT` only. Composite foreign keys tie the session and both actor memberships, the movement to its session, the creator, and the reversal to the same gym. Both tables use forced RLS. A missing or wrong request gym context returns no rows and rejects writes.

Signed movement amount is the amount for `IN` and its negation for `OUT`. `PAYMENT` adds that signed amount to `cash_sales`. `EXPENSE` and `COMMISSION` add the negation, so an `OUT` increases `cash_expenses`. `MANUAL` adds the signed amount to `manual_net`. `REVERSAL` applies this row's direction to the original movement's source type. `PAYMENT`, `EXPENSE`, and `COMMISSION` require `source_id`. `expected_closing` remains `opening_cash + cash_sales - cash_expenses + manual_net`. A deferred constraint trigger on cash-transaction insert and cash-session update checks those stored totals and the formula at commit, so an inserted movement without the matching aggregate update fails instead of leaving the session stale.

`record_cash_movement` takes `SELECT ... FOR UPDATE` on the session itself and runs only inside an open transaction. It rechecks that the session is open and that currency and business date match. `mutate()` reloads the actor, gym, finance entitlement, and live permission in that business transaction, then repeats Reception own-session scope and the interim Owner-only adjustment rule after the lock. A movement racing a close either commits before the close and is included in the recomputed totals, or fails because the session is no longer open. No movement is written after close. A membership that holds protected Reception stays on its own sessions even with another role, and cannot adjust. Until an Owner-configurable policy exists, only protected Owner, and not Reception, may adjust.

### IdempotencyRecord

Phase 4 increment 1 migrates this table as `tenants_idempotency_record`. Cash-session, cash-transaction, payment, receipt, daily-sales, expense, salary-period, product-category, product, and stock-adjustment routes that require a key are published in later increments. Payment void, payment refund, receipt void, and manual expense void are published in Increment 8A. Increment 8B publishes salary-period void and full retail-sale void. A cash correction names an open destination session and does not reopen a closed original session. The partial-return route is published. Its immutable return tables and stock and payment guards are migrated. Category create, product create, product update, and stock adjustment follow the same command policy as salary and expense writes: each requires `Idempotency-Key`.

Fields: common tenant columns; nullable `actor_membership_id`; `http_method`; canonical `route`; `idempotency_key`; lowercase SHA-256 `request_hash`; `state` (`IN_PROGRESS`, `COMPLETED`, `FAILED`); nullable `response_status`, encrypted `response_body_ciphertext`, `response_body_bytes` (1 through 65536), `resource_type`, `resource_id`; nullable `locked_until`; `expires_at`; nullable `created_by_membership_id`.

Constraints: `UNIQUE NULLS NOT DISTINCT (gym_id, actor_membership_id, http_method, route, idempotency_key)`, so a null actor is the single system identity for that key; key format `^[A-Za-z0-9._:-]{16,128}$`; SHA-256 hex; `COMPLETED` and `FAILED` require status, ciphertext, and byte length, with `locked_until` null; `IN_PROGRESS` requires `locked_until` and no response evidence; `expires_at > created_at`. Composite actor and creator FKs. Tenant-leading indexes cover expiry and the active claim. Forced RLS. `gym_runtime` receives `SELECT`, `INSERT`, and `UPDATE` only.

The request hash binds gym, actor, method, canonical route, and the canonical JSON body. The same key and hash replay a completed or failed response only before `expires_at`. After that instant the row is not decrypted and every retry returns `409 IDEMPOTENCY_KEY_EXPIRED` until privileged cleanup removes it. A different hash on an unexpired row is `409 IDEMPOTENCY_KEY_REUSED`. A live claim is `409 IDEMPOTENCY_IN_PROGRESS`. A stale unexpired claim is reclaimed under the row lock. Business writes and the completion write commit together. `mutate()` is database-only. A marked finance-domain failure is stored as `FAILED` with HTTP 400–499 and the API error envelope for the live request. `COMPLETED` stores HTTP 200–299. Authentication, permission, throttle, and validation failures are not stored. A deadlock or other unexpected error releases the claim instead of caching it. Ciphertext uses `FINANCE_IDEMPOTENCY_ENCRYPTION_KEY`, which is not the MFA key, must match a Fernet token, and `octet_length(response_body_ciphertext) <= 87480` (the Fernet token size for 65,536 bytes of plaintext). Plaintext responses are at most 64 KiB, claims last 30 seconds, and stored responses expire after 24 hours. Expired rows may be hard-deleted only by a privileged maintenance identity. Posted finance, receipts, and expense evidence are not deleted on that path. With `ATOMIC_REQUESTS` enabled, an idempotent command view uses `non_atomic_requests` and the runner owns the business transaction. See [ADR 0009](./adr/0009-finance-foundation-defaults.md).

## 5. ERD

```text
GLOBAL
User --< GymSelectionTicket
User --< RefreshSession >-- (selected GymMembership XOR selected Member)
User --< PlatformAuditLog
Permission >--< SystemRoleTemplatePermission >-- SystemRoleTemplate
Feature >--< SubscriptionPlanFeature >-- SubscriptionPlan --< Gym

TENANT ROOT / AUTHORIZATION
Gym --< GymMembership >-- User
 |--1 GymSettings
 |          |--0..1 StaffProfile
 |          +--< GymMembershipRole >-- Role --< RolePermission >-- Permission
 |--< StaffInvitation --< StaffInvitationRole >-- Role
 +--< GymFeatureEntitlement >-- Feature

OPERATIONS
Gym --< Member --< Membership >-- MembershipPlan
 |       |         +--< Payment --< Receipt
 |       +--< CheckIn
 |       +--< MemberServicePackage --< ServiceSessionLedger >-- ServicePackage
 |       +--< PTBooking >-- StaffProfile
 |       +--< ClassBooking >-- ClassOccurrence >-- ClassSchedule
 |       +--< TrainerAssignment >-- StaffProfile
 |       +--< PTSale >-- StaffProfile --< PTCommission
 |
 +--< CheckInChallenge
 +--< ProductCategory --< Product --< StockMovement
 +--< RetailSale --< RetailSaleLine >-- Product
 |            +--< RetailReturn --< RetailReturnLine >-- RetailSaleLine
 +--< Expense >-- ExpenseCategory
 +--< SalaryPeriod --< StaffSalaryLine >-- StaffProfile
 +--< CashSession --< CashTransaction
 +--< TenantSequence
 +--< FileAsset
 +--< Announcement
 +--< MemberDevice
 +--< Notification
 +--< AuditLog
 +--< IdempotencyRecord
```

Every tenant edge above is physically protected by a composite FK containing `gym_id`; the diagram omits those repeated columns for readability.

## 6. Cross-tenant integrity and RLS

Example composite protection:

```sql
ALTER TABLE memberships_membership
  ADD CONSTRAINT membership_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id)
  ON DELETE RESTRICT;
```

Explicit reversible `RunSQL` migrations are required where the Django version cannot express composite FKs, exclusion constraints, forced RLS, or immutability triggers.

The RLS helper is defined explicitly and reads only the canonical transaction-local setting:

```sql
CREATE OR REPLACE FUNCTION app_current_gym_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('app.gym_id', true), '')::uuid
$$;
```

The migration owner owns this function; the runtime role may execute but not replace it. A missing setting returns `NULL` and therefore matches no tenant row; malformed values are rejected rather than selecting a default.

Seven `SECURITY DEFINER` functions set `row_security=off` and are owned by `gym_auth_bootstrap` (`NOLOGIN NOINHERIT BYPASSRLS`): `app_staff_memberships()`, `app_linked_members()`, `app_owner_invitation(text)`, `app_staff_invitation(text)`, `enforce_invitation_role_owner_boundary()`, `app_protect_owner_role_permissions()`, and `app_protect_membership_role_owner()`. Each locks `search_path` to `pg_catalog, public` and contains no dynamic SQL. That role may `SELECT` `tenants_gym`, `tenants_gym_membership`, `tenants_staff_invitation`, `tenants_staff_invitation_role`, `tenants_role`, `tenants_permission`, `tenants_system_role_template`, and `tenants_system_role_template_permission`. On `members_member` it may select only `id`, `gym_id`, `linked_user_id`, `status`, and `authz_version`. It has no insert, update, or delete grant. `PUBLIC` cannot execute the functions. Runtime may execute the membership, linked-member, and invitation lookups. The three integrity functions are triggers and are not directly executable. Runtime has no table-level `INSERT` or `UPDATE` on `users_user`. It may update only `mfa_enabled`, `mfa_secret_encrypted`, `mfa_last_used_step`, and `updated_at` directly. `app_create_invited_identity(text, text, text, text)` creates an ordinary unusable-password identity, `app_accept_invitation_password(text, text, timestamptz)` performs the one invitation password transition and accepts only the current `pbkdf2_sha256` encoding, and `app_confirm_invited_profile(text, text, text, text, timestamptz)` confirms that invited user's name and phone. Insert and update triggers reject direct runtime writes outside those boundaries. These functions are not `row_security=off` and are not owned by `gym_auth_bootstrap`.

For every tenant table listed in section 2, migrations execute:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

CREATE POLICY <table>_tenant_select ON <table>
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY <table>_tenant_insert ON <table>
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY <table>_tenant_update ON <table>
  FOR UPDATE
  USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY <table>_tenant_delete ON <table>
  FOR DELETE USING (gym_id = app_current_gym_id());
```

For staff, `app.gym_id` is established only after global user/session validation and authoritative selection of an active `GymMembership`. For members, global user/session validation first calls `app_linked_members()`. That function can return only `member_id`, `gym_id`, and `authz_version` for active members whose `linked_user_id` equals `app.user_id` and whose gym is active with a trial or active subscription. A missing or malformed `app.user_id` returns no row. The transaction then sets `app.gym_id` before ordinary tenant queries and reloads the claimed member under normal RLS. Login continues only when the function returns exactly one row. Absence or invalid context fails closed. Authorization and staff/member object scope still apply above RLS. Delete policies do not grant business deletion: table grants, state rules, FKs, and triggers restrict it.

RLS policy inventory:

- Standard read/write policy: mutable tenant master/operational tables (`GymSettings`, `GymMembership`, `StaffInvitation`, `StaffInvitationRole`, `Role`, joins, entitlement, staff, member, plan, membership, check-in, category, assignment, salary line while draft, file, notification, idempotency).
- Insert/select plus constrained transition procedures: `Payment`, `Receipt`, `Expense`, `SalaryPeriod`, `PTSale`, `PTCommission`, `CashSession`.
- Insert/select only for runtime: `CashTransaction`, `AuditLog`; maintenance deletion runs as a separately controlled owner role where retention permits.
- `TenantSequence` allocation is `SELECT … FOR UPDATE` in the caller's transaction. A trigger allows only `next_value + 1` and `updated_at`. Runtime column grants match that rule. There is no `SECURITY DEFINER` sequence allocator.

`GymSelectionTicket`, `RefreshSession`, and `PlatformAuditLog` are global control-plane tables and are excluded from tenant RLS inventory. Their restricted authentication-service grants and ticket/session/audit invariants are checked separately in CI.

The application runtime role must not own tables, have `BYPASSRLS`, set arbitrary tenant context outside the trusted transaction wrapper, or call maintenance functions. CI compares the tenant inventory to database metadata and fails if any tenant table lacks `gym_id NOT NULL`, `UNIQUE (gym_id,id)`, enabled/forced RLS, complete `USING`/`WITH CHECK` policies, or tenant-leading indexes.

## 7. Index contract

Every FK has an index. Every tenant operational index starts with `gym_id`; global uniqueness never substitutes for tenant-leading access paths. Required initial indexes include:

- `(user_id, expires_at)` and `(expires_at)` on global gym-selection tickets, in addition to unique ticket hash;
- `(gym_id, status, created_at DESC)` on stateful entities;
- `(gym_id, normalized_email, status)` and `(gym_id, expires_at)` on staff invitations;
- `(gym_id, member_id, status)` on memberships/payments and `(gym_id, member_id, check_in_at DESC)` on check-ins;
- `payment_gym_member_hist_idx` on `(gym_id, member_id, received_at DESC, id DESC)` for published member payment history, replacing the redundant `(gym_id, member_id)` index so the ordered page does not sort the member's full history;
- `(gym_id, business_date, method)` on payments and `(gym_id, business_date, status)` on expenses/cash sessions;
- `(gym_id, trainer_staff_profile_id, service_date)` on PT sales;
- `(gym_id, member_id, status, effective_from, effective_until)` on trainer assignments for the derived PT badge;
- `(gym_id, status, outstanding_amount)` on PT commissions;
- `(gym_id, status, period_start, period_end)` on salary periods and `(gym_id, salary_period_id, staff_profile_id)` on salary lines;
- `(gym_id, scheduled_at, status)` on notifications;
- `(gym_id, purpose)`, `(gym_id, owner_type, owner_id)`, `(gym_id, scan_status)`, `(gym_id, retention_status)`, and `(gym_id, uploaded_by_membership_id)` on file assets; object-key access uses unique `(gym_id, object_key)` and has no index led by `object_key`;
- `(gym_id, target_type, target_id, occurred_at DESC)` on audit logs;
- partial indexes for active/non-archived records and open cash sessions.

Additional trigram/full-text indexes require measured query behavior and `EXPLAIN (ANALYZE, BUFFERS)` evidence.

## 8. Transaction and concurrency invariants

- **Tenant context:** each request uses `transaction.atomic()` and transaction-local `app.gym_id`, `app.user_id`, and `app.request_id`; pooled connections must not retain tenant state.
- **Receipt allocation:** lock exactly one `TenantSequence` row, increment, insert receipt, then commit. A unique-constraint retry handles only serialization/deadlock errors, never generates via `MAX`.
- **Payments/refunds:** lock the original payment and affected membership/PT sale. Posting, refund totals, membership payment status, receipt, and cash transaction are one transaction. Idempotency is claimed before side effects. Concurrent refunds serialize on the original.
- **Finance approval:** submitting a draft freezes its business fields. Approval locks the pending record, revalidates source and cash-session state, proves the approver differs from creator/submitter, and performs all posting side effects once. Rejection records a reason and produces no receipt, settlement, commission, expense, or cash movement.
- **Cash:** lock the open `CashSession` before appending a transaction or closing. Recompute totals from non-void append-only transactions while locked; do not trust client totals. Closing and final totals commit together. Reception access first uses normal same-gym Django scope and forced RLS, then requires `opened_by_membership_id` to equal the current Reception `GymMembership`; this condition allows own-session view/operation/reconcile/close but never adjustment, approval, or posting. Another receptionist's session and every cross-gym session are denied. Closed sessions and their transactions remain immutable.
- **Memberships:** lock the member before create, suspend, resume, or cancel. The exclusion constraint, including suspended rows, is the final guard against concurrent overlap. Sale snapshots and cancellation evidence stay immutable after their approved write. Derive payment status from posted charges minus posted refunds; do not accept it from clients. This sale slice does not post a payment.
- **PT commission:** lock PT sale and commission. Sale posting creates exactly one commission; payout posts its expense/cash transaction and updates totals atomically. Unique constraints make retries safe.
- **Salary:** lock the pending salary period and all lines. Approval creates exactly one `STAFF_SALARY` expense per line and corresponding cash movement where required, links every line, and posts the period atomically; any failure rolls back the whole period.
- **Authorization:** role/permission changes lock the membership and increment `authz_version` in the same transaction so existing tokens can be invalidated.
- **Multi-gym login selection:** create a ticket only after password/MFA success and an authoritative bootstrap query returns multiple active memberships. Lock the global ticket for every selection attempt; verify hash, user, expiry, state, attempt bound, immutable eligible-membership snapshot, and current membership activity before creating any selected session. Consumption and normal `RefreshSession` creation commit together. Concurrent or replayed selection yields at most one session, and terminal failure revokes without installing tenant context.
- **Member authorization:** identity linking, member suspension/archive, and member self-service authorization changes lock the member and increment `Member.authz_version` in the same transaction so existing member tokens can be invalidated.
- **Profile photo:** lock the owning member and then the `FileAsset`. Intent, the complete claim, each complete finalize, and delete use that order. Each of those steps is its own committed transaction. Complete, download-url, and delete must not hold a database transaction or row lock across MinIO, ClamAV, image decode, or re-encode. A `SCANNING` claim at least 120 seconds old may be replaced; finalize still requires the current `scan_attempt_id` and `retention_status = QUARANTINED`, as specified in `API.md`.
- **Deadlocks:** acquire locks in stable order: `Gym`/sequence, member or original financial record, session, then dependent rows. Retry boundedly only for PostgreSQL serialization failures/deadlocks.

## 9. Deletion, immutability, and retention

| Record class                                                       | Rule                                                                                                                                                                 |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gym                                                                | Never hard-delete after provisioning; suspend/close                                                                                                                  |
| User                                                               | Deactivate/anonymize eligible PII; preserve referenced evidence                                                                                                      |
| Memberships, roles, staff, members, plans, categories, assignments | Archive/end/revoke after use; hard-delete only unused drafts/configuration with no references                                                                        |
| Draft/pending finance and salary                                   | Draft may be cancelled; pending may be rejected with reason; hard deletion is limited to privileged cleanup before submission and before any dependent record exists |
| Posted payment/expense/salary/PT/cash/receipt                      | No update/delete of business fields; void/refund/reverse with linked records, actor, reason, and time                                                                |
| Audit log                                                          | Append-only; no runtime update/delete                                                                                                                                |
| Staff invitation                                                   | Retain terminal acceptance/revocation/expiry evidence; plaintext token is never stored                                                                               |
| Gym selection ticket                                               | Retain expired/consumed/revoked hash-only state for 30 days, then privileged hard-delete; never retain plaintext                                                     |
| Refresh/idempotency/notification                                   | Retention purge after expiry and legal/operational window                                                                                                            |
| File asset                                                         | Private object lifecycle; SQL row remains; `DELETED` keeps metadata and checksums; remove the object on delete, rejection, or 24-hour abandonment                    |

FK defaults are `ON DELETE RESTRICT`. `SET NULL` is allowed only for optional global actor attribution after approved anonymization. `CASCADE` is limited to unreferenced global provisioning-template joins and is not used for tenant operational or financial records. Immutability triggers protect posted/closed/issued rows from bulk SQL paths as well as application code.

Retention periods, tax receipt rules, privacy erasure, and statutory rounding exceptions must be confirmed for the operating jurisdiction before production; until then the more conservative retention and `ROUND_HALF_UP` rules above apply.

## 10. Migration and verification rules

- A dedicated migration owner applies schema, RLS, policies, triggers, and constraints before runtime rollout; the runtime role never owns objects.
- Provisioning creates Gym, owner membership, copied roles/permissions, feature entitlements, the canonical protected expense-category catalog, approval-policy default, and sequences in one idempotent transaction.
- Migrations containing composite FKs or RLS include reversible SQL and database-level tests.
- Large backfills are resumable, tenant-batched, and leave forced RLS intact or run through a separately audited maintenance role.
- Destructive changes use expand/migrate/contract deployment with lock-impact, rollback, and data-verification plans.
- Phase 0 tests exercise cross-gym FK rejection, RLS read/write isolation, missing-context fail-closed behavior, gym-selection ticket hash-only storage/constraints/5-minute expiry/single-use/concurrency/retention, posted-row immutability, sequence concurrency, refund limits, cash close races, membership overlap, and commission payout races.

An outbox is intentionally deferred until measured asynchronous delivery/reliability need justifies it. If introduced, every outbox record carrying tenant work will be gym-owned, include `gym_id`, use composite tenant references, and be forced-RLS protected.

Check-in credentials are stored on `members_check_in_device` as a selector plus a one-way hash, under forced RLS. `kind` limits each credential to display issuance, NFC ingestion, or biometric-result ingestion. No biometric template or vendor secret is stored.

## 11. Compatibility and accepted decisions

The approved implementation pins are Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. Database compatibility is established against PostgreSQL 18.6 with the selected Python driver, Django migrations, composite constraints, transaction-local `set_config`, forced RLS, lock, and rollback tests; version labels alone are not evidence.

[ADR 0001](./adr/0001-single-vps-production-topology.md) accepts the single-VPS topology that the retention, backup, and restore rules in sections 9 and 10 depend on. [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md) governs the BFF/session lifecycle, including the global `GymSelectionTicket` foundation, without changing JWT claims. [ADR 0003](./adr/0003-reception-cash-session-authority.md) governs the object condition already supported by `CashSession.opened_by_membership_id`. MFA is TOTP, mandatory for platform administrators and Owners, with recent step-up for protected owner operations, encrypted secrets, and replay protection. Broader role-based MFA may be decided later. Email uses Django's configurable email backend. Tests and end-to-end runs use non-delivery backends. Production must configure an approved transactional provider before invitation, password-recovery, or Phase 2 expiry delivery is production-ready; vendor selection and that deployment configuration remain deferred. Jurisdiction and general retention must be resolved before production. Display-device persistence is accepted in [ADR 0007](./adr/0007-display-device-credentials.md). NFC and biometric ingestion is accepted in [ADR 0008](./adr/0008-device-ingestion.md). Vendor-specific reader software remains optional, and Expo versus bare React Native remains Phase 7.
