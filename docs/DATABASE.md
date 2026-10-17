# Phase 0 Authoritative Database Design

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`API.md`](./API.md), [`SECURITY.md`](./SECURITY.md), [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

This document is the Phase 0 schema contract. PostgreSQL is authoritative; Django models, migrations, services, APIs, tests, and fixtures must conform to it. Reports and dashboards are query/service outputs, never mutable report tables in Phase 0.

The migrated schema includes the Phase 1A foundation (identity, tenancy, authorization, sessions, audit, and RLS), the Phase 1B provisioning, settings, entitlement, staff, platform-audit, MFA enrollment and challenge, and `files_file_asset` tables, and the earlier `Member`, `MembershipPlan`, `Membership`, `Payment`, `Receipt`, and `Notification` tables. Check-in, the remaining finance and inventory tables, PT and classes, announcements, devices, and idempotency records in the inventory below remain contract-only until their phases. The MembershipPlan HTTP API and the Member list, create, detail, update, and archive API are published. Member summary and history, membership, notification, payment, and receipt HTTP endpoints remain unpublished.

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

`GymSettings`, `GymMembership`, `StaffInvitation`, `StaffInvitationRole`, `Role`, `RolePermission`, `GymMembershipRole`, `GymFeatureEntitlement`, `StaffProfile`, `Member`, `MembershipPlan`, `Membership`, `CheckIn`, `CheckInChallenge`, `Payment`, `Receipt`, `ExpenseCategory`, `Expense`, `SalaryPeriod`, `StaffSalaryLine`, `TrainerAssignment`, `ServicePackage`, `MemberServicePackage`, `ServiceSessionLedger`, `ClassType`, `ClassSchedule`, `ClassOccurrence`, `ClassBooking`, `PTBooking`, `PTSale`, `PTCommission`, `ProductCategory`, `Product`, `StockMovement`, `RetailSale`, `RetailSaleLine`, `CashSession`, `CashTransaction`, `TenantSequence`, `Announcement`, `MemberDevice`, `FileAsset`, `Notification`, `AuditLog`, and `IdempotencyRecord`.

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

Fields: common tenant columns; `member_id`, `plan_id`; immutable snapshots `plan_code`, `plan_name`, `duration_value`, `duration_unit`; `start_date`, `expiry_date`; `list_price`, `discount_amount`, `final_amount`, `currency`; `payment_status` (`UNPAID`, `PARTIAL`, `PAID`, `PARTIALLY_REFUNDED`, `REFUNDED`); lifecycle `status` (`PENDING`, `ACTIVE`, `EXPIRED`, `SUSPENDED`, `CANCELLED`); nullable `cancelled_at`, `cancelled_by_membership_id`, `cancellation_reason`; `created_by_membership_id`.

Constraints: composite member, plan, creator, and canceller FKs; `expiry_date >= start_date`; all amounts nonnegative; `discount_amount <= list_price`; exact check `final_amount = list_price - discount_amount`; status and cancellation fields agree. Expiry is calculated once with calendar-aware arithmetic in the gym timezone. Only one active overlapping membership is allowed per member in Phase 0: an exclusion constraint on `(gym_id, member_id, daterange(start_date, expiry_date, '[]'))` where status is `PENDING` or `ACTIVE`.

### CheckIn

Fields: common tenant columns; `member_id`; nullable `membership_id`; `check_in_at`; nullable `check_out_at`; `method` (`MANUAL`, `QR`, `NFC`, `BIOMETRIC`); nullable `recorded_by_membership_id`; nullable `device_id`, `source_metadata JSONB`; nullable `idempotency_key`; `created_by_membership_id` may equal recorder.

Constraints: composite FKs to all tenant parents; checkout is after check-in; `UNIQUE (gym_id, idempotency_key)` when nonnull; source JSON is size-limited and contains no biometric template. Index `(gym_id, member_id, check_in_at DESC)`. Duplicate-window policy is enforced in the service under a member lock, not by unreliable timestamp rounding.

### CheckInChallenge

Fields: common tenant columns; cryptographic `challenge_hash`; `display_location`; `issued_at`; `expires_at`; nullable `consumed_at`; bounded `use_count`, `max_uses`; nullable `issued_by_membership_id`, `display_device_id`; `status` (`ACTIVE`, `CONSUMED`, `EXPIRED`, `REVOKED`).

Constraints: challenge plaintext is never stored; hash is unique and immutable; expiry is short and follows issue; use count is bounded; terminal states agree with timestamps; issuer/device references are same-gym composite FKs. A mobile scan supplies the challenge only alongside an authenticated same-gym member or authorized trainer principal. Challenge verification, principal/self scope, membership eligibility, duplicate-window locking, bounded use consumption, and `CheckIn` insertion occur atomically. A challenge from Gym A cannot create, count, or infer attendance in Gym B.

### Payment

Fields: common tenant columns; nullable `member_id`, `membership_id`, `member_service_package_id`, `pt_sale_id`, `retail_sale_id`; `purpose` (`MEMBERSHIP`, `PT`, `CLASS`, `PRODUCT`, `OTHER`); `entry_type` (`CHARGE`, `REFUND`, `REVERSAL`); `description`; positive `amount`; `currency`; `method` (`CASH`, `BANK_TRANSFER`, `CARD`, `MOBILE_PAYMENT`, `OTHER`); `business_date`; `received_at`; nullable external `reference_number`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `VOID`, `CANCELLED`); nullable `original_payment_id`, `reversal_payment_id`, `reason`, `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `voided_at`, `cancelled_at`, `cancelled_by_membership_id`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`.

