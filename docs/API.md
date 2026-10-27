# Phase 0 REST API Contract

> Part of the Phase 0 contract set indexed in [`README.md`](./README.md). Related: [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`DATABASE.md`](./DATABASE.md), [`SECURITY.md`](./SECURITY.md), [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md), [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md), [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).

This document is the authoritative public HTTP contract for the planned API. Phase 0 is documentation only: no endpoint or feature described here is implemented by this contract. Later implementation, OpenAPI schemas, serializers, generated client types, and contract tests must conform to it.

Phase 1A delivers foundation contracts only. Members, check-in, finance, PT, reports, and mobile endpoints are outside that phase; their publication is annotated in the later sections.

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

`error.code` stays a stable English identifier. `Accept-Language: en` or `Accept-Language: my` (`mm` is an alias) chooses the language of `error.message` and `error.fields`. A missing or unrecognized header keeps those strings in English. An idempotent replay returns the first request's stored body in that request's language, and the idempotency hash does not include the language. Success responses keep `"message": "Success"`.

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
- Password recovery is global `User` scope for platform-administrator, staff, and linked-member identities, recorded in [ADR 0010](./adr/0010-production-provider-readiness.md). Browser calls use same-origin BFF routes `/api/auth/password/forgot` and `/api/auth/password/reset`. Django remains authoritative. The pages are `/forgot-password` and `/reset-password?token=`.
- `POST /auth/password/forgot/` request: `email` only. The response is a constant `200` envelope whether or not the address matches a user, whether mail is sent, and whether the provider fails. The body never reveals account existence, verification, a previous request, or a provider failure. A live token lasts 30 minutes, is high-entropy, single-use, and hash-only under `PASSWORD_RESET_TOKEN_PEPPER`. One live token exists per user. The plaintext is mailed as `/reset-password?token=` and is never stored, logged, or returned.
- `POST /auth/password/reset/` request: `reset_token`, `password`, and `password_confirmation`. The command verifies the peppered hash, user binding, 30-minute expiry, and unconsumed state under lock. It enforces the existing password validators and rejects a password that does not differ from the current password. It consumes the token, revokes every `RefreshSession`, live `GymSelectionTicket`, and live `MFAChallenge` for that user, and increments `authz_version` on every linked `GymMembership` and `Member`. Onsite check-in challenges are not revoked. [ADR 0012](./adr/0012-fcm-push-delivery.md) revokes that user's member devices in the same transaction, clears their ciphertext, and leaves MFA enabled. Success is `204` and issues no access or refresh credential, so login is required afterward. A failed reset is not `204`.
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

Forgot and reset are unauthenticated BFF commands. The reset page is `/reset-password?token=`. The query token is forwarded once in the reset body and is not written to a cookie, `localStorage`, or `sessionStorage`. Every BFF route that mutates cookie-authenticated or selection-ticket state, including login, refresh, select-gym, logout, and logout-all, requires the approved CSRF token/header pair plus strict allowlisted `Origin` validation; strict `Referer` validation is required only when `Origin` is unavailable. A missing or mismatched signal fails closed. CORS does not replace these checks.

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

`POST /api/v1/member-auth/login/`, `POST /api/v1/member-auth/refresh/`, and `POST /api/v1/member-auth/logout/` are the only published member-auth routes. Login authenticates an already linked global user and issues a session only when that user has exactly one active member at an active gym with a trial or active subscription. Zero links, several links, an MFA-enabled identity, and a caller-supplied `member_id` or `gym_id` do not select a member. The several-link case uses the same generic credential error as an unknown account; no selection ticket is issued. See [ADR 0005](adr/0005-member-session-foundation.md). Linking and member MFA completion remain unpublished. A later second link does not become a login selector, and changing or archiving a link revokes that member's sessions.

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
GET   /api/v1/gym/logo/
PUT   /api/v1/gym/logo/
DELETE /api/v1/gym/logo/
GET   /api/v1/settings/
PATCH /api/v1/settings/
```

Gym profile read/update requires `gym.view`/`gym.update`; settings read/update requires `settings.view`/`settings.manage`.

`GET /gym/logo/` returns the gym's uploaded JPEG, PNG, or WebP, or `404` when none is stored. `PUT /gym/logo/` accepts one `multipart/form-data` field named `logo`, at most 1 MB, and replaces the current image. `DELETE /gym/logo/` removes it. Logo bytes stay off the gym profile and off audit records. The profile response includes read-only `has_logo`.

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

Phase 6 Increment 1 publishes `GET /api/v1/dashboard/` and the tenant `/dashboard` page. `reports.view` returns `operational`. `reports.financial` adds `financial`; otherwise `financial` is null. Trainer-only finance closure still returns 404. Presets are `today`, `this_week`, `this_month`, `last_month`, and `this_year`. A custom range sends both `date_from` and `date_to` and is at most 366 inclusive days. The published response includes gym `timezone` and `currency`. Published overdue fields count expired memberships with a positive authoritative `UNPAID` or `PARTIAL` balance only; they do not invent PT or class debt. Top product rows are at most 10 after ranking. There are no report tables. Browser E2E is `web/e2e/phase6.spec.ts`. Gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). Increment 2 publishes the daily and monthly routes below. Increment 3 publishes the cash-book and profit/loss routes below. Increment 4 publishes the membership-expiry and check-in routes below and is gate-clean as of 2026-10-20. Increment 5 publishes the PT session and class routes below and is gate-clean as of 2026-10-20. Increment 6 publishes the product-sales, stock-movement, and inventory routes below and is gate-clean as of 2026-10-20. Increment 7 publishes `GET /api/v1/trainer/dashboard/` and the tenant `/pt/dashboard` page, plus `GET /api/v1/trainer/reports/sessions/`, for protected TRAINER callers with `reports.view` and the `reports` and `pt` entitlements. Those reads are own-profile only and include no money. Dashboard presets and a custom range of at most 366 inclusive days return `generated_at`, `trainer_staff_profile_id`, `active_assigned_members`, session counts, and at most 10 `upcoming_bookings`. The session report is own-profile daily zero-filled PT statuses. The dashboard selector uses 3 queries and the session-report selector uses 2. After authorization, the dashboard handler uses 4 queries and the session handler uses 2. Increment 7 is independently verified and gate-clean as of 2026-10-20. Gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). Increment 8 publishes synchronous CSV downloads for the eleven gym reports and is independently verified and gate-clean as of 2026-10-20. Trainer dashboard and session reports are not exportable. Phase 6 implementation is complete and its local CI-equivalent gate is closed. Production deployment remains separate and incomplete.

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
POST /api/v1/owners/{gym_membership_id}/cancel-invitation/
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

The owner endpoints are a separate OWNER-reserved workflow and are not authorized by `staff.manage` or `roles.manage`. Any active Owner may list the gym's Owners and invite an additional Owner after recent MFA/step-up verification. The invitation accepts `email`, `first_name`, `last_name`, optional `phone`, and optional `send_invitation`; email delivery is required until another secure delivery channel is approved. The server fixes `is_owner = true` and the protected OWNER role, so clients cannot submit role or permission IDs. `POST /owners/invitations/accept/` accepts the single-use invitation token plus password and confirmation through the trusted BFF, and creates or activates another same-gym Owner login without replacing existing Owners. `POST /owners/{gym_membership_id}/cancel-invitation/` requires recent MFA and withdraws a still-pending invitation: the link stops working, the invitation is retained as `REVOKED`, and that invited person leaves the owner list. An expired pending invitation also leaves the list. Revocation requires a different active Owner as actor, a reason, recent MFA, row locking, and proof that at least one other active Owner will remain. All owner changes increment authorization state and create audit events. Responses expose login/role status only; no share percentage, capital contribution, profit-distribution ratio, or legal-ownership value exists in Phase 0.

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
POST /api/v1/members/{id}/restore/
GET  /api/v1/members/{id}/summary/
GET  /api/v1/members/{id}/payments/
GET  /api/v1/members/{id}/check-ins/
```

Filters include allowlisted `status`, `search`, `joined_from`, `joined_to`, and `is_pt_client=true|false`. The API assigns member ownership from request context. `member_id` is unique only within the active gym.

Member list, detail, and summary require `members.view`. Create requires `members.create`. Update requires `members.update`. Archive requires `members.archive`. Restore requires `members.restore`. Published payment history requires `members.view` and `payments.view`. Published check-in history requires `members.view` and `checkins.view`.

Member create request fields: optional client-visible `member_number` only if policy allows manual numbering, `first_name`, `last_name`, optional `phone`, `email`, `gender`, `date_of_birth`, structured address, `join_date`, emergency contact fields, `notes`, and, on the published API only, tenant-scoped `profile_photo_asset_id`. The Phase 2 file implementation rejects a non-null photo on create. Read-only/rejected: `id`, `gym_id`, normalized fields, `linked_user_id`, `is_pt_client`, `status`, `archived_at`, actors, and timestamps. Response uses an explicit member serializer and includes those read-only server fields. `is_pt_client` is server-derived from a same-gym trainer assignment with `status = ACTIVE` whose effective date range contains the gym-local as-of date; it is returned by list/detail/summary and cannot be set directly. The service requires `members.create`, the members feature entitlement, date validity, and per-gym uniqueness. The published photo check is same-gym, `CLEAN`, and `ACTIVE`. The stricter Phase 2 owner and purpose rule is implemented with Signed files.

Published Member routes are list, create, detail, update, archive, restore, summary, payment history, and check-in history. Reads use `members.view`; creation uses `members.create`; update uses `members.update`; archive uses `members.archive`; restore uses `members.restore`. Summary reads use `members.view` and the members entitlement. Payment history requires both live permissions `members.view` and `payments.view`, and both active entitlements `members` and `finance`. Check-in history requires both live permissions `members.view` and `checkins.view`, and both active entitlements `members` and `memberships`. The gym comes from the access token. A member id from another gym returns `404`. A `gym_id` in the body or query returns `400`.

`GET /api/v1/members/` accepts only `status`, `search`, `joined_from`, `joined_to`, `is_pt_client` (`true` or `false`), `limit` (default 25, maximum 100), and an opaque `after` cursor bound to that gym, those filters, and `normalized_full_name,id` order. `is_pt_client=true` returns members with a same-gym `ACTIVE` trainer assignment whose effective range contains the gym-local as-of date; `false` returns the other members. Both filters stay in the same tenant-scoped member query. Clients send `meta.next_after` back only as `after`. The list response is `{success, data, meta, message}` and `meta` is `{has_more, next_after}`.

Omitted or blank `member_number` allocates the next `{prefix}-{sequence}` value from locked gym settings. An explicit number is accepted only when `allow_manual_member_numbers` is true. A duplicate number in the same gym returns `409 MEMBER_CONFLICT`.

`gender` is optional and normalized to `FEMALE`, `MALE`, `OTHER`, or `UNSPECIFIED`. Address fields are `address_line1`, `address_line2`, `city`, `region`, `postal_code`, and `country_code`. The published member validator accepts a same-gym `profile_photo_asset_id` only when that asset is `CLEAN` and `ACTIVE`, and null detaches the pointer. Storage metadata is not returned. The Phase 2 profile-photo routes are implemented under Signed files. Member update attaches only an asset that also has `purpose = PROFILE_PHOTO`, `owner_type = MEMBER`, and `owner_id` equal to that member. Member create must omit `profile_photo_asset_id` or send null, because an upload cannot name a member who does not exist yet; a non-null value on create is a field validation error and creates no member. Null on update detaches the pointer only and does not delete the asset.

Response fields are `id`, `member_number`, `first_name`, `last_name`, `normalized_full_name`, `phone`, `email`, `gender`, `date_of_birth`, `address`, `join_date`, `emergency_contact_name`, `emergency_contact_phone`, `notes`, `profile_photo_asset_id`, `linked_user_id`, `is_pt_client`, `authz_version`, `status`, `archived_at`, `created_at`, and `updated_at`. `is_pt_client` is the server-derived badge defined above. `linked_user_id` and `authz_version` are read-only. Gym and actor ids are omitted. Update accepts any nonempty subset of the create fields except `member_number`.

Archive accepts an empty body, sets `status` to `ARCHIVED`, records `archived_at` once, and increments `authz_version` once. Repeating archive returns that same member and does not move `archived_at`, `updated_at`, or `authz_version`.

`POST /api/v1/members/{id}/restore/` requires `members.restore` and the members entitlement. It accepts an empty body. A real transition sets `status` to `ACTIVE`, clears `archived_at`, records the updater, increments `authz_version` once, and writes one `MEMBER_RESTORED` audit event. Repeating restore, including restore of a member who is not archived, returns that same member and does not move `archived_at`, `updated_at`, `authz_version`, or the updater, and does not write another audit event. A `gym_id` or any other client field in the body returns `400`. A member id from another gym returns `404`. Restore does not delete the member.

`GET /api/v1/members/{id}/summary/` accepts no query parameters. Any query parameter, including `gym_id`, returns `400` with the strict field envelope. The gym comes only from the access token. A cross-gym member id returns `404`.

The response `data` fields are `member`, `current_membership`, `days_remaining`, `is_pt_client`, `membership_history`, `membership_history_has_more`, `payment_summary`, and `check_in_summary`. `member` uses the safe Member response. `current_membership` uses the published Membership response or null. `days_remaining` is the inclusive gym-local count `expiry_date - gym_today + 1`, floored at 0, or null when there is no current membership. `is_pt_client` is the same server-derived badge as the member serializer. `membership_history` is the latest 20 memberships ordered by `start_date DESC, created_at DESC, id DESC`. `membership_history_has_more` is true when a 21st row exists. `check_in_summary` is `{available, currently_checked_in, open_check_in_id, last_check_in_at, last_check_out_at}`. `available` is true. `currently_checked_in` and `open_check_in_id` come from the open visit, which is not assumed to be the latest row. The two timestamps come from the latest row. Callers who can read the summary can read these attendance facts, including callers without `checkins.view`.

`payment_summary` is not a payment list. It does not return payment rows, receipts, references, descriptions, methods, actor ids, or payment timestamps. Its fields are `available`, `currency`, `total_charged`, `total_refunded`, `net_paid`, and `outstanding_membership_balance`. `available` is true because the Payment and Membership tables are the authoritative ledger. Every amount is an exact two-decimal string. Only finalized rows with status `POSTED` or `VOID` count. `CHARGE` is positive and `REFUND` and `REVERSAL` are negative. `total_charged` is the positive finalized charge sum. `total_refunded` is the positive finalized refund plus reversal sum. `net_paid` is `max(0, charged - refunded)`. Outstanding is the sum, across that member's non-cancelled memberships, of `max(0, final_amount - signed finalized payments linked to that membership)`. Unlinked payments do not reduce outstanding. Draft, pending, rejected, and cancelled payments are excluded.

Current membership is the deterministic first row among `PENDING`, `ACTIVE`, and `SUSPENDED` memberships whose inclusive date range contains the gym-local today, ordered by `start_date DESC, created_at DESC, id DESC`. Overlap protection normally makes that row unique.

The read stays bounded as history grows. After loading the member, it uses one current-membership lookup limited to one row, one history fetch limited to 21 rows, one conditional aggregate of the member's finalized payment amounts, one aggregate of the per-membership outstanding balance, one latest check-in, and one open check-in. The summary aggregate stays intact when payment history is also loaded. Notification HTTP routes and general payment or receipt routes remain unpublished.

`GET /api/v1/members/{id}/payments/` is the published read-only payment history. It requires a staff access token, both live permissions `members.view` and `payments.view`, and both active feature entitlements `members` and `finance`. Authorization and entitlements are checked before the member lookup. A membership that holds protected `TRAINER` and does not also hold protected `OWNER` or `MANAGER` receives `404` for every member, assigned or not, before that member is loaded. A custom role that grants `members.view` and `payments.view` does not open the read. A protected owner or manager on the same membership follows those granted permissions. The gym comes only from the access token. A cross-gym or unknown member id returns `404`. The `404` body does not include the member. `GET /api/v1/members/{id}/check-ins/` is the published manual check-in history. It uses the same cursor query as payment history and the check-in response below. Authorization is checked before the member lookup.

The history accepts only `limit` (default 25, maximum 100) and an opaque `after` cursor. Any other query parameter, including `gym_id`, returns `400` with the strict field envelope. The cursor is HMAC-signed, expires after 24 hours, and is bound to the token gym, the path member, the exact order `received_at DESC, id DESC`, and the requested page limit. A cursor from another gym or member, a different limit, a different order, a tampered token, or an expired token returns `400`. The error text does not include decoded cursor values. The service reads `limit + 1` rows.

The response is `{success, data, meta, message}`. `meta` is `{has_more, next_after}`. Each item is an explicit safe read with exactly `id`, nullable `membership_id`, nullable `original_payment_id`, `entry_type`, `purpose`, `description`, decimal-string `amount`, `currency`, `method`, `business_date`, `received_at`, `status`, and nullable `receipt_number`. Every status is visible because this is an authorized history read. `receipt_number` comes from the same-gym one-to-one receipt and is null when that receipt is absent. The existing composite receipt foreign key and RLS stay authoritative. `payment_gym_member_hist_idx` on `(gym_id, member_id, received_at DESC, id DESC)` matches the history order so the page can stop at `LIMIT`. The item does not include gym ids, the member id or name, reference numbers, actor ids, workflow timestamps, reasons, raw receipt snapshots, or internal timestamps. The read uses `select_related('receipt')` and keeps a fixed query count as history grows.

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

Published Membership routes are list, create, detail, note update, cancel, suspend, resume, and the expiry-bucket read. Sale, suspension, resume, cancellation, and expiry reads are published. No payment is posted by these routes. Membership-expiry scheduling and delivery commands are implemented. The member summary, nested member payment history, and nested member check-in history are published. Notification HTTP routes and general payment and receipt routes remain unpublished. Phase 2 implementation is complete. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. The approved QR path is authenticated member-self scan. Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. Production expiry delivery and object storage follow [ADR 0010](./adr/0010-production-provider-readiness.md): Resend and AWS S3 `ap-southeast-1` are selected and implemented in code. The live Resend, AWS, and ClamAV proofs remain open.

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

The staff browser workspace `/members` requests `GET /api/v1/members/` at 25 rows, follows `meta.next_after` only as `after`, and replaces the first page when the allowlisted filters change or a member is created. `/memberships` does the same for `GET /api/v1/memberships/`, loads plans by walking at most 20 pages of 100 with duplicate-id and cursor-cycle checks, searches active members remotely, and reads one expiry bucket from `GET /api/v1/memberships/expiry/` without sending membership list filters. The member profile-photo contract is implemented under Signed files. The staff browser workspace `/check-ins` searches active members, records a manual visit, and checks out an open visit. Nested check-in history is published. Phase 2 implementation is complete. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. The approved QR path is authenticated member-self scan. Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. Production expiry delivery and object storage follow [ADR 0010](./adr/0010-production-provider-readiness.md): Resend and AWS S3 `ap-southeast-1` are selected and implemented in code. The live Resend, AWS, and ClamAV proofs remain open.

`PENDING` and `CANCELLED` rows are absent from every bucket. Inclusive calendar expiry counts both the start date and the expiry date. A one-day plan expires on its start date. Month and year additions clamp to the target month length, including leap day, and the expiry is the day before that anniversary.

Membership-expiry email is system-generated by `schedule_membership_expiry_emails` and delivered by `deliver_membership_expiry_emails`. There is no client endpoint that supplies a destination address or directly sends the warning. Notification list, read, and delivery HTTP routes remain unpublished. For each configured warning day, an active eligible membership with a nonblank current `Member.email` may create one deduplicated `EMAIL` notification. The plain-text email uses the current member display name, snapshotted plan name, expiry date, and current gym contact or display fields. If the member has no email, the scheduler skips the warning without creating a notification, reporting an error, or falling back to SMS or phone. Repeated scheduler runs and delivery reruns use the deterministic deduplication contract from `DATABASE.md`. Production sending uses the Resend HTTP API selected in [ADR 0010](./adr/0010-production-provider-readiness.md). The application `X-Idempotency-Key` translates to Resend `Idempotency-Key` (1–256 characters, retained 24 hours). The Resend client is implemented as Django's production `EMAIL_BACKEND`. Live readiness still requires a verified domain, API key, and idempotency proof.

