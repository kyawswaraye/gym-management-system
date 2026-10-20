# ADR 0012: FCM push delivery

- **Status:** Accepted
- **Decision date:** 2026-10-20
- **Applies from:** Phase 7 notification architecture

## Context

Membership-expiry email already runs as two Django management commands and a tenant-owned `Notification` row. [ADR 0010](0010-production-provider-readiness.md) selects Resend for that mail and leaves non-email providers deferred. Phase 7 needs membership-expiry reminders and gym announcements on Android and iOS, plus an in-app inbox, without adding Redis or Celery. The historical cost estimate excludes push-provider fees until a budget is approved.

The member application in [ADR 0011](0011-bare-react-native-member-app.md) is bare React Native CLI. iOS push must still reach Apple Push Notification service.

## Decision

### Provider

Firebase Cloud Messaging HTTP v1 is the production push provider for both Android and iOS. The API sends `POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send` with a Google service-account bearer token. It does not call Apple's APNs HTTP API itself.

iOS uses the FCM-to-APNs bridge. The iOS app enables the Push Notifications entitlement and registers with FCM. An Apple APNs authentication key (`.p8`), its key id, and the team id are uploaded in the Firebase project. They are not stored in this repository and they are not sent to Django. Android uses the same FCM HTTP v1 endpoint and the same server credential.

The message carries a notification title and body plus one data field, `notification_id`. Android `collapse_key` and the APNs `apns-collapse-id` are that same id. The message does not include a member id, gym id, email, phone, token, or payment amount. FCM has no idempotency header equivalent to Resend. Duplicate protection is the database uniqueness below, row locks, and the collapse id. A crash after FCM accepts a message and before the status commit can still deliver twice. The collapse id keeps one tray entry.

### Test transport and production gate

`PUSH_BACKEND=locmem` records the logical send in memory and does not contact Google. Tests, local development, and end-to-end runs use `locmem`. `PUSH_BACKEND=fcm` is the only production transport.

Production settings fail closed at startup when `PUSH_BACKEND=fcm` and the Firebase project id or service-account secret is missing. The service-account JSON, `google-services.json`, and `GoogleService-Info.plist` are secrets or ignored client files. None of them belong in git.

Live push readiness is closed only when all of these external proofs exist:

- a Firebase project and a service account allowed to call FCM HTTP v1;
- an Android app registration and a live message to a consenting device;
- an iOS app registration, an APNs key installed in that Firebase project, and a live message to a consenting iPhone.

This record does not close that gate. Code may be written against `locmem` first. Production must not send with a missing credential.

### Delivery model

Delivery stays on Django management commands run by host timer or a one-shot Compose job. Redis and Celery are not added. Each command sets one gym's transaction-local context at a time.

The commands, none of which exist yet, are:

- `schedule_membership_expiry_pushes`, which creates in-app and push rows for the same warning days the email scheduler already uses;
- `deliver_push_notifications`, which sends due push rows;
- `publish_scheduled_announcements`, which promotes due scheduled announcements and creates recipient rows;
- `expire_announcements`, which marks a published announcement expired after `ends_at`.

The existing email commands stay email-only. A push or inbox failure does not change membership, booking, payment, or the email row.

`deliver_push_notifications` locks one due push row with `select_for_update(skip_locked=True)` and commits that row's outcome before taking the next. It fans out to at most the member's active consented devices, one FCM call each, then commits. Provider failures store only `PROVIDER_UNAVAILABLE`, `DELIVERY_REJECTED`, or `INVALID_TOKEN`. `INVALID_TOKEN` revokes that device and clears its ciphertext. The other devices are not revoked. Five attempts use the same bounded backoff as expiry email. At five failures with no successful device, the push row is `FAILED`. If at least one device succeeds, the push row is `SENT`. If the member has no eligible device, the push row is `CANCELLED` and FCM is not called.

### Consent, retention, and revocation

Push requires `push_consent=true` and a current ciphertext on a non-revoked device. The in-app inbox does not require push consent. Turning consent off clears the ciphertext and leaves the device row active so the member can consent again. Logout of one refresh session does not revoke every device. The logging-out app deletes its own device registration.

These events revoke every device for that member, clear ciphertext, and stop further push: member archive, linked-user change, member suspension or cancellation, and password reset. The password-reset command already revokes sessions. The same transaction revokes that user's member devices. A sixth active device for the same member revokes the least recently seen device with reason `SUPERSEDED`.

The raw FCM token is encrypted with `PUSH_TOKEN_ENCRYPTION_KEY`, a Fernet key that is not `MFA_ENCRYPTION_KEY`, `PASSWORD_RESET_TOKEN_PEPPER`, or `FINANCE_IDEMPOTENCY_ENCRYPTION_KEY`. A separate `PUSH_TOKEN_FINGERPRINT_PEPPER` produces the HMAC-SHA256 fingerprint used for uniqueness. Logs, audits, errors, and throttle keys never contain the raw token, the ciphertext, or either secret. Revocation sets the ciphertext to null immediately. The revoked row, its fingerprint, and its reason stay as security state. Inbox and delivery rows are not purged by this record. Decision 8 still owns legal retention.

Gym setting `membership_expiry_push_enabled` gates expiry push only. It defaults to false. Expiry in-app rows are still created when the membership is otherwise eligible, including when the member has no email. Announcement push follows publication plus device consent, not that gym setting.

## Security consequences

A stolen FCM token can receive that member's titles and bodies until the device is revoked or the token is rotated. Titles and bodies must stay free of payment amounts, tokens, and other members' data. The service account can send to any registered token of this Firebase project, so it is a production secret with the same handling as the Resend key. `locmem` cannot prove APNs. Only the live iOS proof does.

An announcement cannot name recipient ids. Audience resolution is server-side and same-gym. A push send never takes a caller-selected member or gym.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. Its email-provider and Redis/Celery deferrals stay historical. ADR 0010's Resend contract is unchanged. The current `notifications_notification` email checks stay in force for `channel=EMAIL`. Push, in-app, announcement, and device tables are specified in `DATABASE.md` and are not migrated by this record. No FCM credential is added to the repository.

Push-provider fees were excluded from the historical monthly estimate. This record does not approve a new price.

## Alternatives considered

### Call APNs directly for iOS and FCM only for Android

Rejected. Two server credentials and two send paths duplicate deduplication, revocation, and the test transport. FCM's APNs bridge keeps one Django sender.

### Add Celery now

Rejected. Expiry email already uses locked management commands. Phase 7 uses the same shape. Celery stays deferred until a measured workload justifies it.

### Store the raw FCM token

Rejected. The token is a delivery secret. The database stores ciphertext plus a peppered fingerprint, and responses return neither.