Constraints: all tenant FKs are composite; amount is greater than zero; currency equals gym currency; `CHARGE` has no original, while `REFUND`/`REVERSAL` require an original posted charge; an entry cannot reference itself; one reversal per original; cash method requires a generated cash transaction when posted. `DRAFT` is creator-editable; submission freezes business fields; `PENDING_APPROVAL` has submitter evidence; `POSTED` has a different approver/poster and approval evidence with `posted_by_membership_id = approved_by_membership_id`; `REJECTED` has rejection actor/time/reason and no receipt, settlement, or cash effect; an unsubmitted draft may become `CANCELLED` only with cancellation actor/time/reason. The creator or submitter cannot approve or reject the same record. A posted charge may be voided only by atomically creating a posted `REVERSAL`; customer refunds are posted `REFUND` rows. Sum of posted, non-void refund amounts for an original cannot exceed its posted charge amount. Raw card/PIN data is never stored.

### Receipt

Fields: common tenant columns; `payment_id`; `receipt_number`; `sequence_key`, `sequence_value BIGINT`, `sequence_year`; `issued_at`; immutable snapshots of gym code/name/address, member number/name, description, amount, currency, method, business date, and issuer name; `status` (`ISSUED`, `VOID`, `REPLACED`); nullable `voided_at`, `voided_by_membership_id`, `void_reason`, `replacement_receipt_id`, `rendered_file_asset_id`; `created_by_membership_id`.

Constraints: `UNIQUE (gym_id, receipt_number)` and `UNIQUE (gym_id, sequence_key, sequence_value)`; one partial unique issued receipt per payment; receipt requires a posted payment; amount/currency equal payment; void/replacement fields agree with state; replacement points to a receipt in the same gym. Receipt numbers are never reused, changed, or deleted.

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

Protected `system_key` and code values are immutable and unique per gym. Protected categories cannot be deleted; display names may be localized and unused manual categories may be archived. Clients cannot manually post into `STAFF_SALARY` or `PT_COMMISSION`.

`Expense` fields: common tenant columns; `category_id`; `business_date`; `description`; positive `amount`; `currency`; `payment_method`; nullable `receipt_file_asset_id`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `VOID`, `CANCELLED`); nullable `reversal_expense_id`, `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `voided_at`, `cancelled_at`, `cancelled_by_membership_id`, `reason`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`.