### Check-ins — Phase 3

```text
GET  /api/v1/check-ins/
POST /api/v1/check-ins/
GET  /api/v1/check-ins/{id}/
POST /api/v1/check-ins/{id}/check-out/
POST /api/v1/check-in-challenges/
GET  /api/v1/check-in-display-devices/
POST /api/v1/check-in-display-devices/
POST /api/v1/check-in-display-devices/{id}/rotate-credential/
POST /api/v1/check-in-display-devices/{id}/revoke/
POST /api/v1/device/check-in-challenges/
GET  /api/v1/check-in-integration-devices/
POST /api/v1/check-in-integration-devices/
POST /api/v1/check-in-integration-devices/{id}/rotate-credential/
POST /api/v1/check-in-integration-devices/{id}/revoke/
POST /api/v1/device/check-ins/
```

Manual check-in, check-out, staff challenge issuance, member-self QR redemption, and display-device credentials are implemented. Staff manual routes write `MANUAL`. The member route and the trainer scan route write `QR`. `method` remains extensible for later NFC or biometric ingestion. `POST /api/v1/check-ins/scan/` is not a published route and is not a staff-token alias of the member route. NFC and biometric readers use the same credential table with an immutable kind, and they ingest only through `POST /api/v1/device/check-ins/`. Vendor-specific software remains optional. `CheckIn.device_id` references that credential for NFC and biometric visits and stays null for manual and member QR visits. See [ADR 0007](./adr/0007-display-device-credentials.md) and [ADR 0008](./adr/0008-device-ingestion.md).

Manual check-in request fields: `member_id`, optional `occurred_at` only for callers with `checkins.update`, and optional note. `method` is fixed server-side to `MANUAL`. Read-only/rejected: `gym_id`, selected `membership_id`, `check_in_at` when not explicitly backdated, `check_out_at`, method override, recorder/actors, device/source metadata, eligibility result, and status. Response returns the check-in plus an explicit eligibility snapshot. Requires `checkins.create`, the active `members` and `memberships` entitlements, an active same-gym member, an eligible active membership selected by the service, bounded backdating, and duplicate-window validation under lock. There is no separate check-ins feature. A missing permission or entitlement returns `403 PERMISSION_DENIED`. Check-out requires `checkins.update`. Cross-tenant member or check-in identifiers return `404`.

The service selects the membership with status `ACTIVE` whose inclusive start and expiry contain the gym-local date of `check_in_at`, ordered by `start_date DESC, created_at DESC, id DESC`. Payment status does not gate check-in and is not changed. `occurred_at` on check-in and on check-out must include a timezone offset, must not be more than 24 hours before now, and must not be more than 60 seconds after now. The checkout bound is applied only when the visit is still open. An empty checkout body, or the same stored checkout time, still returns the original row after that window has passed. A different time on a closed visit remains `409 CHECK_IN_STATE_CONFLICT` and is not written. Without `checkins.update`, sending `occurred_at` returns `403` before any other check-in denial. A membership that holds the protected TRAINER role, and does not also hold protected OWNER or MANAGER, receives check-in list, detail, and create results only for members with a same-gym `ACTIVE` assignment that is effective on the gym-local current date and belongs to that caller's active staff profile. A custom role does not remove that limit. An unassigned, missing, or cross-gym member id is `404`. Check-out stays `404` for that caller even when the visit belongs to an assigned member. Display and device routes stay denied for that caller. The duplicate window is `GymSettings.check_in_duplicate_window_seconds`. Zero disables only that time window. Any open visit still returns `409 CHECK_IN_OPEN`. A visit inside the window returns `409 CHECK_IN_DUPLICATE`. An optional `Idempotency-Key` matching `^[A-Za-z0-9._:-]{16,128}$` replays the same member, note, and time as `200` and returns `409 IDEMPOTENCY_KEY_REUSED` when the body differs. Checkout ignores that header. Repeating checkout with an empty body or the same time returns the original row. A different time after checkout returns `409 CHECK_IN_STATE_CONFLICT`. The note is optional, stripped, at most 500 characters, immutable, and omitted from audit. An ineligible member returns `409 CHECK_IN_INELIGIBLE`.

List and detail require `checkins.view` and the same two entitlements. List filters are `member_id`, `open=true|false`, `limit` (default 25, maximum 100), and an opaque `after` cursor. Order is `check_in_at DESC, id DESC`. A cross-gym `member_id` returns `404`. The safe response is `id`, `member` (`id`, `member_number`, `first_name`, `last_name`), `membership_id`, `check_in_at`, `check_out_at`, `method`, derived `status` (`OPEN` or `CHECKED_OUT`), `note`, and `eligibility`. Gym, actors, `device_id`, `source_metadata`, and `idempotency_key` are omitted. Audit actions are `CHECK_IN_RECORDED` and `CHECK_IN_CHECKED_OUT`, with member, membership, method, both times, and the backdated flag. The front-desk page reads a `datetime-local` value in the gym timezone before sending an aware instant.

`POST /api/v1/check-in-challenges/` is the published staff-actor issuance route. It requires `checkins.challenge.issue` and the active `members` and `memberships` entitlements. The request accepts only `display_location` (1–80 characters). TTL and max uses come from `GymSettings`, not the client. `display_device_id` is rejected and stored null. The response returns the plaintext payload once, plus `id`, `display_location`, `issued_at`, `expires_at`, `max_uses`, and `status`. Only the SHA-256 hash is stored. The payload is `gms-checkin:v1:` plus a high-entropy token and contains no gym, member, staff, device, or permanent secret. `Idempotency-Key` is rejected because the plaintext cannot be replayed. A new staff issuance revokes prior active staff-issued challenges in that gym and does not revoke a display's challenge. A trainer-only membership is still `404` when it holds the permission. Assignment scope does not open staff challenge issuance or display-device management. Owner and manager templates receive the permission. Reception and trainer templates do not.

Display management requires an active owner, both `settings.manage` and `checkins.challenge.issue`, and the active `members` and `memberships` entitlements. `settings.manage` stays owner-only and is not delegated. Enrollment, rotation, and revocation also require recent MFA. List does not. The enroll body accepts only `label` and `location`, each 1–80 characters. Rotate and revoke accept an empty body. List returns `id`, `label`, `location`, `status`, `credential_selector`, `credential_version`, `created_at`, `rotated_at`, and `revoked_at`. Enrollment and rotation add `credential` on that response only. The credential is `gms-device:v1:`, a public selector, a dot, and a high-entropy secret. The stored value is a `pbkdf2_sha256` hash. The contract does not publish a sample secret. Audit actions are `CHECK_IN_DISPLAY_DEVICE_ENROLLED`, `CHECK_IN_DISPLAY_DEVICE_ROTATED`, and `CHECK_IN_DISPLAY_DEVICE_REVOKED`, with label, location, status, and credential version only. Rotation invalidates the previous secret immediately and leaves current challenges. Revocation also revokes that device's active challenges. A non-active device returns `409 DISPLAY_DEVICE_STATE_CONFLICT`.

`POST /api/v1/device/check-in-challenges/` authenticates the display. The header scheme is `Device` and the body is empty. It is not a staff JWT, a member JWT, or the browser BFF secret. The device derives its gym from the credential. A failed authentication is `401 AUTHENTICATION_FAILED` for an unknown selector, a wrong or old secret, a revoked device, an inactive gym or subscription, and a missing members or memberships entitlement. The route is limited to 60 attempts per minute per address and 20 per minute per selector hash plus address. The secret is not a throttle key. A successful issue sets `display_device_id`, leaves both membership actors null, and revokes only that device's previous active challenge. An NFC or biometric credential is authentication failure on this route.

Integration enrollment uses the same owner, permission, entitlement, and recent-MFA gate. The body accepts `label`, `location`, and `kind` of `NFC` or `BIOMETRIC`. `DISPLAY` is rejected. List rows include `kind` and omit the hash and the secret. `POST /api/v1/device/check-ins/` accepts `member_id` and a bounded `event_id`. The method and time come from the server. A display credential is authentication failure. The same event for the same member returns the original visit. A different member for that event is `409 IDEMPOTENCY_KEY_REUSED`. The stored idempotency key is a SHA-256 hex digest. The event id, credential, and any capture are absent from the row and the audit. The audit action is `DEVICE_CHECK_IN_RECORDED` with a null membership actor. Limits are 60 attempts per minute per address and 20 per minute per selector hash. See [ADR 0008](./adr/0008-device-ingestion.md).

`POST /api/v1/check-ins/scan/` is not published. Member-self redemption is `POST /api/v1/member/check-ins/scan/` and accepts a member access token only. A staff or platform token is authentication failure, not a fallback. The body accepts only `payload`. `Idempotency-Key` is rejected: the manual check-in header compares a stored request, and this route must not store the payload or its digest on the visit or in the audit row. Gym and member come from the member session. See [ADR 0006](./adr/0006-member-check-in-scan.md) and [ADR 0004](./adr/0004-defer-check-in-scan.md). Assignment-scoped trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. It is not a staff-token alias of the member route. Scoped DISPLAY, NFC, and BIOMETRIC credentials and dedicated ingestion abstractions are implemented. No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Vendor-specific integration remains optional. Public member identity linking, multi-member selection, and member MFA remain deferred and are not prerequisites for the already-linked member QR path. The React Native member app and the Phase 7 member APIs are implemented. Live Firebase and APNs credentials, release signing, store submission, and a production device proof remain open. Production deployment is not complete. The check-in query-plan baseline in [DATABASE.md](./DATABASE.md) is not a production load test. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. Closure evidence is in the repository README: backend checks passed; 225 non-PostgreSQL unit tests passed; the full PostgreSQL suite passed 171 tests before the new check-in plan test, and that focused plan test passed separately; web format, lint, typecheck, build, and 263 component and unit tests passed earlier, with focused static checks passing after the end-to-end spec change; a clean end-to-end reset passed 4 Playwright specs, including member authentication, a rotating onsite challenge, member QR scan, staff front desk, history, and summary, then member logout and refresh replay denial.

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

Increment 3 publishes the payment collection and detail routes, submit, approve, reject, cancel, receipt list, detail, and download, and `GET /api/v1/sales/daily/`. Increment 8A publishes `POST /api/v1/payments/{id}/void/`, `POST /api/v1/payments/{id}/refund/`, and `POST /api/v1/receipts/{id}/void/`. Increment 9 delivers the staff payment and receipt page. Increment 8C publishes partial retail returns. Production deployment is not complete, and the email and object-storage gates remain open. `GET /api/v1/members/{id}/payments/` remains the member-history read. It does not create, submit, approve, void, or refund a payment and does not publish receipt documents.

Payment creation returns a `DRAFT`; it creates no receipt, cash movement, or membership settlement. The creator submits the draft and a different authorized Manager or Owner approves it. Approval is the posting boundary and atomically returns the immutable posted payment, generated receipt, source settlement update, and cash transaction where applicable. Clients cannot submit a receipt number. Create, update, submit, approve, reject, cancel, void, and refund require an `Idempotency-Key`.

Payment create/update fields while `DRAFT`: optional same-gym `member_id`, exactly one supported source reference such as `membership_id`, `member_service_package_id`, `pt_sale_id`, or `retail_sale_id`, `purpose` (`MEMBERSHIP`, `PT`, `CLASS`, `PRODUCT`, `OTHER`), `description`, positive decimal-string `amount`, `currency`, `method`, `business_date`, optional `received_at`, and optional `reference_number`. Member is required for member-owned sources and optional for walk-in retail. Read-only/rejected: `gym_id`, `entry_type`, `status`, submission/approval/posting/actor fields, settlement totals, original/reversal IDs, cash transaction ID, receipt ID/number/snapshots, and all timestamps not explicitly accepted. A successful `201` returns the editable draft. Submit has no writable business fields and freezes the draft. Approve requires `cash_session_id` when the method is cash and rejects it otherwise. This replaces the earlier optional wording, because a posted cash payment must have exactly one matching cash movement. A legacy posted cash row that predates the cash book is marked `legacy_unlinked_cash` and is not given an invented movement.; reject requires `rejection_reason`; cancel requires `reason` and is valid only from `DRAFT`. Approval revalidates all source, amount, currency, and cash-session state before posting.

Void request fields: required nonblank `reason`, optional `business_date`, and optional `cash_session_id`. When `business_date` is omitted it is the gym-local today. The date must be from the original business date through today, inclusive. A cash void requires an open same-gym session whose business date and currency match the correction; a non-cash void rejects a session. The closed original session is never reopened. A `legacy_unlinked_cash` charge has no original movement, so its void rejects `cash_session_id` and does not invent an in or out movement. The reversal is also `legacy_unlinked_cash`. Refund request fields: exact positive decimal-string `amount`, `reason`, `method`, required `business_date`, optional `reference_number`, and optional `cash_session_id`. A cash refund requires the open session and writes one `OUT` movement that does not set `reversal_of`, so several refunds can share one original in movement. A non-cash refund rejects a session. A later cash refund of a legacy charge is a new real out movement, not a fabricated original. Original payment amount, method, purpose, member, and source stay unchanged. Refundable balance is the charge minus posted refunds and a full reversal and cannot go below zero. Void is allowed only while that balance still equals the charge. After any posted refund, the remainder is returned by further refunds. A full refund does not mark the charge `VOID` and does not void its original receipt. Purpose `PRODUCT`, including a payment linked to a retail sale, is rejected with `422 RETAIL_CORRECTION_UNAVAILABLE`. A posted retail sale requires its charge to stay posted and its receipt to stay issued, and these routes do not create stock returns. Daily product payment net therefore stays aligned with posted retail line revenue. Both correction responses include the original and corrective payments, receipt documents, cash effects, and updated refundable and settlement totals. The actor must hold the exact live permission, the finance entitlement, and an active same-gym membership. The creator or submitter may correct, and a custom role granted that exact permission may correct. Platform super-admin status grants no tenant authority. Reception-origin provenance does not grant the caller authority. The protected Reception template does not include these codes, so an ordinary Reception membership is denied.

Payment list and detail require `payments.view`. Payment create/update/submit/approve/void/refund require `payments.create`/`payments.update`/`payments.submit`/`payments.approve`/`payments.void`/`payments.refund` respectively; reject requires `payments.approve` and cancel requires `payments.update`. Every action additionally requires finance entitlement, same-gym source/member/session, exact currency/scale, valid source and cash-session state, and `Idempotency-Key` on commands. Approval or rejection requires an actor different from the creator and submitter; RECEPTIONIST never receives `payments.approve`. Posted facts are never edited. Refunds lock the original and cannot exceed authoritative refundable balance.

`GET /sales/daily/?date=` requires `reports.financial`. Posted and void charges count as gross on their own business date. Posted refunds and reversals subtract on their own business date from the original charge's purpose bucket, so a same-day and a later-day correction both keep the lifetime sum of daily nets equal to the finalized net. Draft, pending, rejected, and cancelled rows are excluded. Product quantity, revenue, cost of goods sold, and gross profit come from posted, non-void retail lines on that business date. `product_quantity` is three decimal places (`"0.000"` when none). `product_revenue`, `product_cogs`, and `product_gross_profit` are exact money. `product_total` is the net of `PRODUCT` payments and reconciles to that line revenue while no product refund exists. Draft, pending, rejected, and cancelled sales contribute nothing. `pt_total` is the net of purpose `PT` charges and their refunds and reversals, using the same business-date gross-minus-refund-and-reversal rule as the other purpose buckets. `class_total` stays `"0.00"` because this phase does not post `CLASS` payments.

Receipt list/detail/download requires `receipts.view`. `POST /receipts/{id}/void/` requires `receipts.void`, a nonblank reason, a same-gym issued receipt, finance entitlement, and `Idempotency-Key`. It voids only that document. Payment, settlement, inventory, cash, and revenue stay unchanged, and the receipt number is never reused. The receipt issuer may void the document when that membership holds `receipts.void`. An ordinary Reception membership is denied because that template does not include the code. A product charge's receipt, or a receipt attached to a posted retail sale, is rejected with `422 RETAIL_CORRECTION_UNAVAILABLE`. A retry or race produces one void.

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

Increment 6 publishes product-category collection list and create, product list, create, detail, and update, `GET /api/v1/products/low-stock/`, stock-movement list, and `POST /api/v1/stock-movements/adjust/`. Increment 7 publishes retail-sale list, create, detail, update, submit, approve, reject, and cancel. Category detail and update stay unpublished. Increment 8C publishes partial retail returns. Increment 9 delivers the inventory and retail staff pages. Payment void, payment refund, receipt void, and manual expense void are published in Increment 8A. Increment 8B publishes `POST /api/v1/retail-sales/{id}/void/` for one full void of a posted sale. The body requires a nonblank `reason`, accepts an optional `business_date` bounded like a salary void, and accepts `cash_session_id` only when the original method is `CASH`. That session must be open, same-gym, and match the correction date and currency. The sale becomes `VOID` with actor, time, `void_reason`, and `void_business_date`. Each line appends one positive `SALE_RETURN` linked by `reverses_movement_id`. The product charge and its receipt become `VOID`, one posted `REVERSAL` payment gets its own receipt, and cash appends one `OUT` movement in the named session. Public product payment void and refund stay `422 RETAIL_CORRECTION_UNAVAILABLE`. Products, categories, stock, and sales are selected only from the active gym.

`POST /api/v1/retail-sales/{id}/return/` is published. It requires live `retail.void`, the `finance` entitlement, an active same-gym membership, and `Idempotency-Key`. The retail staff page exposes it for a posted sale when the caller has `retail.void`. Full void stays available only while no return exists. The body is a nonblank `reason` of at most 2000 characters, an optional `business_date`, an optional `cash_session_id`, and `lines`. Each line is `{line_id, quantity}`. `line_id` values are unique, quantity is a positive `NUMERIC(18,3)` value no greater than that line's remaining quantity, and at least one line is required. Omitted `business_date` means the gym-local today. The date runs from the sale's business date through that today, the same bound as a retail void. The server ignores every client amount. It preserves the original payment method. `cash_session_id` is accepted only when that method is `CASH` and the computed refund is greater than zero; the session must be open, same-gym, and match the correction date and currency. A non-cash return, and a cash return whose computed refund is zero, reject a session id. The closed original till is not reopened.

The sale stays `POSTED`. Repeat returns are allowed until each line's remaining quantity is zero. After any return exists, `POST /api/v1/retail-sales/{id}/void/` is invalid. A full void still requires that no return exists, marks the sale `VOID`, and restores each line in one `SALE_RETURN`. A return does not void the original product charge or its issued receipt. For each returned line the server allocates discount, line revenue, and cost of goods sold from the immutable line totals. The cumulative amount for a taken quantity is `ROUND_HALF_UP(total * taken / original_quantity, 2)` until the taken quantity equals the original quantity, when the cumulative amount is the exact line total. This return's amount is that cumulative value minus the cumulative value of the quantity already returned. Earlier returns are ordered by `(created_at, id)`. Gross profit for the slice is the allocated revenue minus the allocated cost, so the slices reconcile to the line's discount, revenue, cost, and profit. The header stores those sums, the actor, the reason, and the correction date. Each line points at one positive `SALE_RETURN` for its quantity. That movement reverses the line's original `SALE`, keeps the sold unit cost, and uses the correction date. Cumulative returned quantity cannot exceed the quantity sold. A refund greater than zero creates one posted `PRODUCT` `REFUND` of the original charge, for the header refund amount and the original method and reference, plus one issued correction receipt. Cash appends one `OUT` `PAYMENT` movement in the named open session and does not set `reversal_of`, so later partial refunds can share the original in movement. A zero refund restores stock and creates no payment, receipt, or cash movement. Public `POST /api/v1/payments/{id}/void/` and `POST /api/v1/payments/{id}/refund/` still reject purpose `PRODUCT` with `422 RETAIL_CORRECTION_UNAVAILABLE`. The manual stock route still rejects `SALE` and `SALE_RETURN`.

