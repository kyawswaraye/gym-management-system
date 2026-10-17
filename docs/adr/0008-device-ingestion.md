# ADR 0008: NFC and biometric ingestion

- **Status:** Accepted
- **Decision date:** 2026-10-18
- **Applies from:** Phase 3 device ingestion

## Context

[ADR 0007](0007-display-device-credentials.md) gave a gym-owned screen a credential that can mint a rotating challenge. NFC and biometric readers need a credential too, but they must not mint challenges, and a screen must not write attendance by itself. Vendor SDKs, card secrets, and biometric templates stay outside this system. Trainer scan still has no assignment scope.

## Decision

One gym-owned table, `members_check_in_device`, stores every check-in credential. `kind` is immutable:

- `DISPLAY` may issue challenges only.
- `NFC` may ingest NFC events only.
- `BIOMETRIC` may ingest biometric-result events only.

The staff display routes stay display-only. Owners provision NFC and biometric credentials at `/api/v1/check-in-integration-devices/`, with the same owner, `settings.manage`, `checkins.challenge.issue`, entitlement, and recent-MFA gate as display enrollment. List responses include `kind` and never a hash or secret. Enrollment and rotation return the plaintext credential once.

Ingestion is `POST /api/v1/device/check-ins/`. It uses the device credential, not a staff token, a member token, or the BFF secret. The body accepts `member_id` and a bounded `event_id`. The server sets the method from `kind` and the time from its own clock. Gym, membership, status, actors, totals, device override, source metadata, raw captures, templates, embeddings, card secrets, and vendor credentials are rejected. A display credential, a wrong secret, a revoked credential, and an old secret are the same `401 AUTHENTICATION_FAILED`.

The event id is not stored. The idempotency key is the SHA-256 hex of the device id and the event id. The same device, member, and event returns the original visit. A different member for that event is `409 IDEMPOTENCY_KEY_REUSED`. The visit stores the device id, the method, and that key. `source_metadata` stays null. Membership actors stay null. Payment rows are not read as a gate and are not changed.

The transaction locks gym settings, then the device row only, then the member, then the eligible membership. It does not lock `tenants_gym` and it does not lock a challenge. A trigger rejects a check-in whose device kind does not match the method, and a challenge whose device is not a display.

A member-self QR visit stays actorless and device-less. The challenge row already records `display_device_id`. Copying that id onto the visit would name a screen without a non-secret challenge reference, and the challenge hash must not be stored on the visit. Trainer scan stays a Phase 5 assignment feature. This route is not extended to accept a trainer token.

`device_ingest_ip` allows 60 attempts per minute per address. `device_ingest_selector` allows 20 per minute per selector hash plus address. The secret and the event id are not throttle keys and are not logged. The audit action is `DEVICE_CHECK_IN_RECORDED`, with a null membership actor and no credential or event id.

## Security consequences

A stolen reader credential can check in eligible members of that gym until an owner rotates or revokes it. It cannot issue a QR challenge, choose a method, backdate a visit, or store a template. A screen credential cannot create a visit. Two readers can enroll in one gym. Failed authentication does not reveal whether the selector exists.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. The approved QR path is authenticated member-self scan. Trainer scan remains a Phase 5 assignment-scoped extension and is not implemented. Scoped DISPLAY, NFC, and BIOMETRIC credentials and this ingestion abstraction are implemented. No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Vendor-specific integration remains optional. Public member identity linking, multi-member selection, the React Native app, and broader Phase 7 mobile APIs remain deferred and are not prerequisites for the already-linked member QR path. Production email-provider and object-storage gates remain separate. Closure evidence: backend checks passed; 225 non-PostgreSQL unit tests passed; the full PostgreSQL security and integration suite passed 171 tests before the new check-in plan test, and that focused plan test passed separately; web format, lint, typecheck, build, and 263 component and unit tests passed earlier, and focused static checks passed after the end-to-end spec change; a clean end-to-end reset passed 4 Playwright specs, including member authentication, a rotating onsite challenge, member QR scan, staff front desk, history, and summary, then member logout and refresh replay denial. The PostgreSQL query-plan baseline covers the front-desk open list, member history, challenge-hash lookup, and device-selector lookup. It passed and is documented in `docs/DATABASE.md`. It is not a production load test.

## Alternatives considered

### A second credential table for readers

Rejected. The uncommitted device migration can carry `kind` on the one credential table. Two tables would duplicate hash, rotation, revocation, and lookup rules.

### Let a display credential post attendance

Rejected. A screen that can both show a code and assert a visit collapses the member's proof into the device. The screen issues a challenge. The member, or a later Phase 5 trainer, redeems it.

### Store the raw event id

Rejected. The id is an external reference that may be a card or vendor token. The hash is enough to replay the same event and to reject a conflicting member.