Constraints: composite category/file/actor FKs; amount positive; currency equals gym currency; an unsubmitted draft may become `CANCELLED` only with cancellation actor/time/reason; submission freezes business fields; approval requires a different actor from creator/submitter, sets `posted_by_membership_id = approved_by_membership_id`, and atomically posts the expense; rejection requires a different actor, records actor/time/reason, and creates no cash movement. A posted expense is voided only with an equal and opposite linked reversal expense; one reversal per original. A posted cash expense atomically creates its cash transaction.

### SalaryPeriod and StaffSalaryLine

`SalaryPeriod` fields: common tenant columns; `period_start`, `period_end`; `business_date`; `description`; `status` (`DRAFT`, `PENDING_APPROVAL`, `POSTED`, `REJECTED`, `CANCELLED`, `VOID`); nullable `submitted_at`, `submitted_by_membership_id`, `approved_at`, `approved_by_membership_id`, `rejected_at`, `rejected_by_membership_id`, `rejection_reason`, `posted_at`, `cancelled_at`, `cancelled_by_membership_id`, `voided_at`, `reason`; `created_by_membership_id`, `updated_by_membership_id`; nullable `posted_by_membership_id`.

`StaffSalaryLine` fields: common tenant columns; `salary_period_id`; `staff_profile_id`; positive manually entered `amount`; `currency`; `payment_method` (`CASH`, `BANK_TRANSFER`, `CARD`, `MOBILE_PAYMENT`, `OTHER`); nullable `external_reference`, `cash_session_id`, `expense_id`; `description`; `created_by_membership_id`, `updated_by_membership_id`.

Constraints: periods use gym-local dates with `period_end >= period_start`; `UNIQUE (gym_id, salary_period_id, staff_profile_id)`; all tenant FKs are composite; amount is positive and currency equals gym currency. Lines are editable only while their period is `DRAFT`; no fixed salary or PT amount is inferred from `StaffProfile`. An unsubmitted draft may become `CANCELLED` with actor/time/reason. Submission freezes the period and all lines. Approval requires a different actor from creator/submitter, sets `posted_by_membership_id = approved_by_membership_id`, and atomically posts one immutable `Expense` per line under protected `STAFF_SALARY`, plus a cash transaction for each cash line, then links `expense_id`. Rejection also requires a different actor and has no expense/cash effect. Voiding a posted period reverses every linked salary expense and cash movement while preserving the original period and lines.

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

Constraints: all tenant FKs are composite; SKU and barcode uniqueness are per gym; quantities and prices are exact decimals; `line_revenue = quantity * unit_price - discount`, `cost_of_goods_sold = quantity * unit_cost_snapshot`, and `gross_profit = line_revenue - cost_of_goods_sold`. Posting locks every product in deterministic order, rejects negative resulting stock unless an explicit future backorder policy is approved, creates stock movements and payment/receipt/cash effects atomically, and is idempotent. Posted sale facts and cost snapshots are immutable; returns/voids create linked reversing movements and financial records.

### CashSession and CashTransaction

`CashSession` fields: common tenant columns; `business_date`; `register_code`; `opening_cash`; derived `cash_sales`, `cash_expenses`, `manual_net`, `expected_closing`; nullable `actual_closing`, `difference`, `difference_reason`, `closed_at`, `closed_by_membership_id`; `status` (`OPEN`, `CLOSED`, `VOID`); `opened_by_membership_id`; `created_by_membership_id`.

Constraints: money nonnegative except signed `manual_net` and `difference`; one partial unique open session per `(gym_id, register_code)`; exact `expected_closing = opening_cash + cash_sales - cash_expenses + manual_net`; when closed, actual and difference are required and `difference = actual_closing - expected_closing`; nonzero difference requires a reason.

`CashTransaction` fields: common tenant columns; `cash_session_id`; `direction` (`IN`, `OUT`); `source_type` (`PAYMENT`, `EXPENSE`, `COMMISSION`, `MANUAL`, `REVERSAL`); nullable `source_id`; positive `amount`; `currency`; `occurred_at`; `business_date`; `description`; nullable `reversal_transaction_id`, `reason`; `created_by_membership_id`.