The feature catalog has no separate inventory code. Catalog and stock commands require the existing `finance` entitlement. Category create, product create, product update, and stock adjustment each require `Idempotency-Key`, the same command policy used for salary and expense writes. `mutate()` reloads the actor, entitlement, and live permission before it locks or writes. A revoked permission fails before the mutation and does not store a failed claim.

Category create accepts canonical `code` (`^[A-Z][A-Z0-9_]{0,31}$`), a display `name`, and optional `is_active` (default true). The stored name collapses whitespace. The normalized name is that display name uppercased and is unique per gym. There is no category update or archive route. Product create and PATCH accept canonical SKU (`^[A-Z0-9][A-Z0-9_-]{0,31}$`), optional nonblank barcode (spaces removed, uppercased, `^[0-9A-Z]{1,64}$`, unique per gym), name, same-gym `category_id`, unit (`^[A-Z][A-Z0-9_]{0,15}$`), nonnegative `sale_price`, `current_unit_cost`, and `default_unit_cost`, nonnegative `low_stock_threshold`, and `is_active`. PATCH may send only those master fields and rejects an empty body. `gym_id`, quantity on hand, sold quantity, revenue, cost of goods sold, gross profit, currency, actors, timestamps, unit-cost snapshot, and reversal or retail-line identifiers are rejected. A category or product id from another gym is `404`. Duplicate code, normalized name, SKU, or barcode is `400`.

Money stays exact `NUMERIC(18,2)` decimal text. Quantities and the low-stock threshold are `NUMERIC(18,3)`. Extra quantity places, exponent notation, NaN, and infinity are rejected and are not rounded. Quantity on hand is the server sum of immutable movements, returned with three decimal places. Sold quantity, revenue, cost of goods sold, and gross profit are sums of posted, non-void retail lines for that product. Draft, pending, rejected, cancelled, and void sales contribute nothing. Setting `is_active` false records archive time and actor; setting it true clears them.

Manual adjustment accepts same-gym `product_id`, a signed nonzero quantity, `movement_type` (`OPENING`, `PURCHASE`, `DAMAGE`, `LOSS`, `ADJUSTMENT`, `CORRECTION`), `business_date`, and a required explanation. `OPENING` and `PURCHASE` must be positive. `DAMAGE` and `LOSS` must be negative. The body does not accept unit cost, `SALE`, `SALE_RETURN`, a source link, or a reversal link. The server copies the locked product's `current_unit_cost` into `unit_cost_snapshot`. Positive opening or purchase rows do not change `current_unit_cost` or `default_unit_cost` and do not compute a weighted average. Those costs change only through product create or PATCH. The product row is locked before the on-hand sum is checked. A negative result is rejected. The `201` body is the movement plus the product, including the new quantity on hand.

Reads require `inventory.view`. Category and product create and update require `inventory.manage`. Adjustment requires `inventory.adjust`. Lists accept only the allowlisted filters below, `limit` (default 25, maximum 100), and an opaque `after` cursor bound to the gym, the caller, those filters, and the list order. A cursor from another gym or another filter set is `400`. Categories order by `code ASC, id ASC` and filter `is_active`. Products order by `sku ASC, id ASC` and filter `category_id`, `is_active`, exact `sku`, and exact normalized `barcode`. An unknown or cross-gym `category_id` is `404`. Low stock lists active products whose quantity on hand is less than or equal to `low_stock_threshold`, in the same product order, and may filter `category_id`. Movements order by `created_at DESC, id DESC` and filter `product_id`, `movement_type` (including `SALE` and `SALE_RETURN`, which match no manual row), and `business_date`. An unknown or cross-gym `product_id` is `404`.

Retail-sale list and detail require `retail.view`; draft create requires `retail.create`; draft update requires `retail.update`; submit requires `retail.submit`; approve and reject require `retail.approve`; cancel requires `retail.update`. Void requires `retail.void` and is published in Increment 8B. The return command specified below reuses that permission and is published. Increment 7 publishes the other retail routes above. Every published retail command requires the existing `finance` entitlement, same-gym product, member, and session references, exact decimal and currency checks, a valid state, and `Idempotency-Key`. `mutate()` reloads the actor, entitlement, and live permission before it locks or writes. Approval or rejection requires live `retail.approve`, an active protected Manager or Owner, and a membership different from the creator and the submitter. A protected Reception membership never approves. Reception can create, update, submit, and view its own drafts and submissions, and cannot view a posted sale or another creator's record.

A draft accepts an optional same-gym active member, a business date that is not after the gym-local today, a payment method and reference, and 1 to 100 unique product lines of `product_id`, positive `NUMERIC(18,3)` quantity, and an exact money discount. The server resolves current active same-gym products and returns calculated totals. Clients cannot write snapshots, totals, cost, profit, status, actors, or payment and receipt links. Only a `DRAFT` is mutable. Submission freezes input and revalidates the member, currency, and current product price and cost. Approval locks the sale, its lines by product id, the member when present, and the products by id. It revalidates the current active price, cost, and available stock, then either posts everything or rolls back. If the product price or cost changed since submission, approval fails and leaves the submitted totals unchanged.

Quantity times unit price and quantity times unit cost round to `NUMERIC(18,2)` with `ROUND_HALF_UP` (half away from zero), matching PostgreSQL `round(numeric, 2)` for nonnegative values. Example: `1.250 × 0.10` is `0.125`, which becomes `0.13`. Discount must be from zero through that pre-discount line amount. Line revenue is the rounded extension minus discount. Cost of goods sold is the rounded quantity times the snapshotted unit cost. Gross profit is revenue minus that cost. Header subtotal, discount, total, cost, and profit are the sums of those line amounts. The sale total must be greater than zero.

Approval appends exactly one immutable `SALE` stock movement per line, with a negative quantity, the line's current unit cost, and `retail_sale_line_id`. It cannot make stock negative, including when two sales compete for the last unit. The manual adjustment endpoint still rejects `SALE`, `SALE_RETURN`, a reversal link, a unit cost, and a retail-line id. Approval also creates exactly one `POSTED` `PRODUCT` payment linked to the sale, exactly one issued receipt, and, when the method is `CASH`, one `IN` cash transaction. The payment amount equals the sale total. A walk-in sale may have a null member; its receipt snapshot uses `Walk-in`. A cash sale requires an explicit open same-gym `cash_session_id` whose business date and currency match the sale. A non-cash sale rejects that session id. When the sale was created or submitted with Reception provenance, the session must be that Reception membership's own till. Public payment drafts still reject purpose `PRODUCT` and `retail_sale_id`. Reject follows the approver rule and creates no stock, payment, receipt, or cash effect. Cancel requires `retail.update`, a reason, and an unsubmitted draft. Runtime cannot void a posted sale.

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

Expense-category and expense list/detail reads require `expenses.view`. Protected `system_key` categories can never be created, renamed, or deleted through these routes. Custom expense-category create and update require `expense_categories.manage` ([ADR 0009](./adr/0009-finance-foundation-defaults.md)). That code is not satisfied by `expenses.view`, `expenses.create`, `expenses.update`, `expenses.submit`, `expenses.approve`, or `expenses.void`. Increment 4 publishes the expense-category list/create/update routes and the expense list/create/detail/update/submit/approve/reject/cancel routes. Increment 8A publishes `POST /api/v1/expenses/{id}/void/` for a posted manual expense. Expense evidence reuses the private file pipeline with purpose `EXPENSE_RECEIPT`, image-only, at most 5 MiB, gym-owned (`owner_type=GYM`, `owner_id` equal to the token gym). Clean scan status is required before submit and approve. Increment 9 delivers the staff expense page. Increment 8C publishes partial retail returns. Production deployment is not complete, and the email and object-storage gates remain open.

Expense draft create/update fields: `category_id`, `business_date`, `description`, positive decimal-string `amount`, `currency`, `payment_method`, optional `receipt_file_asset_id`, and optional policy note. Read-only/rejected: `gym_id`, `status`, posting/void/cancel/reversal fields, actors, cash effects, and calculated totals. Only `DRAFT` is mutable.

Submit has no writable business fields and freezes the draft. Approve accepts optional `cash_session_id` when cash; reject requires `rejection_reason` and also requires `expenses.approve`; cancel requires `reason`, `expenses.update`, and `DRAFT`. Void requires nonblank `reason`, optional `business_date` (default gym-local today, from the original date through today), and, for a cash original, an open same-gym destination `cash_session_id`. A non-cash void rejects a session. The original session and movement are not reopened or edited. The command marks the original `VOID` and posts one equal reversal expense linked both ways. A cash original appends one `IN` movement on the supplied open session, with `reversal_of` pointing at the original movement. The response returns the original, the reversal, and the cash effects. The actor must hold live `expenses.void`, the finance entitlement, and an active same-gym membership. The creator or submitter may void, and a custom role granted that permission may void. An ordinary Reception membership is denied because that template does not include the code. `STAFF_SALARY` and `PT_COMMISSION` cannot be voided on this route because their source services own reversal; the response is `422 PROTECTED_EXPENSE_CATEGORY`. Commands require `expenses.create/update/submit/approve/void` as applicable, the finance entitlement, same-gym category/file/session, clean file status, valid business date/currency, valid state transition, different creator/approver, and `Idempotency-Key` for submit, approve, reject, cancel, and void. Manual drafts selecting those protected categories return the same `422`. Net expense totals count posted and void originals, then subtract each linked posted reversal once on the reversal's business date and category. Draft, rejected, and cancelled rows are excluded.

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

Salary period create/update accepts `period_start`, `period_end`, `business_date`, and `description`. Line replacement while draft accepts at most 200 unique lines containing `staff_profile_id`, positive decimal-string `amount`, `currency`, `payment_method`, optional external reference, and description. The server sorts lines by `staff_profile_id` and cash mappings by `line_id` before the idempotency hash, so list order is not part of the request identity. Salary values are entered per period; no fixed amount is read from staff or PT configuration. Submission freezes the period and lines and requires at least one line. Approval accepts explicit cash-session mappings, one per cash line and none for a non-cash line. Each mapped session must be open, same-gym, and match the line currency and the period business date. A Reception-origin period can post cash only through the originating Reception membership's own open till. Approval requires a different authorized Manager or Owner and atomically posts one protected `STAFF_SALARY` expense per line plus one `OUT` cash movement per cash line. Reject requires `rejection_reason` and has no financial effect. Cancel requires `reason` and is valid only from `DRAFT`. Void requires `reason` in the contract and would reverse linked expenses and movements, but the route stays unpublished. A cash-origin period can need an open destination session after the original till is closed, and the public contract does not yet say how that session is chosen. That destination-session contract is the remaining Increment 8 blocker. Retail sale void and returns stay unpublished for the same reason. Increment 9 delivers the staff salary page for these published commands. Increment 8C publishes partial retail returns. Production deployment is not complete, and the email and object-storage gates remain open.

Salary list/detail/create/update/submit/approve/void require `salaries.view/create/update/submit/approve/void` as applicable; reject requires `salaries.approve` and cancel requires `salaries.update`. Every action additionally requires the finance entitlement (there is no separate salary feature), same-gym staff/session references, valid period and currency, separation of duties, and `Idempotency-Key` for submit, approve, reject, cancel, and void. Create, update, and line replacement also require `Idempotency-Key`, matching the Increment 4 command policy. That choice is stricter than the minimum command set above. Nested commands include the normalized period UUID in the route and request hash.

`GET /api/v1/salary-periods/` accepts only `status`, `business_date`, `limit` (default 25, maximum 100), and an opaque `after` cursor bound to the gym, the caller, those filters, and `created_at DESC, id DESC`. Increment 5 publishes the salary routes above except void. Increment 8B publishes `POST /api/v1/salary-periods/{id}/void/`. The body requires a nonblank `reason`, accepts an optional `business_date` from the original business date through gym-local today, and accepts `cash_sessions` that map every cash `line_id` once to an open same-gym `cash_session_id` for that correction date and currency. A non-cash line must not be mapped. Mappings are sorted by `line_id` before the idempotency hash. The period becomes `VOID` with actor, time, reason, and `void_business_date`. Each original `STAFF_SALARY` expense becomes `VOID` and one posted reversal expense is created. A cash line appends one `IN` movement in the named open session, linked by `reversal_of` to the original `OUT` movement. The line keeps its original `expense_id`. The response lists reversal ids. One successful void is final. A protected Reception membership can list and read only a period it created or submitted, and a posted or void period is absent (`404` on detail, omitted from the list). The payee object is `id`, `staff_code`, and `display_name`. Another creator's draft is `404`. Manual expense requests still cannot select `STAFF_SALARY`; the salary approval service is the only application writer for that category. Increment 9 delivers the staff salary page. Increment 8C publishes partial retail returns. Production deployment is not complete, and the email and object-storage gates remain open.

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

```text
expected_closing = opening_cash + cash_sales - cash_expenses + manual_net
difference = actual_closing - expected_closing
```

`cash_sales`, `cash_expenses`, and signed `manual_net` are recomputed from append-only movements. A movement's signed amount is its amount when `IN` and the negation when `OUT`. `PAYMENT` adds that signed amount to `cash_sales`. `EXPENSE` and `COMMISSION` add the negation, so an `OUT` increases `cash_expenses`. `MANUAL` adds the signed amount to `manual_net`. `REVERSAL` uses the original movement's source type with this row's direction. Clients cannot submit those totals. `PAYMENT`, `EXPENSE`, and `COMMISSION` rows require a source id. A deferred database constraint checks the stored totals and expected-closing formula at commit.

Open request fields: `register_code`, `business_date`, and nonnegative decimal-string `opening_cash`. `register_code` is an uppercase token of 1 to 32 characters matching `[A-Z0-9_-]+`; lowercase is rejected. `business_date` must be the gym-local current date. Currency is copied from the gym. There is no separate register catalog ([ADR 0009](./adr/0009-finance-foundation-defaults.md)). Reconcile fields: decimal-string `actual_closing` and optional `difference_reason`. Manual adjustment fields: `direction` (`IN` or `OUT`), positive decimal-string `amount`, `currency`, `business_date`, `description`, and required `reason`. Adjustment currency and business date must match the session. Read-only/rejected everywhere: `gym_id`, all status/actor fields, `cash_sales`, `cash_expenses`, `manual_net`, `expected_closing`, `difference`, closing timestamps, source type/link, and reversal IDs.

Open returns `201`, adjustment returns `201`, and reconcile returns `200`. Each command response is the resulting session plus the immutable cash transactions created by that command. Open and reconcile create none. Open and reconcile require `cashbook.open` or `cashbook.reconcile`. Adjustment requires `cashbook.adjust` and, until an Owner-configurable policy exists, the protected Owner role; a Manager, Accountant, custom role, or any membership that also holds protected Reception is denied with `403 PERMISSION_DENIED`. Commands also require the existing `finance` entitlement, same-gym register/session, correct currency/business day, open-session state, and `Idempotency-Key`. Nested commands include the normalized session UUID in the idempotency hash. Reconcile locks the session and recomputes every total; a nonzero server-calculated difference requires a reason. The business transaction reloads the active membership, user, gym, finance entitlement, and live permission, then repeats Reception scope and the Owner-only adjustment check after locking the session. A denial there has no effect and is not stored as `FAILED`. Authentication, authorization, throttling, and serializer validation happen before the idempotency claim and are not stored either. Command responses include `429` when the cash-command throttle is exceeded.

`GET /api/v1/cash-sessions/` accepts only `status` (`OPEN`, `CLOSED`, or `VOID`), `register_code`, `business_date`, `limit` (default 25, maximum 100), and an opaque `after` cursor bound to the gym, the caller, those filters, and `created_at DESC, id DESC`. `GET /api/v1/cash-transactions/` accepts only `cash_session_id`, `business_date`, `source_type`, `direction`, the same `limit`, and an opaque `after` cursor bound to those filters and `occurred_at DESC, id DESC`. A `cash_session_id` the caller cannot see, including another receptionist's session and every cross-gym session, is `404`. Increment 9 delivers the staff cash-book page. Increment 8C publishes partial retail returns. Production deployment is not complete, and the email and object-storage gates remain open. Increment 2 publishes these six routes only.

For the protected RECEPTIONIST template, `cashbook.view`, `cashbook.open`, and `cashbook.reconcile` are conditional. After opening a session, Reception may view it, use it for ordinary front-desk cash operations, and reconcile/close it only while `opened_by_membership_id` equals the current Reception `GymMembership`. Another role on that same membership, including a custom role that grants `cashbook.view`, `cashbook.reconcile`, or `cashbook.adjust`, does not widen that scope and does not allow manual adjustment. Another receptionist's session and every cross-gym session are inaccessible. “Operate” permits normal submitted-draft preparation and the automatic cash effects created later by an authorized posting service; it does not grant `cashbook.adjust`, `payments.approve`, `expenses.approve`, `salaries.approve`, `pt.sales.approve`, or any posting command. Reconcile is the close command: the server locks the session, derives final totals from immutable transactions, records the close once, and closed sessions remain immutable.

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
POST /api/v1/member-service-packages/
GET  /api/v1/member-service-packages/{id}/
GET  /api/v1/member-service-packages/{id}/ledger/
POST /api/v1/member-service-packages/{id}/ledger/adjust/

GET  /api/v1/class-types/
POST /api/v1/class-types/
PATCH /api/v1/class-types/{id}/
GET  /api/v1/class-schedules/
POST /api/v1/class-schedules/
PATCH /api/v1/class-schedules/{id}/
GET  /api/v1/class-occurrences/
POST /api/v1/class-occurrences/{id}/cancel/
GET  /api/v1/class-bookings/
POST /api/v1/class-bookings/
POST /api/v1/class-bookings/{id}/attend/
POST /api/v1/class-bookings/{id}/no-show/
POST /api/v1/class-bookings/{id}/cancel/

GET  /api/v1/pt-bookings/
POST /api/v1/pt-bookings/
GET  /api/v1/pt-bookings/{id}/
POST /api/v1/pt-bookings/{id}/attend/
POST /api/v1/pt-bookings/{id}/no-show/
POST /api/v1/pt-bookings/{id}/cancel/

