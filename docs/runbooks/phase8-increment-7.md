# Phase 8 increment 7 — local load characterization

This increment adds an opt-in, disposable load characterization on top of the
Increment 6 staging rehearsal. It sends a fixed short or bounded profile
through the rehearsal Caddy origin. It does not run in ordinary CI. It does
not deploy, push to GHCR, contact a provider, or prove production capacity.
Phase 8 is not complete.

`load_test_proven`, `soak_test_proven`, `production_capacity_proven`,
`slo_proven`, `production_deploy_proven`, `tenant_isolation_proven`, and
`financial_reconciliation_proven` stay false. A local pass may set only
`local_load_status`, `local_isolation_status`, and
`local_financial_reconciliation_status`. The p95 ceiling is provisional.
It is not an approved SLO. The host used for a local run has a 4 GiB Docker
VM. The production planning target is 8 GiB. arm64 or amd64-emulated ClamAV
on that VM cannot validate the production budget.

## How to run it

Start from a host with no `gym-staging-rehearsal` containers or volumes. The
command rebuilds the Increment 6 stack, runs its migrations and smoke, then
the load profile. It leaves the stack up. Cleanup stays explicit and uses the
same owner-only token file as Increment 6. The token is
`destroy-disposable-staging-rehearsal` and is never placed on the command line.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py load --repo . --profile short
```

`--profile` accepts only `short` or `bounded`. `short` is the profile for
this 4 GiB VM: 300 second safety cap, 2 workers, 20 loop operations, 8 second
request timeout, and 12 authentication failures. `bounded` raises that to
540 seconds, 4 workers, and 80 loop operations. Both stay inside the hard
caps: 10 minutes, 16 workers, 10,000 operations, and 10 second timeouts.
There is no URL, method, or payload argument.

Evidence is `.staging-rehearsal/evidence/load.json`, mode 0600, checksum-sealed.
Cleanup retains it:

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py cleanup --repo . \
  --confirmation-file /path/to/token
```

## Transport

The coordinator is `manage.py staging_load_characterization`. It runs inside
a one-shot API container on the rehearsal network and refuses every settings
module other than `config.settings.staging_rehearsal`. It also requires
`STAGING_REHEARSAL=1` and project `gym-staging-rehearsal`. Passwords, access
cookies, and the invitation token stay in that process. They are not command
arguments, logs, or evidence.

HTTP calls connect to the private `caddy` address, SNI `localhost`, and verify
the rehearsal CA. One redirect is followed only when it is a relative path or
`https://localhost`. Response bodies are clipped at 16 KiB and discarded after
classification. Reconciliation counts are service-only ORM reads. Their time
is not mixed into HTTP latency.

## Covered paths

Every authenticated call below is real HTTP through Caddy and the Next.js BFF,
with the gym cookie jar captured at login. Cross-gym probes send the other
gym's identifier and expect HTTP 404. A 200 or 201 that contains the other
gym's marker fails the run.

| Scenario | Transport | Request |
| --- | --- | --- |
| Public login document | HTTP | `GET /login` |
| Staff API blocked on the public origin | HTTP | `POST /api/v1/auth/login/` with `{}`. Caddy returns 404 `Not found`. |
| Unknown member login | HTTP | One `POST /api/v1/member-auth/login/`. 400, 401, 403, 405, 422, and 429 are expected. |
| Browser authentication failure | HTTP | Twelve `POST /api/auth/login` calls for one unknown synthetic account. 401 and 429 are expected. `auth_rate_limit_observed` records whether a 429 was observed. A missing 429 does not fail the run. |
| Browser owner login | HTTP | `POST /api/auth/login` for each synthetic gym. Tokens stay in memory. |
| Dashboard read | HTTP | `GET /api/operations/dashboard?range=today` |
| Daily report read | HTTP | `GET /api/operations/reports/daily?date=<gym-local-today>` |
| Check-in create and replay | HTTP | `POST /api/operations/check-ins` with a unique idempotency key. Create expects 201. Replay of that same key and body expects 200. Eligibility is the snapshot on the create response. There is no separate eligibility URL. |
| Check-in list | HTTP | `GET /api/operations/check-ins?limit=5` |
| Expense draft | HTTP | `POST /api/operations/expenses` with `Idempotency-Key`, then the same replay, then `GET /api/operations/expenses/<id>`. The draft uses category `RENT`, amount `1.00`, currency `THB`, and method `CASH`. It is not approved and does not post cash. |
| Cash session | HTTP | `POST /api/operations/cash-sessions/open` once per gym, the same replay, and `GET /api/operations/cash-sessions?limit=5`. |
| Cross-gym check-in, expense, and cash | HTTP | The other gym's member, expense, and cash identifiers. Expected status is 404. |

