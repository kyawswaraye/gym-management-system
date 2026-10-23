# Phase 8 increment 13 — local identity-security characterization

This increment adds an opt-in local identity-security characterization on the
disposable two-gym rehearsal. It checks published member authentication,
password reset, staff invitation, and staff MFA boundaries that increment 12
leaves uncovered. It is project-authored local verification and is not a penetration test.
It is not a formal security review or production security proof.
Phase 8 is not complete.

`penetration_test_proven`, `formal_review_proven`, `production_security_proven`,
`tenant_isolation_proven`, `load_test_proven`, `soak_test_proven`,
`production_capacity_proven`, `slo_proven`, and
`financial_reconciliation_proven` stay false. A local pass may set
`local_identity_status`. `local_security_status`, `local_isolation_status`,
and `local_soak_status` stay `not-run`. This file does not change the meaning
of increment 12 `security.json`.

The matrix is bounded: 72 fixed operations, an 8 second request timeout, a
35 second MFA step wait inside the probe phase, and a 180 second probe-phase
cap. There is no URL, method, payload, or target argument. Synthetic accounts
stay on the disposable stack. Response bodies are clipped at 16 KiB and are
not stored. Identity characterization does not wait for ClamAV TCP. A local
pass is not scanner, file-upload, or production ClamAV proof.

## How to run it

Start from a host with no `gym-staging-rehearsal` containers or volumes. The
command rebuilds the rehearsal, then runs the fixed profile. Cleanup stays
explicit and uses the owner-only token file. The token is
`destroy-disposable-staging-rehearsal` and is never placed on the command line.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py identity-security --repo .
```

Evidence is `.staging-rehearsal/evidence/identity-security.json`, mode 0600,
in the mode 0700 evidence directory. The rehearsal seal for this command is
`rehearsal-identity-security.json`. This file does not replace `security.json`,
`endurance.json`, `pt-cash.json`, `pt.json`, `load.json`, or `finance.json`.
Cleanup removes rehearsal containers and volumes and keeps the evidence files.

## Transport

The coordinator is `manage.py staging_identity_security_characterization`. It
runs inside a one-shot API container, requires
`config.settings.staging_rehearsal`, `STAGING_REHEARSAL=1`, and project
`gym-staging-rehearsal`. Passwords, cookies, reset tokens, invitation tokens,
and MFA material stay in that process and are blanked before exit.

Published member routes connect through private `caddy`. Browser routes connect
through the same edge to the Next.js BFF. The three private probes connect to
`api:8000` on the Compose network without the BFF secret. That port is not
published. Redirects that leave `https://localhost:8443` are rejected.

Caddy metrics use the same private scrape as increment 12.
`caddy_metrics_observed` records whether the scrape returned a Prometheus
document. A failed scrape stays unavailable and the 5xx delta stays null. An
observed zero requires that document. A positive 5xx delta fails the local
gate.

## Token source

The API worker mailbox is not shared with the one-shot coordinator, so an
emailed reset or invitation cannot be read from the HTTP request that created
it. Those HTTP responses are checked for anti-enumeration and for the absence
of a credential. Single-use consumption uses `service-local-mailbox`: the
coordinator calls the same service, reads the token from its own locmem
mailbox, submits that token to the published BFF route, and discards it.
`token_sources` must be `service-local-mailbox` for a pass. That is not an
HTTP mailbox round trip. `http_forgot_mailbox_roundtrip` and
`http_invitation_mailbox_roundtrip` stay uncovered.

## Matrix

Eighteen edge probes run once. Twenty-one gym probes then run on gym 0 and
again on gym 1. Logout is three probes on gym 0. Staff MFA is nine probes on
gym 0. The operation cap is 72.