Constraints: composite session/actor/reversal FKs; amount positive; currency equals gym/session currency; source uniqueness `(gym_id, source_type, source_id)` when source is nonnull; one reversal per transaction. Cash transactions are append-only. Manual entries require a reason and elevated permission. Closed sessions accept no transactions; correction is a transaction in a new open session referencing the original, never reopening/editing a closed session.

### TenantSequence

Fields: common tenant columns; `sequence_key` (for example `RECEIPT:2026`); `prefix`; `next_value BIGINT`; `padding SMALLINT`; `created_by_membership_id` nullable only for provisioning.

Constraints: `UNIQUE (gym_id, sequence_key)`; next value positive; padding in `1..18`; prefix format validated. Receipt allocation locks this row with `SELECT ... FOR UPDATE`, consumes `next_value`, increments it, and inserts the receipt in the same transaction:

```text
{gym.code}-{year}-{sequence:06d}
W1-2026-000001
```

Transaction rollback does not consume a number; once committed, a number is never reused, including after void. Do not use `MAX(receipt_number) + 1`.

### FileAsset

Fields: common tenant columns; opaque `object_key`; `original_name`; detected `content_type`; `byte_size BIGINT`; `sha256`; `purpose`; `scan_status` (`PENDING`, `CLEAN`, `REJECTED`, `ERROR`); nullable `owner_type`, `owner_id`; `uploaded_by_membership_id`; `retention_status` (`ACTIVE`, `QUARANTINED`, `DELETE_PENDING`, `DELETED`); nullable `deleted_at`.

Constraints: `UNIQUE (gym_id, object_key)`; byte size nonnegative; SHA-256 format valid; composite uploader FK; owner is an opaque association and must be service-validated under the same gym. Object keys include the gym UUID plus a random UUID so they remain globally collision-resistant without a non-tenant-leading database index. The bucket is private. Physical deletion follows retention policy; metadata remains when needed for audit/finance.

Phase 1B persists this table as `files_file_asset` with `UNIQUE (gym_id, id)`, the checks above, a database check that `object_key` is `{gym_id}/{random_uuid}`, forced RLS using `app_current_gym_id()` for select, insert, update, and delete, and runtime grants for `SELECT`, `INSERT`, and `UPDATE` only. Runtime `DELETE` is not granted. `deleted_at` is required exactly when `retention_status` is `DELETED`; `DELETE_PENDING` and the other non-deleted states keep `deleted_at` null. `owner_type`/`owner_id` stay an opaque pair with no foreign key. `StaffProfile.profile_photo_asset_id` is not a cross-app foreign key: the staff API does not accept or return it, and a future service must prove the asset is same-gym and `CLEAN` before attaching it. Upload, download, signed-URL, and storage-provider APIs are deferred.

### Notification

Fields: common tenant columns; nullable global `recipient_user_id`; nullable tenant `recipient_member_id`; `type`; `channel` (`IN_APP`, `EMAIL`, `SMS`, `PUSH`); `template_key`, `template_version`; size-limited safe `payload JSONB`; `scheduled_at`; nullable `sent_at`, `failed_at`; `status` (`PENDING`, `SENT`, `FAILED`, `CANCELLED`); nullable `provider_id`; `deduplication_key`; `attempt_count`; `created_by_membership_id` nullable for system-generated notices.

Constraints: exactly one recipient is present; composite member FK; `UNIQUE (gym_id, channel, deduplication_key)`; nonnegative attempts; terminal timestamps agree with state. No secrets or unnecessary personal data in payload.

Membership-expiry email uses `type = MEMBERSHIP_EXPIRY`, `channel = EMAIL`, a versioned template, and the member as recipient. Before creation and again before delivery, the service verifies that the membership/member/gym are active as applicable, the configured warning day matches the gym-local date, expiry email is enabled, and `Member.email` remains nonblank. The destination address is resolved from the current normalized member email at send time; it is not accepted from a scheduled payload. The payload contains only member display name, snapshotted plan display name, expiry date, and gym contact/display fields required by the template. The deduplication key is deterministic per `(membership_id, expiry_date, warning_day, template_version)`, so retries or repeated scheduler runs cannot send the same warning twice. A member without email produces no `Notification` row and no failure alert.

