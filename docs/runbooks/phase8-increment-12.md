# Phase 8 increment 12 — local security characterization

This increment adds an opt-in local security characterization on the disposable
two-gym rehearsal. It checks a fixed browser-edge and authorization matrix.
It is project-authored local verification. It is not a penetration test, a
formal security review, or production security proof. Phase 8 is not complete.

`penetration_test_proven`, `formal_review_proven`, `production_security_proven`,
`tenant_isolation_proven`, `load_test_proven`, `soak_test_proven`,
`production_capacity_proven`, `slo_proven`, and
`financial_reconciliation_proven` stay false. A local pass may set
`local_security_status`. `local_soak_status` stays `not-run`. A local
isolation pass is not tenant isolation proof.

The matrix is bounded: 65 fixed operations, an 8 second request timeout, and
a 180 second probe-phase cap. There is no URL, method, payload, or target
argument. Synthetic gym data stays on the disposable stack.

## How to run it

Start from a host with no `gym-staging-rehearsal` containers or volumes. The
command rebuilds the rehearsal, then runs the fixed profile. Cleanup stays
explicit and uses the owner-only token file. The token is
`destroy-disposable-staging-rehearsal` and is never placed on the command line.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py security --repo .
```

Evidence is `.staging-rehearsal/evidence/security.json`, mode 0600. The
rehearsal seal for this command is `rehearsal-security.json`. This file does
not replace `endurance.json`, `pt-cash.json`, `pt.json`, `load.json`, or
`finance.json`.

## Transport

The coordinator is `manage.py staging_security_characterization`. It runs
inside a one-shot API container, requires `config.settings.staging_rehearsal`,
`STAGING_REHEARSAL=1`, and project `gym-staging-rehearsal`. Passwords, cookies,
and the BFF secret stay in that process.

Browser checks connect to private `caddy`, SNI `localhost`, and verify the
rehearsal CA. The BFF secret probes connect to private `api:8000` on the
Compose network, path `/api/v1/auth/login/` only. That port is not published.
Redirect cases that leave the fixed host are evaluated offline. Response
bodies are clipped at 16 KiB. Reconciliation is service-only.

Caddy metrics come from the private `caddy:2020/metrics` scrape.
`caddy_metrics_observed` records whether the scrape returned a Prometheus
document. A failed scrape stays unavailable and the 5xx delta stays null. An
observed zero requires that document. A positive 5xx delta fails the local
gate.

## Matrix

Edge and private probes run once. Both gyms then log in and create their own
rows. Cross-gym probes run only after both gyms have those rows.

| Scenario | Transport | Request | Expected |
| --- | --- | --- | --- |
| Staff API block | BFF/Caddy | `GET /api/v1/members/` | 404 |
| CSRF absent or mismatched | BFF | `POST /api/auth/login` | 403 `CSRF_FAILED` |
| Untrusted Origin | BFF | same login with `https://untrusted.example` | 403, origin not reflected |
| Missing session or invalid access cookie | BFF | `GET /api/operations/members` | 401 |
| Missing or forged BFF secret | private API | `POST /api/v1/auth/login/` | 403 `BFF_REQUIRED` |
| Fixed-host redirects | offline | external host, userinfo, or token query | rejected |
| Owner, manager, and trainer login | BFF | `POST /api/auth/login` | 200, Secure/HttpOnly/SameSite=Lax/Path=/ cookies, no token body |
| Expense draft and submit | BFF | owner and manager, bank transfer `1.00` | 201 then 200 |
| Cash open, check-in, PT sale | BFF | one of each per gym | 201, PT submit 200 |
| Cross-gym reads | BFF | member, expense, cash, check-in, PT sale | 404 |
| Daily report | BFF | own gym date | 200 and no other-gym marker |
| Cross-gym writes | BFF | member patch, check-in, expense approve, PT approve | 404 |
| Mass assignment | BFF | expense and member bodies with gym, status, owner, or approval fields | 400 |
| Self-approval | BFF | owner and manager approve their own expense; owner approves own PT sale | 403 |
| Trainer approval and role replace | BFF | trainer approve, trainer `PUT` membership roles with the owner role and `is_owner` | 403 |

Actors are the owner, the protected MANAGER template, and the TRAINER
template. The trainer is the lower-privilege actor. The manager is used for
self-approval, not for posting a payout.

## Reconciliation

Service-only counts run under each gym's row security context. A finished
pass expects 2 pending expenses, 0 approvals, 1 open cash session, 1
check-in, and 1 pending PT sale per gym. Audit rows must show the draft and
submit actions and zero approval posts. Member notes stay blank, member
status stays active, the owner flag stays on the owner only, and the trainer
role stays TRAINER. A partial run is fail. `data_corruption` stays false
unless stored rows exceed the successful creates or a protected field changed.
An early stop that still matches the seed baseline is `not-run`. Empty
evidence cannot pass.

## Uncovered

These classes are named and are not established. Transport: uncovered.

- The published member API under `/api/v1/member-auth`.
- receipt download and report CSV export. The daily report read is covered.
- Commission payout, booking mutation, and cash close.
- Unknown-account login sampling and edge or WAF rate limits.
- MFA, password reset, invitation guessing, file upload, and platform admin routes.
- Staff API routes other than auth on the private network. The BFF secret is
  required for auth and invitation acceptance.
- A live external redirect, a production WAF, and an independent penetration
  test or formal security review.

## Local gates

A local pass requires every planned probe to match its contract, no cancelled
operation, no credential in a response body, no other-gym marker in a 2xx
body, reconciliation to match, and `data_corruption` to be false. The probe
phase stays inside 180 seconds. When Caddy metrics are observed, the 5xx
delta is 0. When they are unavailable, the delta stays null and does not by
itself fail the run.

Phase 8 is not complete.
