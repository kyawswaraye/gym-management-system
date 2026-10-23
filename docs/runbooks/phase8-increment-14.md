# Phase 8 increment 14 — local tenant-isolation characterization

This increment adds an opt-in local tenant-isolation characterization on the
disposable two-gym rehearsal. It checks a fixed BFF cross-gym matrix and
runtime PostgreSQL forced-RLS checks that `staging_isolation_smoke` and
increment 12 only sample. It is project-authored local verification. It is
not independent tenant-isolation proof. It is not a penetration test, a
formal security review, or production proof. Phase 8 is not complete.

`penetration_test_proven`, `formal_review_proven`, `production_security_proven`,
`tenant_isolation_proven`, `load_test_proven`, `soak_test_proven`,
`production_capacity_proven`, `slo_proven`, and
`financial_reconciliation_proven` stay false. A local pass may set
`local_isolation_status`. `local_security_status`, `local_identity_status`,
and `local_soak_status` stay `not-run`. This file does not change the meaning
of increment 12 `security.json` or increment 13 `identity-security.json`.
`staging_isolation_smoke` remains the small startup gate.

The matrix is bounded: 44 fixed operations, an 8 second request timeout, and
a 180 second probe-phase cap. There is no URL, method, payload, table, or SQL
argument. Synthetic gym data stays on the disposable stack. Response bodies
are clipped at 16 KiB and are not stored. Isolation characterization
does not wait for ClamAV TCP. A local pass is not scanner, file-upload, or
production ClamAV proof.

## How to run it

Start from a host with no `gym-staging-rehearsal` containers or volumes. The
command rebuilds the rehearsal, then runs the fixed profile. Cleanup stays
explicit and uses the owner-only token file. The token is
`destroy-disposable-staging-rehearsal` and is never placed on the command line.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py isolation --repo .
```

Evidence is `.staging-rehearsal/evidence/isolation.json`, mode 0600, in the
mode 0700 evidence directory. The rehearsal seal for this command is
`rehearsal-isolation.json`. This file does not replace `identity-security.json`,
`security.json`, `endurance.json`, `pt-cash.json`, `pt.json`, `load.json`, or
`finance.json`. Cleanup removes rehearsal containers and volumes and keeps
the evidence files.

## Transport

The coordinator is `manage.py staging_isolation_characterization`. It runs
inside a one-shot API container, requires
`config.settings.staging_rehearsal`, `STAGING_REHEARSAL=1`, and project
`gym-staging-rehearsal`. Passwords and cookies stay in that process and are
blanked before exit.

Browser checks connect to private `caddy`, SNI `localhost`, and verify the
rehearsal CA. Runtime checks use the `gym_runtime` role with forced RLS.
Tenant context calls run inside `transaction.atomic()`. Unsafe raw SQL
mutation is not used. Redirects that leave `https://localhost:8443` are
rejected. Evidence stores counts and classifications, not gym IDs.

Caddy metrics use the same private scrape as increment 12.
`caddy_metrics_observed` records whether the scrape returned a Prometheus
document. A failed scrape stays unavailable and the 5xx delta stays null. An
observed zero requires that document. A positive 5xx delta fails the local
gate.

## Matrix

Six runtime probes run once. Nineteen gym probes then run on gym 0 and again on
gym 1. The operation cap is 44.

| Scenario | Transport | Request or check | Expected |
| --- | --- | --- | --- |
| `schema_denied` | runtime | `CREATE TABLE` | denied |
| `grant_denied` | runtime | `GRANT CREATE ON SCHEMA public` | denied |
| `missing_context_read` | runtime | empty gym context counts | zero tenant rows |
| `missing_context_write` | runtime | empty-context `GymSettings` update | zero rows |
| `rollback_context` | runtime | nested gym context then savepoint rollback | outer gym restored |
| `context_reuse` | runtime | new transaction after a committed context | empty context |
| `auth_login` | BFF | `POST /api/auth/login` | 200 session cookies |
| `members_list` | BFF | `GET /api/operations/members` | 200, no other-gym marker |
| `expenses_list` | BFF | `GET /api/operations/expenses` | 200, no other-gym marker |
| `pt_sales_list` | BFF | `GET /api/operations/pt-sales` | 200, no other-gym marker |
| `cash_list` | BFF | `GET /api/operations/cash-sessions` | 200, no other-gym marker |
| `audit_list` | BFF | `GET /api/operations/audit-logs?module=cashbook&limit=25` | 200, no other-gym marker |
| `report_daily` | BFF | `GET /api/operations/reports/daily?date=` | 200, no other-gym marker |
| `idor_member_read` | BFF | other gym member | 404 |
| `idor_expense_read` | BFF | other gym expense | 404 |
| `idor_pt_read` | BFF | other gym PT sale | 404 |
| `idor_cash_read` | BFF | other gym cash session | 404 |
| `idor_member_patch` | BFF | other gym member notes | 404 |
| `idor_expense_approve` | BFF | other gym expense approve | 404 |
| `rls_own_counts` | runtime | own member/finance/PT/file/settings counts | seed counts |
| `rls_cross_read` | runtime | other-gym filters | zero rows |
| `rls_cross_update` | runtime | other-gym expense update | zero rows |
| `rls_file_cross` | runtime | other-gym file metadata | hidden |
| `rls_audit_cross` | runtime | other-gym audit rows | zero rows |
| `rls_aggregate` | runtime | expense sum with other-gym filter | own 1.00, cross 0 |

Seed creates one member, pending expense, open cash session, check-in, pending
PT sale, and file-metadata row per gym through application services. That is
not an HTTP create matrix.

## Reconciliation

Service-only counts run under each gym's row security context. A finished pass
expects 1 member, 1 pending expense, 1 open cash session, 1 check-in, 1
pending PT sale, 1 file row, and 1 gym-settings row per gym. Cross-gym
filters, file rows, audit rows, and expense sums stay 0. Member notes stay
blank, member status stays active, and the owner flag stays on the owner.
A partial run is fail. `data_corruption` stays false unless stored rows exceed
the seed or a protected field changed. An early stop that still matches the
seed baseline is `not-run`. Empty evidence cannot pass.

## Uncovered

These classes are named and are not established. Transport: uncovered.

- `object_storage_key_guessing`. Direct MinIO or S3 object-key access is not
  probed.
- `file_upload_scan_download`. Upload, ClamAV scan, and stored-object download
  are not part of this matrix.
- `file_bff_download_url`. BFF file download-url is not probed. Seeded metadata
  has no stored object, so a 404 would not prove isolation. File RLS metadata
  is covered.
- `receipt_csv_export`. Receipt download and report CSV export stay uncovered.
  The daily report read is covered.
- `member_published_api`. Published member-auth isolation is not this matrix.
- `replica_read`. Replica or lagged-read isolation is not exercised.
- `bypassrls_superuser`. Table-owner, BYPASSRLS, and superuser paths are not
  claimed.
- `cache_http`. HTTP cache and Redis isolation are not this characterization.
- `retail_salary_inventory_booking`. Those resources are not seeded here.
- `platform_admin`. Platform administration routes are not probed.
- `raw_sql_mutation`. Unsafe raw SQL mutation that bypasses application
  invariants is refused.

## Local gates

A local pass requires every planned probe to match its contract, no cancelled
operation, no credential in a response body, no other-gym marker in a 2xx
body, reconciliation to match, and `data_corruption` to be false. The probe
phase stays inside 180 seconds. When Caddy metrics are observed, the 5xx
delta is 0. When they are unavailable, the delta stays null and does not by
itself fail the run. `tenant_isolation_proven` stays false.

Phase 8 is not complete.
