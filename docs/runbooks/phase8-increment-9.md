# Phase 8 Increment 9 — PT sale, session, payout, and report

This is a disposable local characterization on the Increment 6 rehearsal. It is
opt-in. Ordinary CI does not run it. Phase 8 is not complete.

The fixed profile is `pt`. It measures one PT sale, one attended PT session,
one PT payout, and one PT report read for each of two gyms. It is one
thread, at most 180 seconds, and a fixed operation list. It is not an approved
SLO. `load_test_proven`, `soak_test_proven`, `production_capacity_proven`,
`slo_proven`, and `financial_reconciliation_proven` stay false.
`local_soak_status` stays `not-run`. A soak is not established. Production
capacity is not established. An SLO is not established. A load test is not
established.

Evidence is `.staging-rehearsal/evidence/pt.json`, mode `0600`. That file does
not replace `load.json` or `finance.json`. When a previous rehearsal seal
exists, this command keeps it at `rehearsal.json` and writes the new rehearsal
seal to `rehearsal-pt.json`.

## Command

```bash
python scripts/staging_rehearsal.py pt --repo .
```

There is no profile argument. Cleanup stays the explicit owner-only command
from Increment 6. The stack is fresh because `pt` runs the rehearsal first and
the rehearsal refuses existing project volumes.

## HTTP paths

These use Caddy and the Next.js BFF. Bodies, cookies, and tokens are not
stored.

| Step | Method and route | Expected result |
| --- | --- | --- |
| Owner and manager login | `POST /api/auth/login` | 200 |
| PT sale | `POST /api/operations/pt-sales` | 201, draft, gross `100.00`, no payment |
| Submit | `POST /api/operations/pt-sales/{id}/submit` | 200, pending approval |
| Creator approval | `POST /api/operations/pt-sales/{id}/approve` | 403 |
| Manager approval | same route, different actor | 200, commission `10.00` accrued, one session granted |
| Approval replay | same idempotency key | stored 200 |
| Booking | `POST /api/operations/pt-bookings` | 201, booked against the grant |
| Attendance | `POST /api/operations/pt-bookings/{id}/attend` | 200, attended |
| Creator payout | `POST /api/operations/pt-commissions/{id}/record-payment` | 403 |
| Owner payout | same route, different actor | 200, `BANK_TRANSFER`, commission paid, outstanding `0.00` |
| Payout replay | same idempotency key | stored 200 |
| PT report | `GET /api/operations/reports/pt-sessions` | 200, attended sessions `1` |
| Other-gym sale, attendance, and payout | same command routes | 404 |

## Service-only

Gym, owner, manager, trainer, member, one-session package, and trainer
assignment are service-only. They have no role in the measured HTTP counters.
ORM reconciliation is service-only. It counts one posted sale, one paid
commission of `10.00`, one bank-transfer payout, one attended booking, one
attended ledger row, one grant of one session, and one posted PT commission
expense per gym. `gym_mismatch` counts same-gym link failures under RLS. A
posted sale must see its member, trainer, and any named package in that gym.
A grant must see its member and package, and its sale when one is named. An
attended booking must see its trainer, member, and any named grant. A paid
commission must see its sale and trainer. A payout must see its commission
and expense, and its cash session when one is named. An attended ledger row
must see its grant and booking. A posted commission expense must see its
category. A foreign key that is not visible in the owner gym counts. A pass
requires the exact final rows and `gym_mismatch` `0`. Empty deltas with no
successful sale, attendance, or payout are `not-run`. An unfinished plan is
`fail`. `data_corruption` is true only when the finished plan disagrees with
those rows, or when `gym_mismatch` is already nonzero. A short but consistent
partial result is not corruption. That local field is not
`financial_reconciliation_proven`.

The integrated command still waits for a ClamAV TCP `PONG` before
characterization. That gate stays. On a 4 GiB Docker VM, clamd can die after
the rest of the stack starts, at the production 1152m limit. That repeated
failure is an environment blocker. It does not replace a passing `pt.json`,
and it does not mean the integrated command passed.

## Uncovered

- Cash commission payout. This profile pays by `BANK_TRANSFER` and does not
  open a till. Transport: uncovered. Increment 10 records the cash payout in
  `pt-cash.json` and does not replace this file.
- Soak. No soak profile exists. `local_pt_status` does not claim one.
  `local_soak_status` is `not-run`.

## Caddy

`caddy_metrics_observed` is false and `caddy_5xx_delta` is null when the
private metrics endpoint does not answer. That state is unavailable. It is not
recorded as zero. A positive measured delta fails the run.

## Local gate

`local_pt_status` passes only when every planned step completed, cross-gym
probes returned 404, reconciliation matched, no unexpected status or 5xx
occurred, the connection peak stayed within 0 through 32 of `max_connections`
40, and nothing was cancelled. Provisional HTTP success p95 stays at or below
8000 ms. That ceiling is not an approved SLO.

Phase 8 is not complete.