POST /api/v1/trainer/check-ins/scan/
```

Commission values are calculated from the trainer percentage snapshot. A trainer's queryset is additionally scoped to their own assignments and commission data.

PT/class packages expose granted, consumed, and remaining sessions calculated from the append-only session ledger. Clients cannot set remaining sessions. Class scheduling validates trainer, location, capacity, recurrence, and time conflicts. Booking requires a same-gym active member and eligible package with remaining sessions; attendance or a policy-applicable no-show atomically appends one idempotent consumption entry. Cancellation/correction follows gym policy and uses reversal entries rather than editing consumed history. Concurrent booking cannot exceed class capacity, double-book a trainer/member/location, or consume one remaining session twice.

The target contract, not the Increment 2 runtime, is: service-package, member-service-package, ledger, PT-schedule, and PT-booking reads require `pt.schedule.view`; service-package definition and PT-booking create/cancel require `pt.schedule.manage`. Authorized session-ledger corrections require `pt.sessions.adjust`; no other route may append a correcting ledger entry. Increment 2 publishes only the two service-package routes above. In that increment `pt.sessions.adjust` is catalogued and has no route. The remaining Phase 5 target below gives it `POST /api/v1/member-service-packages/` and `POST /api/v1/member-service-packages/{id}/ledger/adjust/`.

Class-type, schedule, occurrence, and booking reads require `classes.view`; class-type and schedule create/update require `classes.manage`; booking create requires `classes.book`; attendance and no-show marking require `classes.attendance`; booking cancellation requires `classes.book` for the booking's own member scope and `classes.manage` otherwise.

Trainer and assignment list/detail routes require `pt.assignments.view`. Trainer assignment create/end/cancel requires `pt.assignments.manage`, the `pt` feature entitlement, same-gym active trainer/member, valid dates, and no overlapping assignment. Create fields are `trainer_staff_profile_id`, `member_id`, `effective_from`, optional `effective_until`, and optional `reason`; end/cancel fields are `effective_until` where applicable and required `reason`. Read-only/rejected fields are `gym_id`, status, ended timestamp, actors, and assignment-derived member/trainer details.

Phase 5 Increment 1 publishes only the six trainer and assignment routes above. PT sales, commissions, service packages, class routes, and PT bookings stay unpublished. Reads and commands require a staff JWT and the active `pt` entitlement. A missing permission or entitlement is `403`. Cross-gym ids and trainer-scoped rows outside the caller are `404`. Create returns `201`. End and cancel return `200`. Commands are transactional and are not idempotent. List routes accept `limit` (default 25, maximum 100) and an opaque `after` cursor. Assignment list also accepts `status`, `trainer_staff_profile_id`, and `member_id`.

Phase 5 Increment 2 publishes only `GET /api/v1/service-packages/` and `POST /api/v1/service-packages/`. It persists PT package definitions. Member-service-package, ledger, class, booking, PT sale, and commission routes were unpublished in this increment. The staff and trainer pages are published in the current Phase 5 paragraph below. This increment does not close the Phase 5 gate. The remaining-session ledger is not stored. Create accepts `type` `PT` only and returns `201`. `type` `CLASS` returns `422 CLASS_CATALOG_UNAVAILABLE` because the class catalog is not published. `class_type_id` is not a request field and is stored null. The server copies `currency` from the gym, sets `is_active` true, and owns the gym, actors, and timestamps. Public fields are `type`, `code`, `name`, `session_count`, `validity_value`, `validity_unit`, and `price`. `code` must already match `^[A-Z][A-Z0-9_]{0,31}$`; lowercase is rejected. `name` is trimmed and internal whitespace is collapsed. `price` is a nonnegative decimal string with at most two decimal places and is not rounded. A duplicate code or normalized name in the same gym is `409 SERVICE_PACKAGE_CONFLICT`. List accepts only `limit` (default 25, maximum 100), `after`, `type`, and `is_active`, ordered by `code`, `id`. Responses include `id`, `type`, `code`, `name`, `session_count`, `validity_value`, `validity_unit`, `price` as a decimal string, `currency`, `class_type_id` null, `is_active`, `created_at`, and `updated_at`. There is no update or archive route. A caller with the granted permission, including a protected trainer, reads and creates gym-wide package definitions. Those routes are not assignment-scoped member data.

Phase 5 Increment 3 publishes only `GET /api/v1/pt-sales/`, `POST /api/v1/pt-sales/`, `GET /api/v1/pt-sales/{id}/`, `PATCH /api/v1/pt-sales/{id}/`, and `POST /api/v1/pt-sales/{id}/cancel/`. It persists draft sales and draft cancellation. That increment does not close the Phase 5 gate. Create and update accept `member_id`, `trainer_staff_profile_id`, `service_date`, `description`, positive decimal-string `gross_fee`, and `currency`. Increment 4 accepts an optional nested `payment` draft. The server owns status, the commission percent snapshot, actors, and timestamps. List, detail, create, update, and cancel return read-only `created_by_membership_id` and `updated_by_membership_id`, plus nullable `submitted_by_membership_id`, `approved_by_membership_id`, `rejected_by_membership_id`, and `cancelled_by_membership_id`. Write requests reject those actor fields. Unpublished actor fields stay null, and cancel fills `cancelled_by_membership_id`. `currency` must equal the gym currency or the response is `422 CURRENCY_MISMATCH`. The member must be `ACTIVE` (`422 MEMBER_NOT_ACTIVE`). The trainer profile and membership must be `ACTIVE` and hold the protected `TRAINER` role (`422 TRAINER_NOT_ELIGIBLE`). The trainer and member need a same-gym `ACTIVE` assignment effective on `service_date` (`422 ASSIGNMENT_NOT_EFFECTIVE`). A protected trainer without protected owner or manager can create only for that caller's own active profile; another trainer id is `404`. A protected receptionist can create, update, and cancel only drafts that membership created, and list/detail hide every other sale. A protected trainer without protected owner or manager sees only sales for that caller's active profile whose assignment is effective today. Create returns `201`. Update and cancel return `200`. Cancel accepts a nonblank `reason`, is valid only from `DRAFT`, and a later command on that row is `409 PT_SALE_STATE_CONFLICT`. Create, update, and cancel require `Idempotency-Key`. List accepts only `status`, `member_id`, `trainer_staff_profile_id`, `service_date_from`, `service_date_to`, `limit` (default 25, maximum 100), and `after`, ordered by `created_at DESC, id DESC`. A `service_date_to` earlier than `service_date_from` is rejected. The response previews `commission_amount`, `gym_share`, `paid_amount` `0.00`, and `outstanding_amount` with half-up rounding and does not store a commission row. Description length is 500 characters.

Phase 5 Increment 4 adds `POST /api/v1/pt-sales/{id}/submit/`, `POST /api/v1/pt-sales/{id}/approve/`, and `POST /api/v1/pt-sales/{id}/reject/`. Sale void, package grants, the session ledger, bookings, and classes were unpublished in this increment. The staff and trainer pages are published in the current Phase 5 paragraph below. This increment does not close the Phase 5 gate. Create and update may include `payment` with `description`, a positive decimal-string `amount`, `method`, `business_date`, and optional `received_at` and `reference_number`. `payment: null` on update cancels the linked draft and clears the sale link so another draft can be attached. Omitting `payment` preserves the draft's description, amount, method, business date, received time, and reference, and copies the sale member and currency onto that draft. The sale forces purpose `PT`, the sale member, a null membership, and the sale currency. Client purpose, member, membership, source, status, and actor fields are rejected. The payment amount is independent of `gross_fee`. A public payment route cannot create or mutate a linked PT payment (`409 PT_PAYMENT_OWNED`). Legacy PT payments with no sale link keep the public payment workflow. Submit requires `pt.sales.submit` and moves `DRAFT` to `PENDING_APPROVAL`, freezing the sale and submitting the linked payment in the same transaction. Approve and reject require `pt.sales.approve`, an active protected manager or owner, no protected receptionist role, and a different actor from the creator and submitter. Approve moves `PENDING_APPROVAL` to `POSTED`. With a linked payment it posts that charge through the shared payment helper: one receipt, and for cash one inbound movement on the supplied open session, including the reception till rule. Without a linked payment, `cash_session_id` is rejected and no payment, receipt, or cash row is created. Approval returns the posted sale, nullable payment, nullable receipt, nullable cash session and transaction, and the accrued commission. Reject requires `rejection_reason`, moves the sale and any linked payment to `REJECTED`, and creates no receipt, cash movement, commission, expense, package, or ledger row. Commission accrual is one immutable `ACCRUED` row per posted sale, with paid amount `0.00`. Increment 5 publishes commission list, detail, and record-payment. Sale void stays unpublished. Every command requires `Idempotency-Key`.

A trainer is an `ACTIVE` `StaffProfile` whose `ACTIVE` gym membership holds the protected `TRAINER` role. The trainer list returns `id`, `staff_code`, and `display_name` for same-gym active trainers. A membership that holds protected `OWNER` or `MANAGER` follows `pt.assignments.view` across the gym. A protected `TRAINER` caller who does not also hold protected `OWNER` or `MANAGER` sees only that caller's own active trainer profile, assignments, and currently effective assigned members. A custom role does not remove that limit. Assigned members are `ACTIVE` assignments whose inclusive gym-local range contains today, returned as `id`, `member_number`, `first_name`, `last_name`, `assignment_id`, `effective_from`, and `effective_until`.

An assignment response is `id`, `trainer_staff_profile_id`, `member_id`, `effective_from`, `effective_until`, `status`, `ended_at`, `reason`, `created_at`, `updated_at`, plus read-only `trainer` (`id`, `staff_code`, `display_name`) and `member` (`id`, `member_number`, `first_name`, `last_name`). The server sets the gym, `ACTIVE` status, actor, and timestamps. End requires `effective_until` on or after `effective_from` and a nonblank `reason`, then stores `ENDED` and `ended_at`. Cancel accepts only `reason`, keeps the effective dates, and stores `CANCELLED` and `ended_at`. A closed row cannot change. Overlap of inclusive `ACTIVE` ranges for the same gym, trainer, and member is `409 ASSIGNMENT_OVERLAP`. An inactive same-gym member is `422 MEMBER_NOT_ACTIVE`. A same-gym staff profile that is not an active trainer is `422 TRAINER_NOT_ELIGIBLE`. A trainer-only caller cannot create an assignment, including one that names that caller's own staff profile; that create is `404` before member eligibility is disclosed. The same caller may end or cancel an existing assignment that already belongs to that caller when `pt.assignments.manage` allows it. `Member.is_pt_client` is derived from a same-gym `ACTIVE` assignment effective on the gym-local as-of date.

A membership that holds the protected `TRAINER` role, and does not also hold protected `OWNER` or `MANAGER`, is assignment-scoped on the published generic member surfaces that the trainer template may use. A custom role does not remove that limit. The assignment must be same-gym, `ACTIVE`, effective on the gym-local current date, and owned by that caller's active `StaffProfile`. Member list, membership list, membership expiry, and check-in list return only those members. Member detail, member summary, membership detail, check-in detail, nested check-in history, and profile-photo upload, complete, and download return `404` for an unassigned id. There is no trainer-editable member field allowlist, so member update, create, archive, and restore are `404` for every member, including an assigned one, and do not write. A custom grant of `members.create`, `members.archive`, or `members.restore` does not open those commands. Membership create stays available when `memberships.create` is granted, but only for an effectively assigned member; any other member id is `404`. Profile-photo delete of an unassigned or missing asset is `404`; delete of an assigned photo stays `403` because the trainer template does not grant `files.delete`. Check-out, staff challenge issuance, and display or device management stay denied for a trainer-only caller. Payment and finance routes stay closed even when a custom role grants the finance permission. Published payment and receipt list, detail, commands, and receipt download; daily sales; expense categories and expenses; cash sessions and transactions; salary periods; product categories, products, stock, and retail sales and returns return `404` before query parsing, object lookup, or mutation. Expense-receipt upload, complete, download, and delete on the shared file routes do the same, while a profile photo keeps its assignment rule. A missing permission or entitlement stays `403`, including a hard-reserved approval a custom role cannot hold. The published tenant dashboard stays closed for that trainer-only caller the same way. Nested payment history is `404` for every member before the member row is loaded, including an assigned member and a custom grant of `payments.view`. Protected owner or manager on that membership follows the granted payment permission. A membership that also holds protected `OWNER` or `MANAGER` keeps its granted gym-wide scope on those generic routes. A non-trainer custom role with the granted permission stays unrestricted. This increment does not publish packages, classes, PT sales, commissions, or trainer UI, and it does not close the Phase 5 gate.

The target contract, not the Increment 3 runtime, is: PT sale create/update fields while draft include `member_id`, `trainer_staff_profile_id`, `service_date`, `description`, positive decimal-string `gross_fee`, `currency`, and an optional payment draft using the payment-create writable fields. Increment 4 publishes that optional draft with sale-owned fields and still rejects client purpose, member, membership, source, and status. Read-only/rejected: `gym_id`, sale/approval status, payment/receipt IDs and numbers, all commission percentage snapshots and amounts, gym share, paid/outstanding totals, `created_by_membership_id`, `updated_by_membership_id`, `submitted_by_membership_id`, `approved_by_membership_id`, `rejected_by_membership_id`, `cancelled_by_membership_id`, and posting timestamps. Create returns a draft. Submit freezes the sale and optional payment. Approve accepts cash-session data where required and atomically returns the posted sale, payment/receipt, and generated commission accrual. Reject requires `rejection_reason` and creates no payment, receipt, commission, or cash movement. Cancel requires `reason` and is valid only from `DRAFT`.

Commission payment request fields: positive decimal-string `amount`, `business_date`, `payment_method`, optional `cash_session_id`, optional external reference, and required/optional policy note. Read-only/rejected: commission basis, percentage snapshot, `commission_amount`, `gym_share`, prior/new `paid_amount`, `outstanding_amount`, status, expense/reversal IDs, actors, and timestamps. Response returns the commission before/after summary and linked immutable expense/cash movement.

PT sale list/detail, create/update, submit, approve, and void require `pt.sales.view`, `pt.sales.create`/`pt.sales.update`, `pt.sales.submit`, `pt.sales.approve`, and `pt.sales.void` respectively; reject requires `pt.sales.approve` and cancel requires `pt.sales.update`. Approval or rejection requires a different actor from creator/submitter; RECEPTIONIST cannot approve. Commission list/detail requires `pt.commissions.view`; commission payment requires hard-reserved `pt.commissions.pay` on a protected owner or manager who did not create the accrual. Every financial command requires `Idempotency-Key`, PT feature entitlement, same-gym references, trainer assignment/object-scope rules, valid states, exact decimal/currency checks, and authoritative calculation under lock.

The paragraphs above that name an increment describe published runtime. Phase 5 Increment 5 publishes the commission list, detail, and record-payment paragraph below. The package-ledger increment publishes member-service-package list, detail, admin grant, ledger read, and ledger adjust, and it stores an optional package on a draft PT sale. Sale void is published at `POST /api/v1/pt-sales/{id}/void/`. Class-type list/create/update, class-schedule list/create/update, class-occurrence list, and occurrence cancel are published. PT and class booking list, create, attend, no-show, and cancel are published, including PT booking detail. Trainer scan is published. The staff and trainer pages are published at `/pt/trainers`, `/pt/packages`, `/pt/member-packages`, `/pt/sales`, `/pt/commissions`, `/pt/bookings`, and `/classes`, and `/check-ins` includes assignment-scoped trainer scan. Phase 5 full code scope is complete as of 2026-10-19: backend PT, classes, and trainer scan; staff and trainer web; and browser E2E. Linked-member self-service, including `GET /api/v1/member/service-packages/`, `GET /api/v1/member/schedule/`, and `POST /api/v1/member/class-bookings/`, remains Phase 7. This is code and test gate closure, not production deployment. ADR 0010 live Resend, AWS, and ClamAV provider proofs remain open. At that Phase 5 close, Phase 6, mobile, production readiness, and provider closure were not claimed. `make backend-check`: Ruff passed; format 312 files; mypy 199 source files; migration drift none. `make backend-test`: 334 passed, 298 deselected. Final serial `make backend-test-pg`: 298 passed, 334 deselected, 398.50s. Node 24.20.0 web format, lint, typecheck, and build passed; the build has 29 routes; Vitest 61 files / 326 tests. A fresh `spectacular --validate --fail-on-warn` temporary export byte-matched `contracts/openapi.v1.json`; generated types are current. IDE lints and `git diff --check` were clean. The existing `pt` entitlement gates every PT route and every class route. A class permission does not create a second feature. RBAC stays separate: a granted class or PT permission still fails when `pt` is inactive, and an active `pt` entitlement grants no permission by itself.

Draft PT sale create and update accept optional `service_package_id`. Omitting it leaves the current selection. `service_package_id: null` clears it while the sale is `DRAFT`. The package must be same-gym, `type=PT`, `is_active`, and the same currency as the sale. The server stores `package_price_snapshot` from `ServicePackage.price`. `gross_fee` and the nested payment `amount` stay whatever the staff sent. The server does not copy the package price into either field. Submit freezes `service_package_id` and that snapshot with the other business fields. Approval rechecks the package. When it is still eligible, approval inserts exactly one `MemberServicePackage` and exactly one `GRANT` ledger row in the same transaction as the sale, payment, receipt, cash, and commission. When no package is selected, approval inserts neither. A package that is inactive, cross-gym, not `PT`, or a different currency is `422 PACKAGE_NOT_ELIGIBLE`, and the approval rolls back.

`GET /api/v1/member-service-packages/` and `GET /api/v1/member-service-packages/{id}/` require `pt.schedule.view` and the `pt` entitlement. List filters are `member_id`, `status`, `package_type`, `limit` (default 25, maximum 100), and `after`, ordered by `created_at DESC, id DESC`. A trainer without protected owner or manager sees grants only for members assigned to that caller today. `POST /api/v1/member-service-packages/` requires exact `pt.sessions.adjust`, an active protected manager or owner, no protected receptionist or trainer role, and the `pt` entitlement. The body is `member_id`, `service_package_id`, `business_date`, required boolean `complimentary`, and nonblank `reason`. The server rejects amount, session count, validity, payment, sale, status, and actor fields. A `CLASS` package may be complimentary or not. Catalog create accepts `class_type_id` for `CLASS` and requires an active same-gym class type. A `PT` package rejects `class_type_id`. Package definitions stay immutable. A `PT` package requires `complimentary=true`; otherwise the response is `422 SALE_GRANT_REQUIRED`. The command creates no payment, receipt, cash movement, or sale. It snapshots the package, sets `valid_from` to `business_date`, inserts one `GRANT`, writes an audit row, and returns `201` with the grant and that ledger entry. `Idempotency-Key` is required.

`GET /api/v1/member-service-packages/{id}/ledger/` uses the same read permission and returns `occurred_at DESC, id DESC` with `limit` and `after`. `POST /api/v1/member-service-packages/{id}/ledger/adjust/` uses the same manager/owner and `pt.sessions.adjust` rule. The body is `reverses_entry_id` and nonblank `reason`. The reason stored on the new row is `CORRECTION`. `session_delta` is the exact negation of the named entry. The named entry must belong to this grant, must not be the original `GRANT`, and must not already have a correction. The resulting sum of deltas must stay inside `0..granted_sessions`. A breach is `409 LEDGER_BOUNDS`. The route cannot append `GRANT`, `ATTENDED`, `POLICY_NO_SHOW`, `REFUND`, or `EXPIRY`. `Idempotency-Key` is required.

`POST /api/v1/pt-sales/{id}/void/` requires `pt.sales.void`, the `pt` entitlement, and `Idempotency-Key`. The body is required nonblank `reason`, required `business_date`, optional `cash_session_id`, and `payout_reversals`. `business_date` is gym-local and must fall from the posted sale `service_date` through today. `payout_reversals` is an array of `{commission_payment_id, cash_session_id}`. It must name every `POSTED` payout of that sale's commission exactly once. A cash payout requires an open same-gym destination session for that business date and currency. A non-cash payout requires `cash_session_id: null`. An incomplete map is `422 PAYOUT_REVERSAL_INCOMPLETE`. When the sale has a cash payment, top-level `cash_session_id` is required and follows the same open-session rule, including the reception-till rule when the original payment has reception provenance. A sale with no payment, or a non-cash payment, rejects top-level `cash_session_id`. The command locks the commission, then the sale, then destination cash sessions by id, then the payment and its receipts, then the member package. Posted payout children are locked inside each reversal after that commission lock. The ledger grant is read under the package lock. Effects then run in this order: voids every posted payout and its protected `PT_COMMISSION` expense, with one cash `IN` when that payout was cash; reverses the linked payment, receipt, and cash `IN` through the internal payment helper when a payment exists; when a grant exists and its remaining sessions still equal `granted_sessions`, appends one `REFUND` for the full grant and marks the grant `VOID`; marks the commission `VOID` with `paid_amount` `0.00` and `outstanding_amount` `0.00`; marks the sale `VOID`. A consumed grant, meaning remaining sessions are below `granted_sessions`, is `409 PACKAGE_CONSUMED` and the command rolls back. The sale does not insert a second `PTSale`, and `reversal_pt_sale_id` stays null. The payment reversal uses the existing payment reversal link. The receipt void links to the original receipt. The cash correction sets `reversal_of`. Each payout child stores its reversal expense and reversal cash transaction. The package `REFUND` sets `reverses_entry_id` to the `GRANT`. A second void key is `409 PT_SALE_STATE_CONFLICT`. There is no public route that voids one payout while leaving the sale posted. Public payment and expense commands still cannot mutate the sale-owned payment or the protected `PT_COMMISSION` expense.

Commission list requires `pt.commissions.view`. Filters are `status`, `trainer_staff_profile_id`, `service_date_from`, `service_date_to`, `limit`, and `after`, ordered by `created_at DESC, id DESC`. A trainer without protected owner or manager sees only that caller's commissions. Detail returns the commission plus its payout children ordered by `created_at, id`. `POST /api/v1/pt-commissions/{id}/record-payment/` requires `pt.commissions.pay` on an active protected owner or manager. The permission is hard-reserved, so an accountant may view commissions but cannot pay them, and a receptionist, trainer, or custom role cannot receive the permission. A membership that also holds protected receptionist or trainer is denied. The payer must differ from `commission.created_by_membership_id`, and the generated expense uses that accrual creator as both creator and submitter. Cash uses the open same-gym session named on the payout. The trainer template does not have the permission. The body is positive decimal-string `amount` with at most two decimal places, `business_date`, `payment_method`, optional `cash_session_id`, optional `reference_number`, and optional `policy_note`. `business_date` must run from the posted sale `service_date` through the gym-local today, inclusive, for cash and every other method. A date outside that window is `400` on `business_date` and writes no payout, expense, or cash movement. The same window is enforced by the payout insert guard. The server rejects commission basis, snapshots, totals, status, expense ids, and actors. `amount` must be less than or equal to `outstanding_amount`. Cash requires an open same-gym session for that date and currency and writes one `OUT`. Any other method rejects `cash_session_id` and writes no cash movement. The command inserts one immutable `PTCommissionPayment`, one protected `PT_COMMISSION` expense through the internal expense writer, and updates `paid_amount`, `outstanding_amount`, and status to `ACCRUED`, `PARTIALLY_PAID`, or `PAID`. Concurrent payouts lock the commission so two callers cannot spend the same outstanding amount. The response is the commission before and after, the payout child, the expense, and the nullable cash session and transaction. Replay of the same idempotency key returns that response. A posted payout stays immutable until sale void.

Class routes require the `pt` entitlement in addition to `classes.view`, `classes.manage`, `classes.book`, or `classes.attendance`. This increment publishes class-type list, create, and update, class-schedule list, create, and update, class-occurrence list, occurrence cancel, and the PT and class booking routes. Class-type reads and writes are gym-wide for a caller who holds the permission. Schedule list accepts `status`, `trainer_staff_profile_id`, `class_type_id`, `limit` (default 25, maximum 100), and `after`, ordered by `effective_from`, `local_start_time`, `id`. Class-type list accepts `is_active`, `limit`, and `after`, ordered by `code`, `id`. Create, update, and cancel commands are throttled. Class-schedule create and patch, and occurrence cancel, require `Idempotency-Key`. Class-type create and patch do not. More than 400 matching dates is `422 SCHEDULE_TOO_LARGE`. A range with no matching weekday is `422 SCHEDULE_NO_MATCHES`. Trainer or exclusive-location overlap is `409 SCHEDULE_CONFLICT`. A second cancel is `409 OCCURRENCE_STATE_CONFLICT`. `POST` and `PATCH /api/v1/class-types/` use `classes.manage`. Create fields are `code`, `name`, optional `description`, positive `default_duration_minutes`, and positive `default_capacity`. `code` uses the service-package code rule. Patch may change name, description, duration, capacity, and `is_active`. Code is immutable. Schedule create and patch use `classes.manage`. Create materializes occurrences. Patch regenerates only future occurrences that have no `BOOKED`, `ATTENDED`, or `NO_SHOW` booking. Booked and historical occurrences stay. `POST /api/v1/class-occurrences/{id}/cancel/` requires `classes.manage`, a nonblank `reason`, and `Idempotency-Key`. It is valid only while the occurrence is `SCHEDULED` and has no `ATTENDED` or `NO_SHOW` booking. It marks the occurrence `CANCELLED` and every `BOOKED` booking `CANCELLED` without a ledger entry. Occurrence and booking reads use `classes.view`. Booking create and cancel use `classes.book` for the caller's own member scope and `classes.manage` otherwise. Attend and no-show use `classes.attendance`. PT booking create and cancel use `pt.schedule.manage`. PT attend and no-show use `pt.schedule.manage` as well, because there is no separate PT attendance code. List filters for occurrences and both booking collections are `status`, `trainer_staff_profile_id`, `member_id` where the resource has a member, `date_from`, `date_to`, `limit`, and `after`, ordered by start time then `id`. `view=calendar` requires a gym-local range of at most 42 days. A trainer without protected owner or manager sees and writes only that caller's trainer, schedules, occurrences, and assigned members.

A booking requires `member_service_package_id` when attendance or no-show will consume a session. The package must be `ACTIVE`, same member, unexpired on the gym-local start date, and have remaining sessions. The caller may omit it only by sending `unmetered: true` and nonblank `unmetered_reason`, and only when that caller is an active protected manager or owner. Trainer and receptionist calls with `unmetered: true` are `403`. An unmetered attend or no-show writes no ledger row. A metered PT attend and a metered PT no-show each append one `-1` entry, `ATTENDED` or `POLICY_NO_SHOW`, exactly once. A metered class attend does the same. A metered class no-show appends `POLICY_NO_SHOW` only when `GymSettings.class_no_show_consumes_session` is true. Cancel is valid only from `BOOKED`. `ATTENDED` is valid only when the current time is at or after `starts_at`. `NO_SHOW` is valid only when the current time is at or after `ends_at`. For a class booking those instants are the locked occurrence `starts_at` and `ends_at`. An earlier terminal command is `409 BOOKING_STATE_CONFLICT` and writes no ledger row and no audit row. The same instants are enforced by the booking update guards, so a raw runtime update fails as well. Equality with the boundary is allowed. A class cancel at or after start minus `class_cancellation_cutoff_minutes` is `409 CLASS_CUTOFF`, except a protected manager or owner with `classes.manage` may cancel late with a required `reason`. Capacity locks the occurrence and counts bookings that are not `CANCELLED`; overflow is `409 CLASS_FULL`. Trainer, member, and exclusive-location overlaps are `409 SCHEDULE_CONFLICT`. Every booking command requires `Idempotency-Key`.

`POST /api/v1/trainer/check-ins/scan/` is published. It is not `POST /api/v1/check-ins/scan/`, and that generic staff route stays unpublished. A member token is rejected. The caller is a staff JWT whose membership holds protected `TRAINER`, exact `checkins.create`, and the active `members` and `memberships` entitlements used by manual check-in. A custom role with a similar name is not that protected role. The body is `payload` and `member_id` only. The member must be same-gym and `ACTIVE`, with a same-gym `ACTIVE` assignment to the caller's active `StaffProfile` that contains gym-local today. Any other member is `404`. This route stays assignment-scoped when the membership also holds protected `OWNER` or `MANAGER`. A caller who lacks `checkins.create`, an entitlement, or the protected `TRAINER` role is `403`. Challenge ownership, expiry, replay, eligibility, duplicate window, and concurrency match `POST /api/v1/member/check-ins/scan/`. The visit is `method=QR`. Both membership actor columns on the visit stay null. The audit action is `CHECK_IN_RECORDED` and the audit actor is the trainer membership. The response uses the staff check-in shape and omits the payload. `Idempotency-Key` is rejected, as on the member route. The route is rate-limited by `trainer_check_in_scan` on the gym membership and client address, and separately by `trainer_check_in_scan_ip` on the client address. Neither key includes the payload, and neither bucket is the member-self `member_check_in_scan` bucket. Excess requests return `429` and do not consume a challenge use, write a check-in, or write an audit row. A failed scope, permission, challenge, or duplicate path does not consume a challenge use and does not write a check-in or an audit row.

Phase 5 staff and trainer web is published. Each page requires the active `pt` entitlement and the listed view permission. Navigation hides a page when either check fails. Direct navigation redirects to the first authorized tenant home. Action controls follow the exact command permission and protected-role rule; hiding a control is presentation only.

- `/pt/trainers` uses `pt.assignments.view` for the directory, assignments, and assigned members.
- `/pt/packages` uses `pt.schedule.view` for the package catalog.
- `/pt/member-packages` uses `pt.schedule.view` for grants and the ledger. The grant and adjust actions require `pt.sessions.adjust` and a protected manager or owner.
- `/pt/sales` uses `pt.sales.view` for draft, submit, approve, reject, cancel, and void.
- `/pt/commissions` uses `pt.commissions.view`. Record payment is shown only with `pt.commissions.pay`, which stays hard-reserved to protected owner and manager.
- `/pt/bookings` uses `pt.schedule.view` for the gym-local PT calendar.
- `/classes` uses `classes.view` for types, schedules, occurrences, bookings, attendance, and no-show.
- `/check-ins` gains a trainer scan action only for a protected trainer with `checkins.create`. It posts to the trainer scan route and sends the selected assigned member. It does not enable a generic staff scan.

The browser E2E for that web ran in the 2026-10-19 closure. A clean full `make e2e` passed 16 tests in 5.2m, including 10 Phase 5 browser tests (assignments/catalogs; Reception submit + separate Owner approval cash sale; grant+commission; two payouts; consumed void rollback + untouched refund/reconciliation; class schedule/book/attendance/no-show/cutoff; trainer commission scope and QR scan assigned/unassigned/generic-route absence). A formal security review reported no medium-or-higher finding in its reviewed slice but was partial, and that partial review is not full Phase 5 coverage. A targeted GPT-5.6 Phase 5 audit found and fixed trainer finance bypasses, premature attendance and no-show, payout date bounds, and trainer scan throttling. Final targeted verification found no remaining medium-or-higher issue in the audited Phase 5 scope.

### Reports and dashboard — Phase 6

Phase 6 Increment 2 publishes `GET /api/v1/reports/daily/?date=YYYY-MM-DD` and `GET /api/v1/reports/monthly/?year=YYYY&month=M`, and the tenant `/reports` page. Both require staff auth, the active `reports` entitlement, and `reports.financial`. `reports.view` alone is denied. Trainer finance closure still returns 404. The query is exactly `date`, or exactly integer `year` and `month`, inside the supported reporting window. Unknown gym, status, or formula parameters are rejected. The response is read-only: `gym` `{id, code, name}`, `timezone`, `currency`, `range` `{date_from, date_to}`, `generated_at`, `source_status_policy`, `rows`, and `totals`. Daily has one row. Monthly zero-fills every gym-local calendar date, at most 31 rows. Each row has operational counts, six mutually exclusive revenue buckets including `CLASS`, three-decimal product quantity, product COGS and gross profit from immutable snapshots net of a full void and partial returns on the correction date, `total_expenses`, and `net_profit`. Totals sum the rows. `unique_attendees` is distinct across the whole range. Totals also include staff-salary and PT-commission subtotals and the ten protected expense categories zero-filled in catalog order. Clients cannot set the source-status policy. The selector uses 10 queries for one day or 31 days. There are no migrations, report tables, caches, Redis, or Celery. Tests cover backend security and unit checks, web component and route checks, and `web/e2e/phase6.spec.ts`. At the Increment 2 close, Phase 6 was not complete. Production deployment is not complete.

Phase 6 Increment 3 publishes `GET /api/v1/reports/cash-book/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` and `GET /api/v1/reports/profit-loss/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` on the same tenant `/reports` page. Both require staff auth, the active `reports` entitlement, and `reports.financial`. `reports.view` alone is denied. Trainer finance closure still returns 404. The query is exactly ordered `date_from` and `date_to`, inclusive, from 1 through 366 days, inside the supported reporting window. Unknown, duplicate, and empty parameters are rejected. Rows are ascending and zero-filled for every date. Cash book groups `CashSession` and append-only `CashTransaction` rows by `business_date`. Amounts stay positive and direction carries the sign. `VOID` sessions are counted separately from open and closed. Actual closing and difference sum terminal snapshots and are `0.00` when a date has none. The cash-book selector uses 2 queries for 1 or 366 days. Profit/loss reuses the Increment 2 correction-date formulas: six revenue buckets including `CLASS`, product COGS and gross profit, total expenses, and net profit as total revenue minus total expenses. Totals add staff-salary and PT-commission subtotals and the ten protected categories. The profit/loss selector uses 6 queries for 1 or 366 days. There are no migrations, report tables, caches, Redis, or Celery. Increment 3 is gate-clean as of 2026-10-19. Gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). Phase 6 Increment 4 publishes `GET /api/v1/reports/membership-expiry/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` and `GET /api/v1/reports/check-ins/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` on the same tenant `/reports` page. Both require staff auth, the active `reports` entitlement, and `reports.view`. They do not require `reports.financial` and do not include `currency` or other money fields. Trainer-only callers still return 404. The query is the same ordered inclusive 1 through 366 day range. Membership expiry counts current `ACTIVE`, `SUSPENDED`, and `EXPIRED` rows by `expiry_date` and excludes `PENDING` and `CANCELLED`. It is current status at generation, not a historical point-in-time status. Daily `unique_members` is distinct that day; the range total is distinct across the range. Check-ins interpret instants in the gym timezone. Method counts for manual, QR, NFC, and biometric are mutually exhaustive. Check-outs group by the gym-local `check_out_at` date. `open_visits_from_day` counts that local check-in date when checkout is still null. `currently_checked_in` counts every same-gym null checkout at generation and is independent of the selected range. Membership expiry uses 2 queries and check-ins use 4, for 1 or 366 days. There are no migrations, report tables, caches, Redis, or Celery. Increment 4 is gate-clean as of 2026-10-20. Phase 6 Increment 5 publishes `GET /api/v1/reports/pt-sessions/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` and `GET /api/v1/reports/classes/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` on the same page. Both require staff auth, the active `reports` entitlement, and `reports.view`. They do not require `reports.financial` or the `pt` entitlement and do not include `currency`, money, or member or staff PII. Trainer-only callers still return 404. PT sessions group current `PTBooking` status by the gym-local `starts_at` date. Booked, attended, no-show, and cancelled are mutually exhaustive. Metered and unmetered include every status. Daily unique members and unique trainers are distinct that day; each range total is distinct across the range. The PT selector uses 2 queries for 1 or 366 days. Class rows group `ClassOccurrence.local_date` and the current occurrence and booking status. Available capacity sums capacity for scheduled and completed occurrences and is not joined through bookings. Reserved slots are booked, attended, and no-show bookings. Remaining capacity is that day's available capacity minus reserved slots, clamped at zero, and totals sum those daily values. Range unique members are distinct across bookings. Range unique trainers are distinct across occurrences and appear on totals. The class selector uses 4 queries for 1 or 366 days. Increment 5 is gate-clean as of 2026-10-20. Gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). Phase 6 Increment 6 publishes `GET /api/v1/reports/product-sales/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`, `GET /api/v1/reports/stock-movements/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`, and `GET /api/v1/reports/inventory/` on the same page. Product sales requires `reports.financial` and includes `currency`. It pages the same-gym product catalog, including archived products and products with no activity in the range. Posted and void sale lines add on the sale `business_date`. A full void subtracts on `void_business_date`. A partial return subtracts on the return `business_date`. Quantity, revenue, COGS, and gross profit come from the immutable line snapshots, not the current product cost. Totals cover the whole catalog and range. The selector reads one catalog count, one `limit + 1` page, three page aggregates, and three range aggregates: 8 queries for any limit from 1 through 100 and any range from 1 through 366 days. Stock movements require `reports.view`, have no paging and no money, and zero-fill one row per date from stored signed `StockMovement.quantity` grouped by `business_date`. That selector uses 1 query. Inventory requires `reports.view`, has no date range and no money, and pages the current catalog. Stock is the current sum of same-gym movement quantities, or zero. `is_low_stock` is true when that sum is less than or equal to `low_stock_threshold`. Totals cover the whole catalog. That selector uses 3 queries. `limit` defaults to 25 and cannot exceed 100. `after` is an opaque signed cursor valid for 24 hours. Its position is `sku` and `id` only, ordered `sku ASC, id ASC`, and it is bound to the gym, actor membership, route, filters, and limit. A cursor from another route, gym, actor, filter, or limit is rejected. These three routes do not require the finance or inventory source entitlements. Trainer-only callers still return 404 once they hold the route permission. Increment 6 is gate-clean as of 2026-10-20. Phase 6 Increment 7 publishes `GET /api/v1/trainer/dashboard/` and `GET /api/v1/trainer/reports/sessions/`, and the tenant `/pt/dashboard` page, for protected TRAINER callers with `reports.view` plus the `reports` and `pt` entitlements. Both reads are own-profile only and include no money. Dashboard presets and a custom range of at most 366 inclusive days return `generated_at`, `trainer_staff_profile_id`, `active_assigned_members`, session counts, and at most 10 `upcoming_bookings`. The session report is that profile's daily zero-filled PT statuses. The dashboard selector uses 3 queries and the session-report selector uses 2. After authorization, the dashboard handler uses 4 queries and the session handler uses 2. Increment 7 is independently verified and gate-clean as of 2026-10-20. Gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). Increment 8 publishes synchronous CSV downloads for the eleven gym reports and is independently verified and gate-clean as of 2026-10-20. Trainer dashboard and session reports are not exportable. Phase 6 implementation is complete and its local CI-equivalent gate is closed. Production deployment is not complete, and the ADR 0010 live Resend, AWS, and ClamAV proofs remain open. Final gate evidence is in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md).

```text
GET /api/v1/reports/daily/?date=2026-08-15
GET /api/v1/reports/monthly/?year=2026&month=8
GET /api/v1/reports/cash-book/?date_from=...&date_to=...
GET /api/v1/reports/profit-loss/?date_from=...&date_to=...
GET /api/v1/reports/membership-expiry/?date_from=...&date_to=...
GET /api/v1/reports/check-ins/?date_from=...&date_to=...
GET /api/v1/reports/pt-sessions/?date_from=...&date_to=...
GET /api/v1/reports/classes/?date_from=...&date_to=...
GET /api/v1/reports/product-sales/?date_from=...&date_to=...
GET /api/v1/reports/stock-movements/?date_from=...&date_to=...
GET /api/v1/reports/inventory/
GET /api/v1/reports/daily/export/?date=2026-08-15
GET /api/v1/reports/monthly/export/?year=2026&month=8
GET /api/v1/reports/cash-book/export/?date_from=...&date_to=...
GET /api/v1/reports/profit-loss/export/?date_from=...&date_to=...
GET /api/v1/reports/membership-expiry/export/?date_from=...&date_to=...
GET /api/v1/reports/check-ins/export/?date_from=...&date_to=...
GET /api/v1/reports/pt-sessions/export/?date_from=...&date_to=...
GET /api/v1/reports/classes/export/?date_from=...&date_to=...
GET /api/v1/reports/product-sales/export/?date_from=...&date_to=...
GET /api/v1/reports/stock-movements/export/?date_from=...&date_to=...
GET /api/v1/reports/inventory/export/
```

`POST /api/v1/reports/exports/` and `GET /api/v1/reports/exports/{id}/` are not published. Increment 8 does not create export jobs or stored export rows.

Financial reports, including product sales, require `reports.financial`. Membership-expiry, check-in, PT session, class, stock-movement, and inventory reports require `reports.view` and do not require `reports.financial`. CSV download additionally requires `reports.export` and the active `reports` entitlement. Daily, monthly, cash-book, profit-loss, and product-sales exports also require `reports.financial`. The other six exports require `reports.view`. Trainer-only memberships stay 404 even with `reports.export`. Trainer dashboard and session reports have no export route. Every selector begins with active-gym scope.

Report request contracts are read-only query serializers:

- daily: required `date`;
- monthly: required `year` and `month`;
- cash book, profit/loss, membership expiry, check-ins, PT sessions, classes, and stock movements: required `date_from` and `date_to` only, ordered, inclusive, 1 through 366 days, inside the reporting window;
- product sales: required `date_from` and `date_to`, plus optional `limit` (1 through 100, default 25) and optional `after`;
- inventory: optional `limit` and optional `after` only;
- product-sales export: required `date_from` and `date_to` only. `limit` and `after` are rejected. The file contains the whole catalog for that range;
- inventory export: no query parameters. `limit` and `after` are rejected. The file contains the whole current catalog;
- every other export: the same query as its JSON report.

A successful export is one RFC 4180 CSV file, CRLF line endings, `QUOTE_MINIMAL`. `Content-Type` is `text/csv; charset=utf-8`. `Content-Disposition` is an attachment. `X-Content-Type-Options` is `nosniff`. `Cache-Control` is `no-store, private`. The filename is `{kind}-{gym_code}-{from}-{to}.csv`, or `{kind}-{gym_code}.csv` for inventory. The table starts with `record_type`, repeats gym identity, timezone, currency, range, generation instant, and source-status policy on every row, then the report row fields, then total-only fields. Data rows use `ROW`. Exactly one `TOTALS` row follows. Expense categories are `expense_{SYSTEM_KEY}` columns. A cell whose value starts with `=`, `+`, `-`, or `@` gains one leading single quote. The tenant `/reports` page downloads that file through the BFF and does not parse it.

Responses contain read-only `gym` display identity, gym timezone/currency, normalized date range, generation instant, source-status policy, rows, and server-calculated totals/subtotals. No client total, formula, gym selector, actor, or source status override is accepted. Operational reports require `reports.view`; financial totals and exports require `reports.financial`; each also checks its report/export entitlement and configured row/date limits.

### Signed files — Phase 2 member profile photo

```text
POST   /api/v1/files/upload-intents/
POST   /api/v1/files/{id}/complete/
GET    /api/v1/files/{id}/download-url/
DELETE /api/v1/files/{id}/
```

This section is the Phase 2 file contract plus the Increment 4 expense-receipt purpose. These four routes, presigned POST issuance, ClamAV scanning, and canonical re-encode are implemented against an S3-compatible API. [ADR 0010](./adr/0010-production-provider-readiness.md) selects AWS S3 in `ap-southeast-1` as the production store: a private bucket and presigned access over direct AWS HTTPS. Production MinIO is no longer planned. Caddy does not proxy the production object origin. Private ClamAV stays on the VPS private network. Local and end-to-end Compose still use digest-pinned `docker.io/pgsty/silo`, a third-party MinIO-compatible fork, with the local Caddy object proxy. That fork is test-only and is not official MinIO. An end-to-end upload through it is interoperability evidence only. Sentences below that name MinIO describe that test store and the shared SigV4 rules. They are not a production MinIO deployment. The AWS bucket, IAM, CORS, and a live upload and download remain external gates. A ClamAV deployed-host proof remains open. Block Public Access, SSE-S3, and versioning are planned only. Current-object retention, noncurrent expiry, backup and replication, and jurisdiction or legal approval stay open. Accepted purposes are `PROFILE_PHOTO` (staff operation for an existing member's profile photo, `owner_type=MEMBER`) and `EXPENSE_RECEIPT` (gym-owned expense evidence, `owner_type=GYM`, `owner_id` the token gym). Profile-photo rules are unchanged: members entitlement, `members.update`/`members.view`, image-only, at most 5 MiB. Expense receipts require the finance entitlement and `expenses.create` or `expenses.update` to create, complete, download, and delete. Create and complete also require `files.upload`. Download also requires `files.view`. Delete does not require `files.delete` or `members.update`; `files.delete` remains the profile-photo delete permission. An attached receipt follows expense visibility. A protected Reception membership may download or delete it only when that membership created or submitted the expense, and another expense's receipt is `404`. An unattached receipt may be completed, downloaded, or deleted by its uploader, or by a same-gym actor who holds `expenses.create` or `expenses.update` and does not hold the protected Reception role. Staff photos, announcement media, member identity documents, and any other purpose still return `422 UNSUPPORTED_FILE_PURPOSE`. Member-self profile-photo routes are published in section 7. They do not add a new purpose. Staff routes still reject a member token.

The gym comes only from the staff access token. A body or query `gym_id` returns `400 INVALID_REQUEST`. Platform tokens and member tokens return `403 PERMISSION_DENIED`. A selected-gym staff token is required.

S3 presigned POST is the browser upload mechanism. Its policy requires the exact object key, the exact `Content-Type`, and a `content-length-range`. The intent response returns that POST URL and the form fields the browser submits.

Accepted declared and detected types are exactly `image/jpeg`, `image/png`, and `image/webp`, with no parameters or alternate spellings. Declared and actual object size must be an integer from 1 through 5,242,880 bytes (5 × 1024 × 1024). A decoded image must have integer width and height of at least 1, and `width × height` must be at most 20,000,000. Zero, missing, or non-integer dimensions, multi-frame or animated images, and decompression bombs are rejected.

#### Upload intent

The shared upload-intent view requires `files.upload` for every purpose. A `PROFILE_PHOTO` intent also requires `members.update` before body validation, and the active `members` feature entitlement. An `EXPENSE_RECEIPT` intent does not require `members.update`. It requires `expenses.create` or `expenses.update` and the active `finance` feature entitlement. The request body is exactly:

| Field           | Rule                                                                             |
| --------------- | -------------------------------------------------------------------------------- |
| `original_name` | Required string, trimmed, 1–255 characters, no `/`, `\`, or code points below 32 |
| `content_type`  | Required, one of `image/jpeg`, `image/png`, `image/webp`                         |
| `byte_size`     | Required JSON integer, 1 through 5,242,880                                       |
| `sha256`        | Required 64-character lowercase hex SHA-256 of the original file bytes           |
| `purpose`       | Required, `PROFILE_PHOTO` or `EXPENSE_RECEIPT`                                   |
| `owner_type`    | `MEMBER` for `PROFILE_PHOTO`; `GYM` for `EXPENSE_RECEIPT`                        |
| `owner_id`      | Existing member UUID, or the token gym id for an expense receipt                 |

`sha256` and `byte_size` identify the original file the browser will upload. After sanitizing, the asset's authoritative `sha256`, `byte_size`, and `content_type` identify the canonical object. Unknown fields are `400 INVALID_REQUEST`. `purpose` other than `PROFILE_PHOTO` or `EXPENSE_RECEIPT` is `422 UNSUPPORTED_FILE_PURPOSE`. A purpose/owner pair that is not `PROFILE_PHOTO`/`MEMBER` or `EXPENSE_RECEIPT`/`GYM`, or a missing owner pair, is `422 UNSUPPORTED_FILE_OWNER`. A disallowed `content_type` is `422 FILE_TYPE_NOT_ALLOWED`. `byte_size` below 1 is `422 FILE_EMPTY`. `byte_size` above 5,242,880 is `422 FILE_TOO_LARGE`. A malformed `sha256` is `422 FILE_CHECKSUM_INVALID`. A malformed `original_name` is `422 INVALID_REQUEST`. Field failures name the field in `error.fields` and create no asset.

Authorization order for `PROFILE_PHOTO` is unchanged: authentication; `files.upload`; `members.update`; body validation; member lookup; object scope; `members` entitlement. Missing permission codes are `403 PERMISSION_DENIED`. A disabled `members` entitlement is `403 FEATURE_NOT_ENTITLED`. An unknown member, a member from another gym, and a same-gym member outside the caller's object scope are `404`. Those responses do not say which case occurred. A staff membership that holds the protected TRAINER role, and does not also hold protected OWNER or MANAGER, may upload, complete, and download a profile photo only for a member with an effective assignment to that caller's active staff profile. A custom role does not remove that limit. An unassigned member is `404`. Delete of that unassigned photo is `404`; an assigned photo still requires `files.delete` and is `403` for the trainer template. Expense-receipt access is unchanged. An `EXPENSE_RECEIPT` intent requires `files.upload`, then the finance entitlement and `expenses.create` or `expenses.update` inside the service. It does not require `members.update`. `owner_id` must equal the token gym. A caller who has `files.upload` but neither expense permission receives `403 PERMISSION_DENIED`.

A valid intent always inserts a new `FileAsset`. Repeating the same body creates another asset. There is no idempotency key and no second POST policy for an existing asset. The insert sets `scan_status = PENDING`, `retention_status = QUARANTINED`, `deleted_at = null`, `rejection_code = null`, `scan_attempt_id = null`, and `scan_started_at = null`. It stores the declaration in both the current object columns and the immutable `upload_sha256`, `upload_byte_size`, and `upload_content_type` columns. `uploaded_by_membership_id` is the caller. The object key is `{gym_id}/{random_uuid}`. The model default `retention_status = ACTIVE` must not be used for this insert.

The `201` response `data` is:

```json
{
  "file_id": "7df4f2d6-79d3-4fd1-b850-968142b1b6dd",
  "purpose": "PROFILE_PHOTO",
  "owner_type": "MEMBER",
  "owner_id": "4c0d1c1e-6a4a-4f0e-9c2a-0a0c2d6e8f10",
  "upload": {
    "method": "POST",
    "url": "https://files.example.com/gym-private",
    "file_field": "file",
    "expires_at": "2026-10-17T10:05:00Z",
    "fields": {
      "key": "gym-uuid/object-uuid",
      "Content-Type": "image/jpeg",
      "policy": "base64-policy",
      "x-amz-algorithm": "AWS4-HMAC-SHA256",
      "x-amz-credential": "access-key-id/date/region/s3/aws4_request",
      "x-amz-date": "20261017T100000Z",
      "x-amz-signature": "signature",
      "success_action_status": "204"
    }
  }
}
```

`expires_at` is exactly 300 seconds after the intent is committed, in RFC 3339 UTC. The POST policy expiration is that same instant. `upload.url` is the configured public object origin plus the bucket, never the internal MinIO address. `fields.key` is the exact object key and is not itself a credential. `x-amz-credential` contains the access key id because SigV4 requires it. The response never includes the secret key, a session token, a bucket policy, or a download URL. Static Phase 2 credentials omit `x-amz-security-token`.

The signer builds this policy explicitly. Its conditions are the configured bucket; `["eq", "$key", object_key]`; `["eq", "$Content-Type", declared content type]`; `["content-length-range", 1, 5242880]`; `["eq", "$success_action_status", "204"]`; `["eq", "$x-amz-algorithm", "AWS4-HMAC-SHA256"]`; `["eq", "$x-amz-credential", the credential returned in the form]`; and `["eq", "$x-amz-date", the signing timestamp returned in the form]`. S3 and MinIO require an exact condition for every submitted form field except `file`, `policy`, and `x-amz-signature`. Static credentials add no `x-amz-security-token` field and therefore no condition for it. The bucket is in the POST URL and is still a policy condition. The policy grants no ACL, no public-read, and no `x-amz-meta-*` field. A browser POST that adds user metadata fails at MinIO.

Browser flow:

1. The staff browser asks the same-origin BFF, which forwards to this route and returns the JSON unchanged. The browser does not call Django directly.
2. The browser creates `FormData`, appends every returned `upload.fields` entry unchanged, and appends the file last under the field name `file`.
3. The browser `POST`s that body to `upload.url`. It lets the browser set the multipart boundary. It does not send `Authorization`, cookies, or a gym id.
4. Success is HTTP `204` with an empty body. Any other status means MinIO did not accept the object. An expired policy fails at MinIO and cannot be refreshed.
5. The browser then calls complete with the same original `sha256` and `byte_size` it declared. The public object origin allows cross-origin `POST` and `GET` only from the configured staff web origins, as specified in `SECURITY.md`.

#### Complete

`POST /api/v1/files/{id}/complete/` uses the same authentication. A `PROFILE_PHOTO` uses `files.upload`, `members.update`, the `members` entitlement, and the profile-photo `404` rules. An `EXPENSE_RECEIPT` uses `files.upload`, the finance entitlement, `expenses.create` or `expenses.update`, and the receipt visibility rules above. It does not require `members.update`. A cross-gym or unknown id is `404`. The body is exactly `sha256` and `byte_size` for the original upload. There is no provider token, ETag, or object key in the body. Format errors use the intent field codes and do not change the asset.

Upload intent, complete, download-url, and delete disable the request-scoped atomic wrapper. The intent transaction therefore commits on its own when that block exits, so an outer request transaction cannot move the commit later than the presign expiry. Every database step in those routes is its own `transaction.atomic()` that installs transaction-local tenant context, locks, writes, commits, and releases its locks. A savepoint inside the request transaction leaves that outer transaction and its row locks open, so it does not qualify. The service must commit the claim, release the member and asset locks, and only then call MinIO, ClamAV, the image decoder, or the re-encoder. No database transaction or row lock is held across that external work.

The claim transaction locks the owning member and then the file asset, in that order, for a profile photo. An expense receipt has no member row; that claim locks the gym-owned file asset after the finance entitlement and an expense create or update permission are checked. It validates the confirmation against the locked row and either returns or claims the work. It does not call MinIO or ClamAV. Delete uses the same lock order.

A `SCANNING` row holds `scan_attempt_id` and `scan_started_at`. The claim is stale when `scan_started_at` is at least 120 seconds before the database transaction timestamp (`CURRENT_TIMESTAMP`). The worker that still holds that token may finalize after 120 seconds until a committed transaction replaces or clears the token. One hundred twenty seconds covers two 15-second ClamAV scans, 10-second image decode, and a 5-second ceiling on each server-side MinIO read, canonical overwrite, and read-back (55 seconds) before another request may take the row.

The claim transaction evaluates a `REJECTED` row before delete retention, so a rejected asset keeps returning its stored code after object removal:

| Asset state                                        | Confirmation body              | Result                                                                                        |
| -------------------------------------------------- | ------------------------------ | --------------------------------------------------------------------------------------------- |
| `PENDING` or `ERROR`, `QUARANTINED`                | Differs from stored `upload_*` | `422 FILE_CONFIRMATION_MISMATCH`; asset unchanged                                             |
| `PENDING` or `ERROR`, `QUARANTINED`                | Matches `upload_*`             | Claim, commit, then external verification                                                     |
| `SCANNING`, `QUARANTINED`, younger than 120s       | Any syntactically valid body   | `409 FILE_SCAN_IN_PROGRESS`; asset unchanged                                                  |
| `SCANNING`, `QUARANTINED`, stale                   | Differs from stored `upload_*` | `422 FILE_CONFIRMATION_MISMATCH`; the stale claim stays                                       |
| `SCANNING`, `QUARANTINED`, stale                   | Matches `upload_*`             | Replace the claim, commit, then external verification                                         |
| `CLEAN`, `ACTIVE`                                  | Matches stored `upload_*`      | `200` with the stored sanitized metadata; no claim, no MinIO call, no second scan, no rewrite |
| `CLEAN`, `ACTIVE`                                  | Differs from stored `upload_*` | `422 FILE_CONFIRMATION_MISMATCH`; asset unchanged                                             |
| `REJECTED`, any retention                          | Any                            | `422` using the stored `rejection_code`; no second scan                                       |
| `DELETE_PENDING` or `DELETED`, scan not `REJECTED` | Any                            | `409 FILE_STATE_CONFLICT`; asset unchanged                                                    |

`409 FILE_SCAN_IN_PROGRESS` uses the standard error envelope. The response includes header `Retry-After`: the whole seconds remaining until `scan_started_at` plus 120 seconds, rounded up, at least 1 and at most 120. The body omits the attempt id, object key, and object bytes.

A claim sets `scan_status = SCANNING`, a new random `scan_attempt_id`, and `scan_started_at` to the database transaction timestamp. Retention stays `QUARANTINED`. `rejection_code` stays null. `upload_*` and the current checksum columns stay unchanged. Reclaiming a stale row replaces `scan_attempt_id` and `scan_started_at` the same way. The previous token then matches nothing. The transaction commits before any external call.

External work uses server credentials and the internal MinIO endpoint. Each MinIO read, canonical overwrite, and read-back stops at 5 seconds. Each ClamAV call stops at 15 seconds. Decode runs in a child process and stops at 10 seconds. If that process is still running, the parent terminates it and joins it, so a slow decoder cannot keep using CPU or memory. The upload is then rejected with `FILE_IMAGE_TOO_LARGE`. The worker keeps the attempt UUID in memory and does not adopt a newer token.

If the object is absent, a short transaction sets `scan_status` back to `PENDING` and clears `scan_attempt_id` and `scan_started_at` only when the token still matches and retention is still `QUARANTINED`. The response is then `409 FILE_UPLOAD_INCOMPLETE`. If that predicate fails, the worker leaves the row unchanged and uses the discard response below. A storage timeout or other storage failure finalizes `ERROR` and returns `503 FILE_STORE_FAILED`. An object whose size is outside 1 through 5,242,880 is terminal `FILE_EMPTY` or `FILE_TOO_LARGE` and is not decoded.

When the stored object matches `upload_sha256` and `upload_byte_size`, completion scans those original bytes with ClamAV. An infected object is terminal `FILE_INFECTED`. A scanner timeout, connection failure, or engine error finalizes `scan_status = ERROR`, leaves `retention_status = QUARANTINED`, and returns `503 FILE_SCAN_UNAVAILABLE`. The same confirmation may be retried, and the retry takes a new attempt.

A reachable clean object is decoded. Magic bytes, the decoder format, and `upload_content_type` must all agree. The server sets the decoder pixel ceiling to 20,000,000 and also rejects header dimensions outside the width, height, and product rules. Terminal rejection codes are `FILE_TYPE_MISMATCH`, `FILE_MALFORMED` (including animation or multiple frames), `FILE_INVALID_DIMENSIONS`, `FILE_IMAGE_TOO_LARGE` (including a decompression bomb or a decode that exceeds 10 seconds), and `FILE_CHECKSUM_MISMATCH` when the object is neither the declared upload nor a canonical object from the crash rule below. Each terminal code, including `FILE_INFECTED`, `FILE_EMPTY`, and `FILE_TOO_LARGE`, is a `REJECTED` finalize.

Sanitization re-encodes the decoded bitmap in the same detected type and strips EXIF, XMP, IPTC, ICC profiles, comments, and thumbnails. JPEG is `RGB`, quality 90, `optimize` false, progressive false, and subsampling `4:2:0`. PNG is `RGBA` when the source has alpha and `RGB` otherwise, `optimize` false, compress level 9. WebP uses the same alpha rule, lossless, quality 100, method 4, and exact pixel values. The implementation pins its image library so those settings are deterministic. The canonical bytes must also be from 1 through 5,242,880 bytes; a larger result is `FILE_TOO_LARGE`.

The canonical overwrite is one server PUT of those bytes to the same key. Immediately before that PUT, the worker checks again that its attempt token still matches and retention is still `QUARANTINED`, and it skips the PUT when the check fails. After the PUT returns, the worker checks the claim again and does not publish `CLEAN` when the check fails. That PUT sets exactly three user-metadata headers from the bytes just written: `x-amz-meta-gms-sha256` (64-character lowercase hex SHA-256), `x-amz-meta-gms-content-type` (the detected type), and `x-amz-meta-gms-byte-size` (the decimal byte length). The presigned POST cannot write those headers. The service reads the object back and requires the body size, body SHA-256, and those three headers to equal the bytes just written. It scans those canonical bytes with ClamAV before marking the asset clean. An infected canonical object is terminal `FILE_INFECTED`. Only a clean read-back and a clean second scan may finalize `CLEAN`. `upload_*` stays unchanged. `content_type` remains the detected type. A storage read-back failure, including a missing or differing server-metadata header, returns `503 FILE_STORE_FAILED`. A second-scan timeout or engine failure returns `503 FILE_SCAN_UNAVAILABLE`. Both finalize `scan_status = ERROR` and leave `retention_status = QUARANTINED`. The same confirmation may be retried.

If the worker crashes after that PUT and before finalize, the row stays `SCANNING` under the old attempt and the object already holds the canonical bytes plus the three server-metadata headers. A complete while that claim is younger than 120 seconds returns `409 FILE_SCAN_IN_PROGRESS`. After the claim is stale, a matching confirmation replaces the attempt, commits, and reads the object with no row lock held. The service keeps that object, with no second rewrite, only when the SHA-256 differs from `upload_sha256`, the object decodes within the same limits, it matches `upload_content_type`, it is already the deterministic canonical encoding of its decoded bitmap, the three server-metadata headers match those stored bytes, and ClamAV accepts them. Finalize then marks it `CLEAN` and `ACTIVE`. Any other object whose hash differs from `upload_sha256` is terminal `FILE_CHECKSUM_MISMATCH`. Headers the original browser POST could have written do not satisfy this rule.

Every `CLEAN`, `ERROR`, and `REJECTED` write is a new short transaction. It locks the member and then the asset. The write succeeds only when `scan_attempt_id` still equals this worker's token and `retention_status` is still `QUARANTINED`. Under `fileasset_scan_claim_ck` a live token means the row is still `SCANNING`. The same transaction clears `scan_attempt_id` and `scan_started_at`.

- `CLEAN` sets `content_type`, `byte_size`, and `sha256` to the sanitized object, `scan_status = CLEAN`, and `retention_status = ACTIVE`, and appends `FILE_UPLOAD_COMPLETED`.
- `ERROR` sets `scan_status = ERROR`, leaves `retention_status = QUARANTINED` and the checksum columns unchanged, and appends `FILE_UPLOAD_ERROR`.
- `REJECTED` sets `scan_status = REJECTED`, the stable `rejection_code`, and `retention_status = QUARANTINED`, leaves the checksum columns unchanged, and appends `FILE_UPLOAD_REJECTED`.

If the token or retention check fails, the worker discards its verdict. It does not write the row and it does not PUT again. An overwrite that already committed stays in place when this worker's token still matches and retention is still `QUARANTINED`, including a stale claim, and when a newer `SCANNING` claim now owns the row. Those objects remain for the crash rule above. When the locked row is `DELETE_PENDING` or `DELETED`, with no scan claim, and this worker's canonical PUT may already have committed, the worker deletes that object after releasing the row lock. A claim this worker still holds, including a stale claim, and a newer `SCANNING` claim keep the object. A second short transaction then locks the member and the asset. It writes only when that same asset is still `DELETE_PENDING` or `DELETED` and still has no scan claim. If the object delete succeeded, the transaction finishes `DELETED` and sets `deleted_at` when the row was `DELETE_PENDING`, and leaves an existing `DELETED` row and its `deleted_at` unchanged. If the object delete failed, the transaction sets `retention_status = DELETE_PENDING` and `deleted_at = null` so `expire_profile_photo_uploads` retries. That write does not change scan status, the claim columns, the checksum columns, or `Member.profile_photo_asset_id`. That removal is what stops a delete committed between the pre-write check and the PUT from leaving the object behind after a successful delete. The worker then responds from the locked row: `CLEAN` and `ACTIVE` with a matching confirmation returns `200` with the stored sanitized metadata; a differing confirmation returns `422 FILE_CONFIRMATION_MISMATCH`; `REJECTED` returns `422` with the stored `rejection_code`; `DELETE_PENDING` or `DELETED` with any other scan status returns `409 FILE_STATE_CONFLICT`; a newer `SCANNING` claim returns `409 FILE_SCAN_IN_PROGRESS` with `Retry-After` taken from that claim, or `Retry-After: 1` when that claim is already stale; `PENDING` returns `409 FILE_UPLOAD_INCOMPLETE`; `ERROR` returns `503 FILE_SCAN_UNAVAILABLE`.

A terminal rejection commits before the response. Only after that commit, and with no row lock held, the service deletes the object. Success or a missing object moves the row to `DELETED` with `deleted_at` set. A storage failure moves it to `DELETE_PENDING` with `deleted_at` still null. That retention update is a short transaction and changes a row only when it is still `REJECTED`, still `QUARANTINED`, and still carries the same `rejection_code`. The client still receives the rejection, not a storage error. Rejected and errored assets are not attachable or downloadable.

A successful complete, including the idempotent `CLEAN` replay, returns `200`. `data` is `file_id`, `purpose`, `owner_type`, `owner_id`, `original_name`, sanitized `content_type`, sanitized `byte_size`, sanitized `sha256`, `scan_status`, `retention_status`, and `attachable`. `attachable` is true only for `CLEAN` plus `ACTIVE`. The sanitized checksum and size will normally differ from the confirmation body. The response omits the object key, bucket, credentials, policy, attempt id, and uploader. The short transaction that returns this `200`, including the `CLEAN` replay, appends the audit event specified in `SECURITY.md`. If that audit insert fails, the transaction rolls back, a still-open claim stays `SCANNING`, and no clean metadata is returned. `Idempotency-Key` is not used. Repeating complete on `CLEAN` and `ACTIVE` with the same confirmation returns the stored sanitized metadata and does not scan or rewrite. Repeating it on `REJECTED` returns the same `rejection_code` and does not scan.

The supported attach sequence is create the member without a photo, complete this upload for that member, then `PATCH` the member with `profile_photo_asset_id` set to `file_id`. Complete does not attach the photo by itself.

#### Download URL

`GET /api/v1/files/{id}/download-url/` for a `PROFILE_PHOTO` requires `files.view`, `members.view`, and the active `members` entitlement. A cross-gym file id, an unknown id, a non-member owner, a missing owner member, and a same-gym owner outside object scope are `404`. An `EXPENSE_RECEIPT` download requires `files.view`, the finance entitlement, and `expenses.create` or `expenses.update`. It does not require `members.view`. Visibility follows the receipt rules above, and a cross-gym or unknown id is `404`. It accepts no query parameters. This route uses the same committed-transaction rule as complete. It checks integrity with `HEAD` and does not download the object body.

A first short transaction locks the owning member and then the asset. An authorized asset that is not both `CLEAN` and `ACTIVE` is `409 FILE_NOT_DOWNLOADABLE` and includes no URL. When the row is downloadable, the transaction commits and releases those locks, keeping the object key and the sanitized `content_type`, `byte_size`, and `sha256` for the check that follows.

With no database transaction open, the service sends `HEAD` to the internal endpoint with server credentials, under the same 5-second storage ceiling. The check passes only when `Content-Length` equals the stored `byte_size`, `x-amz-meta-gms-byte-size` equals that same decimal length, `x-amz-meta-gms-sha256` equals the stored `sha256`, and `x-amz-meta-gms-content-type` equals the stored `content_type`. A missing object, a missing header, or any mismatch returns `409 FILE_INTEGRITY_FAILED`, issues no URL, and does not change the row. A successful HEAD whose `Content-Length` is missing, non-integer, negative, or otherwise invalid is that same integrity failure. A network timeout or provider error remains `503 FILE_STORE_FAILED`. `Content-Type`, `ETag`, checksum headers, and every other user-metadata header are ignored. The original browser POST is not an authority for those values; the presigned POST policy grants no `x-amz-meta-*` field, and only the server canonical PUT writes the three `x-amz-meta-gms-*` headers.

A second short transaction locks the member and asset again. It requires the row still `CLEAN` and `ACTIVE` with the same sanitized `content_type`, `byte_size`, and `sha256`, appends `FILE_DOWNLOAD_URL_ISSUED`, and commits. If that re-check fails, the response is `409 FILE_NOT_DOWNLOADABLE` and no URL is issued. If the audit insert fails, the transaction rolls back and no URL is returned. The service then signs the GET locally. `expires_at` is exactly 60 seconds after that signature is created.

Success returns `200` with `url`, `method` = `GET`, `expires_at`, `filename`, `content_type`, `byte_size`, and `sha256`. The URL is a presigned GET on the public object origin. Its signed response overrides are `response-content-type` equal to the sanitized type, `response-content-disposition` equal to `inline; filename="profile-photo.jpg"` (`.png` or `.webp` for those types), and `response-cache-control` equal to `private, no-store`. The filename is that fixed name, never `original_name`. The disposition is `inline` only because this route's sole downloadable object is a sanitized profile image rendered by the staff UI in an `img` element. It is not a general document mode, and no other purpose may reuse this override. Each authorized call may issue a new URL. An issued URL remains valid until its own expiry or until the object is removed. The signature expires 60 seconds after it is created.

#### Delete

`DELETE /api/v1/files/{id}/` for a `PROFILE_PHOTO` requires `files.delete`, `members.update`, and the active `members` entitlement. The same profile-photo `404` rules apply. An `EXPENSE_RECEIPT` delete does not require `files.delete` or `members.update`. It requires the finance entitlement, `expenses.create` or `expenses.update`, and the receipt visibility rules. A receipt attached to an expense is `409 FILE_STATE_CONFLICT` for a caller who can see that expense. A caller who cannot see it, and a cross-gym or unknown id, receive `404`. The body is empty. This is a metadata retention transition plus object removal. The database row is not deleted, and the runtime role still has no SQL `DELETE` grant.

The first transaction locks the owning member, then the asset. It sets `Member.profile_photo_asset_id` to null when it equals this asset, sets `retention_status = DELETE_PENDING` with `deleted_at` still null, and appends `FILE_DELETE_REQUESTED`. When `scan_status` is `SCANNING`, that same update sets `scan_status = ERROR` and clears `scan_attempt_id` and `scan_started_at`. `fileasset_scan_claim_ck` requires a cleared token to leave `SCANNING`, and `ERROR` records that this attempt did not finish. `PENDING`, `ERROR`, `REJECTED`, and `CLEAN` keep their scan status; their claim columns are already null. The transaction commits before the storage call. The service then deletes the object with server credentials and no row lock held. If the object is gone, a second transaction sets `retention_status = DELETED` and `deleted_at` to the current instant only while the row is still `DELETE_PENDING`, and the response is `200`. If storage deletion fails, the row stays `DELETE_PENDING` and the response is `503 OBJECT_DELETE_PENDING`. Repeating `DELETE` retries object removal. Repeating it after `DELETED` returns `200` with the same `deleted_at` and does not clear a different photo the member has since attached.

A late complete whose token was cleared by this delete cannot finalize. Its `CLEAN`, `ERROR`, or `REJECTED` write fails the token and `QUARANTINED` checks, so it cannot mark the asset `ACTIVE` or put the row back into `SCANNING`. If that worker already PUT the canonical object, it deletes that object. Success finishes `DELETED`. A storage failure sets `DELETE_PENDING` with `deleted_at` null so the retention command retries. Neither outcome changes scan status or detaches a newer photo. A current or newer scan claim is not deleted.

Delete `data` is `file_id`, `purpose`, `owner_type`, `owner_id`, `scan_status`, `retention_status`, `rejection_code`, and `deleted_at`. `PENDING`, `SCANNING`, `ERROR`, `REJECTED`, and `CLEAN` assets may all be deleted. Delete does not require a clean scan. Deleting a `SCANNING` asset returns `scan_status = ERROR` with the `DELETE_PENDING` or `DELETED` retention that the storage step reached.

A one-shot management command, `expire_profile_photo_uploads`, is part of this contract and is implemented. It is not an HTTP route and not a Celery worker. It uses a short transaction per asset and calls MinIO only after that transaction commits. For each gym it marks assets still `QUARANTINED` 24 hours after `created_at` as `REJECTED` with `rejection_code = FILE_UPLOAD_EXPIRED` when the scan status is `PENDING` or `ERROR`, or when it is `SCANNING` and the claim is already stale. A `SCANNING` update also clears `scan_attempt_id` and `scan_started_at`, and it matches the attempt id read under the lock so a newer claim is left in place. A `SCANNING` claim younger than 120 seconds is left unchanged. The command then applies the same object-removal transition. It also retries object removal for `REJECTED` rows still `QUARANTINED` and for every `DELETE_PENDING` row, including a row returned to `DELETE_PENDING` after a late canonical PUT's cleanup delete failed.

The same command retires an unattached clean profile photo. A `PROFILE_PHOTO` row is eligible only when `scan_status` is `CLEAN`, `retention_status` is `ACTIVE`, `created_at` is at least 24 hours before the database clock, and no same-gym `Member.profile_photo_asset_id` references that asset. This covers a sanitized photo that was never attached, including one abandoned when the browser left the member after complete and before attach, and a replaced photo whose caller could not call `DELETE`. The candidate query is bounded by the same `--limit` (1 through 500, default 100) and ordered by `created_at`, then `id`. For each selected row the command locks the owning member and then the asset. Under those locks it rechecks the purpose, the `CLEAN` and `ACTIVE` state, the 24-hour age against `CURRENT_TIMESTAMP`, and that no same-gym member references the asset. Only when every check still holds does it set `retention_status = DELETE_PENDING` with `deleted_at` null and append `FILE_DELETE_REQUESTED`. That claim does not change `scan_status`, the checksum columns, or `Member.profile_photo_asset_id`. The transaction commits before the object delete. If the object is removed, a later short transaction sets `DELETED` and `deleted_at` only while the row is still `DELETE_PENDING`. If the object delete fails, the row stays `DELETE_PENDING` with `deleted_at` null and a later run retries it through the existing `DELETE_PENDING` path. An asset that is attached, younger than 24 hours, or no longer `CLEAN` and `ACTIVE` when the lock is taken is left unchanged. A photo that is currently attached is never deleted by this rule. Scheduling is a host timer.

Intent creation is limited to 10 requests per membership per 300 seconds, complete to 20 per 300 seconds, download-url to 60 per 60 seconds, and delete to 10 per 300 seconds. Excess requests return `429 RATE_LIMITED` and, for intent, create no asset.

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

Staff `GET /api/v1/notifications/` and `POST /api/v1/notifications/{id}/read/` stay unpublished. Members read and mark their own inbox on the member routes in section 7. There is no staff route that sends a push, chooses a provider token, or names a recipient list. `notifications.view` and `notifications.manage` stay in the catalog and still have no staff HTTP route.

Announcement reads require `announcements.view`. Create, update, publish, and cancel require `announcements.manage`. Every announcement route also requires the active `members` entitlement. A member token, a platform token, and a missing staff gym are `401 AUTHENTICATION_FAILED`. A staff caller without the permission is `403 PERMISSION_DENIED`. These announcement routes are implemented and published.

The audience enum is exactly `ALL_ACTIVE_MEMBERS`, `PT_CLIENTS`, `EXPIRING_WITHIN_7_DAYS`, and `EXPIRING_WITHIN_30_DAYS`. The type enum is exactly `EVENT`, `PROMOTION`, and `GENERAL`. Create fields are `type`, `title`, `body`, `audience`, and optional `starts_at` and `ends_at`. `title` is trimmed, 1–120 characters. `body` is trimmed, 1–4000 characters. Instants are RFC 3339 with an offset. When both are present, `ends_at` is after `starts_at`. `ends_at` must be after the server clock. `starts_at` more than 60 seconds in the past is `400`. Unknown fields, `gym_id`, `member_ids`, `recipient_ids`, `media_file_id`, `status`, actor ids, and provider fields are `400 INVALID_REQUEST`. There is no announcement media purpose. Create returns `201` with status `DRAFT`.

`PATCH` accepts any nonempty subset of those create fields and only while the row is `DRAFT`. Any other status is `409 ANNOUNCEMENT_STATE_CONFLICT` and writes nothing. `POST /api/v1/announcements/{id}/publish/` accepts an empty body and only from `DRAFT`. When `starts_at` is null or not after now, the same transaction sets `PUBLISHED`, sets `published_at`, and inserts the deduplicated inbox and push rows defined in `DATABASE.md`. When `starts_at` is in the future, the same transaction sets `SCHEDULED` and inserts no recipient rows. A second publish is `409 ANNOUNCEMENT_STATE_CONFLICT`. `POST /api/v1/announcements/{id}/cancel/` accepts an empty body. From `DRAFT` or `SCHEDULED` it sets `CANCELLED` and inserts nothing. From `PUBLISHED` it sets `CANCELLED`, cancels push rows that are still `PENDING`, and leaves already `SENT` inbox rows readable. Repeating cancel returns the stored row. `EXPIRED` is `409 ANNOUNCEMENT_STATE_CONFLICT`. `Idempotency-Key` is rejected on create, patch, publish, and cancel. List filters are `status`, `type`, `audience`, `limit` (default 25, maximum 100), and opaque `after`, ordered by `created_at DESC, id DESC`. The response fields are `id`, `type`, `title`, `body`, `audience`, `starts_at`, `ends_at`, `status`, `published_at`, `cancelled_at`, `expired_at`, `created_at`, and `updated_at`. Recipient counts, actor ids, gym id, and provider ids are omitted. A cross-gym id is `404`.

Audience resolution uses the token gym only. `ALL_ACTIVE_MEMBERS` is every `Member.status=ACTIVE`. `PT_CLIENTS` is every active member whose server-derived PT badge is true on the gym-local today. `EXPIRING_WITHIN_7_DAYS` and `EXPIRING_WITHIN_30_DAYS` are active members whose current membership, using the summary's current-membership selector, has `expiry_date` from the gym-local today through today plus 7 or 30 days. The 7-day audience is a subset of the 30-day audience. These are not the mutually exclusive staff expiry buckets. Suspended, cancelled, expired, and archived members are not recipients. The resolved set is snapshotted into notification rows. Later assignment or membership changes do not add recipients to an announcement that was already published.

`publish_scheduled_announcements` promotes a due `SCHEDULED` row to `PUBLISHED` under the same recipient rules. `expire_announcements` sets `EXPIRED` when `ends_at` is before now and the status is `PUBLISHED`. Both commands are implemented. Neither uses Redis or Celery.

## 7. Member mobile API — Phase 7

Member routes enforce gym scope and self scope. [ADR 0005](./adr/0005-member-session-foundation.md) still allows login only for an active user with exactly one active linked member at an active gym with a trial or active subscription. Public linking, multi-gym member selection, and member MFA stay deferred. An MFA-enabled user still fails closed. The request still cannot send `member_id` or `gym_id`.

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
POST /api/v1/member/files/upload-intents/
POST /api/v1/member/files/{id}/complete/
GET  /api/v1/member/files/{id}/download-url/
DELETE /api/v1/member/files/{id}/
```

