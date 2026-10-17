# ADR 0007: Display-device credentials

- **Status:** Accepted
- **Decision date:** 2026-10-18
- **Applies from:** Phase 3 display-device challenge issuance

## Context

Staff can issue one unbound onsite challenge, and a linked member can redeem it. A front-desk screen needs to mint the next challenge without a staff browser session. A staff JWT, a member JWT, or the browser BFF secret would make that screen a person or a web tier. NFC and biometric vendor ingestion are a different contract: they are not part of this decision, and this system still stores no biometric template and no vendor secret.

`settings.manage` is already reserved to the owner role and is not delegable. `checkins.challenge.issue` is already on the owner and manager templates. Display enrollment is an owner-level operation, so it requires both codes rather than a new permission.

## Decision

Add `members_check_in_device`, owned by the gym and protected by forced RLS. A row has an immutable kind, a bounded label and location, status `ACTIVE` or `REVOKED`, a globally unique non-secret credential selector, a one-way hash of a high-entropy secret, a credential version, and created, rotated, and revoked times with same-gym membership actors. This record covers `DISPLAY`. `NFC` and `BIOMETRIC` are specified in [ADR 0008](0008-device-ingestion.md). The hash is a Django `pbkdf2_sha256` password hash. Plaintext is not a column. `gym_runtime` receives `SELECT`, `INSERT`, and `UPDATE` only.

Staff management is:

- `GET` and `POST /api/v1/check-in-display-devices/`
- `POST /api/v1/check-in-display-devices/{id}/rotate-credential/`
- `POST /api/v1/check-in-display-devices/{id}/revoke/`

Every call requires an active owner, both `settings.manage` and `checkins.challenge.issue`, and the active `members` and `memberships` entitlements. Enrollment, rotation, and revocation also require `RequiresRecentMFA`. List does not. Serializers reject unknown fields. Enrollment and rotation return the plaintext credential once. List, audit, logs, and later responses do not. `Idempotency-Key` is rejected. Rotating or revoking a device that is not `ACTIVE` returns `409 DISPLAY_DEVICE_STATE_CONFLICT`. A device id from another gym is `404`.

Device authentication is `POST /api/v1/device/check-in-challenges/`. It is not a JWT scheme and it is not behind the BFF secret. The `Authorization` scheme is `Device`, followed by one token: `gms-device:v1:`, the selector, a dot, and the secret. Parsing is bounded. Verification is `check_password` on the locked row. An unknown selector still runs a password check against a dummy hash. Wrong, old, revoked, inactive-gym, and missing-entitlement failures are the same `401 AUTHENTICATION_FAILED`. The body cannot select a gym, device, actor, location, max uses, or TTL. The gym comes from the credential. Location, TTL, and max uses come from the locked device and `GymSettings`.

`app_check_in_device(selector)` is a bootstrap-owned security-definer lookup. It returns the device id, gym id, status, credential version, and kind, and only when the gym is active with a trial or active subscription. It does not return the hash, label, or location. `gym_auth_bootstrap` has column `SELECT` on those lookup columns and no table-level privilege. After the lookup, the request sets the gym context and reloads the row under RLS.

A device challenge sets `display_device_id` and leaves both membership actor columns null. A staff challenge does the opposite. The composite foreign key `(gym_id, display_device_id)` references the device. Issuing again revokes only that device's previous active challenge, so two displays can show two codes. Rotation changes the hash and version immediately and leaves current challenges in place. Revocation changes the device to `REVOKED` and revokes its active challenges in the same transaction. The QR payload stays `gms-checkin:v1:` plus a one-time token. It carries no person, gym id, device id, or permanent secret. Only the SHA-256 hash is stored.

Lock order, for any transaction that takes more than one of these rows:

1. `GymSettings`
2. the display device, and only that row
3. that device's challenge rows, ordered by id
4. the member, then the eligible membership, for a member scan

Staff issuance locks settings and then unbound challenges ordered by id. It does not lock a device. Member scan locks one challenge, then the member, then the membership. It does not lock settings or a device. Credential rotation locks only the device. Revocation locks the device, then its active challenges. Device locks do not follow the gym relation: `FOR UPDATE` on `tenants_gym` deadlocks with the `KEY SHARE` an insert takes through the gym foreign key. The password check runs while the device row is locked, so a secret that lost a rotation race cannot pass.

`device_challenge_ip` allows 60 attempts per minute per address. `device_challenge_selector` allows 20 per minute, keyed by a hash of the selector plus the address. The secret is not a throttle key and is not logged.

## Security consequences

A copied screen credential works until an owner rotates or revokes it. Rotation invalidates the old secret before the next request can use it. Revocation also kills the code on that screen. A failed authentication does not reveal whether the selector exists. The plaintext credential and the challenge payload stay out of audits, OpenAPI examples, errors, and logs. The selector may appear on the staff list because it is not secret. NFC and biometric ingestion is specified in [ADR 0008](0008-device-ingestion.md). No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Trainer scan remains a Phase 5 assignment extension and is not implemented.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. Phase 3 implementation is complete as of 2026-10-18. Trainer scan remains a Phase 5 assignment-scoped extension and is not implemented. [ADR 0004](0004-defer-check-in-scan.md) still forbids the generic staff scan route. [ADR 0006](0006-member-check-in-scan.md) still defines member redemption. No new permission code is added.

## Alternatives considered

### Reuse a staff or member JWT on the display

Rejected. The screen would then be a person, with that person's permissions, refresh session, and revocation semantics. The display needs a credential that can be rotated without signing a person out.

### Revoke the visible challenge on every credential rotation

Rejected. Rotation is a secret change. The code already on the screen stays valid until its own TTL, use bound, or the next issue from that same device. Revocation is the operation that must take the screen down.

### Grant `settings.manage` to managers

Rejected. That code is reserved to the owner and is not delegable. Requiring it together with `checkins.challenge.issue` keeps enrollment inside the existing owner policy.
