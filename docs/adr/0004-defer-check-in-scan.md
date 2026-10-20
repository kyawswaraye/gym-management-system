# ADR 0004: Do not publish a staff check-in scan route

- **Status:** Accepted. The generic staff scan route stays unpublished. Member-self scan, display credentials, and NFC and biometric ingestion are specified in ADRs 0006–0008. Trainer scan was later published as an assignment-scoped route and is outside this record.
- **Decision date:** 2026-10-17
- **Applies from:** Phase 3 staff challenge issuance

## Context

The check-in contract allows an unattended scan only when the caller is an authenticated member of that gym or a trainer acting inside an assignment. The challenge itself is not admission authority. The server must still check the challenge gym, expiry, state, use bound, principal scope, membership eligibility, and duplicate window in one transaction.

Member-specific access sessions are Phase 7. Trainer assignment scope is Phase 5. This checkout therefore has no principal that can satisfy the scan contract.

## Decision

`POST /api/v1/check-in-challenges/` is published for a staff actor who holds `checkins.challenge.issue` and the active `members` and `memberships` entitlements. The plaintext payload is returned once. The database stores only its SHA-256 hash. `display_device_id` stays null and has no foreign key.

`POST /api/v1/check-ins/scan/` is not published. This record does not approve a staff-token scan, a device-credential model, or a staff-operated substitute. Member-self redemption is a different route, specified in [ADR 0006](0006-member-check-in-scan.md).

These workarounds are forbidden:

- accepting a staff access token as the scanning principal;
- accepting a member id, gym id, or membership id chosen by the caller;
- treating the QR payload as sufficient authority without a member-self or assigned-trainer session;
- persisting the plaintext challenge in browser storage, logs, or audit values.

Device-authenticated issuance was pending in this record until a separate device-credential contract was approved. That contract is accepted in ADRs 0007 and 0008. A trainer-only staff membership remains outside member scope until Phase 5 assignments exist.

## Security consequences

Staff can rotate an onsite code. They cannot turn that code into attendance. A photographed code becomes useless when the next staff issuance revokes the previous active staff challenge, and it cannot be redeemed by this increment at all.

The payload is `gms-checkin:v1:` plus a high-entropy token. It contains no member, gym, staff, or permanent secret. Audit records the location, lifetime, use bound, and status, not the payload or hash.

## Concurrency consequences

Issuance locks gym settings, revokes prior active staff-issued challenges, and inserts the new row in one transaction. Member scan must not lock settings after taking the challenge lock, or it can deadlock with issuance. [ADR 0006](0006-member-check-in-scan.md) records that lock order.

## Compatibility consequences

No staff scan route is added. Owner and manager role templates gain `checkins.challenge.issue` through a forward data migration. Reception and trainer templates stay unchanged because their matrix entries are conditional and those conditions are not yet implemented. [ADR 0005](0005-member-session-foundation.md) publishes member login, refresh, and logout. [ADR 0006](0006-member-check-in-scan.md) publishes member-self redemption only. [ADR 0007](0007-display-device-credentials.md) adds a gym-owned display credential and a separate device challenge route. The generic `/api/v1/check-ins/scan/` path stays unpublished so a staff token cannot be aimed at an ambiguous scan URL.

`docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and is not edited by this deferral. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. This record still forbids the generic staff scan route. The approved QR path is authenticated member-self scan. Trainer scan was later published at `POST /api/v1/trainer/check-ins/scan/`. This record does not define that route. Display, NFC, and biometric credentials are specified in later ADRs and do not reopen this route.

## Alternatives considered

### Let staff scan on behalf of a selected member

Rejected. It collapses unattended member proof into a front-desk action the manual check-in route already covers, and it would teach clients a principal the contract forbids.

### Publish scan for the trainer permission without assignments

Rejected. `checkins.create` on a trainer-only membership is not assignment scope. Phase 5 has to define that scope before a trainer scan can be conforming.

### Store the plaintext so a lost response can be shown again

Rejected. A stored or replayed plaintext is a reusable secret. Callers issue a new challenge instead.