Published member routes are `POST /api/v1/member-auth/login/`, `POST /api/v1/member-auth/refresh/`, `POST /api/v1/member-auth/logout/`, `POST /api/v1/member/check-ins/scan/`, the member-self profile, dashboard, membership, payment, check-in, package, schedule, class-booking, and profile-photo routes, `GET /api/v1/member/notifications/`, `POST /api/v1/member/notifications/{id}/read/`, and `POST`, `PATCH`, and `DELETE` for `/api/v1/member/devices/`. Staff `/api/v1/notifications/` stays unpublished. A staff token or a platform token on any `/api/v1/member/` route is `401 AUTHENTICATION_FAILED`, not a fallback to staff permissions. These routes are not behind the browser BFF secret.

`POST /api/v1/member/check-ins/scan/` is the published exception. It accepts a short-lived onsite gym challenge and derives member and gym exclusively from the authenticated member session. The body is `payload` only. It does not accept personal information, a member or gym selector, a method, a timestamp, a device, source metadata, an actor, or a total. `Idempotency-Key` is rejected. The route is rate-limited as `member_check_in_scan` by the authenticated user and client address, not by the payload. This member route does not accept a trainer staff principal. Assignment-scoped trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. It is not a staff-token alias of the member route.

The scan locks the challenge, then the token member, then the eligible membership. It does not lock gym settings. A malformed, unknown, modified, expired, revoked, consumed, or other-gym payload is `400 CHECK_IN_CODE_INVALID` with one message, and it does not change the challenge. An ineligible membership is `409 CHECK_IN_INELIGIBLE`. An open visit is `409 CHECK_IN_OPEN`. A repeat by the same member, including after checkout when the duplicate window is zero, is `409 CHECK_IN_DUPLICATE`. Those failures consume neither a use nor a check-in. `max_uses` can admit different members. The last successful use sets `CONSUMED` and `consumed_at` in the same update. The inserted row is `method=QR`, `backdated=false`, `check_in_at` equal to the server clock, with both membership actor columns null. Payment state does not change. The audit action is `MEMBER_CHECK_IN_RECORDED`, with the linked user as `actor_user` and no actor membership, payload, or hash. The response uses the staff check-in shape and omits actors, device, source metadata, and the secret.