Seed data is service-only: two gyms, owner invitations accepted in process,
one zero-price plan, and one active membership per check-in create. Those
calls are not reported as HTTP latency.

Reconciliation is service-only. It compares per-gym expense, check-in, and
cash-session row deltas with the count of unique HTTP create successes.
`local_financial_reconciliation_status` passes only when every planned
financial step succeeded, the planned expense, check-in, and cash creates all
succeeded, and those deltas match. An early stop whose deltas are both zero
is `not-run`, not a pass. `data_corruption` is true only when the measured
counts disagree. That local field is not `financial_reconciliation_proven`.

## Coverage gaps

These paths are named in the evidence and are not called. Each row below records transport `uncovered`.

- Receipt issuance follows payment posting, and this profile does not post a payment. The receipt issuance transport is uncovered. Increment 8 records receipt allocation and cash close in `finance.json` and does not replace this file.
- Receipt download at `GET /api/operations/receipts/{id}/download/`. No receipt exists. Transport: uncovered.
- PT sale or session at `POST /api/v1/pt-sales/`. No trainer or package is seeded. Transport: uncovered. Increment 9 records PT sale, session, payout, and report in `pt.json` and does not replace this file.
- PT report at `GET /api/operations/reports/pt-sessions/`. An empty read would not characterize PT activity. Transport: uncovered.

## Local gates

A local pass requires all of the following. They are characterization gates,
not approved budgets or SLOs.

- `cross_gym_leakage` is 0 and every cross-gym scenario returned an expected 404. Fixed-phase expense and cash probes run only after both gyms have created those records, because that phase is a single thread.
- `credential_exposure` is 0.
- `unexpected_errors` is 0 and `api_unexpected_5xx` is 0.
- Caddy 5xx delta is 0 when the private metrics endpoint answers. If it does not answer, the delta is null and does not by itself fail the run.
- Reconciliation matches after the planned financial creates completed, and `data_corruption` is false.
- PostgreSQL `numbackends` peak is an integer from 0 through 32 and `max_connections` is 40.
- No operation was cancelled and every planned step completed.
- Authentication-failure responses are measured. HTTP 401 and 429 stay expected. The application throttle is per-process LocMem, the anonymous limit is 30/min, and Gunicorn uses 2 workers, so a 429 is not deterministic. Edge and WAF rate control remain unproven.
- HTTP success p95 is at or below the provisional ceiling of 8000 ms.

Expected 401, 404, 409, 422, and 429 responses stay in `expected_rejections`
when the scenario lists them. They are not system errors. A 5xx is always
unexpected. Authentication failures use one unknown synthetic account. They
are not a brute-force of a real credential.

## What stays unproven

- No production deploy, GHCR push, release attestation, or protected environment is proven.
- No soak is proven. Production capacity is not established. An SLO is not established. `load_test_proven` stays false.
- Local isolation status is not `tenant_isolation_proven`.
- Local financial reconciliation status is not `financial_reconciliation_proven`.
- The 4 GiB VM result does not validate the 8 GiB production planning budget.
- Application throttle behavior is not edge or WAF rate control. `auth_rate_limit_observed` is a local observation only.

Phase 8 is not complete.
