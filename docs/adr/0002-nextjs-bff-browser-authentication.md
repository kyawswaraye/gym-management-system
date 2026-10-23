# ADR 0002: Next.js BFF Browser Authentication

- **Status:** Accepted
- **Decision date:** 2026-09-02
- **Applies from:** Phase 1A foundation

## Context

The staff web application needs short-lived Django-issued JWT access credentials and rotating refresh credentials without exposing either credential to browser JavaScript. Direct token delivery would increase the impact of XSS and invite inconsistent client refresh behavior. Cookie authentication also requires a complete CSRF and request-provenance policy.

Django already owns identity, selected-gym validation, token issuance, refresh-session state, authorization-version checks, rotation, reuse detection, and revocation. The web boundary must not become a second authentication authority.

## Decision

Next.js is the browser backend-for-frontend (BFF). Browser JavaScript calls same-origin BFF routes and never receives an access JWT or refresh token in JSON, HTML, URLs, logs, or client-visible state. Tokens are never stored in `localStorage` or `sessionStorage`.

Django remains authoritative for credentials, MFA decisions, selected gym, JWT/session issuance and validation, refresh rotation, token-family reuse revocation, logout/logout-all, and authorization. The BFF forwards commands, holds credentials at the server boundary, and strips token values from browser responses.

The BFF uses `HttpOnly`, host-scoped cookies with no `Domain` attribute and `SameSite=Lax`. Cookies are `Secure` outside isolated `http://localhost` development. Cookie paths and clearing attributes must match their set attributes.

After password and required MFA checks succeed, Django evaluates the authoritative bootstrap list of active `GymMembership` records for that user:

- exactly one active membership proceeds directly to the normal selected-gym access and rotating-refresh session;
- more than one active membership causes Django to issue a high-entropy, opaque `GymSelectionTicket` with an exact lifetime of **5 minutes**;
- no active membership fails closed without issuing a ticket or session.

The ticket is a pre-session login bridge, not an access token. Django stores only its cryptographic hash together with the user, expiry, consumed/revoked state, bounded attempt count, and safe audit metadata. It grants no gym authority, creates no tenant context, selects no membership, and adds no JWT claim. The trusted BFF receives the plaintext ticket once and stores it only in a dedicated host-scoped `HttpOnly`, `SameSite=Lax` cookie that is `Secure` outside isolated localhost. Browser JavaScript never receives the plaintext ticket. The browser receives only safe display fields for the eligible memberships.

The browser posts the chosen `gym_membership_id` to the BFF. The BFF supplies both that identifier and the ticket to Django. In one transaction Django locks the ticket, verifies that it is live, unconsumed, unrevoked, bound to the authenticated user, and within its bounded attempt policy, then verifies the chosen active membership against the ticket's authoritative bootstrap list. Success atomically consumes the ticket and issues the normal selected-gym access and rotating-refresh session. A terminal failure revokes the ticket; expiry, prior consumption, replay, user mismatch, membership substitution, or exhausted attempts never creates a session. The BFF clears the ticket cookie on success and every terminal outcome.

Stable configurable JWT identifiers are:

- issuer: `gym-platform-api`;
- staff/web audience: `gym-platform`;
- member audience: `gym-platform-member`.

These identifiers are not secrets. Signing and refresh material remains secret. This ADR adds no JWT claims; the claim contracts remain in `API.md`.

The staff access-token target lifetime is 10 minutes. On an application request, the BFF reads the access cookie server-side and forwards the JWT to Django. If and only if Django returns the one recognized access-expiry response, the BFF may refresh once and retry the original request once. It does not refresh on other authentication or authorization failures and never loops.

Every BFF route that creates, rotates, selects, revokes, or otherwise mutates cookie-authenticated or gym-selection-ticket state requires the approved CSRF token/header pair and strict allowlisted `Origin` validation. Strict `Referer` validation is required only when `Origin` is unavailable. Missing, malformed, or mismatched provenance fails closed; CORS is not a substitute.

Refresh rotates the credential and hashed server session atomically. Reuse revokes the entire family. Logout and logout-all invoke Django's authoritative revocation before completion, then clear all matching authentication cookies. Cookies are also cleared when Django reports the session already expired or revoked, but cookie clearing alone is not revocation.

## Security consequences