Member auth request/response fields mirror the staff rotation contract but use member-specific serializers, audience `gym-platform-member`, `token_type=member_access`, sessions, and rate limits. Login allows 30 attempts per minute per address and 10 per minute per normalized email plus address. Refresh and logout each allow 30 per minute per address and 30 per minute per peppered refresh-token hash plus address. Those buckets are not the staff `anon` bucket. The three published routes return tokens in the JSON body. They do not accept the staff gym-selection ticket. Every later resource in this section is selected from token `gym_id` plus token `member_id`. A body or query `gym_id` or `member_id` is `400 INVALID_REQUEST`.

### Profile

`GET /api/v1/member/me/` and `PATCH /api/v1/member/me/` require the active `members` entitlement. They accept no query parameters. The response fields are `id`, `member_number`, `first_name`, `last_name`, `phone`, `email`, `gender`, `date_of_birth`, `address`, `join_date`, `emergency_contact_name`, `emergency_contact_phone`, `profile_photo_asset_id`, `is_pt_client`, `status`, `archived_at`, `created_at`, and `updated_at`. `address` uses `address_line1`, `address_line2`, `city`, `region`, `postal_code`, and `country_code`. The response omits `notes`, `normalized_full_name`, `linked_user_id`, `authz_version`, gym id, and actor ids. `email` is the gym contact address, not the login `User.email`.