`Announcement` fields: common tenant columns; `type` (`EVENT`, `PROMOTION`, `GENERAL`); title; size-limited body; optional start/end and media file; audience definition from a fixed allowlist; status (`DRAFT`, `SCHEDULED`, `PUBLISHED`, `CANCELLED`, `EXPIRED`); publisher and audit timestamps. Publishing resolves only same-gym recipients and creates deduplicated notifications; audience filters never accept arbitrary cross-tenant IDs.

`MemberDevice` fields: common tenant columns; member; device identifier; platform; encrypted/provider-safe push token reference; notification consent; last-seen time; revoked time and reason. Constraints: composite member FK, gym/member/device uniqueness, and one active ownership binding per token fingerprint. Logout, member/gym suspension, unlinking, or consent withdrawal revokes delivery eligibility.

### AuditLog

Fields: common tenant columns; nullable global `actor_user_id`; nullable tenant `actor_membership_id`, `impersonator_membership_id`; `action`, `module`, `target_type`, `target_id UUID`; redacted `old_values JSONB`, `new_values JSONB`; `request_id`; nullable `ip_address`, `user_agent`; `outcome` (`SUCCESS`, `DENIED`, `ERROR`); `occurred_at`.

Constraints: composite membership FKs; append-only trigger rejects `UPDATE` and `DELETE`; runtime grants permit insert/select only as authorized. Tenant audit pages use `(gym_id, occurred_at DESC, id DESC)` so a gym-scoped keyset does not sort the table. Target lookup keeps `(gym_id, target_type, target_id, occurred_at DESC)`. Platform-only events belong in `PlatformAuditLog`, never in this tenant table with a nullable `gym_id`.

### RefreshSession (global)

Fields: `id`; `principal_type` (`STAFF`, `MEMBER`); `user_id`; immutable `selected_gym_id`; nullable `gym_membership_id`; nullable `member_id`; unique `token_hash`; `family_id UUID`; `issued_at`, `expires_at`; nullable `rotated_at`, `revoked_at`, `replaced_by_session_id`; `revocation_reason`; `ip_address`, `user_agent`, `device_id`; `created_at`, `updated_at`.

Constraints: exactly one selected principal context is present: `STAFF` requires `gym_membership_id IS NOT NULL AND member_id IS NULL`, while `MEMBER` requires `member_id IS NOT NULL AND gym_membership_id IS NULL`; expiry follows issue; revoked/rotated fields agree; replacement remains in the same family, user, principal type, and selected context; token plaintext is never stored. Composite FKs `(selected_gym_id, gym_membership_id)` and `(selected_gym_id, member_id)` protect physical gym selection. Creation, refresh, and validation additionally verify authoritatively that a staff membership's `user_id`, or a member's `linked_user_id`, equals session `user_id`, that the selected principal is active, and that its current authorization version matches the token. Indexes include `(user_id, revoked_at, expires_at)`, `(family_id)`, `(selected_gym_id, gym_membership_id)` where present, and `(selected_gym_id, member_id)` where present.

`RefreshSession` is global control-plane security state, not tenant business data. It is never queried through tenant repositories, reports, exports, or ordinary tenant APIs; only the authentication/session service receives narrow access. Sessions are revoked and then retention-purged; user deletion is `RESTRICT` until purge/anonymization.

For browser sessions, Next.js is only the BFF credential boundary; Django remains the sole authority that inserts, rotates, links, revokes, and validates `RefreshSession`. Browser JavaScript never receives token values. The staff/web issuer and audience (`gym-platform-api`, `gym-platform`) and member audience (`gym-platform-member`) are stable startup-validated configuration identifiers, not secrets and not additional database columns or JWT claims.

### PlatformAuditLog (global)

Fields: `id`; `actor_user_id`; `action`; `module`; nullable `target_gym_id`, `target_type`, `target_id`; nullable `effective_user_id`, `effective_gym_id`; redacted `old_values JSONB`, `new_values JSONB`; `request_id`; nullable `ip_address`, `user_agent`; `outcome` (`SUCCESS`, `DENIED`, `ERROR`); `occurred_at`; `created_at`, `updated_at`.

