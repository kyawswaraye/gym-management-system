# ADR 0005: Member session foundation

- **Status:** Deferred for linking, multi-member selection, and member MFA
- **Decision date:** 2026-10-17
- **Applies from:** Phase 3 member-auth prerequisite

## Context

Check-in scan needs an authenticated member of the same gym, or a Phase 5 assigned trainer. Member authentication was scheduled with the Phase 7 mobile app. The session foundation can exist before that app. The published contract says a member token maps one global user to exactly one gym-scoped member, while the database allows one linked user per gym and the same user in several gyms. Staff gym-selection tickets select a `GymMembership`, not a `Member`. No approved member-selection or identity-linking route exists.

## Decision

Publish only:

- `POST /api/v1/member-auth/login/`
- `POST /api/v1/member-auth/refresh/`
- `POST /api/v1/member-auth/logout/`

A member access token uses audience `gym-platform-member` and `token_type=member_access`. Its claim set includes `member_id` and excludes `gym_membership_id`. Staff and platform tokens fail member validation, and a member token fails staff and platform validation.

Login issues a `MEMBER` refresh session only when the password matches an active user who has exactly one active linked member at an active gym with a trial or active subscription. Zero links and several links return the same generic credential error. The request cannot send `member_id` or `gym_id`. No selection ticket is created. An MFA-enabled user fails closed; this login does not start or skip an MFA challenge.

`app_linked_members()` is the bootstrap. After `app.user_id` is set, it may return only `member_id`, `gym_id`, and `authz_version`. The application then sets `app.gym_id` and reloads that member under forced RLS. The function is `SECURITY DEFINER`, locks `search_path`, turns row security off, is owned by `gym_auth_bootstrap`, and is executable only by `gym_runtime`. That role may select only the member columns the function uses.

Refresh rotation stays inside the same family, user, principal, gym, and member. Reuse revokes the family. Logout revokes the presented refresh session. Archiving a member, or changing its linked user, increments `authz_version` and revokes that member's sessions. Staff sessions are left in place.

There is no public linking endpoint. Tests link an existing member through `link_member_identity`. Onboarding remains deferred.

These routes are not behind the browser BFF secret. Tokens are in the JSON body for a future mobile client. Browser storage still must not receive them.

`POST /api/v1/member/check-ins/scan/` is specified separately in [ADR 0006](0006-member-check-in-scan.md). The generic `POST /api/v1/check-ins/scan/` path and the rest of the member mobile API stay unpublished.

## Security consequences

A user with memberships at two gyms cannot log in until an approved selection contract exists. An existing session for one already selected member remains usable after a second link is added; a new login does not choose between them. A staff access token cannot scan and cannot call member authentication.

Member login, refresh, and logout use their own address buckets and do not use the staff `anon` throttle. Login is also limited by a hash of the normalized email. Refresh and logout are also limited by a peppered hash of the presented refresh token. The raw token is not a cache key. Traffic on these routes cannot exhaust staff login or gym selection, and staff anonymous traffic cannot exhaust them.

The bootstrap role cannot read member names, email, or phone. A pooled connection does not keep `app.user_id` after its transaction ends.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. [ADR 0004](0004-defer-check-in-scan.md) still rejects a staff scan route. [ADR 0006](0006-member-check-in-scan.md) publishes the member route. Phase 3 implementation is complete as of 2026-10-18. Public member identity linking, multi-member selection, and member MFA remain deferred and are not prerequisites for the already-linked member QR path. [ADR 0011](0011-bare-react-native-member-app.md) and [ADR 0012](0012-fcm-push-delivery.md) approve the bare React Native CLI client and FCM delivery. The later implementation publishes the member app and the remaining member routes. Live Firebase and APNs credentials, release signing, store submission, and a production device proof remain open. Production deployment is not complete.

## Alternatives considered

### Accept `member_id` or `gym_id` when several links exist

Rejected. That is an unproven selector. The client would be choosing tenant scope.

### Reuse the staff gym-selection ticket

Rejected. That ticket authorizes a `GymMembership`, returns gym display names, and is stored by the browser BFF. Member login is a different principal and must not gain that authority by analogy.

### Require the BFF secret on member login

Rejected for the mobile principal. The secret would force member tokens through the browser BFF. Staff `/api/v1/auth/` remains BFF-only.
