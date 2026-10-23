# Phase 8 increment 11 — local bounded endurance

This increment adds an opt-in local bounded endurance characterization on the
disposable two-gym rehearsal. It is a fixed-duration paced HTTP profile. It
is not a production soak. Phase 8 is not complete.

`load_test_proven`, `soak_test_proven`, `production_capacity_proven`,
`slo_proven`, and `financial_reconciliation_proven` stay false. A local pass
may set `local_endurance_status`. `local_soak_status` stays `not-run`. The
existing evidence schema uses `local_soak_status` for a soak claim, and this
profile does not redefine that field as a local-only pass. The p95 ceiling is
provisional. It is not an approved SLO.

Elapsed minutes on a 4 GiB Docker VM are not production-like soak evidence.
The production planning target is 8 GiB. This profile runs one paced phase of
about two to three minutes with two workers and synthetic reads and drafts.
A production soak would cover a business day, the production host budget, a
production traffic mix, and an approved SLO. None of those are measured here.

## How to run it

Start from a host with no `gym-staging-rehearsal` containers or volumes. The
command rebuilds the rehearsal, then runs the fixed profile. Cleanup stays
explicit and uses the owner-only token file. The token is
`destroy-disposable-staging-rehearsal` and is never placed on the command line.
There is no URL, method, payload, worker, or duration argument.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py endurance --repo .
```

The HTTP phase hard cap is 180 seconds. Two workers pace loop starts 2.5
seconds apart. The paced loop is 110 operations, so the measured phase is
about 137 seconds when requests stay inside the pace. Setup logins and the
one-time creates run before that phase and are not paced. The request timeout
is 8 seconds. The operation cap is 126. A deadline cancels work that has not
started. The queue is bounded to the loop length.

Evidence is `.staging-rehearsal/evidence/endurance.json`, mode 0600. The
rehearsal seal for this command is `rehearsal-endurance.json`. This file does
not replace `pt-cash.json`, `pt.json`, `load.json`, or `finance.json`.

## Transport

The coordinator is `manage.py staging_endurance_characterization`. It reuses
the rehearsal HTTP client. It runs inside a one-shot API container, requires
`config.settings.staging_rehearsal`, `STAGING_REHEARSAL=1`, and project
`gym-staging-rehearsal`. Passwords and cookies stay in that process.

HTTP connects to private `caddy`, SNI `localhost`, and verifies the rehearsal
CA. Response bodies are clipped at 16 KiB. Reconciliation is service-only.

Caddy metrics come from the private `caddy:2020/metrics` scrape. A failed
scrape or a body that is not a Prometheus document stays unavailable and the
5xx delta stays null. An observed zero requires a real Prometheus document.
`caddy_metrics_observed` records that distinction.

Container cgroup memory, cgroup CPU usage, and container filesystem free bytes
are recorded only when those local files can be read. A missing sample stays
null. It is not stored as zero. The Docker socket is not mounted, so Docker
API CPU, memory, and disk samples stay unavailable. Those readings are
provisional. They are not an approved SLO.

## Schedule

Both gyms are interleaved. Setup, one thread, gym 0 then gym 1 for each step:
owner login, one cash open, its idempotent replay, one expense draft, its
replay, a draft read, one check-in create, and its replay. The loop then
repeats five times. Each repeat runs the following for gym 0 and gym 1:
dashboard, daily report, PT report, check-in list, a new check-in, a new
expense draft, the setup draft read, the cash list, and cross-gym check-in,
expense, and cash probes. Cross-gym probes therefore run throughout the
phase, not only at the end. The schedule identity is the multiset of phase,
scenario, gym, and ordinal. Worker completion order may differ.

| Scenario | Request | Expected |
| --- | --- | --- |
| Owner login | `POST /api/auth/login` | 200 |
| Dashboard | `GET /api/operations/dashboard?range=today` | 200 |
| Daily report | `GET /api/operations/reports/daily?date=<gym-local-today>` | 200 |
| PT report | `GET /api/operations/reports/pt-sessions?date_from=<today>&date_to=<today>` | 200 |
| Check-in create | `POST /api/operations/check-ins` | 201 |
| Check-in replay | same key and body as the first create | 200 |
| Check-in list | `GET /api/operations/check-ins?limit=5` | 200 |
| Expense draft | `POST /api/operations/expenses` | 201 |
| Expense replay | same key and body as the setup draft | 200 or the stored 201 |
| Expense read | `GET /api/operations/expenses/<setup-id>` | 200 |
| Cash open | `POST /api/operations/cash-sessions/open` register `ENDUR` opening `0.00` | 201 |
| Cash replay | same open key and body | 200 or the stored 201 |
| Cash read | `GET /api/operations/cash-sessions?limit=5` | 200 |
| Cross-gym check-in, expense, and cash | the other gym's member, expense, or cash id | 404 |

Authentication-failure sampling is omitted. Repeating unknown logins on this
process can throttle the owner sessions that the rest of the profile needs.
That sampling transport is uncovered.

## Reconciliation

Service-only counts run under the gym's row security context. Each gym is
expected to gain 6 check-ins, 6 expense drafts, and 1 open cash session when
every unique write succeeds. The comparison is against the writes that
succeeded, not against an empty zero. Same-gym links are the check-in member,
the expense category, and the cash session opener. A partial or cancelled run
is fail. `data_corruption` stays false unless the stored rows disagree with
the completed writes or a same-gym link is already broken. An early stop whose
writes and stored rows are all zero is `not-run`. `local_financial_reconciliation_status`
may pass only for a finished matching run. That local field is not
`financial_reconciliation_proven`.

## Uncovered

These conditions are named and are not established. Transport: uncovered.

- Production-like soak: multi-hour duration, an 8 GiB production host, a
  production traffic mix, and an approved SLO. A few paced minutes on a 4 GiB
  VM do not establish a soak.
- Authentication failure and edge or WAF rate limits.
- Repeated cash close, commission payout, and receipt issuance. Those
  one-time operations stay in `finance.json`, `pt.json`, and `pt-cash.json`.
- PT sale creation. The PT report is a stable authenticated read and may be
  empty. PT payout evidence stays in `pt.json` and `pt-cash.json`.
- Host Docker API samples. The socket is not mounted, so those samples stay
  unavailable.

## Local gates

A local pass requires the paced phase to finish at or above 120 seconds and
inside the 180 second cap, both workers to have been active, every planned
step to complete, no cancelled operation, no unexpected status or 5xx, no
credential exposure, no cross-gym leakage, reconciliation to match, and
`data_corruption` to be false. PostgreSQL `numbackends` peak stays within 0
through 32 and `max_connections` is 40. When Caddy metrics are observed, the
5xx delta is 0. When they are unavailable, the delta stays null and does not
by itself fail the run. HTTP success p95 at or below 8000 ms is a provisional
ceiling, not an approved SLO.

Phase 8 is not complete.