| Scenario | Transport | Request | Expected |
| --- | --- | --- | --- |
| `member_login_unknown` | Caddy | `POST /api/v1/member-auth/login/` | 401 `AUTHENTICATION_FAILED` |
| `member_login_shape` | Caddy | login with an extra field | 400 |
| `member_mfa_closed` | Caddy | MFA-enabled linked member login | 401, same generic error, no challenge |
| `member_me_absent` | Caddy | `GET /api/v1/member/me/` | 401 or 403 authentication rejection |
| `member_staff_blocked` | Caddy | `GET /api/v1/members/` | 404 |
| `forgot_csrf` | BFF | `POST /api/auth/password/forgot` without CSRF | 403 `CSRF_FAILED` |
| `forgot_unknown` | BFF | forgot for an unknown address | 200, same envelope, no token |
| `forgot_private` | private API | `POST /api/v1/auth/password/forgot/` | 403 `BFF_REQUIRED` |
| `reset_guess` | BFF | `POST /api/auth/password/reset` | 401 `AUTHENTICATION_FAILED` |
| `reset_shape` | BFF | reset with an extra field | 400 |
| `reset_private` | private API | `POST /api/v1/auth/password/reset/` | 403 `BFF_REQUIRED` |
| `invite_guess` | BFF | `POST /api/auth/accept-staff-invitation` | 401 `INVALID_INVITATION` |
| `invite_guess_replay` | BFF | the same unknown token | 401 `INVALID_INVITATION` |
| `invite_shape` | BFF | accept with an extra field | 400 |
| `invite_csrf` | BFF | accept without CSRF | 403 `CSRF_FAILED` |
| `invite_private` | private API | `POST /api/v1/staff/invitations/accept/` | 403 `BFF_REQUIRED` |
| `owner_invite_guess` | BFF | `POST /api/auth/accept-owner-invitation` | 401 `INVALID_INVITATION` |
| `mfa_absent` | BFF | `POST /api/auth/mfa/challenge` without a challenge cookie | 401 |
| `member_login` | Caddy | linked member login | 200, access and refresh in the body |
| `member_me` | Caddy | `GET /api/v1/member/me/` with that access token | 200, token not echoed |
| `member_wrong` | Caddy | wrong member password | 401 |
| `member_staff_login` | Caddy | staff password on member login | 401 |
| `member_on_staff_me` | BFF | member access cookie on `GET /api/auth/me` | 401 |
| `member_on_staff_refresh` | BFF | member refresh on `POST /api/auth/refresh` | 401, member session kept |
| `staff_login` | BFF | `POST /api/auth/login` | 200, Secure/HttpOnly/SameSite=Lax/Path=/ cookies, no token body |
| `staff_on_member_me` | Caddy | staff access token on member me | 401 |
| `staff_on_member_refresh` | Caddy | staff refresh on member refresh | 401, staff session kept |
| `member_refresh` | Caddy | `POST /api/v1/member-auth/refresh/` | 200 |
| `member_refresh_replay` | Caddy | the rotated refresh | 401 and the family is revoked |
| `member_access_revoked` | Caddy | the access token after replay | 401 |
| `invite_create` | BFF | `POST /api/operations/staff/invitations` | 201 pending, no invitation token |
| `invite_accept` | BFF | published accept with a `service-local-mailbox` token | 200 and session cookies |
| `invite_replay` | BFF | the same token | 401 `INVALID_INVITATION` |
| `invite_session` | BFF | `GET /api/auth/me` | 200 for the accepted trainer |
| `forgot_known` | BFF | forgot for the owner | 200, same envelope as unknown |
| `reset_consume` | BFF | published reset with a `service-local-mailbox` token | 204 |
| `reset_replay` | BFF | the same token | 401 |
| `reset_old_session` | BFF | `GET /api/auth/me` with the pre-reset session | 401 |
| `reset_old_login` | BFF | the previous owner password | 401 |
| `member_logout_login` | Caddy | a fresh member login on gym 0 | 200 |
| `member_logout` | Caddy | `POST /api/v1/member-auth/logout/` | 204 |
| `member_logout_replay` | Caddy | refresh after logout | 401 |
| `mfa_login` | BFF | gym 0 manager login before MFA | 200 session cookies |
| `mfa_enroll` | BFF | `POST /api/operations/auth/mfa/enroll` | 200 with secret material in that response only |
| `mfa_confirm` | BFF | `POST /api/operations/auth/mfa/enroll/confirm` | 200 `mfa_enabled` true |
| `mfa_relogin` | BFF | login after MFA | 200 `requires_mfa`, challenge cookie, no access cookie |
| `mfa_bypass` | BFF | login with `mfa_code` and no challenge | still `requires_mfa` |
| `mfa_me_blocked` | BFF | `GET /api/auth/me` with only the challenge cookie | 401 |
| `mfa_wrong` | BFF | challenge with a wrong code | 401 |
| `mfa_ok` | BFF | the next TOTP step | 200 session cookies |
| `mfa_replay` | BFF | the consumed challenge | 401 |

Member login, member refresh, and the logout login are the only scenarios
where an access token and refresh token in the body are expected. MFA enroll
is the only scenario where a secret, enrollment token, or `otpauth://` URI is
expected. The same material anywhere else fails `credential_exposure`. Evidence
redacts those values and fails closed if a credential-shaped marker remains.

## Reconciliation

Service-only counts run under each gym's row security context. A finished pass
expects one pending trainer invitation, one accepted trainer invitation, the
owner password changed, the reset consumed, and zero live owner or member
sessions. The owner flag stays on the owner. The accepted membership is not an
owner. MFA is enabled only after `mfa_confirm` passes, and that flag is the
same on both gym rows because it lives on the gym 0 manager. A partial run is
fail. `data_corruption` stays false unless stored rows exceed those counts or
a protected field changed. An early stop that still matches the seed baseline
is `not-run`. Empty evidence cannot pass.

## Uncovered

These classes are named and are not established. Transport: uncovered.

- `http_forgot_mailbox_roundtrip`. The published forgot response does not
  return a reset credential, and the API worker mailbox is not shared.
- `http_invitation_mailbox_roundtrip`. The published create response does not
  return an invitation credential.
- `owner_invitation_acceptance`. Owner invitation guessing is covered. A real
  owner invitation is not issued.
- `member_mfa_challenge`. Member login fails closed when MFA is enabled. No
  member challenge route exists.
- `edge_rate_limit`. Edge and WAF rate limits are not sampled.
- `platform_admin`. Platform administration routes are not probed.
- `live_provider`. Live email delivery is not proven.
- ClamAV TCP. Identity characterization does not wait for ClamAV TCP. That
  is not file-upload or production scanner proof.

## Local gates

A local pass requires every planned probe to match its contract, no cancelled
operation, no unexpected credential, no other-gym marker in a 2xx body,
reconciliation to match, both token sources to be `service-local-mailbox`,
and `data_corruption` to be false. The probe phase stays inside 180 seconds.
When Caddy metrics are observed, the 5xx delta is 0. When they are
unavailable, the delta stays null and does not by itself fail the run.

Phase 8 is not complete.
