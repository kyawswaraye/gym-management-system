# ADR 0010: Production provider readiness

- **Status:** Accepted
- **Decision date:** 2026-10-19
- **Applies from:** production provider selection

## Context

Invitation mail, membership-expiry mail, and password recovery are specified, and the file pipeline already speaks an S3-compatible presigned API. Production vendor choice is Resend, AWS S3 in `ap-southeast-1`, and private ClamAV. Official MinIO images are not anonymously distributable, so local and end-to-end Compose use the third-party fork `docker.io/pgsty/silo`. Jurisdiction, retention, backup, and data residency remain a production blocker in decision 8.

This record selects the production providers and the password-recovery contract. Code readiness is implemented in the repository. Live deployment gates remain open.

## Decision

### Transactional email

Resend's HTTP API is the production transactional email provider. Production sends are `POST https://api.resend.com/emails`. The application `X-Idempotency-Key` is translated to Resend's `Idempotency-Key` header. That provider key is 1–256 characters, and Resend retains it for 24 hours. Keys outside that length are not sent.

Invitation delivery, password-recovery mail, and membership-expiry delivery use this provider in production. Tests and end-to-end runs keep non-delivery backends. Invitation delivery stays inside the provisioning transaction and still rolls that transaction back when delivery fails. Password-recovery provider failure stays private: the forgot response remains the constant `200`. Expiry delivery keeps the existing deduplicated notification contract. A provider failure there still stores only `PROVIDER_UNAVAILABLE` or `DELIVERY_REJECTED`.

The current expiry deduplication key fits inside 1–256 characters. Finance `Idempotency-Key` values are a separate database claim and are not Resend keys.

### Password recovery

Password recovery is global `User` scope for platform-administrator, staff, and linked-member identities. The browser uses same-origin BFF routes. Django remains authoritative. The reset page is `/reset-password?token=`.

`POST /api/v1/auth/password/forgot/` accepts `email` only and returns a constant `200` envelope. The body does not reveal whether an account exists, whether mail was sent, or whether the provider failed.

A reset token is high-entropy, hash-only, single-use, and expires 30 minutes after issue. The stored value is a peppered hash using the dedicated `PASSWORD_RESET_TOKEN_PEPPER`. That pepper is not `REFRESH_TOKEN_PEPPER`, `MFA_ENCRYPTION_KEY`, or `FINANCE_IDEMPOTENCY_ENCRYPTION_KEY`. The plaintext is not stored, logged, audited, or returned. One live token exists per user. A later request replaces the previous live token.

`POST /api/v1/auth/password/reset/` accepts `reset_token`, `password`, and `password_confirmation`. Success is `204` with an empty body. It issues no access token, refresh session, or gym-selection ticket. Login is required afterward.

The command verifies the peppered hash, user binding, expiry, and unconsumed state under lock. It enforces the existing password validators: user-attribute similarity, minimum length 12, and the common-password check. It does not add composition rules. The new password must differ from the current password. It then consumes the token.

Revocation covers every applicable credential for that user: every `RefreshSession`, every live `GymSelectionTicket`, and every live `MFAChallenge`. Onsite check-in challenges are not login credentials and are not revoked by this command. The command increments `authz_version` on every linked `GymMembership` and every linked `Member`. MFA stays enabled. The reset does not change `is_active`, platform-administrator flags, or membership status.

Runtime still cannot update `password_hash` directly. `app_accept_invitation_password` remains the invitation-only unusable-to-usable write. `app_accept_password_reset` is the narrow privileged replacement for this command. `app_bump_password_reset_authz` increments every linked `GymMembership` and `Member` `authz_version`.

### Object storage and malware scanning

AWS S3 in `ap-southeast-1` is the selected primary object store. The bucket is private. Browsers use presigned POST and GET over direct AWS HTTPS. The API uses server credentials over HTTPS for read, canonical overwrite, and delete. Production MinIO is no longer planned. Caddy does not proxy the production object origin.

Private ClamAV stays on the VPS private network. It publishes no host port. The API reaches it only on that network. Scanning stays in the application path before an object can become `CLEAN`.

Local and end-to-end Compose keep digest-pinned `docker.io/pgsty/silo`, a third-party MinIO-compatible fork, plus the local Caddy object proxy and local ClamAV. That fork is test-only. It is not official MinIO and it is not the production store. An end-to-end upload through it is interoperability evidence only.

