# Phase 0 REST API Contract

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`DATABASE.md`](./DATABASE.md), [`SECURITY.md`](./SECURITY.md), [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

This document is the authoritative public HTTP contract for the planned API. Phase 0 is documentation only: no endpoint or feature described here is implemented by this contract. Later implementation, OpenAPI schemas, serializers, generated client types, and contract tests must conform to it.

Phase 1A delivers foundation contracts only. Members, check-in, finance, PT, reports, and mobile endpoints remain unimplemented until their later annotated phases.

## 1. Contract principles

- Base path: `/api/v1/`.
- JSON field names use `snake_case` consistently with the Python API.
- Resource IDs are opaque UUID strings.
- Dates use `YYYY-MM-DD`; instants use RFC 3339 UTC.
- Monetary values are strings to preserve decimal precision:

```json
{
  "amount": "100000.00",
  "currency": "THB"
}
```

- Clients never submit `gym_id` as an ownership field.
- Serializers define explicit read and write fields; Django models are not exposed automatically.
- Every endpoint uses a named input serializer and a separate named output serializer with explicit fields. `fields = "__all__"`, model-derived writable fields, and passing request dictionaries directly to model constructors are prohibited.
- Collection endpoints use allowlisted filters, stable ordering, and cursor pagination.
- Resource APIs return `404` for absent or cross-tenant objects.
- Breaking changes require a new API version.

Unless a command below says otherwise, these fields are always read-only and are rejected if supplied: `gym_id`, actor/creator/updater/submitter/approver fields, lifecycle, approval, and posting `status` fields, receipt/sequence numbers, calculated dates, derived member PT indicators, totals, balances, payment state, commission snapshots/calculations, cash expected totals, and cash differences. Server timestamps, IDs, audit metadata, and authorization versions are also read-only.

## 2. Standard envelopes

Single-resource success:

```json
{
  "success": true,
  "data": {
    "id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd"
  },
  "message": "Success"
}
```

Collection success:

```json
{
  "success": true,
  "data": [],
  "meta": {
    "next_cursor": null,
    "has_more": false
  },
  "message": "Success"
}
```

Error:

```json
{
  "success": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "You do not have permission to perform this action.",
    "fields": {}
  },
  "request_id": "req_01..."
}
```

Internal exceptions and database details are never returned.

## 3. HTTP behavior

| Status | Meaning                                         |
| ------ | ----------------------------------------------- |
| `200`  | Successful read or command                      |
| `201`  | Resource created                                |
| `204`  | Successful command with no body                 |
| `400`  | Malformed request/query                         |
| `401`  | Missing, invalid, or expired authentication     |
| `403`  | Authenticated but capability denied             |
| `404`  | Resource absent or inaccessible in active gym   |
| `409`  | State, uniqueness, or idempotency conflict      |
| `412`  | Optimistic concurrency failure                  |
| `422`  | Semantically invalid input                      |
| `429`  | Rate limit exceeded                             |
| `5xx`  | Server failure with safe message and request ID |

Validation codes are stable, for example `INVALID_DATE_RANGE`, `MEMBERSHIP_EXPIRED`, `CASH_DIFFERENCE_REASON_REQUIRED`, `APPROVAL_REQUIRED`, `INVALID_APPROVAL_STATE`, `SELF_APPROVAL_FORBIDDEN`, `PROTECTED_EXPENSE_CATEGORY`, and `CROSS_TENANT_REFERENCE`.

## 4. Authentication API

**Delivery: Phase 1A — foundation.**

```text
POST /api/v1/auth/login/
POST /api/v1/auth/refresh/
POST /api/v1/auth/logout/
POST /api/v1/auth/logout-all/
GET  /api/v1/auth/me/
GET  /api/v1/auth/gyms/
POST /api/v1/auth/select-gym/
POST /api/v1/auth/password/forgot/
POST /api/v1/auth/password/reset/
POST /api/v1/auth/mfa/challenge/
POST /api/v1/auth/mfa/enroll/
POST /api/v1/auth/mfa/enroll/confirm/
```

Staff access tokens are short-lived JWTs. Refresh credentials are opaque, rotate on every use, and are stored only as hashes in server-side sessions. A staff access token selects exactly one `GymMembership`; it never represents the union of the user's gym memberships.

Required JOSE header:

```json
{
  "typ": "JWT",
  "alg": "RS256",
  "kid": "signing-key-id"
}
```

`alg` is an example from the deployment allowlist, not a client choice. `alg=none`, an algorithm outside the configured allowlist, a missing/unknown `kid`, and key/algorithm confusion are rejected.

Required staff access claims:

```json
{
  "iss": "gym-platform-api",
  "aud": "gym-platform",
  "sub": "user-uuid",
  "gym_id": "gym-uuid",
  "gym_membership_id": "membership-uuid",
  "session_id": "session-uuid",
  "authz_version": 4,
  "token_type": "access",
  "iat": 1786791000,
  "nbf": 1786791000,
  "exp": 1786791600,
  "jti": "token-uuid"
}
```

The configured identifiers are stable, non-secret values: issuer `gym-platform-api`, staff/web audience `gym-platform`, and member audience `gym-platform-member`. They remain startup-validated configuration rather than hard-coded security secrets. Permission lists are not embedded as authority. The backend evaluates the current selected membership, roles, permissions, object rules, and entitlements.

Staff authentication field contracts:

- `POST /auth/login/` request: `email`, `password`, optional `mfa_code`, optional `device_name`. Passwords and MFA secrets are never echoed. After successful password/MFA checks, Django derives the authoritative active-membership bootstrap list. With one active membership it immediately issues the normal selected-gym access/rotating-refresh session. With multiple active memberships it creates a high-entropy, opaque, single-use gym-selection ticket expiring exactly 5 minutes after issue; it creates no refresh session, access JWT, selected gym, or tenant context.
- Trusted BFF-to-Django multi-membership response: `requires_gym_selection: true`, plaintext `gym_selection_ticket`, `gym_selection_expires_at`, and `available_gym_memberships[]` containing only read-only `gym_membership_id`, `gym_id`, and safe gym display fields. The response contains no access/refresh credential. The BFF stores the ticket in its dedicated protected cookie and removes `gym_selection_ticket` before returning the safe selection state and choices to browser JavaScript.
- Browser login response for multiple memberships: `requires_gym_selection: true`, `gym_selection_expires_at`, and the same safe `available_gym_memberships[]`; it contains no ticket, token, refresh-session value, internal attempt count, or security state. With one membership, the browser receives only `requires_gym_selection: false` and safe authenticated application state after the BFF has stored the Django-issued credentials.
- `POST /auth/refresh/` request: rotating `refresh_token` or the approved secure cookie. Response: a new access credential and replacement refresh credential/expiry. Reuse revokes the entire token family.
- `POST /auth/logout/` request: current refresh credential/session; response `204`. `logout-all` revokes all of the user's refresh sessions and increments/revokes authorization state as required.
- `GET /auth/me/` response: `user` (`id`, `email`, `first_name`, `last_name`), selected `gym` display fields, selected `gym_membership` (`id`, `status`, `is_owner`, `roles`), effective permission codes, effective feature codes/limits, and `authz_version`; all are read-only.
- `GET /auth/gyms/` returns only active memberships belonging to the authenticated user.
- `POST /auth/password/forgot/` request: `email` only. The response is a constant `200` envelope that never reveals whether an account exists, whether the address is verified, or when a previous request was made. Any reset token is high-entropy, single-use, hash-only at rest, short-lived, and delivered out of band; it is never returned in the response body.
- `POST /auth/password/reset/` request: `reset_token`, `password`, and `password_confirmation`. The command verifies token hash, user binding, expiry, and unconsumed state under lock, enforces password policy, consumes the token, revokes every existing `RefreshSession` for that user, and increments authorization state. Response is `204`; it issues no access or refresh credential, so the user must log in again through the normal selection path.
- Password-verified users with TOTP enabled receive a high-entropy `mfa_challenge_token` through the trusted BFF only. The BFF stores it in a short-lived HttpOnly cookie and browser JavaScript receives only `requires_mfa` and its expiry.
- `POST /auth/mfa/challenge/` request: the BFF-held `challenge_token` plus a six-digit `code`. Django locks and consumes the hash-only challenge, enforces expiry and bounded attempts, verifies TOTP within the approved clock window, and then resumes the normal direct-login or gym-selection flow. Replay never issues credentials.
- `POST /auth/mfa/enroll/` requires an authenticated session and current password. It returns a short-lived enrollment reference plus the one-time TOTP secret and `otpauth` URI for immediate setup; the secret is never logged or returned again.
- `POST /auth/mfa/enroll/confirm/` accepts the enrollment reference and a valid six-digit TOTP code, encrypts the confirmed secret, enables MFA, revokes other sessions, and marks the current refresh session as recently MFA-verified. Owner mutations require this recent session proof.
- Trusted BFF-to-Django `POST /auth/select-gym/` request during login: `gym_membership_id` plus plaintext `gym_selection_ticket`; neither field selects authority by itself. Django atomically locks the hashed ticket record, verifies its user binding, exact 5-minute expiry, unconsumed/unrevoked state, bounded attempts, and that the chosen currently active membership is in the immutable authoritative bootstrap list, then consumes it and returns newly scoped access/refresh credentials and selected context.
- Browser-to-BFF select request: `gym_membership_id` only. Browser JavaScript cannot supply or read the ticket; the BFF reads it from the protected cookie, forwards it to Django, strips credentials from the response, stores normal auth cookies only on success, and clears the ticket cookie on success or terminal failure. Expiry, replay, user mismatch, substituted membership, exhausted attempts, or revoked/consumed state never issues credentials. A failed terminal state revokes the ticket.

No gym authority or tenant context exists between the multi-membership login response and successful selection. Gym selection accepts a membership identifier only from the ticket-bound authoritative bootstrap list, revalidates current membership state server-side, and issues a new scoped session. It does not trust an arbitrary gym ID or add any JWT claim.

### Browser BFF contract

Browser authentication terminates at same-origin Next.js BFF routes; browser JavaScript does not call the token-returning Django authentication routes directly and never receives an access JWT or refresh token. The BFF may forward credentials and selected membership IDs, but Django is authoritative for login, gym selection, issuance, rotation, validation, family revocation, logout, and logout-all.

The BFF stores credentials and the pre-session gym-selection ticket only in separate `HttpOnly`, host-scoped cookies with no `Domain` attribute, `SameSite=Lax`, and `Secure` outside isolated `http://localhost` development. It never writes them to `localStorage` or `sessionStorage`. The selection-ticket cookie lasts no longer than the ticket's exact 5-minute server expiry and is cleared on success or any terminal result. The access-token target is 10 minutes. For application requests, the BFF reads the access cookie server-side and forwards the JWT to Django; client-visible responses exclude token, ticket, and refresh-session values.

Every BFF route that mutates cookie-authenticated or selection-ticket state, including login, refresh, select-gym, logout, and logout-all, requires the approved CSRF token/header pair plus strict allowlisted `Origin` validation; strict `Referer` validation is required only when `Origin` is unavailable. A missing or mismatched signal fails closed. CORS does not replace these checks.

After the one specifically recognized access-expiry response from Django, the BFF may perform one refresh and retry the original request once. Refresh rotates the credential and its hashed `RefreshSession` state atomically. Refresh reuse revokes the entire family, and non-expiry `401`/`403` responses never trigger a refresh loop. Logout invokes Django revocation first, then clears all auth cookies with matching host/path attributes; cookies are also cleared when the authoritative session is already expired or revoked, but clearing cookies alone is never considered revocation.

### Staff token validation sequence

Protected staff requests fail closed in this order:

1. parse one canonical JWT and reject malformed tokens, duplicate claims, or unsupported critical JOSE headers;
2. require the configured explicit `alg` allowlist and `kid`, resolve the expected key, and verify the signature;
3. verify exact `iss` and allowed `aud`;
4. require `token_type=access`, nonblank `sub`, UUID `jti`, and sane `iat`;
5. verify `exp` and `nbf` using only the small configured clock-skew allowance;
6. require UUID `session_id`, `gym_id`, and `gym_membership_id`, plus positive integer `authz_version`;
7. begin the request database transaction and install transaction-local user ID and request ID;
8. load the global non-revoked, non-expired `STAFF` refresh session and active user identified by `session_id`/`sub`, and verify its selected gym-membership context matches the token;
9. verify the active gym and subscription;
10. install transaction-local gym context, then load exactly the selected active `GymMembership` under RLS and verify its `id`, `user_id`, and physical `gym_id` match every token/session claim;
11. compare the current membership `authz_version` exactly with the token claim;
12. resolve current roles and permission codes, then enforce endpoint permission, object conditions, and feature entitlement.

Failure through step 6 or invalid/revoked session/user returns `401`. An authenticated identity lacking current gym membership, permission, or entitlement returns `403`, except resource identifiers outside the selected tenant return `404`. No request header, URL parameter, cookie value, or body `gym_id` may replace token-selected context.

### Member token contract

Member authentication uses separate routes, audience policy, serializers, and token type. It maps a global user to exactly one gym-scoped `Member` record.

```json
{
  "iss": "gym-platform-api",
  "aud": "gym-platform-member",
  "sub": "user-uuid",
  "gym_id": "gym-uuid",
  "member_id": "member-uuid",
  "session_id": "session-uuid",
  "authz_version": 3,
  "token_type": "member_access",
  "iat": 1786791000,
  "nbf": 1786791000,
  "exp": 1786791600,
  "jti": "token-uuid"
}
```

Member tokens use the same strict JOSE header/signature, explicit algorithm/`kid`, issuer, audience, time, session, user, gym, and authorization-version checks as staff tokens. They additionally require `token_type=member_access`, verify the selected member principal through the approved bootstrap path, then load the claimed `member_id` under the established gym RLS context and verify `Member.linked_user_id=sub`, active member status, exact gym match, and current `Member.authz_version`. Every member endpoint adds immutable self scope (`member_id` from the token); a path, query, or body identifier can never select another member. Staff and member sessions share the global `RefreshSession` control-plane store but have explicit, mutually exclusive `principal_type` and selected-context fields; their audiences and validation paths remain separate.

Member validation therefore runs the full sequence explicitly: parse canonical JWT; require allowlisted `alg` plus known `kid`; verify signature; verify exact member issuer/audience and `token_type`; enforce `exp`, `nbf`, `iat`, `jti`, and small clock skew; begin a transaction and install transaction-local user/request context; verify the global active `MEMBER` session and user; use only the narrowly approved bootstrap function/policy to prove that the selected `Member` is linked to that user and derive its gym; verify the active gym; install transaction-local gym context; reload that one member under ordinary RLS; compare current `Member.authz_version`; and finally enforce gym plus member-self scope and endpoint entitlement. Member tokens do not select a staff `GymMembership` or inherit staff roles. Staff tokens always select exactly one `GymMembership`.

## 5. Platform administration — Phase 1B

All routes require platform permissions and produce audit events.

Canonical requirements are: dashboard `platform.dashboard.view`; gym list/detail `platform.gyms.view`; gym provisioning `platform.gyms.create`; gym update `platform.gyms.update`; activate `platform.gyms.activate`; suspend `platform.gyms.suspend`; initial or additional owner creation through the protected owner workflow `platform.owners.create`; subscription reads/updates `platform.subscriptions.manage`; and platform audit reads `platform.audit.view`.

`platform.settings.manage` remains in the historical permission catalog and is not deleted. Phase 1B marks that row inactive. It has no approved HTTP route, no endpoint may claim it, and a platform session does not receive it while it is inactive. It must never authorize tenant `settings.manage` or substitute for `platform.dashboard.view`, gym administration, `platform.subscriptions.manage`, or `platform.audit.view`. Reactivation requires an approved decision; the retirement migration does not reverse the row back to active.

```text
GET  /api/v1/admin/dashboard/
GET  /api/v1/admin/gyms/
POST /api/v1/admin/gyms/
GET  /api/v1/admin/gyms/{gym_id}/
PATCH /api/v1/admin/gyms/{gym_id}/
POST /api/v1/admin/gyms/{gym_id}/activate/
POST /api/v1/admin/gyms/{gym_id}/suspend/
POST /api/v1/admin/gyms/{gym_id}/owners/
GET  /api/v1/admin/subscriptions/
PATCH /api/v1/admin/subscriptions/{id}/
GET  /api/v1/admin/audit-logs/
```

`GET /admin/dashboard/` accepts only platform authentication and `platform.dashboard.view`. Its data envelope is exactly `gym_statuses` (`total`, `PENDING`, `ACTIVE`, `SUSPENDED`, `CLOSED`), `gym_subscription_statuses` (`TRIAL`, `ACTIVE`, `PAST_DUE`, `CANCELLED`), and `subscription_plan_statuses` (`ACTIVE`, `RETIRED`). Counts come from bounded aggregate queries and omit tenant business records, user PII, memberships, entitlements, and finance. This read does not write an audit event.

`GET /admin/subscriptions/` requires `platform.subscriptions.manage`. Each item is `id`, `code`, `name`, `status`, `price`, `currency`, and `billing_interval`, ordered by `name` then `id`. It has no feature mappings or tenant usage. The optional strict query `status` accepts `ACTIVE`, `RETIRED`, or the management value `ALL`. Omitted `status` defaults to `ACTIVE`, so provisioning still sees only assignable plans. `status=ALL` returns active and retired plans; `status=RETIRED` returns retired plans. Any other value or query parameter is rejected.

`PATCH /admin/subscriptions/{id}/` requires `platform.subscriptions.manage`. A strict body accepts one or more of `name`, `status` (`ACTIVE` or `RETIRED`), nonnegative `price`, and `billing_interval`. `id`, `code`, `currency`, feature mappings, actors, and timestamps are rejected. Retiring a plan returns `409 SUBSCRIPTION_PLAN_IN_USE` while any gym with subscription status `TRIAL` or `ACTIVE` references it. The route never changes tenant entitlements. A real change writes one redacted platform audit event whose old and new values contain only the changed writable fields; an unchanged body returns the plan and writes no audit event. The response is the explicit plan: `id`, `code`, `name`, `status`, `price`, `currency`, `billing_interval`, `created_at`, and `updated_at`.

Gym creation is a single transactional service operation that:

1. validates gym code, timezone, currency, and subscription;
2. creates the gym;
3. creates or safely links the owner identity;
4. creates the initial owner gym membership as `ACTIVE`, assigns the protected OWNER role, and leaves that membership active so the gym is never ownerless;
5. seeds protected default roles, permissions, and the canonical expense categories (`RENT`, `UTILITIES`, `INTERNET`, `STAFF_SALARY`, `PT_COMMISSION`, `CLEANING_SUPPLIES`, `MAINTENANCE`, `MARKETING`, `WATER_DRINKS`, `OTHER`);
6. records redacted platform audit evidence and, when an invitation is issued, redacted tenant audit evidence that omits the token and email;
7. when invitation delivery is requested, emails the acceptance link before the transaction commits.

No outbox or Celery task is used. Invitation email is synchronous and is sent as late as practical inside the provisioning transaction. If delivery fails, the request fails and the transaction rolls back every provisioning row, including the idempotency record, so the same key can be retried. An asynchronous outbox remains deferred until measured delivery need; if introduced later, its tenant records must be gym-owned and RLS-protected.

`POST /admin/gyms/` is the single gym-and-owner provisioning command.

- Request fields: `gym` (`code`, `name`, optional `legal_name`, `email`, `phone`, structured address, `timezone`, `currency`, `subscription_plan_id`, `subscription_status`, optional subscription dates) and `owner` (`email`, `first_name`, `last_name`, optional `phone`, optional `send_invitation`).
- `owner.send_invitation` defaults to `true` when omitted. `true` creates one pending invitation for the provisioned ACTIVE owner membership and protected OWNER role, stores only the hashed single-use token, and emails an `APP_PUBLIC_URL` `/accept-owner-invitation` link. The plaintext token is not stored, returned, logged, or audited. Acceptance uses `POST /owners/invitations/accept/`: a new or passwordless user sets a policy-valid password; an existing usable password must be presented and is not reset. Acceptance re-confirms `ACTIVE` and increments `authz_version`.
- `send_invitation=false` succeeds only when that email already belongs to an eligible user with a usable password. A new identity, or an existing user without a usable password, returns `409 OWNER_CREDENTIAL_REQUIRED` with the same safe message and rolls back the provisioning transaction. Inactive users and platform-administrator identities remain rejected on their existing results.
- Read-only/rejected: all IDs, both `gym_id` occurrences, gym/subscription lifecycle timestamps, gym `status`, owner user/membership IDs, membership `status`, `is_owner`, role IDs, permissions, feature entitlements, categories, sequences, actors, and audit fields.
- `201` response: explicit `gym` output, explicit safe owner `user`, owner `gym_membership`, seeded `roles`, effective `feature_entitlements`, and seeded `expense_categories[]` containing `id`, `code`, `system_key`, `name`, and `is_active`; no password, invitation secret, refresh credential, or internal provisioning state.
- Authorization: `platform.gyms.create` and `platform.owners.create`; both are required because this command provisions the gym and its initial owner, and neither is a normal tenant permission. The service validates owner identity/link policy and subscription compatibility, then atomically creates or links the identity, creates the required initial owner membership, assigns the OWNER role, seeds tenant configuration, and audits the actor. `Idempotency-Key` is required because provisioning is externally retryable. Replaying a successful key returns the original response and does not create or email another invitation. The same key with a different `send_invitation` value is `409 IDEMPOTENCY_KEY_REUSED`. Additional owners may be added later without replacing the initial owner.

## 6. Tenant resource API

### Gym profile and settings — Phase 1B

```text
GET   /api/v1/gym/
PATCH /api/v1/gym/
GET   /api/v1/settings/
PATCH /api/v1/settings/
```

Gym profile read/update requires `gym.view`/`gym.update`; settings read/update requires `settings.view`/`settings.manage`.

`PATCH /settings/` accepts only typed operational settings: `week_starts_on`, `allow_manual_member_numbers`, `member_number_prefix`, `receipt_number_prefix`, `check_in_duplicate_window_seconds`, `check_in_challenge_ttl_seconds`, `check_in_challenge_max_uses`, `membership_expiry_warning_days`, `membership_expiry_email_enabled`, `membership_expiry_push_enabled`, `class_cancellation_cutoff_minutes`, `class_no_show_consumes_session`, and `require_separate_finance_approval`. The approval flag is read/write only for OWNER policy administration and defaults to `true`; Phase 0 requires every Payment, Expense, Retail Sale, PT Sale, and Salary record to be approved by a different authorized Manager or Owner and does not permit disabling this control through the API. The response returns those fields plus read-only `gym_id`, actors, and timestamps from the tenant-owned one-to-one `GymSettings` record. Gym identity, address, timezone, currency, lifecycle, and subscription remain fields of `/gym/`, not `/settings/`. Arbitrary client-defined keys and unbounded JSON settings are rejected.

### Dashboard — Phase 6

```text
GET /api/v1/dashboard/?range=this_month
GET /api/v1/dashboard/?date_from=2026-08-01&date_to=2026-08-15
```

Dashboard totals are computed for the active gym and its timezone. Supported preset ranges are `today`, `this_week`, `this_month`, `last_month`, and `this_year`. Custom ranges are bounded.

Tenant dashboard access requires `reports.view`; financial measures additionally require `reports.financial`.

The response has stable `operational` and `financial` blocks. Users lacking `reports.financial` receive `financial: null`; totals never silently downgrade to partial financial data.

```json
{
  "range": { "date_from": "2026-08-01", "date_to": "2026-08-31" },
  "operational": {
    "active_members": 128,
    "new_members": 14,
    "expired_memberships": 9,
    "check_ins": 642,
    "unique_attendees": 311,
    "currently_checked_in": 23,
    "expiring_within_3_days": 4,
    "expiring_within_7_days": 11,
    "expiring_within_30_days": 29,
    "pending_approvals": 6
  },
  "financial": {
    "total_revenue": "900000.00",
    "new_member_revenue": "250000.00",
    "renewal_revenue": "350000.00",
    "pt_revenue": "200000.00",
    "class_revenue": "50000.00",
    "product_revenue": "40000.00",
    "other_revenue": "10000.00",
    "pending_payment_member_count": 7,
    "pending_payment_amount": "90000.00",
    "revenue_sources_ranked": [
      { "source": "RENEWAL", "amount": "350000.00" },
      { "source": "NEW_MEMBER", "amount": "250000.00" },
      { "source": "PT", "amount": "200000.00" },
      { "source": "CLASS", "amount": "50000.00" },
      { "source": "PRODUCT", "amount": "40000.00" },
      { "source": "OTHER", "amount": "10000.00" }
    ],
    "top_product_items": [
      {
        "product_id": "uuid",
        "name": "Protein drink",
        "quantity_sold": "15.00",
        "revenue": "30000.00",
        "gross_profit": "10500.00"
      }
    ],
    "total_expenses": "310000.00",
    "expenses_by_category": [
      { "system_key": "RENT", "amount": "30000.00" },
      { "system_key": "UTILITIES", "amount": "15000.00" },
      { "system_key": "INTERNET", "amount": "5000.00" },
      { "system_key": "STAFF_SALARY", "amount": "160000.00" },
      { "system_key": "PT_COMMISSION", "amount": "80000.00" },
      { "system_key": "CLEANING_SUPPLIES", "amount": "5000.00" },
      { "system_key": "MAINTENANCE", "amount": "5000.00" },
      { "system_key": "MARKETING", "amount": "5000.00" },
      { "system_key": "WATER_DRINKS", "amount": "3000.00" },
      { "system_key": "OTHER", "amount": "2000.00" }
    ],
    "staff_salary_expense": "160000.00",
    "pt_commission_expense": "80000.00",
    "net_profit": "590000.00"
  }
}
```

All financial values use posted, non-void source records for the selected gym and business-date range. `total_revenue` is posted charges minus posted refunds/reversals; its new-member, renewal, PT, class, product, and other components are mutually exclusive and reconcile to the total. `revenue_sources_ranked` is sorted by that net posted revenue, not by profit. Product ranking separately exposes quantity, revenue, and server-calculated gross profit from immutable sale-line cost snapshots. `total_expenses` is posted expenses net of linked reversals and includes salary and PT commission payouts. `expenses_by_category` always returns all ten protected `system_key` values in canonical display order, using `"0.00"` for a category with no activity. `net_profit = total_revenue - total_expenses`.

`pending_payment_member_count` and `pending_payment_amount` cover expired membership/PT/class obligations with an authoritative `UNPAID` or `PARTIAL` balance; they are distinct from `pending_approvals`, which counts submitted Payment, Expense, PT Sale, Retail Sale, and SalaryPeriod records awaiting approval. `active_members` is the end-of-range count with an eligible active membership; `new_members` uses `Member.join_date`; `expired_memberships` counts memberships whose expiry occurs in range. `check_ins` is the number of same-gym check-in events, `unique_attendees` is the distinct member count, and `currently_checked_in` counts open check-ins with no checkout. Expiry buckets use the gym-local date. Draft and rejected records never affect revenue, expenses, stock sold, cost, or profit. This is the tenant dashboard's owner-focused financial view; any delegated Manager or Accountant access still requires the documented report permissions.

### Staff, roles, and permissions — Phase 1B

```text
GET  /api/v1/staff/
POST /api/v1/staff/invitations/
POST /api/v1/staff/invitations/accept/
GET  /api/v1/staff/{id}/
PATCH /api/v1/staff/{id}/
POST /api/v1/staff/{id}/activate/
POST /api/v1/staff/{id}/suspend/

GET  /api/v1/owners/
POST /api/v1/owners/invitations/
POST /api/v1/owners/invitations/accept/
POST /api/v1/owners/{gym_membership_id}/revoke/

GET  /api/v1/roles/
POST /api/v1/roles/
GET  /api/v1/roles/{id}/
PATCH /api/v1/roles/{id}/
POST /api/v1/roles/{id}/archive/
GET  /api/v1/permissions/
PUT  /api/v1/roles/{id}/permissions/
GET  /api/v1/gym-memberships/{id}/roles/
PUT  /api/v1/gym-memberships/{id}/roles/
```

Protected role templates cannot be renamed or deleted. Assignment APIs validate role and staff membership belong to the current gym.

Staff listing/detail requires `staff.view`; invitation, staff lifecycle, and membership-role assignment require `staff.manage`; role and permission listing requires `roles.view`; role mutation and permission replacement require `roles.manage`.

The owner endpoints are a separate OWNER-reserved workflow and are not authorized by `staff.manage` or `roles.manage`. Any active Owner may list the gym's Owners and invite an additional Owner after recent MFA/step-up verification. The invitation accepts `email`, `first_name`, `last_name`, optional `phone`, and optional `send_invitation`; email delivery is required until another secure delivery channel is approved. The server fixes `is_owner = true` and the protected OWNER role, so clients cannot submit role or permission IDs. `POST /owners/invitations/accept/` accepts the single-use invitation token plus password and confirmation through the trusted BFF, and creates or activates another same-gym Owner login without replacing existing Owners. Revocation requires a different active Owner as actor, a reason, recent MFA, row locking, and proof that at least one other active Owner will remain. All owner changes increment authorization state and create audit events. Responses expose login/role status only; no share percentage, capital contribution, profit-distribution ratio, or legal-ownership value exists in Phase 0.

`POST /api/v1/admin/gyms/{gym_id}/owners/` provides the equivalent audited Super Admin assistance path for adding an Owner. It follows the same identity-link, invitation, tenant, role, last-active-owner, authorization-version, and audit rules and does not grant the Super Admin ordinary access to gym business data. The same route can issue one replacement credential invitation for an existing active Owner of that exact gym when the user still has an unusable password and no live pending invitation remains. That recovery does not change the membership, OWNER assignment, or `authz_version` until acceptance. Its audit records `invitation_reissue` and does not store the token. A live pending invitation is returned without another email or a rotated token; its message states that a new link can be issued after expiry. Tenant owner calls cannot reinvite an existing active Owner. An active Owner who already has a usable password, and an inactive or platform-administrator identity, remain conflicts. Public results do not reveal whether an arbitrary email exists outside the target gym.

Staff invitation/acceptance and role contracts:

- Invite request: `email`, `first_name`, `last_name`, optional `phone`, `staff_code`, `display_name`, `joined_date`, optional employment fields, and nonempty `role_ids`. Read-only/rejected: `gym_id`, user/membership/profile IDs, invitation token, all statuses/timestamps, `is_owner`, `authz_version`, actors, effective permissions, and role details. Response contains safe invitation metadata and explicit membership/staff output. Requires `staff.manage`; callers may assign only roles and permissions they are authorized to delegate.
- Accept request: `invitation_token`, `password`, `password_confirmation`, and optional profile confirmation fields. The token is single-use, hashed at rest, scoped to the invited membership/gym, and expires. Response follows login/select-gym token boundaries. The command verifies invitation state, user binding, password policy, selected membership, and gym state before atomically activating the one invited `GymMembership`; clients cannot choose a gym or role.
- Role create/update request: `key`, `name`, optional `description`, and `is_active` where permitted. Permission replacement request: unique `permission_codes`. Read-only: `gym_id`, source template ID/version, protected/template status, actors, timestamps, and effective assignment count.
- Membership-role assignment request: unique nonempty `role_ids`; response returns the selected `gym_membership_id`, assigned explicit role outputs, and read-only `authz_version`. It requires `staff.manage`, an active same-gym non-owner membership, active non-OWNER roles, anti-escalation/delegation checks, owner-protection invariants, and increments authorization version once when the assigned set changes.
- `GET /api/v1/roles/` and `GET /api/v1/permissions/` are cursor collections. `limit` defaults to 25 and cannot exceed 100. `after` is an opaque cursor bound to that collection; clients send `meta.next_after` back only as `after`. Role rows include protected/template status, source template id/version, permission codes and count, assignment count, the creating membership, and timestamps. The permission catalog is the tenant catalog only and never returns `platform.*`.

There is no separate delegable-set endpoint. The tenant delegable set is the active protected MANAGER role's current permissions, excluding the five hard-reserved approval codes (`payments.approve`, `expenses.approve`, `retail.approve`, `pt.sales.approve`, `salaries.approve`) and the OWNER-only control `settings.manage`. An active Owner changes that set only by replacing the protected MANAGER role's permissions. That replacement must keep all five hard-reserved approvals, must not add `settings.manage` or `platform.*`, and rejects unknown or inactive codes. OWNER role permissions are immutable. For RECEPTIONIST, ACCOUNTANT, and TRAINER, only an Owner may replace permissions, and the resulting set must stay inside the current delegable set; their protected baseline is that hard-reserved approvals and `settings.manage` remain denied. Custom roles, including roles created by an Owner, cannot receive hard-reserved approvals or `settings.manage`. A non-owner with `roles.manage` may create and change only custom roles, and only when the full resulting permission set is a subset of both that caller's live permissions and the delegable set. The same helpers validate staff-invitation role assignment, so invitation and membership-role replacement cannot diverge.

The published OpenAPI document includes role create, update, archive, permission replacement, the permission catalog, and membership-role assignment. `GET /api/v1/roles/` and `GET /api/v1/permissions/` accept only `limit` (default 25, maximum 100) and an opaque `after` cursor. `GET /api/v1/staff/` accepts the existing status, membership status, and search filters plus the same `limit` and `after` cursor bound to that filter set. List responses are `{success, data, meta, message}` where `meta` is `{has_more, next_after}`; clients send `next_after` back only as `after` and do not interpret its contents. The browser workspace `/roles` requires `roles.view` and loads both collections through same-origin BFF routes. Readers see protected or custom status, permission codes, and assignment counts. Callers with `roles.manage` can create custom roles and can edit or archive only custom roles. An active Owner can replace permissions on protected non-OWNER roles, including the MANAGER policy, and cannot change OWNER identity or permissions or protected identity and archive controls. The browser labels hard-reserved approvals and owner-only `settings.manage` as unavailable and offers other permission controls only inside the caller's possible delegation boundary; the server remains authoritative. The browser workspace `/staff` requires `staff.view`. Invitation, profile update, activate/suspend, and membership-role assignment go through same-origin BFF routes. Invitation, profile update, and activate/suspend require `staff.manage`; activate and suspend also require a reason. Assignment uses the selected staff `gym_membership_id`, requires `staff.manage`, submits a nonempty unique role list, never offers the OWNER role, and shows the authorization version returned by the save. The browser does not offer owner assignment, free-text role or permission ids, termination, staff archive, or a profile photo. Invitation role choices load only when invitation is available and the caller has `roles.view`; the client walks the role cursor with a bounded number of pages and keeps only active non-OWNER roles while the server revalidates the assignment. `POST /api/v1/staff/invitations/accept/` is reached through the trusted BFF: the single-use token is taken from the invitation URL into that POST body only, is not written to browser storage, and the BFF stores the Django session in HttpOnly cookies before opening the authenticated tenant home. Optional confirmation fields are `first_name`, `last_name`, `phone`, and `display_name`.

### Members — Phase 2

```text
GET  /api/v1/members/
POST /api/v1/members/
GET  /api/v1/members/{id}/
PATCH /api/v1/members/{id}/
POST /api/v1/members/{id}/archive/
GET  /api/v1/members/{id}/summary/
GET  /api/v1/members/{id}/payments/
GET  /api/v1/members/{id}/check-ins/
```

Filters include allowlisted `status`, `search`, `joined_from`, `joined_to`, and `is_pt_client=true|false`. The API assigns member ownership from request context. `member_id` is unique only within the active gym.

Member list, detail, summary, and history reads require `members.view`; create requires `members.create`; update requires `members.update`; archive requires `members.archive`. The nested `payments/` and `check-ins/` histories additionally require `payments.view` and `checkins.view` respectively.

Member create request fields: optional client-visible `member_number` only if policy allows manual numbering, `first_name`, `last_name`, optional `phone`, `email`, `gender`, `date_of_birth`, structured address, `join_date`, emergency contact fields, `notes`, and tenant-scoped `profile_photo_asset_id`. Read-only/rejected: `id`, `gym_id`, normalized fields, `linked_user_id`, `is_pt_client`, `status`, `archived_at`, actors, and timestamps. Response uses an explicit member serializer and includes those read-only server fields. `is_pt_client` is server-derived from a same-gym trainer assignment with `status = ACTIVE` whose effective date range contains the gym-local as-of date; it is returned by list/detail/summary and cannot be set directly. The service requires `members.create`, any required member-management feature, same-gym clean file asset, date validity, and per-gym uniqueness.

Published Member routes are list, create, detail, update, and archive. Summary, payment history, and check-in history remain unpublished. Reads use `members.view`; creation uses `members.create`; update uses `members.update`; archive uses `members.archive`. Each route also requires the members feature entitlement. The gym comes from the access token. A member id from another gym returns `404`. A `gym_id` in the body or query returns `400`.

`GET /api/v1/members/` accepts only `status`, `search`, `joined_from`, `joined_to`, `is_pt_client` (`true` or `false`), `limit` (default 25, maximum 100), and an opaque `after` cursor bound to that gym, those filters, and `normalized_full_name,id` order. `is_pt_client=true` returns an empty page until trainer assignments exist; `false` does not add a filter. Clients send `meta.next_after` back only as `after`. The list response is `{success, data, meta, message}` and `meta` is `{has_more, next_after}`.

Omitted or blank `member_number` allocates the next `{prefix}-{sequence}` value from locked gym settings. An explicit number is accepted only when `allow_manual_member_numbers` is true. A duplicate number in the same gym returns `409 MEMBER_CONFLICT`.

`gender` is optional and normalized to `FEMALE`, `MALE`, `OTHER`, or `UNSPECIFIED`. Address fields are `address_line1`, `address_line2`, `city`, `region`, `postal_code`, and `country_code`. `profile_photo_asset_id` must be a same-gym file asset whose scan status is `CLEAN` and whose retention status is `ACTIVE`; null detaches it. Storage metadata is not returned.

Response fields are `id`, `member_number`, `first_name`, `last_name`, `normalized_full_name`, `phone`, `email`, `gender`, `date_of_birth`, `address`, `join_date`, `emergency_contact_name`, `emergency_contact_phone`, `notes`, `profile_photo_asset_id`, `linked_user_id`, `is_pt_client`, `authz_version`, `status`, `archived_at`, `created_at`, and `updated_at`. `is_pt_client` is false until Phase 5. `linked_user_id` and `authz_version` are read-only. Gym and actor ids are omitted. Update accepts any nonempty subset of the create fields except `member_number`.

Archive accepts an empty body, sets `status` to `ARCHIVED`, records `archived_at` once, and increments `authz_version` once. Repeating archive returns that same member and does not move `archived_at`, `updated_at`, or `authz_version`.

### Membership plans and memberships — Phase 2

```text
GET  /api/v1/membership-plans/
POST /api/v1/membership-plans/
GET  /api/v1/membership-plans/{id}/
PATCH /api/v1/membership-plans/{id}/
POST /api/v1/membership-plans/{id}/archive/

GET  /api/v1/memberships/
POST /api/v1/memberships/
GET  /api/v1/memberships/{id}/
PATCH /api/v1/memberships/{id}/
POST /api/v1/memberships/{id}/cancel/
POST /api/v1/memberships/{id}/suspend/
POST /api/v1/memberships/{id}/resume/
GET  /api/v1/memberships/expiry/?bucket=within_7_days
```

Published MembershipPlan routes are list, create, detail, update, and archive. Reads use `memberships.view`; creation uses `memberships.create`; update and archive use `memberships.update`. Each route also requires the memberships feature entitlement. The gym comes from the access token. A plan id from another gym returns `404`.

`GET /api/v1/membership-plans/` accepts only `limit` (default 25, maximum 100) and an opaque `after` cursor bound to that gym and to `code,id` order. Clients send `meta.next_after` back only as `after`. The list response is `{success, data, meta, message}` and `meta` is `{has_more, next_after}`.

Plan create request fields: `code`, `name`, optional `description`, `duration_value` (positive integer), `duration_unit` (`DAY`, `WEEK`, `MONTH`, `YEAR`), and nonnegative decimal-string `price` with at most two decimal places. Plan update accepts any nonempty subset of those fields. Read-only/rejected: `id`, `gym_id`, `currency`, `is_active`, `archived_at`, normalized fields, actors, and timestamps. `currency` is copied from the gym at creation and is not client-writable. Response fields are `id`, `code`, `name`, `description`, `duration_value`, `duration_unit`, `price`, `currency`, `is_active`, `archived_at`, `created_at`, and `updated_at`. A duplicate code in the same gym returns `409 MEMBERSHIP_PLAN_CONFLICT`.

Archive accepts an empty body, sets `is_active` false, and records `archived_at` once. Repeating archive returns that same plan and does not move `archived_at`.

Published Membership routes are list, create, detail, note update, cancel, suspend, resume, and the expiry-bucket read. Sale, suspension, resume, cancellation, and expiry reads are published. No payment is posted by these routes. Membership-expiry scheduling and delivery commands are implemented. Member summary and history, notification HTTP routes, payment, and receipt routes remain unpublished.

Membership list, detail, and the expiry-bucket route require `memberships.view`; create requires `memberships.create`; note update, suspend, and resume require `memberships.update`; cancel requires `memberships.cancel`. Each route also requires the memberships feature entitlement. The gym comes from the access token. A member, plan, or membership id from another gym returns `404`.

`memberships.create` is the discount authority. It allows one discount from zero through the copied plan list price, inclusive. There is no separate discount permission. A discount outside that range is `400`.

Membership create request fields: `member_id`, `plan_id`, `start_date`, optional `discount_amount`, and optional non-financial `note` of at most 4000 characters. Read-only/rejected: `gym_id`, plan snapshots, `expiry_date`, `list_price`, `final_amount`, `currency`, `payment_status`, lifecycle `status`, cancellation data, actors, and timestamps. The service copies the active same-gym plan code, name, duration, list price, and currency, calculates an inclusive calendar expiry in the gym timezone, stores exact two-decimal totals, and sets `payment_status` to `UNPAID`. Status is `PENDING` when the start date is after the gym-local today, `EXPIRED` when the inclusive expiry is before that today, and `ACTIVE` otherwise. The member and plan must both be active. Pending, active, and suspended date ranges for the same member cannot overlap; a conflict is `409 MEMBERSHIP_OVERLAP` and does not identify the other row.

Response fields are `id`, `member_id`, `plan_id`, `plan_code`, `plan_name`, `duration_value`, `duration_unit`, `start_date`, `expiry_date`, `list_price`, `discount_amount`, `final_amount`, `currency`, `payment_status`, `status`, `note`, `cancelled_at`, `cancelled_by_membership_id`, `cancellation_reason`, `created_by_membership_id`, `created_at`, and `updated_at`.

`GET /api/v1/memberships/` accepts only `status`, `member_id`, `payment_status`, `limit` (default 25, maximum 100), and an opaque `after` cursor bound to that gym, those filters, and `start_date DESC, id DESC` order. Clients send `meta.next_after` back only as `after`. A cursor from another gym, filter, or order is `400`. A `member_id` filter must name a member in the selected gym. A missing or cross-gym member id returns `404` and does not return an empty page. A same-gym member with no memberships returns an empty page.

`PATCH /api/v1/memberships/{id}/` accepts only `note`. The body must contain that field. Snapshots, totals, currency, status, payment state, cancellation evidence, actors, and timestamps stay immutable.

Cancel request field: nonblank `reason` of at most 500 characters. The first successful cancel from `PENDING`, `ACTIVE`, or `SUSPENDED` sets `CANCELLED`, `cancelled_at`, `cancelled_by_membership_id` to the current actor, and the trimmed reason. Repeating that same reason for the same actor returns the original cancellation and does not move `cancelled_at`. A different reason, a different actor, or a cancel from `EXPIRED` is `409 MEMBERSHIP_STATE_CONFLICT` and leaves the stored evidence unchanged.

Suspend and resume accept an empty body, lock the member and membership, and are deterministic. Suspend changes `ACTIVE` to `SUSPENDED` only while the gym-local today is inside the inclusive start/expiry range. Repeating suspend on that same suspended membership returns it unchanged. Resume changes `SUSPENDED` back to `ACTIVE` under the same date rule and the same overlap guard, including other suspended rows. Repeating resume on that same active membership returns it unchanged. A pending, expired, cancelled, or out-of-range membership is `409 MEMBERSHIP_STATE_CONFLICT`.

`GET /api/v1/memberships/expiry/` is a separate route. It requires `bucket` plus the same `limit` and `after` cursor, bound to the gym, that bucket, and `expiry_date ASC, id ASC`. Buckets use the gym-local today and are mutually exclusive, so a membership appears in at most one bucket:

- `expired`: `expiry_date < today` and status is `ACTIVE`, `SUSPENDED`, or `EXPIRED`.
- `today`: `expiry_date = today` and status is `ACTIVE` or `SUSPENDED`.
- `within_3_days`: `today < expiry_date <= today + 3 days` and status is `ACTIVE` or `SUSPENDED`.
- `within_7_days`: `today + 3 days < expiry_date <= today + 7 days` and status is `ACTIVE` or `SUSPENDED`.
- `within_30_days`: `today + 7 days < expiry_date <= today + 30 days` and status is `ACTIVE` or `SUSPENDED`.

`PENDING` and `CANCELLED` rows are absent from every bucket. Inclusive calendar expiry counts both the start date and the expiry date. A one-day plan expires on its start date. Month and year additions clamp to the target month length, including leap day, and the expiry is the day before that anniversary.

Membership-expiry email is system-generated by `schedule_membership_expiry_emails` and delivered by `deliver_membership_expiry_emails`. There is no client endpoint that supplies a destination address or directly sends the warning. Notification list, read, and delivery HTTP routes remain unpublished. For each configured warning day, an active eligible membership with a nonblank current `Member.email` may create one deduplicated `EMAIL` notification. The plain-text email uses the current member display name, snapshotted plan name, expiry date, and current gym contact or display fields. If the member has no email, the scheduler skips the warning without creating a notification, reporting an error, or falling back to SMS or phone. Repeated scheduler runs and delivery reruns use the deterministic deduplication contract from `DATABASE.md`. Production sending remains blocked until Django's email backend is an approved transactional provider that can honor `X-Idempotency-Key`.

### Check-ins — Phase 3

```text
GET  /api/v1/check-ins/
POST /api/v1/check-ins/
GET  /api/v1/check-ins/{id}/
POST /api/v1/check-ins/{id}/check-out/
POST /api/v1/check-in-challenges/
POST /api/v1/check-ins/scan/
```

Manual check-in is implemented first. `method` remains extensible. The exact integration credential/device model is deferred to the Phase 3 approved integration design; any future integration credential or device is gym-owned and RLS-protected and uses a dedicated ingestion contract rather than staff tokens.

Manual check-in request fields: `member_id`, optional `occurred_at` only for callers with `checkins.update`, and optional note. `method` is fixed server-side to `MANUAL`. Read-only/rejected: `gym_id`, selected `membership_id`, `check_in_at` when not explicitly backdated, `check_out_at`, method override, recorder/actors, device/source metadata, eligibility result, and status. Response returns the check-in plus an explicit eligibility snapshot. Requires `checkins.create`, check-in entitlement, active same-gym member, an eligible active membership selected by the service, bounded backdating, and duplicate-window validation under lock. Check-out/correction requires `checkins.update`; cross-tenant member/membership identifiers return `404`.

Check-in list and detail require `checkins.view`.

`POST /check-in-challenges/` requires `checkins.challenge.issue` and creates a short-lived onsite challenge for an authorized same-gym display device or staff actor; the plaintext challenge is returned once for QR rendering and only its hash/state is stored. `POST /check-ins/scan/` requires no staff permission because it is authorized by the scanning principal itself: an authenticated member-self mobile session, or a trainer session holding `checkins.create` within its assignment scope. It does not accept a caller-selected member or gym. The server atomically validates challenge gym, expiry, state/use bound, principal scope, membership eligibility, and duplicate window before creating a `QR` check-in. Expired, replayed, photographed-old, cross-gym, or principal-substituted challenges fail safely.

### Payments, sales, and receipts — Phase 4

```text
GET  /api/v1/payments/
POST /api/v1/payments/
GET  /api/v1/payments/{id}/
PATCH /api/v1/payments/{id}/
POST /api/v1/payments/{id}/submit/
POST /api/v1/payments/{id}/approve/
POST /api/v1/payments/{id}/reject/
POST /api/v1/payments/{id}/cancel/
POST /api/v1/payments/{id}/void/
POST /api/v1/payments/{id}/refund/
GET  /api/v1/sales/daily/?date=2026-08-15

GET  /api/v1/receipts/
GET  /api/v1/receipts/{id}/
POST /api/v1/receipts/{id}/void/
GET  /api/v1/receipts/{id}/download/
```

Payment creation returns a `DRAFT`; it creates no receipt, cash movement, or membership settlement. The creator submits the draft and a different authorized Manager or Owner approves it. Approval is the posting boundary and atomically returns the immutable posted payment, generated receipt, source settlement update, and cash transaction where applicable. Clients cannot submit a receipt number. Submit, approve, reject, cancel, void, and refund commands require an `Idempotency-Key` header.

Payment create/update fields while `DRAFT`: optional same-gym `member_id`, exactly one supported source reference such as `membership_id`, `member_service_package_id`, `pt_sale_id`, or `retail_sale_id`, `purpose` (`MEMBERSHIP`, `PT`, `CLASS`, `PRODUCT`, `OTHER`), `description`, positive decimal-string `amount`, `currency`, `method`, `business_date`, optional `received_at`, and optional `reference_number`. Member is required for member-owned sources and optional for walk-in retail. Read-only/rejected: `gym_id`, `entry_type`, `status`, submission/approval/posting/actor fields, settlement totals, original/reversal IDs, cash transaction ID, receipt ID/number/snapshots, and all timestamps not explicitly accepted. A successful `201` returns the editable draft. Submit has no writable business fields and freezes the draft. Approve accepts optional `cash_session_id` when the payment method is cash; reject requires `rejection_reason`; cancel requires `reason` and is valid only from `DRAFT`. Approval revalidates all source, amount, currency, and cash-session state before posting.

Void request fields: required `reason`, optional `business_date`, and optional `cash_session_id`. Refund request fields: positive decimal-string `amount`, `reason`, `method`, `business_date`, optional `reference_number`, and optional cash session. Original payment amount/status, refundable balance, entry type, reversal/refund IDs, receipt number, actors, and derived settlement state are read-only. Both responses include original and corrective payment entries, receipt documents, cash effects, and updated refundable/settlement totals.

Payment list and detail require `payments.view`. Payment create/update/submit/approve/void/refund require `payments.create`/`payments.update`/`payments.submit`/`payments.approve`/`payments.void`/`payments.refund` respectively; reject requires `payments.approve` and cancel requires `payments.update`. Every action additionally requires finance entitlement, same-gym source/member/session, exact currency/scale, valid source and cash-session state, and `Idempotency-Key` on commands. Approval or rejection requires an actor different from the creator and submitter; RECEPTIONIST never receives `payments.approve`. Posted facts are never edited. Refunds lock the original and cannot exceed authoritative refundable balance.

`GET /sales/daily/?date=` requires `reports.financial` and returns posted, non-void payments for one gym-local business date grouped by purpose and method, with new-member/renewal/PT/class/product/other revenue, refunds/reversals, and net total. Product output also reconciles sold quantity, cost of goods sold, and gross profit from posted retail sale lines. Pending, rejected, cancelled, and draft records are excluded. Before Phase 5, PT/class totals are `"0.00"`; after Phase 5 they use the same posted service-package/PT payments as dashboard/report selectors.

Receipt list/detail/download requires `receipts.view`. `POST /receipts/{id}/void/` requires `receipts.void`, a reason, same-gym issued receipt, finance entitlement, and `Idempotency-Key`; it never relies on `payments.void`.

### Product inventory and retail sales — Phase 4

```text
GET  /api/v1/product-categories/
POST /api/v1/product-categories/
GET  /api/v1/products/
POST /api/v1/products/
GET  /api/v1/products/{id}/
PATCH /api/v1/products/{id}/
GET  /api/v1/products/low-stock/
GET  /api/v1/stock-movements/
POST /api/v1/stock-movements/adjust/

GET  /api/v1/retail-sales/
POST /api/v1/retail-sales/
GET  /api/v1/retail-sales/{id}/
PATCH /api/v1/retail-sales/{id}/
POST /api/v1/retail-sales/{id}/submit/
POST /api/v1/retail-sales/{id}/approve/
POST /api/v1/retail-sales/{id}/reject/
POST /api/v1/retail-sales/{id}/cancel/
POST /api/v1/retail-sales/{id}/void/
```

Products, categories, stock, sales, and every line are selected only from the active gym. Product create/update accepts gym-local SKU/barcode, name, category, unit, sale price, default/current unit cost, low-stock threshold, and active state; `gym_id`, quantity on hand, sold quantity, revenue, cost of goods sold, and profit are read-only. Manual stock adjustment accepts product, signed quantity, movement reason, business date, and required explanation; it requires `inventory.adjust`, idempotency, and audit.

Product-category, product, low-stock, and stock-movement reads require `inventory.view`; category and product create/update require `inventory.manage`. Retail-sale list and detail require `retail.view`; draft create requires `retail.create`; draft update requires `retail.update`; submit requires `retail.submit`; approve requires `retail.approve`; reject requires `retail.approve` and cancel requires `retail.update`; void requires `retail.void`. Every retail and stock command additionally requires inventory/retail feature entitlement, same-gym product/member/session references, exact decimal and currency checks, valid state, and `Idempotency-Key`. Approval or rejection requires an actor different from the creator and submitter; RECEPTIONIST never receives `retail.approve`.

A retail-sale draft accepts an optional same-gym member, business date, payment method/reference, and one or more unique product lines with positive quantity and allowed discount. The server resolves current prices/costs and returns calculated totals. Submission freezes input. Approval by a different Manager/Owner locks products in deterministic order, revalidates available stock and price/cost policy, posts immutable sale-line snapshots, deducts stock, and creates Payment/Receipt/Cash effects atomically. Returns and voids create linked reversing records. Negative stock, cross-gym products, client totals/cost/profit, duplicate posting, and self-approval are rejected.

### Expenses — Phase 4

```text
GET  /api/v1/expense-categories/
POST /api/v1/expense-categories/
PATCH /api/v1/expense-categories/{id}/

GET  /api/v1/expenses/
POST /api/v1/expenses/
GET  /api/v1/expenses/{id}/
PATCH /api/v1/expenses/{id}/
POST /api/v1/expenses/{id}/submit/
POST /api/v1/expenses/{id}/approve/
POST /api/v1/expenses/{id}/reject/
POST /api/v1/expenses/{id}/cancel/
POST /api/v1/expenses/{id}/void/
```

Only drafts are editable. A receipt uses the file upload flow and a tenant-scoped file asset ID. Reception may create and submit drafts but cannot approve them.

Expense-category and expense list/detail reads require `expenses.view`. Protected `system_key` categories can never be created, renamed, or deleted through these routes. The stable permission code governing custom expense-category create/update is not yet assigned in the Phase 0 catalog; it is an open decision that must be resolved before Phase 4 implementation, and until then no route may fall back to a broader finance permission.

Expense draft create/update fields: `category_id`, `business_date`, `description`, positive decimal-string `amount`, `currency`, `payment_method`, optional `receipt_file_asset_id`, and optional policy note. Read-only/rejected: `gym_id`, `status`, posting/void/cancel/reversal fields, actors, cash effects, and calculated totals. Only `DRAFT` is mutable.

Submit has no writable business fields and freezes the draft. Approve accepts optional `cash_session_id` when cash; reject requires `rejection_reason` and also requires `expenses.approve`; cancel requires `reason`, `expenses.update`, and `DRAFT`. Void requires `reason`, optional `business_date`, and optional destination/open cash session for the correcting movement. Statuses, approval/reversal amount/link, actors, differences, and cash transaction are server-owned. Approval/voiding returns the original plus immutable posting/reversal and cash effects. Commands require `expenses.create/update/submit/approve/void` as applicable, expense entitlement, same-gym category/file/session, clean file status, valid business date/currency, valid state transition, different creator/approver, and `Idempotency-Key` for submit, approve, reject, cancel, and void. Manual requests selecting protected `STAFF_SALARY` or `PT_COMMISSION` categories return `422 PROTECTED_EXPENSE_CATEGORY`.

### Salaries — Phase 4

```text
GET   /api/v1/salary-periods/
POST  /api/v1/salary-periods/
GET   /api/v1/salary-periods/{id}/
PATCH /api/v1/salary-periods/{id}/
PUT   /api/v1/salary-periods/{id}/lines/
POST  /api/v1/salary-periods/{id}/submit/
POST  /api/v1/salary-periods/{id}/approve/
POST  /api/v1/salary-periods/{id}/reject/
POST  /api/v1/salary-periods/{id}/cancel/
POST  /api/v1/salary-periods/{id}/void/
```

Salary period create/update accepts `period_start`, `period_end`, `business_date`, and `description`. Line replacement while draft accepts unique lines containing `staff_profile_id`, positive decimal-string `amount`, `currency`, `payment_method`, optional external reference, and description. Salary values are entered per period; no fixed amount is read from staff or PT configuration. Submission freezes the period and lines. Approval accepts cash-session mappings for cash-paid lines, requires a different authorized Manager or Owner, and atomically posts one protected `STAFF_SALARY` expense per line plus cash movements. Reject requires `rejection_reason` and has no financial effect. Cancel requires `reason` and is valid only from `DRAFT`. Void requires `reason` and reverses all linked expenses/movements.

Salary list/detail/create/update/submit/approve/void require `salaries.view/create/update/submit/approve/void` as applicable; reject requires `salaries.approve` and cancel requires `salaries.update`. Every action additionally requires salary entitlement, same-gym staff/session references, valid period and currency, separation of duties, and `Idempotency-Key` for submit, approve, reject, cancel, and void.

### Cash book — Phase 4

```text
GET  /api/v1/cash-sessions/
POST /api/v1/cash-sessions/open/
GET  /api/v1/cash-sessions/{id}/
POST /api/v1/cash-sessions/{id}/adjustments/
POST /api/v1/cash-sessions/{id}/reconcile/
GET  /api/v1/cash-transactions/
```

The reconcile request supplies actual closing cash and an optional reason. Expected closing and difference are server-calculated. A non-zero difference without a reason is rejected.

Open request fields: `register_code`, `business_date`, and nonnegative decimal-string `opening_cash`. Reconcile fields: decimal-string `actual_closing` and optional `difference_reason`. Manual adjustment fields: `direction`, positive decimal-string `amount`, `currency`, `business_date`, `description`, and required `reason`. Read-only/rejected everywhere: `gym_id`, all status/actor fields, `cash_sales`, `cash_expenses`, `manual_net`, `expected_closing`, `difference`, closing timestamps, source type/link, and reversal IDs.

Open, reconcile, and adjustment responses return the explicit cash session plus created immutable cash transaction(s). They require `cashbook.open`, `cashbook.reconcile`, or elevated `cashbook.adjust`, the cashbook entitlement, same-gym register/session, correct currency/business day, open-session state, and `Idempotency-Key`. Reconcile locks the session and recomputes every total; a nonzero server-calculated difference requires a reason.

For the protected RECEPTIONIST template, `cashbook.view`, `cashbook.open`, and `cashbook.reconcile` are conditional. After opening a session, Reception may view it, use it for ordinary front-desk cash operations, and reconcile/close it only while `opened_by_membership_id` equals the current Reception `GymMembership`. Another receptionist's session and every cross-gym session are inaccessible. “Operate” permits normal submitted-draft preparation and the automatic cash effects created later by an authorized posting service; it does not grant `cashbook.adjust`, `payments.approve`, `expenses.approve`, `salaries.approve`, `pt.sales.approve`, or any posting command. Reconcile is the close command: the server locks the session, derives final totals from immutable transactions, records the close once, and closed sessions remain immutable.

### Trainers, assignments, PT sales, and commissions — Phase 5

```text
GET  /api/v1/trainers/
GET  /api/v1/trainers/{id}/assigned-members/
GET  /api/v1/trainer-assignments/
POST /api/v1/trainer-assignments/
GET  /api/v1/trainer-assignments/{id}/
POST /api/v1/trainer-assignments/{id}/end/
POST /api/v1/trainer-assignments/{id}/cancel/
GET  /api/v1/pt-sales/
POST /api/v1/pt-sales/
GET  /api/v1/pt-sales/{id}/
PATCH /api/v1/pt-sales/{id}/
POST /api/v1/pt-sales/{id}/submit/
POST /api/v1/pt-sales/{id}/approve/
POST /api/v1/pt-sales/{id}/reject/
POST /api/v1/pt-sales/{id}/cancel/
POST /api/v1/pt-sales/{id}/void/
GET  /api/v1/pt-commissions/
GET  /api/v1/pt-commissions/{id}/
POST /api/v1/pt-commissions/{id}/record-payment/

GET  /api/v1/service-packages/
POST /api/v1/service-packages/
GET  /api/v1/member-service-packages/
GET  /api/v1/member-service-packages/{id}/ledger/

GET  /api/v1/class-types/
POST /api/v1/class-types/
GET  /api/v1/class-schedules/
POST /api/v1/class-schedules/
GET  /api/v1/class-occurrences/
GET  /api/v1/class-bookings/
POST /api/v1/class-bookings/
POST /api/v1/class-bookings/{id}/attend/
POST /api/v1/class-bookings/{id}/cancel/

GET  /api/v1/pt-bookings/
POST /api/v1/pt-bookings/
POST /api/v1/pt-bookings/{id}/attend/
POST /api/v1/pt-bookings/{id}/cancel/
```

Commission values are calculated from the trainer percentage snapshot. A trainer's queryset is additionally scoped to their own assignments and commission data.

PT/class packages expose granted, consumed, and remaining sessions calculated from the append-only session ledger. Clients cannot set remaining sessions. Class scheduling validates trainer, location, capacity, recurrence, and time conflicts. Booking requires a same-gym active member and eligible package with remaining sessions; attendance or a policy-applicable no-show atomically appends one idempotent consumption entry. Cancellation/correction follows gym policy and uses reversal entries rather than editing consumed history. Concurrent booking cannot exceed class capacity, double-book a trainer/member/location, or consume one remaining session twice.

Service-package, member-service-package, ledger, PT-schedule, and PT-booking reads require `pt.schedule.view`; service-package definition and PT-booking create/cancel require `pt.schedule.manage`. Authorized session-ledger corrections require `pt.sessions.adjust`; no other route may append a correcting ledger entry.

Class-type, schedule, occurrence, and booking reads require `classes.view`; class-type and schedule create/update require `classes.manage`; booking create requires `classes.book`; attendance and no-show marking require `classes.attendance`; booking cancellation requires `classes.book` for the booking's own member scope and `classes.manage` otherwise.

Trainer and assignment list/detail routes require `pt.assignments.view`. Trainer assignment create/end/cancel requires `pt.assignments.manage`, PT entitlement, same-gym active trainer/member, valid dates, and no overlapping assignment. Create fields are `trainer_staff_profile_id`, `member_id`, `effective_from`, optional `effective_until`, and optional `reason`; end/cancel fields are `effective_until` where applicable and required `reason`. Read-only/rejected fields are `gym_id`, status, ended timestamp, actors, and assignment-derived member/trainer details.

PT sale create/update fields while draft: `member_id`, `trainer_staff_profile_id`, `service_date`, `description`, positive decimal-string `gross_fee`, `currency`, and optional payment draft using the payment-create writable fields. Read-only/rejected: `gym_id`, sale/approval status, payment/receipt IDs and numbers, all commission percentage snapshots and amounts, gym share, paid/outstanding totals, actors, and posting timestamps. Create returns a draft. Submit freezes the sale and optional payment. Approve accepts cash-session data where required and atomically returns the posted sale, payment/receipt, and generated commission accrual. Reject requires `rejection_reason` and creates no payment, receipt, commission, or cash movement. Cancel requires `reason` and is valid only from `DRAFT`.

Commission payment request fields: positive decimal-string `amount`, `business_date`, `payment_method`, optional `cash_session_id`, optional external reference, and required/optional policy note. Read-only/rejected: commission basis, percentage snapshot, `commission_amount`, `gym_share`, prior/new `paid_amount`, `outstanding_amount`, status, expense/reversal IDs, actors, and timestamps. Response returns the commission before/after summary and linked immutable expense/cash movement.

PT sale list/detail, create/update, submit, approve, and void require `pt.sales.view`, `pt.sales.create`/`pt.sales.update`, `pt.sales.submit`, `pt.sales.approve`, and `pt.sales.void` respectively; reject requires `pt.sales.approve` and cancel requires `pt.sales.update`. Approval or rejection requires a different actor from creator/submitter; RECEPTIONIST cannot approve. Commission list/detail requires `pt.commissions.view`; commission payment requires `pt.commissions.pay`. Every financial command requires `Idempotency-Key`, PT feature entitlement, same-gym references, trainer assignment/object-scope rules, valid states, exact decimal/currency checks, and authoritative calculation under lock.

### Reports and dashboard — Phase 6

```text
GET /api/v1/reports/daily/?date=2026-08-15
GET /api/v1/reports/monthly/?year=2026&month=8
GET /api/v1/reports/cash-book/?date_from=...&date_to=...
GET /api/v1/reports/profit-loss/?date_from=...&date_to=...
POST /api/v1/reports/exports/
GET /api/v1/reports/exports/{id}/
```

Financial reports require `reports.financial`. Export creation/download additionally requires `reports.export`. Export jobs are introduced only when synchronous limits are exceeded. Every selector begins with active-gym scope.

Report request contracts are read-only query serializers:

- daily: required `date`;
- monthly: required `year` and `month`;
- cash book and profit/loss: required `date_from`, `date_to`, optional allowlisted grouping/filter values;
- export create: `report_type`, the corresponding validated bounded parameters, and `format` from an allowlist.

Responses contain read-only `gym` display identity, gym timezone/currency, normalized date range, generation instant, source-status policy, rows, and server-calculated totals/subtotals. No client total, formula, gym selector, actor, or source status override is accepted. Operational reports require `reports.view`; financial totals and exports require `reports.financial`; each also checks its report/export entitlement and configured row/date limits.

### Signed files — Phases 2-7 by owning feature

```text
POST /api/v1/files/upload-intents/
POST /api/v1/files/{id}/complete/
GET  /api/v1/files/{id}/download-url/
DELETE /api/v1/files/{id}/
```

Signed URLs are short-lived. Complete verifies object metadata. File IDs from another gym return `404`.

Upload-intent request fields: `original_name`, declared `content_type`, `byte_size`, `sha256`, `purpose`, and optional intended `owner_type`/`owner_id`. Read-only/rejected: `gym_id`, `object_key`, signed URL fields, scan/retention status, detected metadata, uploader/actors, and timestamps. Response returns `file_id`, constrained signed upload `url`, required headers, and `expires_at`; object credentials and bucket-wide access are never returned.

Complete request fields: `sha256` and `byte_size` confirmation plus any provider upload token required by the intent. Response returns explicit file metadata and authoritative `scan_status`; it does not claim attachment eligibility until clean/verified. Download response returns a short-lived signed `url`, expiry, and safe filename/content type. Intent/complete require `files.upload`, download requires `files.view`, and delete requires `files.delete`; each also checks owning-feature entitlement, same-gym owner/object, purpose-specific size/type limits, clean/retention state, and object conditions. Completion verifies actual object size, checksum, and detected type; client metadata is never authoritative.

### Audit and notifications — Phases 1B and 7

```text
GET  /api/v1/audit-logs/
GET  /api/v1/notifications/
POST /api/v1/notifications/{id}/read/
GET  /api/v1/announcements/
POST /api/v1/announcements/
GET  /api/v1/announcements/{id}/
PATCH /api/v1/announcements/{id}/
POST /api/v1/announcements/{id}/publish/
POST /api/v1/announcements/{id}/cancel/
```

`GET /api/v1/audit-logs/` is the tenant audit read. It accepts only a staff access token and requires the active permission `audit.view`. A platform principal is rejected. The route has no gym selector. Every query filters the authenticated gym in the ORM and is also covered by forced row-level security.

An active owner with `audit.view` receives every same-gym tenant audit row. Collection metadata includes `scope=GYM`. Any other caller with `audit.view`, including a conditional manager or accountant grant, receives only rows whose `actor_membership_id` equals that caller's membership. Their metadata `scope` is `SELF`, so a partial page is not mistaken for the full gym audit. Rows with no actor membership, other staff, other owners, and platform-written tenant rows stay outside `SELF`.

Each item is `id`, nullable `actor_membership_id`, nullable `impersonator_membership_id`, `action`, `module`, `target_type`, `target_id`, redacted `old_values`, redacted `new_values`, `request_id`, `outcome`, and `occurred_at`. The response omits `gym_id`, the actor's global user and email, IP address, user agent, created and updated timestamps, tokens, secrets, credentials, and passwords. Credential-like keys are redacted again when a row is serialized, including nested objects and lists.

Optional strict query fields are `action` (maximum 100 characters), `module` (maximum 50), `outcome` (`SUCCESS`, `DENIED`, or `ERROR`), `target_type` (maximum 100), `target_id` (UUID), `limit` (default 25, maximum 100), and opaque `after` (maximum 2048). Unknown or repeated parameters are rejected. Ordering is fixed at `occurred_at DESC, id DESC`. `after` is signed, expires after 24 hours, and is bound to the gym, the effective scope, the caller membership when the scope is `SELF`, and every filter. A tampered, expired, cross-filter, cross-membership, or cross-gym cursor returns `400`. A `target_id` from another gym returns an empty page and does not reveal that the row exists. Audit records have no update or delete endpoint.

The browser page `/audit-logs` requires `audit.view` and reads through same-origin `GET /api/operations/audit-logs`. That BFF route forwards only the seven allowlisted query fields and rejects unknown, duplicate, blank, or malformed values before any upstream request. It has no mutation method. The page labels `scope=GYM` as all gym events and `scope=SELF` as the caller's own actions.

Notification list/read requires `notifications.view`; delivery configuration or lifecycle commands require `notifications.manage`. Announcement reads require `announcements.view`; create/update/publish/cancel requires `announcements.manage`. Announcement input accepts an allowlisted type, title/body, optional schedule/media, and a fixed tenant-scoped audience selector; recipient IDs, delivery counts, status, publisher, and provider fields are server-owned.

## 7. Member mobile API — Phase 7

Member routes enforce gym scope and self scope:

```text
POST /api/v1/member-auth/login/
POST /api/v1/member-auth/refresh/
POST /api/v1/member-auth/logout/
GET  /api/v1/member/me/
PATCH /api/v1/member/me/
GET  /api/v1/member/dashboard/
GET  /api/v1/member/membership/
GET  /api/v1/member/payments/
GET  /api/v1/member/check-ins/
GET  /api/v1/member/service-packages/
GET  /api/v1/member/schedule/
POST /api/v1/member/class-bookings/
POST /api/v1/member/check-ins/scan/
GET  /api/v1/member/notifications/
POST /api/v1/member/notifications/{id}/read/
POST /api/v1/member/devices/
PATCH /api/v1/member/devices/{id}/
DELETE /api/v1/member/devices/{id}/
```

The mobile scan endpoint accepts a short-lived onsite gym challenge and derives member and gym exclusively from the authenticated member session. It does not encode or accept personal information or authoritative permissions. Authorized trainer mobile check-in uses the staff/trainer principal contract and cannot impersonate an arbitrary member.

Member auth request/response fields mirror the staff rotation contract but use member-specific serializers, audience, token types, sessions, and rate limits. Every response resource is selected from token `gym_id` plus token `member_id`; endpoints do not accept another member selector. Profile update accepts only the allowlisted self-service contact/profile fields and never `gym_id`, `member_id`, `linked_user_id`, status, membership/payment totals, or actors.

Member package and schedule responses include PT/class package expiry, granted/used/remaining sessions, trainer/class display details, and gym-local booking times. Announcement notifications contain only published same-gym event/promotion content. Device registration and push-consent management are authenticated member-self operations; notification delivery never accepts a caller-selected recipient or gym.

## 8. Pagination, filtering, and concurrency

Collection parameters:

```text
limit=25             # default; maximum 100
after=<opaque>       # cursor
ordering=-created_at # allowlisted values only
```

Cursors encode a stable tuple such as `(created_at, id)`. Invalid cursors return `400`.

`after` is opaque, signed or integrity-protected, and bound to route, selected gym, normalized filters, and ordering. Clients must not construct it. The server returns deterministic tie-break ordering and never exposes raw database offsets or cross-tenant cursor state.

Mutable resources may expose a `version` and `ETag`. Sensitive updates require `If-Match`; stale writes return `412`.

Bulk endpoints are not created until their atomicity, per-item authorization, limits, and response behavior are specified.

## 9. Idempotency

`Idempotency-Key` is required for every financial command, including payment submit/approve/reject/cancel/void/refund, expense submit/approve/reject/cancel/void, salary submit/approve/reject/cancel/void, cash open/adjust/reconcile, PT sale submit/approve/reject/cancel/void, commission payment/reversal, and every receipt-affecting command. It is also required for gym provisioning.

The server keys records by gym, actor, method, route, and key. Repeating the same request returns the original result. Reusing a key with a different request hash returns `409 IDEMPOTENCY_KEY_REUSED`.

Keys are high-entropy client-generated strings with a documented length limit. The canonical request hash includes semantically relevant body and query fields. Concurrent duplicates wait for or safely report the in-progress operation; completed responses replay the original status/body. Validation that can affect side effects, idempotency claim, locks, writes, receipt allocation, and audit append occur in one transaction.

## 10. API security requirements

- Strict serializer field allowlists prevent mass assignment.
- Relationship fields use tenant-scoped querysets.
- Viewsets scope list and object retrieval; DRF object permission alone is insufficient for lists.
- Custom actions call object permission checks.
- Rate limits distinguish authentication, ordinary reads, writes, exports, and administrative actions.
- Login-ticket issue and gym selection have dedicated account/source/ticket-aware throttles. Logs, audit events, traces, errors, and rate-limit keys never contain ticket plaintext.
- CORS uses explicit production origins.
- Cookie-based browser mutations require CSRF protection.
- Query complexity, date ranges, page sizes, uploads, and exports are bounded.
- API schemas and generated TypeScript types are versioned in CI.

### Permission and feature-entitlement evaluation

Authentication alone never grants a domain action. For every protected endpoint, the API:

1. completes the token/session/user/gym/selected-membership validation sequence;
2. resolves current active roles and stable permission codes from the selected membership;
3. checks the endpoint action permission;
4. loads relationships through gym-scoped serializer querysets/selectors and returns `404` for cross-tenant identifiers;
5. enforces object conditions, including trainer assignment and member self scope;
6. resolves the effective `GymFeatureEntitlement`, effective dates, enabled state, and applicable usage limit;
7. validates command state and business invariants inside the transaction immediately before mutation.

A valid same-tenant target with a missing permission or disabled/limited feature returns `403` with a stable safe code such as `PERMISSION_DENIED`, `FEATURE_NOT_ENTITLED`, or `FEATURE_LIMIT_REACHED`. The response must not disclose permissions, roles, subscription details, or foreign-tenant existence beyond what the caller may read.

## 11. Delivery map

The implementation baseline for Phase 1A is pinned to Python 3.14.7, Django 5.2.17 LTS, Django REST Framework 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. Compatibility is verified through installation, schema generation, strict type/build checks, the PostgreSQL migration/RLS suite, and BFF/API contract tests.

- **Phase 1A — foundation:** envelopes, errors, request IDs, cursor pagination, staff JWT/refresh/session contracts, the 5-minute single-use multi-gym login-selection bridge, selected tenant context, strict serializers, permission/entitlement framework, and tenant-isolation behavior.
- **Phase 1B — Super Admin, gym, staff, roles, settings:** platform administration, transactional gym/owner provisioning, staff invitation/acceptance, role and permission management, membership-role assignment, gym profile/settings, audit reads, and foundational files.
- **Phase 2 — membership:** members, membership plans, memberships, expiry, derived PT-client badge/filter, member summaries/history, and membership-owned files.
- **Phase 3 — check-in:** manual check-in/check-out, eligibility/duplicate rules, rotating onsite gym challenges, and authenticated unattended mobile scanning.
- **Phase 4 — finance and inventory:** payment, expense, and retail-sale draft/manager-approval workflows; products and append-only stock movements; receipts, void/refund/returns, salary periods/lines, cash sessions/transactions/reconciliation, and signed financial evidence files.
- **Phase 5 — PT and classes:** trainer reads, assignments, service packages and remaining-session ledger, trainer/class schedules and bookings, attendance/no-show/correction, PT sale approval, commission accruals, and commission payments.
- **Phase 6 — reporting:** explicit owner attendance/expiry/pending-payment/ranked-revenue/product metrics, daily/monthly/cash-book/profit-loss/inventory/class/PT reports, and bounded exports.
- **Phase 7 — mobile:** member authentication, strict tenant+self API, onsite QR scanner, PT/class schedule and remaining sessions, expiry reminders, event/promotion announcements, push notifications, and mobile file/profile boundaries.
- **Phase 8 — hardening and deployment:** compatibility/schema gates, attack and isolation tests, rate/load limits, observability, key rotation, file security, deployment checks, and production operational controls. Phase 8 adds no undocumented business API.

A delivery annotation never means implementation exists. Each phase remains unimplemented until its approved development work, migrations, tests, security review, and schema publication are complete. Phase 1A and Phase 1B have passed that bar. Published OpenAPI includes those routes, including role create, update, archive, and permission replacement, plus MembershipPlan and Member list, create, detail, update, and archive, and Membership list, create, detail, note update, cancel, suspend, resume, and expiry buckets. Membership-expiry scheduling and delivery commands are implemented outside HTTP. Member summary and history, notification HTTP routes, payment, and receipt endpoints remain unpublished. Production expiry delivery remains gated on an idempotency-capable transactional provider.

Accepted refinements: [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md) defines browser authentication, and [ADR 0003](./adr/0003-reception-cash-session-authority.md) defines Reception's conditional cash-session scope. MFA is TOTP and is mandatory for platform administrators and Owners, with recent step-up, encrypted secrets, and replay protection. Broader role-based MFA may be decided later. Email uses Django's configurable email backend. Tests and end-to-end runs use non-delivery backends. Production must configure an approved transactional provider before invitation, password-recovery, or Phase 2 expiry delivery is production-ready; vendor selection and that deployment configuration remain deferred. Jurisdiction remains a production-gate decision, device design remains Phase 3, and Expo versus bare React Native remains Phase 7.
