# ADR 0006: Member-self QR challenge redemption

- **Status:** Accepted
- **Decision date:** 2026-10-17
- **Applies from:** Phase 3 member check-in scan

## Context

Staff can issue a rotating onsite challenge, and a linked member can hold a member access session. The older Phase 3 list names `POST /api/v1/check-ins/scan/` without saying which principal presents the token. Publishing that path for a staff token, or publishing both that path and the Phase 7 member path, would leave two scan contracts. Trainer assignment scope does not exist. Display-device credentials are a later decision in [ADR 0007](0007-display-device-credentials.md).

The check-in actor columns are gym-membership identifiers. A member is not a gym membership. Putting the member id in `created_by_membership_id` would fail the composite foreign key or, worse, alias one identifier as the other. Manual rows must keep a real staff actor.

`max_uses` may be greater than one. The previous challenge trigger allowed `ACTIVE` to become `CONSUMED` only by jumping `use_count` to `max_uses`, and it rejected an `ACTIVE` row whose `use_count` increased but stayed below the bound.

## Decision

Publish only `POST /api/v1/member/check-ins/scan/`. Authenticate it with `MemberJWTAuthentication`. Do not publish `POST /api/v1/check-ins/scan/`. Do not accept a staff token, a platform token, or the browser BFF secret on the member route.

The JSON body accepts `payload` and nothing else. `Idempotency-Key` is rejected. The manual check-in convention compares a stored request, and this route must not store the plaintext or its digest on the visit, in source metadata, or in the audit row. Same-member replay is bounded by the open-visit rule, the duplicate window, and one redemption per member for the active challenge. A rejected replay does not increment `use_count`.

Gym and member come from the validated member access token. The service hashes a well-formed payload and, in one transaction, locks in this order:

1. the challenge row by its global hash, `FOR UPDATE`;
2. the token member, `FOR UPDATE`;
3. the eligible membership, `FOR UPDATE`.

It does not lock `GymSettings`. Issuance locks settings and then active challenges, so the opposite order can deadlock. The duplicate window is a plain read.

A missing, malformed, modified, expired, revoked, consumed, exhausted, or other-gym payload raises one `400 CHECK_IN_CODE_INVALID` and writes nothing. The scan does not mark a row expired as a side effect. Membership eligibility uses the gym-local date of the server clock. The visit cannot be backdated. Payment state is not read as a gate and is not changed.

`created_by_membership_id` is nullable. `MANUAL` still requires both membership actor columns. `QR` requires both to be null, along with `device_id` and `source_metadata`. The audit action is `MEMBER_CHECK_IN_RECORDED`. `actor_user` is the linked user and `actor_membership_id` is null. The audit values omit the payload and the hash.

Each successful scan adds exactly one to `use_count`. While that count remains below `max_uses`, the row stays `ACTIVE` and `consumed_at` stays null. The update that reaches `max_uses` sets `CONSUMED` and `consumed_at` together. Concurrent scans cannot pass that bound because they serialize on the challenge row. A second scan by the same member sees the open visit, the duplicate window, or the earlier QR visit from this challenge, and rolls back before the counter moves.

The route is limited by `member_check_in_scan`, keyed by the authenticated user and client address. The payload is not a throttle key and is not logged.

## Security consequences

A photographed code still admits only an eligible member of that gym, and only while the challenge remains active and under its use bound. A staff token cannot redeem it. A code from another gym returns the same invalid-code error as a code that does not exist. Forced RLS hides the other gym's row from `gym_runtime`. The plaintext remains absent from the database, the response, and the audit log.

There is no React Native client in this increment. The staff browser does not gain a scan control.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. Phase 3 implementation is complete as of 2026-10-18. The approved QR path is this authenticated member-self scan. Trainer scan remains a Phase 5 assignment-scoped extension and is not implemented. Display-device credentials are recorded in [ADR 0007](0007-display-device-credentials.md). [ADR 0004](0004-defer-check-in-scan.md) still forbids the generic staff route. [ADR 0005](0005-member-session-foundation.md) remains the session foundation this route uses. Public linking, multi-member selection, the React Native app, and broader Phase 7 mobile APIs remain deferred and are not prerequisites for an already-linked member.

## Alternatives considered

### Publish `POST /api/v1/check-ins/scan/` for the member token

Rejected. The path sits beside staff check-in routes. Clients would send a staff token to it, or the server would have to accept two audiences on one URL. The Phase 7 member path already names the principal.

### Accept `Idempotency-Key` and store the challenge hash on the visit

Rejected. The hash is the lookup secret for the challenge. Copying it onto the check-in or into the audit log keeps it next to the member after the code should have disappeared. Open-visit and same-member guards give the required replay bound without that copy.

### Store the member id in `created_by_membership_id`

Rejected. That column references `GymMembership`. A member id is a different entity, and a null actor is the honest record for a self scan.