`PATCH` accepts a nonempty subset of exactly this allowlist: `phone`, `email`, `gender`, `date_of_birth`, `address`, `emergency_contact_name`, and `emergency_contact_phone`. `phone` and `email` may be null. `gender` is `FEMALE`, `MALE`, `OTHER`, `UNSPECIFIED`, or null. `date_of_birth` may be null and must not be after the stored `join_date`. A present `address` replaces the whole object and may contain only the six keys above. Null `address` stores an empty object. Phone and email use the existing member normalization. An empty body, an unknown field, and any field outside the allowlist are `400 INVALID_REQUEST` and write nothing. Rejected fields include `id`, `gym_id`, `member_id`, `member_number`, `first_name`, `last_name`, `normalized_full_name`, `join_date`, `notes`, `profile_photo_asset_id`, `linked_user_id`, `is_pt_client`, `authz_version`, `status`, `archived_at`, timestamps, and actor ids. The command does not change `User.email`, `linked_user_id`, `authz_version`, or refresh sessions. The audit action is `MEMBER_PROFILE_UPDATED` and stores only changed allowlisted fields. Reads allow 60 per minute per member user. Writes allow 20 per minute per member user.

### Dashboard, membership, payments, and check-ins

`GET /api/v1/member/dashboard/` accepts no query parameters. It requires the active `members` entitlement. `data` uses the staff summary fields `member`, `current_membership`, `days_remaining`, `is_pt_client`, `membership_history`, `membership_history_has_more`, `payment_summary`, and `check_in_summary`, with the same definitions. `member` uses the self profile shape. Membership objects omit `cancelled_by_membership_id` and `created_by_membership_id`. `payment_summary` stays the aggregate, not a payment list. `finance` entitlement off still returns the summary, with `payment_summary.available=false` and the amount fields null, because this route must not become a finance read by itself.