The presigned policy, 300-second POST, 60-second GET, quarantine, and canonical re-encode rules in `API.md` stay in force for both the test store and AWS S3.

### Draft legal retention

The selected retention posture is draft-only. These controls are planned and are not evidence that retention is approved or deployed:

- S3 Block Public Access;
- SSE-S3;
- bucket versioning.

These items remain open and must not be described as complete:

- current-object retention;
- noncurrent-version expiry;
- backup and replication;
- jurisdiction and legal approval.

Decision 8 stays open. RPO and RTO targets in ADR 0001 stay targets until a restore drill proves them.

### Code readiness and live production readiness

These are different states.

Code readiness is the Resend HTTP backend, the hash-only reset-token table, `PASSWORD_RESET_TOKEN_PEPPER`, the `/forgot-password` and `/reset-password` pages, BFF routes, AWS virtual-hosted SigV4 addressing with optional session tokens, the `infra/aws/` draft, and the private ClamAV production fragment. Tests and end-to-end runs keep non-delivery backends and the MinIO-compatible fork. This record still does not close live credentials or legal approval.

Live production readiness is closed only when all of these external proofs exist:

- a verified Resend domain, a production API key, and a live idempotency proof;
- an AWS bucket, IAM, CORS, and a live upload and download proof;
- a ClamAV deployed-host proof.

None of those credentials or proofs are in this repository. Phase 5 waits when the program requires those live gates to be closed before Phase 5 starts. Phase 5's personal-training scope is unchanged, and this record does not start it.

## Security consequences

A forgot response cannot be used to enumerate accounts or to detect a provider outage. A reset token hash is useless without `PASSWORD_RESET_TOKEN_PEPPER`, and that pepper does not decrypt MFA secrets or finance idempotency ciphertext. One live token means a second request invalidates the earlier link. Reset revokes staff and member refresh sessions, open gym-selection tickets, and open MFA challenges, and it bumps every linked authorization version. MFA enrollment survives the reset, so a later login still demands the authenticator. The reset page token is a query credential: it is not written to a cookie, `localStorage`, or `sessionStorage`, and it is omitted from logs.

Production objects are not served from the VPS disk and are not anonymously readable. Presigned URLs stay short-lived. ClamAV remains a private scanner on the application network. Block Public Access, SSE-S3, and versioning are intentions until the bucket is actually configured. They do not satisfy the open retention, replication, or legal items.

The historical approximately $80 monthly estimate included on-VPS MinIO. AWS S3 is a managed object service. This record does not approve a new price. Decision 11 still requires a new approved budget before a managed-service cost is treated as funded.

## Compatibility consequences

ADR 0001's single-VPS application topology remains: Cloudflare, Caddy, Next.js, Django, PostgreSQL with forced RLS, private ClamAV, GHCR, and encrypted offsite database backups. Its production MinIO choice is superseded. ADR 0002's rule that PostgreSQL publishes no host port remains. The same no-published-port rule applies to local and end-to-end object storage and to ClamAV. It does not describe a production MinIO container.

`docs/DEVELOPMENT_PLAN.md` section 15 items 4 and 10 are refined by this record. Item 8 is not resolved. Historical phase sections that still name private MinIO stay as the baseline written when those phases were specified. Current status, the decision register, and the other authoritative documents follow this record.

Password-recovery routes, the reset-token table, and production validation are implemented. Live Resend, AWS, and ClamAV proofs remain outside the repository.

## Alternatives considered

### Keep production MinIO on the VPS

Rejected. Official MinIO images are not anonymously distributable, and the selected production store is AWS S3 in `ap-southeast-1`. The MinIO-compatible fork remains available for local and end-to-end tests.

### Choose an SMTP provider and leave the vendor unnamed

Rejected. Production mail is the Resend HTTP API, with the application idempotency key translated to Resend's header and a 24-hour provider retention window.

### Disable MFA or issue a session from the reset response

Rejected. MFA stays enabled. Success is `204`, and the user logs in through the existing login path.

### Treat the draft bucket settings as a finished retention policy

Rejected. Block Public Access, SSE-S3, and versioning are planned only. Current-object retention, noncurrent expiry, backup and replication, and jurisdiction or legal approval stay open.
