# Phase 8 Increment 8 — Receipt allocation and cash close

This is a disposable local characterization on the Increment 6 rehearsal. It is
opt-in. Ordinary CI does not run it. Phase 8 is not complete.

The fixed profile is `receipt`. It measures receipt allocation and cash close.
It is one thread, at most 180 seconds, and a
fixed operation list. It is not an approved SLO. `load_test_proven`,
`soak_test_proven`, `production_capacity_proven`, `slo_proven`, and
`financial_reconciliation_proven` stay false. `local_soak_status` stays
`not-run`. A soak is not established. Production capacity is not established.
An SLO is not established. A load test is not established.

Evidence is `.staging-rehearsal/evidence/finance.json`, mode `0600`. That file
does not replace `load.json`. Increment 7 evidence stays in `load.json`.

## Command

```bash
python scripts/staging_rehearsal.py finance --repo .
```

There is no profile argument. Cleanup stays the explicit owner-only command
from Increment 6. The stack is fresh because `finance` runs the rehearsal
first and the rehearsal refuses existing project volumes.

## HTTP paths

These use Caddy and the Next.js BFF. Bodies, cookies, and tokens are not
stored.

| Step | Method and route | Expected result |
| --- | --- | --- |
| Owner and manager login | `POST /api/auth/login` | 200 |
| Open till | `POST /api/operations/cash-sessions/open` | 201, opening `0.00` |
| Cash draft | `POST /api/operations/payments` | 201, purpose `OTHER`, method `CASH`, amount `1.00` |
| Submit | `POST /api/operations/payments/{id}/submit` | 200 |
| Preparer approval | `POST /api/operations/payments/{id}/approve` | 403 |
| Manager approval | same route, different actor | 200. Receipt allocation is this command. |
| Approval replay | same idempotency key | stored 200, no second receipt |
| Receipt download | `GET /api/operations/receipts/{id}/download` | 200 HTML. The body is discarded. |
| Expected cash | `GET /api/operations/cash-sessions/{id}` | 200, status `OPEN`, expected closing `1.00` |
| Cash close | `POST /api/operations/cash-sessions/{id}/reconcile` | 200. Reconcile is the close command. |
| Close replay | same idempotency key | stored 200 |
| Close again | a new idempotency key | 409 |
| Cross-gym download, close, and approve | the other gym's ids | 404 |

Both gyms finish the fixed phase before any cross-gym probe. The fixed phase
is a single thread.

## Service-only

Gym provisioning, owner password acceptance, and manager invitation acceptance
are service-only. There is no browser step for those prerequisites. Database
reconciliation is service-only. It counts issued receipts, posted payments,
distinct receipt numbers, and closed tills. A pass requires one issued receipt
and one closed till per gym, cash sales `1.00`, actual closing `1.00`,
difference `0.00`, and a closed timestamp. Empty deltas are `not-run`. A
measured disagreement is `data_corruption`. That local field is not
`financial_reconciliation_proven`.

## Uncovered

- PT sale at `POST /api/v1/pt-sales/`. No trainer or package is seeded, and the
  approval chain is separate. Transport: uncovered.
- PT session. No session ledger is seeded. Transport: uncovered.
- PT payout. Commission payment is deferred with the PT sale. Transport: uncovered.
- PT report. An empty read would not characterize a payout. Transport: uncovered. Increment 9 records PT sale, session, payout, and report in `pt.json` and does not replace `finance.json`.
- Soak. No soak profile exists. Repeating a closed till is not a production
  soak. `local_soak_status` is `not-run`.

## Caddy

`caddy_metrics_observed` is false and `caddy_5xx_delta` is null when the
private metrics endpoint does not answer. That state is unavailable. It is not
recorded as zero. A positive measured delta fails the run.

## Local gate

`local_finance_status` passes only when every planned step completed, cross-gym
probes returned 404, reconciliation matched, no unexpected status or 5xx
occurred, the connection peak stayed within 0 through 32 of `max_connections`
40, and nothing was cancelled. Provisional HTTP success p95 stays at or below
8000 ms. That ceiling is not an approved SLO.

Phase 8 is not complete.