XSS cannot directly read authentication credentials or the gym-selection ticket from browser APIs, although XSS remains dangerous and still requires CSP and output controls. CSRF protection applies to every cookie mutation, including login, refresh, selected-gym changes, and logout. Host scoping limits cookie reach, and production-like deployments cannot disable `Secure`.

The BFF must redact credentials and tickets and prevent authentication responses from being cached across users or gyms. Django `/api/v1/auth/` accepts only requests that present the configured BFF shared secret. Production does not publish a Django host port; Gunicorn binds inside the container, and Caddy rejects `/api/v1/auth/`. Django rate-limits ticket issue and selection by safe combinations of account/user, source, and ticket hash prefix or internal ticket ID; audit and diagnostic records never contain plaintext. Django continues to enforce exact issuer/audience, claims, session, gym membership, authorization version, Django scope, and forced RLS.

### Production network boundary

Desired topology: `Browser → Next.js BFF → private Django → PostgreSQL`. Internet hosts must not reach Django.

**Code can enforce (application fail-closed):**

- `/api/v1/auth/` requires `X-Gym-BFF-Secret` matching a server-only secret of at least 32 characters;
- production settings refuse to start without that secret;
- the BFF strips inbound browser `gym_selection_ticket` / access / refresh values and forwards a ticket only from its HttpOnly cookie.

**Infrastructure must enforce (this repository cannot prove it at runtime):**

- only Caddy (behind Cloudflare) publishes host ports 80/443;
- the Django/Gunicorn container has **no published host port**. Caddy proxies only `/api/v1/member-auth/` and `/api/v1/member/` to it and rejects every other `/api/v1` path. Staff routes are reachable only from the Next.js BFF on the private Docker network;
- PostgreSQL has **no published host port**. Production object storage is AWS S3 under [ADR 0010](./0010-production-provider-readiness.md), not a VPS MinIO container. Local and end-to-end object storage, and ClamAV, publish no host port;
- host firewall and Docker networks match ADR 0001;
- mobile clients call the member allowlist and never receive the BFF shared secret. They do not get a general Django listener.

The shared secret authenticates the BFF process. It is **not** a substitute for network isolation. A leaked secret plus a publicly bound Django listener is a failed deployment, not an accepted control.

## Concurrency consequences

Concurrent refreshes may cause one rotation to win and another to present a replaced credential. Django's atomic rotation and reuse policy is authoritative; the BFF does not attempt client-side race repair. Each request permits at most one refresh and one retry. Logout racing with refresh must end with the authoritative session revoked and browser cookies cleared.

Tests cover simultaneous refresh, replay-family revocation, logout/refresh races, stale access tokens, and absence of retry loops.

Gym-selection tests cover 5-minute expiry boundaries, single use, concurrent selection, replay, user binding, bootstrap-list membership substitution, membership status changes, bounded invalid attempts, terminal revocation, rate limits, cookie attributes and clearing, safe browser responses, and proof that no tenant context or JWT/session exists before successful selection.

## Compatibility consequences

The approved baseline is Python 3.14.7, Django 5.2.17 LTS, DRF 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. Phase 1A verifies the BFF route/cookie behavior, Django/DRF token contracts, strict TypeScript build, and real-PostgreSQL refresh-session transactions on those exact pins.

Mobile is not routed through this browser BFF and remains Phase 7. Its separate member audience and secure-storage contract are unchanged.

## Alternatives considered

### Return tokens to browser JavaScript

Rejected because it permits token exposure to JavaScript and encourages browser storage and duplicated refresh logic.

### Direct Django secure-cookie termination

Viable, but rejected for the approved web topology because the Next.js server already owns same-origin browser rendering and route mediation. Splitting cookie behavior between Django and Next.js would make lifecycle and CSRF ownership less clear.

### Server-side session identifier only

Potentially viable later, but it would change the current JWT/refresh API and session model without a demonstrated need. It is not required for Phase 1A.

## Rollback and migration

There is no Phase 0 data migration. Phase 1A introduces the BFF boundary and global `GymSelectionTicket` control-plane table before any browser client is released, so no browser token migration is expected. Expired, consumed, and revoked ticket rows are retained for a bounded security/audit window and then purged by privileged maintenance; plaintext is never recoverable from the database.

A future move to direct Django cookie termination or opaque browser sessions requires a new ADR, coordinated cookie-name/path and CSRF migration, compatibility tests, and a staged revocation/expiry plan for existing sessions. The public domain APIs, JWT claim set, Django authority, tenant selection, explicit Django scoping, and forced RLS must remain intact.