Constraints: append-only trigger rejects `UPDATE` and `DELETE`; runtime grants allow insertion only through the platform audit service and bounded reads only with `platform.audit.view`; actor/effective user and gym FKs use `RESTRICT`; redaction and payload-size limits apply. Required indexes include `(occurred_at DESC)`, `(actor_user_id, occurred_at DESC)`, `(target_gym_id, occurred_at DESC)`, and `(request_id)`. This table has no tenant RLS and is separate from tenant `AuditLog`.

### IdempotencyRecord

Fields: common tenant columns; nullable `actor_membership_id`; `http_method`; canonical `route`; `idempotency_key`; `request_hash`; `state` (`IN_PROGRESS`, `COMPLETED`, `FAILED`); nullable `response_status`, encrypted/size-limited `response_body`, `resource_type`, `resource_id`; `locked_until`, `expires_at`; `created_by_membership_id` nullable for service actors.

Constraints: `UNIQUE (gym_id, actor_membership_id, http_method, route, idempotency_key)` with a documented sentinel actor for system requests; same key with a different request hash is rejected; expiry follows creation; completed response is replayed. Composite actor FK. Expired rows may be hard-deleted by a privileged maintenance role.

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

Six `SECURITY DEFINER` functions set `row_security=off` and are owned by `gym_auth_bootstrap` (`NOLOGIN NOINHERIT BYPASSRLS`): `app_staff_memberships()`, `app_owner_invitation(text)`, `app_staff_invitation(text)`, `enforce_invitation_role_owner_boundary()`, `app_protect_owner_role_permissions()`, and `app_protect_membership_role_owner()`. Each locks `search_path` to `pg_catalog, public` and contains no dynamic SQL. That role may `SELECT` only `tenants_gym`, `tenants_gym_membership`, `tenants_staff_invitation`, `tenants_staff_invitation_role`, `tenants_role`, `tenants_permission`, `tenants_system_role_template`, and `tenants_system_role_template_permission`. It has no insert, update, or delete grant. `PUBLIC` cannot execute the functions. Runtime may execute the membership and invitation lookups. The three integrity functions are triggers and are not directly executable. Runtime has no table-level `INSERT` or `UPDATE` on `users_user`. It may update only `mfa_enabled`, `mfa_secret_encrypted`, `mfa_last_used_step`, and `updated_at` directly. `app_create_invited_identity(text, text, text, text)` creates an ordinary unusable-password identity, `app_accept_invitation_password(text, text, timestamptz)` performs the one invitation password transition and accepts only the current `pbkdf2_sha256` encoding, and `app_confirm_invited_profile(text, text, text, text, timestamptz)` confirms that invited user's name and phone. Insert and update triggers reject direct runtime writes outside those boundaries. These functions are not `row_security=off` and are not owned by `gym_auth_bootstrap`.

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

For staff, `app.gym_id` is established only after global user/session validation and authoritative selection of an active `GymMembership`. For members, global user/session validation first verifies the claimed selected `Member` identity through a narrowly approved bootstrap function/policy that can return only the member row whose `linked_user_id` equals the authenticated `app.user_id`; the transaction then sets `app.gym_id` before ordinary tenant queries and reloads the member under normal RLS. Absence or invalid context fails closed. Authorization and staff/member object scope still apply above RLS. Delete policies do not grant business deletion: table grants, state rules, FKs, and triggers restrict it.

RLS policy inventory:

- Standard read/write policy: mutable tenant master/operational tables (`GymSettings`, `GymMembership`, `StaffInvitation`, `StaffInvitationRole`, `Role`, joins, entitlement, staff, member, plan, membership, check-in, category, assignment, salary line while draft, file, notification, idempotency).
- Insert/select plus constrained transition procedures: `Payment`, `Receipt`, `Expense`, `SalaryPeriod`, `PTSale`, `PTCommission`, `CashSession`.
- Insert/select only for runtime: `CashTransaction`, `AuditLog`; maintenance deletion runs as a separately controlled owner role where retention permits.
- `TenantSequence` is select/update only through a `SECURITY DEFINER` allocator that validates caller gym and has a fixed `search_path`.