`GET /api/v1/member/membership/` accepts no query parameters and requires the active `members` and `memberships` entitlements. `data` is the current membership, using the summary selector, or null. The object uses the staff membership response minus the two actor ids above.

`GET /api/v1/member/payments/` requires the active `members` and `finance` entitlements. It does not require `payments.view`. The query is only `limit` (default 25, maximum 100) and opaque `after`, bound to the token gym, the token member, `received_at DESC, id DESC`, and that limit. Each item uses the staff payment-history fields: `id`, nullable `membership_id`, nullable `original_payment_id`, `entry_type`, `purpose`, `description`, decimal-string `amount`, `currency`, `method`, `business_date`, `received_at`, `status`, and nullable `receipt_number`. It omits reference numbers, actor ids, and workflow timestamps. The amounts are server values. The client does not recalculate them.

`GET /api/v1/member/check-ins/` requires the active `members` and `memberships` entitlements. The query is only `limit` and `after`, ordered by `check_in_at DESC, id DESC`, bound the same way. Each item uses the staff check-in response: `id`, `member` (`id`, `member_number`, `first_name`, `last_name`), `membership_id`, `check_in_at`, `check_out_at`, `method`, derived `status`, `note`, and `eligibility`. Gym, actors, `device_id`, and `source_metadata` are omitted.

### Packages, schedule, and class booking

`GET /api/v1/member/service-packages/` requires the active `pt` entitlement. It does not require a staff PT permission and it does not accept `member_id`. Filters are `status`, `package_type` (`PT` or `CLASS`), `limit`, and `after`, ordered by `created_at DESC, id DESC`. Each item is `id`, `package_type`, `package_code`, `package_name`, `status`, `granted_sessions`, `remaining_sessions`, `valid_from`, `valid_until`, and `complimentary`. `remaining_sessions` is the server ledger sum. The response omits price, sale id, reason, and actors. There is no member ledger-adjust route.

`GET /api/v1/member/schedule/` requires the active `pt` entitlement. The query is required `date_from` and `date_to`, inclusive gym-local dates, ordered, from 1 through 42 days. Any other parameter is `400`. More than 400 matching occurrences is `422 SCHEDULE_TOO_LARGE`. `data.bookings` lists this member's PT and class bookings that overlap the range. Each booking is `id`, `kind` (`PT` or `CLASS`), `status`, `starts_at`, `ends_at`, `trainer_display_name`, nullable `class_name`, nullable `location`, nullable `member_service_package_id`, and `unmetered`. `unmetered_reason` is omitted. `data.occurrences` lists same-gym `SCHEDULED` class occurrences in the range. Each occurrence is `id`, `class_name`, `trainer_display_name`, nullable `location`, `starts_at`, `ends_at`, `capacity`, `booked_count`, and nullable `member_booking_id` for this member's non-cancelled booking. Other members' names are omitted. This response is one object, not a cursor page.

`POST /api/v1/member/class-bookings/` requires the active `pt` entitlement, `Idempotency-Key`, and a body of exactly `occurrence_id` and `member_service_package_id`. `unmetered`, `unmetered_reason`, `member_id`, trainer ids, and status fields are `400`. The package must be same-gym, `CLASS`, owned by the token member, `ACTIVE`, unexpired on the occurrence's gym-local date, and have remaining sessions of at least 1. The occurrence must be `SCHEDULED` and its `starts_at` must be after now. Capacity, member overlap, and the class booking locks match the staff class-booking command. The member is not a manager, so there is no late or unmetered exception. Overflow is `409 CLASS_FULL`. A conflicting booking is `409 SCHEDULE_CONFLICT`. Success is `201` with the booking item shape above. The idempotency actor is `actor_member_id`, specified in `DATABASE.md`. There is no member attend, no-show, or cancel route.

### Inbox and devices

`GET /api/v1/member/notifications/` requires the active `members` entitlement. The query is only `limit` and `after`, ordered by `created_at DESC, id DESC`, and returns only `channel=IN_APP` rows for the token member. Each item is `id`, `type` (`MEMBERSHIP_EXPIRY` or `ANNOUNCEMENT`), `title`, `body`, nullable `announcement_type` (`EVENT`, `PROMOTION`, or `GENERAL`), nullable `read_at`, and `created_at`. Email rows, push rows, payloads, provider ids, and tokens are not returned. A row for another member is `404`.

`POST /api/v1/member/notifications/{id}/read/` accepts an empty body. It sets `read_at` once when the row is that member's `IN_APP` row and `read_at` is null. Repeating it returns the same row and does not move `read_at`. An email row, a push row, an unknown id, and another member's id are `404`. The audit action is `MEMBER_NOTIFICATION_READ` and does not copy the body.

`POST /api/v1/member/devices/` requires the active `members` entitlement. The body is `platform` (`ANDROID` or `IOS`), `installation_id` (UUID), `push_consent` (boolean), optional `push_token`, `app_version` (1–32), `os_version` (1–32), and optional `device_name` (1–64). `push_consent=true` requires `push_token` of 1–4096 characters. `push_consent=false` requires `push_token` to be omitted or null. Unknown fields, `gym_id`, and `member_id` are `400`. The first registration for that member and `installation_id` returns `201`. The same pair returns `200` and updates consent, token, versions, name, and `last_seen_at`. `platform` cannot change; a different platform for the same installation is `409 DEVICE_CONFLICT`. The response is `id`, `platform`, `installation_id`, `push_consent`, `push_token_present`, `app_version`, `os_version`, `device_name`, `last_seen_at`, and `revoked_at`. It never returns the token, ciphertext, or fingerprint.

`PATCH /api/v1/member/devices/{id}/` accepts any nonempty subset of `push_consent`, `push_token`, `app_version`, `os_version`, and `device_name`, with the same consent and token rule. `platform` and `installation_id` are rejected. Another member's device is `404`. Setting `push_consent=false` clears the stored token and leaves the row unrevoked.

`DELETE /api/v1/member/devices/{id}/` revokes that device with reason `CLIENT_DELETED`, clears the ciphertext, and returns `204`. Repeating delete on an already revoked row returns `204` and does not change `revoked_at`. Logout of the refresh session does not revoke other installations. The logging-out app deletes its own device. A member may have at most five active devices. A sixth active registration revokes the least recently seen active device with reason `SUPERSEDED`.

Push delivery, deduplication, the test transport, and the production credential gate are [ADR 0012](./adr/0012-fcm-push-delivery.md). The server never accepts a caller-selected recipient, gym, or raw provider message id.

### Member profile photo

Staff file routes stay staff-token only and still return `403 PERMISSION_DENIED` for a member token. The member routes below are published. They reuse the profile-photo pipeline: `image/jpeg`, `image/png`, or `image/webp`, 1 through 5,242,880 bytes, the same presigned POST, ClamAV, canonical re-encode, and 60-second GET. They require the active `members` entitlement and no staff file permission.

`POST /api/v1/member/files/upload-intents/` accepts exactly `original_name`, `content_type`, `byte_size`, and `sha256`. `purpose`, `owner_type`, `owner_id`, and `gym_id` are `400`. The server sets `purpose=PROFILE_PHOTO`, `owner_type=MEMBER`, `owner_id` to the token member, `uploaded_by_membership_id` null, and `uploaded_by_member_id` to the token member. The `201` upload object matches the staff intent response. Complete accepts exactly `sha256` and `byte_size`. When finalize reaches `CLEAN` and `ACTIVE` for that owner, the same transaction sets `Member.profile_photo_asset_id` to this file. The previous asset is not deleted by that write. It follows the existing unattached-photo retirement. Download and delete require the asset owner to be the token member. Any other owner is `404`. Delete nulls the pointer when it matches, using the staff retention transitions. `Idempotency-Key` is not used. Write routes allow 10 attempts per minute per member user. The download route allows 30. The audit actions stay the staff file actions plus the profile update when the pointer changes. The response never includes the object key or a credential.

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

`Idempotency-Key` is required for every financial command, including payment submit/approve/reject/cancel/void/refund, expense submit/approve/reject/cancel/void, salary submit/approve/reject/cancel/void, cash open/adjust/reconcile, PT sale submit/approve/reject/cancel/void, commission payment, member-service-package grant, ledger adjust, class-schedule create and patch, PT and class booking create/attend/no-show/cancel, and every receipt-affecting command. It is also required for gym provisioning and for `POST /api/v1/member/class-bookings/`. Trainer scan rejects the header. Member check-in scan, member profile patch, member file routes, device registration, and announcement publish reject the header. The member class-booking actor is `actor_member_id`. That column is migrated. Staff rows keep a null member actor, so an existing staff request hash does not change.

The server keys records by gym, actor, method, route, and key. Repeating the same request returns the original result while the stored response is unexpired. Reusing a key with a different request hash returns `409 IDEMPOTENCY_KEY_REUSED`. An expired key returns `409 IDEMPOTENCY_KEY_EXPIRED` and is not decrypted. A deterministic client or domain failure is replayed from the API error envelope. A deadlock or other unexpected failure leaves the key retryable. `mutate()` is database-only. Idempotent command views opt out of `ATOMIC_REQUESTS` with `non_atomic_requests`; the runner owns the business transaction.

Profile-photo intent, complete, download-url, and delete do not use `Idempotency-Key`. Intent always creates a new asset. Complete and delete use the state transitions in the Signed files section.

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
- **Phase 6 — reporting:** Increment 1 publishes the owner dashboard at `GET /api/v1/dashboard/` and the tenant dashboard page, including attendance, expiry, pending-payment, ranked-revenue, and product metrics on that route. Increment 2 publishes `GET /api/v1/reports/daily/` and `GET /api/v1/reports/monthly/` and the tenant `/reports` page. Increment 3 publishes `GET /api/v1/reports/cash-book/` and `GET /api/v1/reports/profit-loss/` on that page. Increment 4 publishes `GET /api/v1/reports/membership-expiry/` and `GET /api/v1/reports/check-ins/` on that page and is gate-clean as of 2026-10-20. Increment 5 publishes `GET /api/v1/reports/pt-sessions/` and `GET /api/v1/reports/classes/` on that page and is gate-clean as of 2026-10-20. Increment 6 publishes `GET /api/v1/reports/product-sales/`, `GET /api/v1/reports/stock-movements/`, and `GET /api/v1/reports/inventory/` on that page and is gate-clean as of 2026-10-20. Increment 7 publishes `GET /api/v1/trainer/dashboard/` and `GET /api/v1/trainer/reports/sessions/`, and the tenant `/pt/dashboard` page, and is independently verified and gate-clean as of 2026-10-20. Increment 8 publishes `GET /api/v1/reports/{kind}/export/` as an immediate CSV download for the eleven gym reports and is independently verified and gate-clean as of 2026-10-20. Export jobs stay unpublished. Trainer dashboard and session reports are not exportable. Phase 6 implementation is complete and its local CI-equivalent gate is closed. Production deployment is not complete, and the ADR 0010 live Resend, AWS, and ClamAV proofs remain open.
- **Phase 7 — mobile:** member authentication, strict tenant+self API, onsite QR scanner, PT/class schedule and remaining sessions, expiry reminders, event/promotion announcements, push notifications, and mobile file/profile boundaries. [ADR 0011](./adr/0011-bare-react-native-member-app.md) and [ADR 0012](./adr/0012-fcm-push-delivery.md) approve bare React Native CLI and FCM. Section 7 specifies the member routes. Login, refresh, logout, member check-in scan, and the member-self profile, dashboard, membership, payment, check-in, package, schedule, class-booking, and profile-photo routes are published. Staff announcement routes and member notification and device routes are published. Staff `/api/v1/notifications/` stays unpublished. Public linking, multi-gym member selection, and member MFA stay deferred. The bare React Native Android/iOS app is implemented. Live FCM/APNs proof, release signing, and app-store submission remain open release gates.
- **Phase 8 — hardening and deployment:** compatibility/schema gates, attack and isolation tests, rate/load limits, observability, key rotation, file security, deployment checks, and production operational controls. Phase 8 adds no undocumented business API.

Phase 7 code and local test gates are complete as of 2026-10-21. The measured
backend and web evidence is in the repository README. Live Firebase and APNs
credentials, release signing, store submission, and a production device proof
remain open. Production deployment is not complete, and Phase 8 remains open.
Historical delivery prose below that calls the React Native app deferred is
superseded by this Phase 7 closure; only public linking, multi-member
selection, and member MFA remain deferred.

A delivery annotation never means implementation exists. Each phase remains unimplemented until its approved development work, migrations, tests, security review, and schema publication are complete. Phase 1A and Phase 1B have passed that bar. Published OpenAPI includes those routes, including role create, update, archive, and permission replacement, plus MembershipPlan and Member list, create, detail, update, and archive, Membership list, create, detail, note update, cancel, suspend, resume, and expiry buckets, the member summary, and nested member payment history. Membership-expiry scheduling and delivery commands are implemented outside HTTP. The Phase 2 profile-photo upload, complete, download-url, and delete routes are implemented and published. Phase 2 implementation is complete. Manual check-in routes, nested check-in history, staff check-in challenge issuance, display-device management, `POST /api/v1/device/check-in-challenges/`, and `POST /api/v1/member/check-ins/scan/` are published. `POST /api/v1/check-ins/scan/` is not published. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. The approved QR path is authenticated member-self scan. Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. Scoped DISPLAY, NFC, and BIOMETRIC credentials and dedicated ingestion abstractions are implemented. No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Vendor-specific integration remains optional. Public member identity linking, multi-member selection, the React Native app, and public linking remain deferred and are not prerequisites for the already-linked member QR path. Staff notification HTTP routes remain unpublished. Increment 4 publishes expense-category and expense approval routes; expense void stays unpublished. Increment 5 publishes salary-period list/create/detail/update, line replacement, and submit/approve/reject/cancel; salary void stays unpublished. Increment 6 publishes product-category list/create, product list/create/detail/update, low-stock, stock-movement list, and manual adjustment; category update stays unpublished. Increment 7 publishes retail-sale list/create/detail/update and submit/approve/reject/cancel; retail void, returns, and void/refund stay unpublished. The Phase 4 gate is not closed. Increment 7 gate evidence is in the repository README. The query-plan baseline is not a production load test. [ADR 0010](./adr/0010-production-provider-readiness.md) selects Resend and AWS S3 in `ap-southeast-1`. Password recovery and the Resend client are implemented. The production bucket is not live. Compose still pins `docker.io/pgsty/silo`, a test-only MinIO-compatible fork, not official MinIO and not the production store. Live Resend, AWS, and ClamAV proofs remain outside the repository. Legal retention stays draft-only.

Accepted refinements: [ADR 0002](./adr/0002-nextjs-bff-browser-authentication.md) defines browser authentication, and [ADR 0003](./adr/0003-reception-cash-session-authority.md) defines Reception's conditional cash-session scope. MFA is TOTP and is mandatory for platform administrators and Owners, with recent step-up, encrypted secrets, and replay protection. Broader role-based MFA may be decided later. [ADR 0010](./adr/0010-production-provider-readiness.md) selects the Resend HTTP API and the password-recovery contract. Tests and end-to-end runs use non-delivery backends. Password recovery and the Resend client are implemented. The live domain, key, and idempotency proof remain open. Jurisdiction remains a production-gate decision. Display-device credentials are accepted in [ADR 0007](./adr/0007-display-device-credentials.md). NFC and biometric ingestion is accepted in [ADR 0008](./adr/0008-device-ingestion.md). Vendor-specific reader software remains optional. Bare React Native CLI and FCM are approved in [ADR 0011](./adr/0011-bare-react-native-member-app.md) and [ADR 0012](./adr/0012-fcm-push-delivery.md). The Android/iOS app, staff announcement routes, and member notification and device routes are implemented. Staff `/api/v1/notifications/` stays unpublished. Public linking, multi-gym member selection, and member MFA remain deferred. Live FCM/APNs proof, release signing, and app-store submission remain open.
