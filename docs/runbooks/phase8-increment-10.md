# Phase 8 Increment 10 — cash PT commission payout and till

This is a disposable local characterization on the Increment 6 rehearsal. It is
opt-in. Ordinary CI does not run it. Phase 8 is not complete.

The fixed profile is `pt-cash`. It measures one open cash session, one PT sale,
one attended PT session, one CASH commission payout, the expected closing, one
till reconcile, and one PT report read for each of two gyms. It is one thread,
at most 180 seconds, and a fixed operation list. It is not an approved SLO.
`load_test_proven`, `soak_test_proven`, `production_capacity_proven`,
`slo_proven`, and `financial_reconciliation_proven` stay false.
`local_soak_status` stays `not-run`. A soak is not established. Production
capacity is not established. An SLO is not established. A load test is not
established.

Evidence is `.staging-rehearsal/evidence/pt-cash.json`, mode `0600`. That file
does not replace `pt.json`, `load.json`, or `finance.json`. When a previous
rehearsal seal exists, this command keeps it at `rehearsal.json` and writes
the new rehearsal seal to `rehearsal-pt-cash.json`.

## Command

```bash
python scripts/staging_rehearsal.py pt-cash --repo .
```

There is no profile argument. Cleanup stays the explicit owner-only command
from Increment 6. The stack is fresh because `pt-cash` runs the rehearsal
first and the rehearsal refuses existing project volumes.

## HTTP paths

These use Caddy and the Next.js BFF. Bodies, cookies, and tokens are not
stored.

| Step | Method and route | Expected result |
| --- | --- | --- |
| Owner and manager login | `POST /api/auth/login` | 200 |
| Open till | `POST /api/operations/cash-sessions/open` | 201, opening `100.00` |
| PT sale | `POST /api/operations/pt-sales` | 201, draft, gross `100.00`, no payment |
| Submit | `POST /api/operations/pt-sales/{id}/submit` | 200, pending approval |
| Creator approval | `POST /api/operations/pt-sales/{id}/approve` | 403 |
| Manager approval | same route, different actor | 200, commission `10.00` accrued, one session granted |
| Approval replay | same idempotency key | stored 200 |
| Booking | `POST /api/operations/pt-bookings` | 201, booked against the grant |
| Attendance | `POST /api/operations/pt-bookings/{id}/attend` | 200, attended |
| Creator cash payout | `POST /api/operations/pt-commissions/{id}/record-payment` | 403 |
| Owner cash payout | same route, `CASH`, this gym's open cash session | 200, paid, outstanding `0.00`, cash movement `OUT` `10.00` |
| Payout replay | same idempotency key | stored 200, still one payout |
| Expected closing | `GET /api/operations/cash-sessions/{id}` | 200, open, expected `90.00`, cash expenses `10.00` |
| Reconcile | `POST /api/operations/cash-sessions/{id}/reconcile` | 200, closed, difference `0.00` |
| Reconcile replay | same idempotency key | stored 200 |
| Second close | new idempotency key | 409 |
| PT report | `GET /api/operations/reports/pt-sessions` | 200, attended sessions `1` |
| Other-gym sale, attendance, payout, cash session read, and close | same command routes | 404 |

## Service-only

Gym, owner, manager, trainer, member, one-session package, and trainer
assignment are service-only. They have no role in the measured HTTP counters.
ORM reconciliation is service-only. Per gym it requires one posted sale of
`100.00`, one paid commission of `10.00`, one posted `CASH` payout, one cash
outflow of `10.00`, one closed cash session opened at `100.00` with cash
expenses `10.00`, expected and actual closing `90.00`, and difference `0.00`,
plus one attended booking, one attended ledger row, one grant of one session,
and one posted PT commission expense. Same-gym links cover the sale, grant,
booking, commission, payout, expense, cash movement, and the closed till's
opener. A pass requires those exact rows and `gym_mismatch` `0`. Empty deltas
with no successful till open, cash payout, or close are `not-run`. An
unfinished plan is `fail`. `data_corruption` is true only when the finished
plan disagrees with those rows, or when `gym_mismatch` is already nonzero. A
short but consistent partial result is not corruption. That local field is
not `financial_reconciliation_proven`.

## Caddy

The private metrics listener stays on container port `2020` and is not
published to the host. The rehearsal Caddyfile now enables the same global
`metrics` option the production file uses, so the private `/metrics` handler
can emit Caddy series. `caddy_metrics_observed` is false and
`caddy_5xx_delta` is null when that endpoint does not answer. That state is
unavailable. It is not recorded as zero. A successful Prometheus document with
no 5xx series is an observed zero. A positive measured delta fails the run.
`local_pt_cash_status` does not treat unavailable metrics as a pass-blocking
zero.

## Uncovered

- Soak. No soak profile exists. Transport: uncovered.
  `local_pt_cash_status` does not claim one. `local_soak_status` is `not-run`.

## Local gate

`local_pt_cash_status` passes only when every planned step completed,
cross-gym probes returned 404, reconciliation matched, no unexpected status
or 5xx occurred, the connection peak stayed within 0 through 32 of
`max_connections` 40, and nothing was cancelled. Provisional HTTP success p95
stays at or below 8000 ms. That ceiling is not an approved SLO.

Phase 8 is not complete.