`GymSelectionTicket`, `RefreshSession`, and `PlatformAuditLog` are global control-plane tables and are excluded from tenant RLS inventory. Their restricted authentication-service grants and ticket/session/audit invariants are checked separately in CI.

The application runtime role must not own tables, have `BYPASSRLS`, set arbitrary tenant context outside the trusted transaction wrapper, or call maintenance functions. CI compares the tenant inventory to database metadata and fails if any tenant table lacks `gym_id NOT NULL`, `UNIQUE (gym_id,id)`, enabled/forced RLS, complete `USING`/`WITH CHECK` policies, or tenant-leading indexes.

## 7. Index contract

Every FK has an index. Every tenant operational index starts with `gym_id`; global uniqueness never substitutes for tenant-leading access paths. Required initial indexes include:

- `(user_id, expires_at)` and `(expires_at)` on global gym-selection tickets, in addition to unique ticket hash;
- `(gym_id, status, created_at DESC)` on stateful entities;
- `(gym_id, normalized_email, status)` and `(gym_id, expires_at)` on staff invitations;
- `(gym_id, member_id, status)` on memberships/payments and `(gym_id, member_id, check_in_at DESC)` on check-ins;
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
- **Memberships:** lock the member before create/cancel/renew. The exclusion constraint is the final guard against concurrent overlap. Derive payment status from posted charges minus posted refunds; do not accept it from clients.
- **PT commission:** lock PT sale and commission. Sale posting creates exactly one commission; payout posts its expense/cash transaction and updates totals atomically. Unique constraints make retries safe.
- **Salary:** lock the pending salary period and all lines. Approval creates exactly one `STAFF_SALARY` expense per line and corresponding cash movement where required, links every line, and posts the period atomically; any failure rolls back the whole period.
- **Authorization:** role/permission changes lock the membership and increment `authz_version` in the same transaction so existing tokens can be invalidated.
- **Multi-gym login selection:** create a ticket only after password/MFA success and an authoritative bootstrap query returns multiple active memberships. Lock the global ticket for every selection attempt; verify hash, user, expiry, state, attempt bound, immutable eligible-membership snapshot, and current membership activity before creating any selected session. Consumption and normal `RefreshSession` creation commit together. Concurrent or replayed selection yields at most one session, and terminal failure revokes without installing tenant context.
- **Member authorization:** identity linking, member suspension/archive, and member self-service authorization changes lock the member and increment `Member.authz_version` in the same transaction so existing member tokens can be invalidated.
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
| File asset                                                         | Private object lifecycle; preserve metadata/checksum while referenced or legally required                                                                            |

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

The exact integration credential/device persistence model is intentionally deferred to the Phase 3 approved integration design. No device credential table is authorized in Phase 0; any future integration credential or device record is gym-owned and forced-RLS protected.

## 11. Compatibility and accepted decisions

The approved implementation pins are Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. Database compatibility is established against PostgreSQL 18.6 with the selected Python driver, Django migrations, composite constraints, transaction-local `set_config`, forced RLS, lock, and rollback tests; version labels alone are not evidence.

[ADR 0001](./adr/0001-single-vps-production-topology.md) accepts the single-VPS topology that the retention, backup, and restore rules in sections 9 and 10 depend on. [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md) governs the BFF/session lifecycle, including the global `GymSelectionTicket` foundation, without changing JWT claims. [ADR 0003](./adr/0003-reception-cash-session-authority.md) governs the object condition already supported by `CashSession.opened_by_membership_id`. MFA is TOTP, mandatory for platform administrators and Owners, with recent step-up for protected owner operations, encrypted secrets, and replay protection. Broader role-based MFA may be decided later. Email uses Django's configurable email backend. Tests and end-to-end runs use non-delivery backends. Production must configure an approved transactional provider before invitation, password-recovery, or Phase 2 expiry delivery is production-ready; vendor selection and that deployment configuration remain deferred. Jurisdiction and general retention must be resolved before production, device persistence remains Phase 3, and Expo versus bare React Native remains Phase 7.
