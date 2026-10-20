# Gym Management Platform

Multi-tenant gym management SaaS under phased development.

Phase 1A (security foundation) and Phase 1B (Super Admin provisioning and gym
prerequisites) are complete. The Phase 1B gate closed on 2026-10-17 on the
local CI-equivalent evidence in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).
CI still enforces those check classes on push and pull request.

Phase 2 implementation is complete as of 2026-10-17. Staff can manage
members, membership plans, and memberships inside one gym, including the full
member profile, private profile photos, the member summary, nested payment
history, expiry buckets, and membership-expiry scheduling and delivery
commands. `docs/DEVELOPMENT_PLAN.md` remains the immutable phase specification
and was not edited for this closure. Manual check-in and check-out, including
nested member check-in history and the member summary attendance facts, are
implemented. Staff can issue a rotating onsite QR challenge, and an
authenticated member can redeem it at `POST /api/v1/member/check-ins/scan/`.
The generic staff scan path is not published. An owner can enroll, rotate,
and revoke a display device, and that device can issue its own rotating
challenge. An owner can enroll an NFC or biometric reader, and that reader
can record attendance only through the device ingestion route. No
vendor-specific adapter is implemented, and no biometric template, raw
capture, or vendor secret is stored. Vendor-specific integration remains
optional. Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. Public member identity linking, multi-gym member selection, and member MFA remain deferred and are
not prerequisites for the already-linked member QR path. ADRs 0011 and 0012
approve bare React Native CLI and FCM. Phase 7 now implements the published
member-self, announcement, notification, device, and profile-file routes plus
the Android and iOS member app. Live FCM/APNs proof, release signing, and
app-store submission remain production release gates.

Phase 7 code and local test gates are complete as of 2026-10-21. Backend Ruff
check passed, and Ruff format reported 368 files already formatted. mypy found
no issues in 238 source files. `makemigrations --check --dry-run` reported no
changes, and the migration graph has no conflicts. Spectacular
`--validate --fail-on-warn` was exported twice; both files byte-matched
`contracts/openapi.v1.json` (SHA-256
`1cca6b96375a6f78f417dad4654c1ad74b40e87dba337adc2d903e30a35710d5`).
Non-PostgreSQL pytest passed 367 tests and deselected 333 in 49.14s. The
PostgreSQL suite passed 333 tests and deselected 367 in 452.74s. On Node
24.20.0, web format, lint, typecheck, and the production build passed, and
Vitest passed 70 files and 355 tests. Two generator runs wrote identical web
and mobile TypeScript (SHA-256
`c1657d72cbe2748efbc27f55c1981e486ae0989e4c0ebba18e54889b850051b1`).
`git diff --check` was clean. Mobile format, lint, strict types, unit tests,
the member-journey E2E, the Android debug build, and the unsigned iOS
simulator build were already green and were not re-run in this gate. This is
code and test closure. Live Firebase and APNs credentials, release signing,
store submission, and a production device proof remain open. Production
deployment is not complete.

Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008.
Phase 3 closure evidence: backend checks passed; 225 non-PostgreSQL unit
tests passed; the full PostgreSQL security and integration suite passed 171
tests before the new check-in plan test, and that focused plan test passed
separately; web format, lint, typecheck, build, and 263 component and unit
tests passed earlier, and focused static checks passed after the end-to-end
spec change; a clean end-to-end reset passed 4 Playwright specs, including
member authentication, a rotating onsite challenge, member QR scan, staff
front desk, history, and summary, then member logout and refresh replay
denial. The peak query-plan baseline passed and is documented in
`docs/DATABASE.md`. It is not a production load test.

Phase 4 increment 1, the finance foundation under ADR 0009, is implemented.
`docs/DEVELOPMENT_PLAN.md` section 15 item 15 is resolved there. The increment
adds exact decimal money, encrypted idempotency claims, transactional receipt
sequences, and the `expense_categories.manage` permission. No payment,
receipt, expense, inventory, retail, or cash HTTP route is published.
Increment 1 gate evidence: backend ruff, format, and mypy passed and
migrations are in sync; 252 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 189 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI regeneration did not change the published
contract.
Phase 4 increment 2 publishes the cash-book API: open, manual adjustment,
reconcile/close, and the session and transaction lists. A membership that
holds Reception can view, use, and close only a session it opened, even when
it also holds another role, and it cannot adjust. Until an Owner-configurable
policy exists, only the protected Owner role may adjust. Movements are
append-only. The cash-book staff page is delivered in increment 9. Notification HTTP routes stay
unpublished for later phases.
Increment 2 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 255 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 194 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
six cash routes and the command `429` responses. A second schema export
matched that file. The local `make check` contract step still reports those
files because this increment is not committed.
Phase 4 increment 3 publishes payment drafts, submit, approve, reject, cancel,
receipt list, detail, and download, and daily sales. Void, refund, and receipt
void stay unpublished. Approval requires a different active protected Manager
or Owner, and a cash approval must use an open session. Legacy posted cash
rows are marked `legacy_unlinked_cash` instead of receiving an invented cash
movement. The payment and receipt staff page is delivered in increment 9.
Increment 3 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 258 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 216 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
payment, receipt, and daily-sales routes. A second schema export matched the
working tree. The local `make check` contract step still reports those files
because this increment is not committed.
Phase 4 increment 4 publishes expense-category list/create/update and expense
list/create/detail/update/submit/approve/reject/cancel. Expense void stays
unpublished. Protected system categories stay immutable. Custom category
changes require `expense_categories.manage`. Cash approval posts one `OUT`
expense movement on an open same-gym session. The expense staff page is delivered in increment 9.
Increment 4 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 263 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 224 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
expense-category and expense command routes. A second schema export matched the
working tree. The local `make check` contract step still reports those files
because this increment is not committed.
Phase 4 increment 5 publishes salary-period list/create/detail/update, draft
line replacement, and submit/approve/reject/cancel. Salary void, inventory,
retail, and payment/receipt/expense void/refund stay unpublished. Approval
posts one protected staff-salary expense per line and one cash-out movement
per cash line, or rolls the whole period back. Reception sees only its own
non-posted periods. The salary staff page is delivered in increment 9.
Increment 5 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 267 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 229 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
salary-period routes. A second schema export matched the working tree. The
local `make check` contract step still reports those files because this
increment is not committed.
Phase 4 increment 6 publishes product-category list and create, product list,
create, detail, and update, low-stock, stock-movement list, and manual stock
adjustment. Category update stayed unpublished in that increment. Returns, the
inventory page, and void/refund routes were published later. Quantity on hand is the sum of append-only movements.
Adjustment snapshots current unit cost and does not compute a weighted
average. Catalog and stock commands use the finance entitlement.
The inventory staff page is delivered in increment 9.
Phase 4 increment 7 publishes retail-sale list, create, detail, update,
submit, approve, reject, and cancel. Increment 8C later publishes partial returns. The retail staff page, including full void, is delivered in increment 9 and exposes that return. Approval posts one sale movement per line, one product
payment, one receipt, and one cash-in movement when the method is cash.
Quantity times money rounds half up to minor units. Daily product metrics
come from posted retail lines.
Phase 4 increment 8B publishes salary-period void and full retail-sale void.
Increment 8C publishes partial retail returns. A cash correction names an open
destination session and does not reopen the original till. Product payment
corrections are rejected so posted retail stock and receipts stay aligned.
A legacy unlinked cash void does not invent a movement. Closed sessions are
not reopened. A correction requires the exact live permission, the finance
entitlement, and an active same-gym membership. The creator, submitter, or
receipt issuer may correct, including through a custom role that holds that
permission. Approval and rejection still require a different protected Manager
or Owner. Phase 4 increment 9 delivers the tenant staff pages for the published cash-book, payment, receipt, expense, salary, inventory, and full retail-void workflows. Navigation requires the matching view permission and the finance entitlement. Cash approval and cash correction select an open session the server returns for that caller. Product payment corrections stay on the retail return or full void. The retail page exposes a partial return for a posted sale. Phase 4 implementation is complete as of 2026-10-18 against the code and test gates. Manager finance and security evidence review is complete. Production deployment is not complete. [ADR 0010](docs/adr/0010-production-provider-readiness.md) selects Resend and AWS S3 `ap-southeast-1` and records password recovery. The Resend client, password recovery, AWS virtual-hosted addressing, and ClamAV production fragment are implemented. The live Resend, AWS, and ClamAV proofs remain open. Legal retention stays draft-only.
Increment 8C gate evidence: backend ruff, format, and mypy passed and migrations stayed in sync. 293 non-PostgreSQL backend tests passed and 252 were deselected. The PostgreSQL suite passed 252 tests and 293 were deselected, with zero failures. Web format, lint, typecheck, and build passed. Vitest passed 51 files and 288 tests. OpenAPI export and TypeScript generation were each run twice, and the second run matched the first. `git diff --exit-code` on the contract files still fails because this increment is not committed. `make e2e` passed 6 specs twice in a row, including the finance smoke and the manager partial-return journey. Each API start drops and recreates only `gym_platform_e2e`.
Phase 5 Increment 1 publishes the trainer directory and
trainer-assignment list, create, detail, end, and cancel routes. Assignment
overlap is protected by PostgreSQL, and `is_pt_client` is now derived from an
effective same-gym active assignment. A protected Trainer can see only
currently assigned members through the published member, membership,
check-in, and profile-photo surfaces and cannot create an assignment to widen
that scope. Phase 5 Increment 2 publishes PT service-package list and create
with server-owned currency and immutable definitions. Class packages remain
closed until ClassType exists. Phase 5 Increment 3 publishes PT-sale draft
list, create, detail, update, and cancellation with assignment scope,
idempotency, and commission previews. Phase 5 Increment 4 publishes an
optional sale-owned PT payment draft, submit, and Manager/Owner separate-actor
approve and reject. Approval atomically posts the optional payment, receipt,
and cash movement and exactly one immutable ACCRUED commission. Approval with
no linked payment remains valid and creates no receipt or cash movement.
Generic payment commands cannot mutate a sale-owned payment. Increment 5
publishes commission list, detail, and record-payment, including partial
payout, the protected commission expense, and optional cash out. Payout
reversal is an internal helper for a future sale void and has no public route.
Sale void, member package grants, the session ledger, bookings, classes,
and schedules were unpublished in Increment 5. Later increments publish those
routes, class types, schedules, occurrences, class and PT bookings, and
trainer scan. The staff and trainer pages and browser E2E are published.
Increments 1–5 did not by themselves close Phase 5. Production deployment is
not complete.
Phase 5 Increments 1–3 historical gate evidence: backend Ruff, format, mypy on 177 files,
and migration drift checks passed. The full non-PostgreSQL backend suite
passed 320 tests, and the full real-PostgreSQL suite passed 266 tests. Web
format, lint, typecheck, and build passed; Vitest passed 53 files and 296
tests. A fresh OpenAPI export validated with zero warnings and errors and
matched the committed contract. No browser E2E was added because these
increments publish backend APIs without a web workflow.
Phase 5 Increments 1–4 historical Increment 4 gate evidence: backend Ruff and format passed; mypy passed on 179 source files; migration drift was clean. The non-PostgreSQL suite passed 321 tests and 269 were deselected. The real PostgreSQL suite passed 269 tests and 321 were deselected. Web format, lint, typecheck, and build passed using Node 24.20.0. Vitest passed 53 files and 296 tests. OpenAPI spectacular validation with `--fail-on-warn` passed, and the fresh output matched the committed `contracts/openapi.v1.json`. IDE lints and `git diff --check` were clean. No E2E was run because Increment 4 is backend/API only and no new web workflow was published. Phase 5 remained open after Increment 4.
Phase 5 code and test gate is complete as of 2026-10-19. Full code scope is
complete: backend PT, classes, and trainer scan; staff and trainer web; and
browser E2E. Linked-member self-service remains Phase 7. This is code and test
gate closure, not production deployment. [ADR 0010](docs/adr/0010-production-provider-readiness.md)
live Resend, AWS, and ClamAV provider proofs remain open. Phase 6, mobile,
production readiness, and provider closure are not claimed.
`make backend-check`: Ruff passed; format 312 files; mypy 199 source files;
migration drift none. `make backend-test`: 334 passed, 298 deselected. Final
serial `make backend-test-pg`: 298 passed, 334 deselected, 398.50s. Node
24.20.0 web format, lint, typecheck, and build passed; the build has 29
routes; Vitest 61 files / 326 tests. A fresh `spectacular --validate
--fail-on-warn` temporary export byte-matched `contracts/openapi.v1.json`;
generated types are current. A clean full `make e2e` passed 16 tests in 5.2m,
including 10 Phase 5 browser tests (assignments/catalogs; Reception submit +
separate Owner approval cash sale; grant+commission; two payouts; consumed
void rollback + untouched refund/reconciliation; class
schedule/book/attendance/no-show/cutoff; trainer commission scope and QR scan
assigned/unassigned/generic-route absence). A formal security review reported
no medium-or-higher finding in its reviewed slice but was partial, and that
partial review is not full Phase 5 coverage. A targeted GPT-5.6 Phase 5 audit
found and fixed trainer finance bypasses, premature attendance and no-show,
payout date bounds, and trainer scan throttling. Final targeted verification
found no remaining medium-or-higher issue in the audited Phase 5 scope. IDE
lints and `git diff --check` were clean.
Phase 6 Increment 1 is gate-clean as of 2026-10-19. It publishes canonical
`GET /api/v1/dashboard/` and the tenant dashboard page. `reports.view` returns
the operational block. `reports.financial` adds the full financial block;
otherwise `financial` is null. Trainer finance closure remains. Presets are
`today`, `this_week`, `this_month`, `last_month`, and `this_year`, plus a
custom range of at most 366 inclusive days. The response states the gym
timezone and currency. Financial totals follow posted ledger correction-date
semantics. Six mutually exclusive revenue buckets include `CLASS`. Pending
approvals are included. Overdue balance is the membership-only authoritative
unpaid or partial amount; this increment does not invent PT or class debt. Ten
protected expense categories are zero-filled in canonical order, with total
expenses and net profit. Product rows use immutable snapshots net of a full
void and partial returns. Top products are the first 10 after ranking. There
are no migrations or report tables. Browser E2E is `web/e2e/phase6.spec.ts`.
Cash-book, profit/loss, membership/expiry, check-in/occupancy, PT/class,
product/stock, inventory, and trainer-scoped dashboards, plus exports, remain.
At that increment close, Phase 6 was not complete. Production deployment is not complete, and the
[ADR 0010](docs/adr/0010-production-provider-readiness.md) live Resend, AWS, and
ClamAV proofs remain open. Gate evidence:
`make backend-check` Ruff passed, format 321 files, mypy 205 source files, no
migration drift. Non-PostgreSQL: 340 passed, 301 deselected. PostgreSQL: 301
passed, 340 deselected, 391.23s. Targeted dashboard and two-gym tests also
passed. Node 24.20.0 web format, lint, typecheck, and build passed. Vitest 62
files / 330 tests. A fresh `spectacular --validate --fail-on-warn` export
byte-matched `contracts/openapi.v1.json`; the regenerated TypeScript hash
stayed unchanged. A clean full `make e2e` passed 17 tests in 5.8m, including
the owner dashboard BFF and server-total test.
Phase 6 Increment 2 is gate-clean as of 2026-10-19. It publishes
`GET /api/v1/reports/daily/?date=YYYY-MM-DD` and
`GET /api/v1/reports/monthly/?year=YYYY&month=M`, and the tenant `/reports`
page and navigation. Both require `reports.financial` and the `reports`
entitlement. `reports.view` alone is denied. Trainer finance closure remains.
The response includes gym display identity, timezone, currency, the normalized
range, `generated_at`, and a read-only source-status policy. Daily returns one
row. Monthly zero-fills every gym-local calendar date. Rows include operational
counts, six revenue buckets including `CLASS`, product quantity, COGS, and
gross profit, total expenses, and net profit. Totals include range-distinct
attendees, staff-salary and PT-commission subtotals, and the ten protected
expense categories. Dates stay inside the supported reporting window. A one-day
report and a 31-day report each run 10 selector queries. There are no
migrations, report tables, caches, Redis, or Celery. Tests cover backend
security and unit checks, web component and route checks, and
`web/e2e/phase6.spec.ts`. Cash-book, profit/loss, membership/expiry,
occupancy/check-in, PT/class, product/stock/inventory, and trainer-scoped
dashboards and reports, plus exports, remain. At that increment close, Phase 6 was not complete.
Production deployment is not complete, and the
[ADR 0010](docs/adr/0010-production-provider-readiness.md) live Resend, AWS, and
ClamAV proofs remain open. Gate evidence: `make backend-check` Ruff and format
passed, mypy 206 source files, no migration drift. Non-PostgreSQL: 344 passed,
303 deselected. Final serial PostgreSQL: 303 passed, 344 deselected, 388.23s.
There was no transient failure; a clock-dependent Phase 5 fixture was fixed by
setting a deterministic non-overlapping future PT slot. Node 24.20.0 web
format, lint, typecheck, and build passed. Vitest 65 files / 336 tests. A fresh
validated OpenAPI export and generated TypeScript are stable (SHA-256 contract
`99f0df02137a4fc0276196d3b80fceb5f35b450ea4dc4426ad5b73dff79c6198`, types
`aabcd406a897bc45dc233be80634904674b1ea4d8aebdf5107c23b8a171bc009`). A clean
full `make e2e` passed 18 tests in 6.6m. IDE lints and `git diff --check` were
clean.
Phase 6 Increment 3 is gate-clean as of 2026-10-19. It publishes
`GET /api/v1/reports/cash-book/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
and `GET /api/v1/reports/profit-loss/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
on the tenant `/reports` page. Both require `reports.financial` and the
`reports` entitlement. Trainer finance closure remains. Ranges are ordered,
inclusive, and 1 through 366 days inside the supported window. Cash book is
gym-wide, groups sessions and append-only transactions by business date, keeps
amounts positive, and counts void sessions separately. Actual closing and
difference sum terminal snapshots. Profit/loss reuses the Increment 2
correction-date formulas, including six revenue buckets and net profit as total
revenue minus total expenses. Cash book uses 2 queries and profit/loss uses 6,
for 1 or 366 days. There are no migrations, report tables, caches, Redis, or
Celery. Membership/expiry, occupancy/check-in, PT/class, product/stock/inventory,
and trainer-scoped dashboards and reports, plus exports, remain. Phase 6 is not
complete. Production deployment is not complete, and the
[ADR 0010](docs/adr/0010-production-provider-readiness.md) live Resend, AWS, and
ClamAV proofs remain open. `make backend-check`: Ruff passed; format 326 files;
mypy 206 source files; migration drift none. Non-PostgreSQL: 348 passed, 306
deselected, 43.70s. Final serial PostgreSQL: 306 passed, 348 deselected,
398.29s. Targeted range reports and Increment 2 reports: 5 passed. Unit and
OpenAPI targeted tests: 33 passed. Node 24.20.0 web format, lint, typecheck,
and build passed. Vitest 65 files / 337 tests. Focused report tests: 7 passed.
A fresh validated OpenAPI export and generated TypeScript are stable (SHA-256
contract `4aa85c7a2f02e3dd14393f3fed7ffea1994b0a146de944f591a014dd4ef5d56e`,
types `a573b85d4df74315cb1214497d67e9fb730fdbccb2574aca768aaa52ba7e2990`).
Focused Phase 6 Playwright passed 2 tests after the daily Date label was made
exact. A clean full `make e2e` passed 18 tests in 6.5m. IDE lints and
`git diff --check` were clean.
Phase 6 Increment 4 is gate-clean as of 2026-10-20. It publishes
`GET /api/v1/reports/membership-expiry/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
and `GET /api/v1/reports/check-ins/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
on the tenant `/reports` page. Both require `reports.view` and the `reports`
entitlement. They do not require `reports.financial` and do not expose money.
Trainer-only callers stay closed. Ranges are strict, ordered, and inclusive,
from 1 through 366 days. Membership expiry counts current ACTIVE, SUSPENDED,
and EXPIRED rows by expiry date. Pending and cancelled rows are excluded.
Range unique members are distinct across the range. Check-ins use gym-local
windows, method counts, check-outs, open visits from that local check-in date,
and a range-independent currently checked-in total. Membership expiry uses 2
queries and check-ins use 4, for 1 or 366 days. There are no migrations,
report tables, caches, Redis, or Celery. PT/class, product/stock/inventory,
and trainer-scoped dashboards and reports, plus exports, remain. Phase 6 is
not complete. Production deployment is not complete, and the
[ADR 0010](docs/adr/0010-production-provider-readiness.md) live Resend, AWS, and
ClamAV proofs remain open. `make backend-check`: Ruff passed; format 327 files;
mypy 206 source files; migration drift none. Targeted unit and OpenAPI tests:
35 passed. Targeted PostgreSQL reports: 7 passed. Non-PostgreSQL: 350 passed,
308 deselected, 43.04s. Final serial PostgreSQL: 308 passed, 350 deselected,
394.57s. Node 24.20.0 web format, lint, typecheck, and build passed. Vitest 65
files / 338 tests. A fresh validated OpenAPI export and generated TypeScript
are stable (SHA-256 contract
`16b01111ab2378fb9fbe7a94851c886045e1c9977fe458622e4cc73087c647fb`, types
`1a879ca9cb961b9aaec1fd06340dd13f69f1cf63da63170e4fc659ca3f469c2d`). Focused
Phase 6 Playwright passed 2 tests in 1.0m after fixing only the
accessibility-aware test assertion. A clean full `make e2e` passed 18 tests in
6.6m. IDE lints and `git diff --check` were clean.

Phase 6 Increment 5 is gate-clean as of 2026-10-20. It publishes
`GET /api/v1/reports/pt-sessions/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
and `GET /api/v1/reports/classes/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
on the tenant `/reports` page. Both require `reports.view`, the `reports`
entitlement, and trainer finance closure. They do not require
`reports.financial` or the `pt` entitlement, and they do not expose money or
member or staff PII. They are gym-wide operational reports. Ranges reuse the
strict ordered inclusive 1 through 366 day query. PT sessions group the current
`PTBooking` status by the gym-local `starts_at` date. Booked, attended,
no-show, and cancelled are mutually exhaustive, and metered and unmetered
include every status. Range unique members and unique trainers are each
distinct across the range. That selector uses 2 queries for 1 or 366 days.
Class rows use `ClassOccurrence.local_date` and the current occurrence and
booking status. Available capacity sums scheduled and completed occurrence
capacity. Reserved slots exclude cancelled bookings. Remaining capacity is
available minus reserved, clamped at zero, and totals sum those daily values.
Range unique members are distinct across bookings, and unique trainers are
distinct across occurrences. That selector uses 4 queries for 1 or 366 days.
There are no migrations, report tables, caches, Redis, or Celery. Product,
stock, and inventory reports, trainer-scoped dashboards and reports, and
exports remain. At that increment close, Phase 6 was not complete. Production deployment is not complete,
and the
[ADR 0010](docs/adr/0010-production-provider-readiness.md) live Resend, AWS, and
ClamAV proofs remain open. `make backend-check`: Ruff passed; format 328 files;
mypy 206 source files; migration drift none. Targeted unit and OpenAPI tests:
37 passed. Targeted PostgreSQL reports: 10 passed. Non-PostgreSQL: 352 passed,
311 deselected, 42.24s. Final serial PostgreSQL: 311 passed, 352 deselected,
384.05s. Node 24.20.0 web format, lint, typecheck, and build passed. Vitest 65
files / 338 tests. A fresh validated OpenAPI export and generated TypeScript
are stable (SHA-256 contract
`9a6cbf3e812ee98509bce6de706cedc9ee4905198493f5b3225572d2a34acf1c`, types
`dbb6a4eff4eb5b9705ad04ddf98b1df0e98d44034dd54cce109dc5728b4a6606`). A clean
full `make e2e` passed 18 tests in 5.9m. IDE lints and `git diff --check`
were clean.

Phase 6 Increment 6 is gate-clean as of 2026-10-20. It publishes
`GET /api/v1/reports/product-sales/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`,
`GET /api/v1/reports/stock-movements/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`,
and `GET /api/v1/reports/inventory/` on the tenant `/reports` page. Product
sales requires `reports.financial`, includes currency, and pages the same-gym
catalog, including archived and zero-activity products. Posted and void sale
lines add on the sale business date, a full void subtracts on the void
business date, and a partial return subtracts on the return business date.
COGS, revenue, and gross profit use the immutable line snapshots. That
selector uses 8 queries. Stock movements and inventory require `reports.view`
and do not include money. Stock movements zero-fill every date, keep the
stored signed quantity, and use 1 grouped query. Inventory pages the current
catalog and sums current same-gym movements in 3 queries. A product is low
stock when that sum is less than or equal to its threshold. Optional `limit`
defaults to 25 and stops at 100. `after` is an opaque signed cursor, valid for
24 hours, ordered by `sku ASC, id ASC`, and bound to the gym, actor, route,
filters, and limit. These routes do not require the finance or inventory
source entitlements. Trainer-only callers stay closed. There are no
migrations, report tables, caches, Redis, or Celery. Trainer-scoped dashboards
and reports, and exports, remain. At that increment close, Phase 6 was not complete. Production
deployment is not complete. `make backend-check`: Ruff passed; format 330
files; mypy 206 source files; migration drift none. Targeted unit and OpenAPI
tests: 40 passed in 4.30s. Targeted PostgreSQL reports: 14 passed in 38.07s.
Non-PostgreSQL: 355 passed, 315 deselected, 43.13s. Final serial PostgreSQL:
315 passed, 355 deselected, 394.18s. Web format, lint, typecheck, and build
passed. A full web rerun passed 65 files / 338 tests in 8.40s. An earlier web
run had transient unrelated UI timing and contention failures; the rerun was
clean. OpenAPI and generated type hashes were stable before and after
generation. A clean full `make e2e` passed 18 tests in 6.2m. `git diff
--check` and IDE lints had passed before the final gates.

Phase 6 Increment 7 is independently verified and gate-clean as of 2026-10-20.
It publishes `GET /api/v1/trainer/dashboard/` and
`GET /api/v1/trainer/reports/sessions/`, and the tenant `/pt/dashboard` page.
Both routes require a protected TRAINER caller, `reports.view`, and the
`reports` and `pt` entitlements. They are own-profile only and include no
money. Dashboard presets and a custom range of at most 366 inclusive days
return `generated_at`, `trainer_staff_profile_id`, `active_assigned_members`,
session counts, and at most 10 `upcoming_bookings`. The session report is the
caller's own profile, zero-filled by day, for PT statuses. The dashboard
selector uses 3 queries and the session-report selector uses 2. After
authorization, the dashboard handler uses 4 queries and the session handler
uses 2. Report exports remain unpublished. At that increment close, Phase 6 was not complete. Production
deployment is not complete. Independent parent-run evidence: `make
backend-check` Ruff passed, format 331 files, mypy 207 source files, no
migration drift. Targeted unit and OpenAPI tests: 41 passed in 5.07s. Targeted
trainer-report PostgreSQL: 1 passed in 16.56s after a fixture entitlement fix.
Non-PostgreSQL: 356 passed, 316 deselected, 43.39s. Final serial PostgreSQL:
316 passed, 356 deselected, 406.34s. Full web tests: 69 files / 352 tests.
OpenAPI hashes stayed stable (SHA-256 contract
`6ca490caec0d993a64154637ba8134fdb32f3f91c16179fd2504a4354c6bdb66`, types
`6bc8301ac01dbb1a2e920baa6a3efd3a2edcc28297529e5482539f79ef3fece8`). A full
`make e2e` passed 18 tests in 6.6m.

Phase 6 Increment 8 is independently verified and gate-clean as of 2026-10-20. It
publishes immediate CSV downloads at `GET /api/v1/reports/{kind}/export/` for
the eleven gym reports. It does not publish export jobs, and it adds no
migrations, caches, Redis, or Celery. Trainer dashboard and session reports
are not exportable. Phase 6 implementation is complete and its local
CI-equivalent gate is closed. Final evidence: `make backend-check` passed Ruff,
format for 335 files, mypy for 209 source files, and no migration drift; focused
unit/OpenAPI tests passed 36 tests in 4.97s; focused PostgreSQL export tests
passed 2 tests in 14.69s; non-PostgreSQL passed 358 tests with 318 deselected in
45.60s; and the clean final PostgreSQL rerun passed 318 tests with 358
deselected in 427.21s. An earlier PostgreSQL run had two unrelated token-clock
failures; both passed in isolation before the clean rerun. Web format, lint,
typecheck, build, and 70 files / 355 tests passed. OpenAPI and generated types
were stable at SHA-256
`d8c15d388379fb69232ba04e2e376cbae0f835d1c9ac4158fd41e19002365ab8` and
`03eed6bfbc981e43837bf3f4ed4a7559e152474a653c61ef46728d9364671343`. The final
full E2E rerun passed 18 tests in 6.2m, and `git diff --check` was clean.
Production deployment is not complete; the ADR 0010 live Resend, AWS, and
ClamAV proofs remain open.

Phase 7 increment 1, dated 2026-10-20, is contracts only. [ADR 0011](docs/adr/0011-bare-react-native-member-app.md)
approves bare React Native CLI for Android and iOS. [ADR 0012](docs/adr/0012-fcm-push-delivery.md)
approves FCM HTTP v1 for both platforms, with the iOS APNs bridge, an in-memory
test transport, and management-command delivery. `docs/API.md` section 7 specifies
the member profile allowlist, member profile-photo routes, announcement audience
and lifecycle, device payloads, and the inbox/push contract. Login, refresh,
logout, and member check-in scan stay the only published member routes. Public
linking, multi-gym member selection, and member MFA stay deferred. No `/mobile`
workspace and no new migration are part of this increment.

Increment 6 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 273 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 234 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
product-category, product, low-stock, and stock-movement routes. A second
schema export matched the working tree. The local `make check` contract step
still reports those files because this increment is not committed.
Increment 7 gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 279 non-PostgreSQL backend tests passed; the
PostgreSQL suite passed 239 tests; web format, lint, typecheck, and build
passed, with 263 web tests. OpenAPI and the generated web types include the
retail-sale routes and omit retail void. A second schema export matched the
working tree. End-to-end browser tests were not run. The local `make check`
contract step still reports the schema files because this increment is not
committed. Phase 4 remains open.
Increment 8A gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync; 282 non-PostgreSQL backend tests passed and 244
were deselected; the PostgreSQL suite passed 244 tests and 282 were
deselected. Those suite results are after the correction-authorization
alignment. Web format, lint, typecheck, and build passed, with 263 web tests.
OpenAPI and the generated web types include payment void, payment refund,
receipt void, and manual expense void, and omit salary void and retail
void/returns. A second schema export matched the working tree. End-to-end
browser tests were not run. The local `make check` contract step still reports
the schema files because this increment is not committed. Phase 4 remains open.
Increment 8B gate evidence: backend ruff, format, and mypy passed and
migrations stayed in sync. 284 non-PostgreSQL backend tests passed and 248
were deselected. The PostgreSQL suite passed 248 tests and 284 were
deselected, with zero failures. Web format, lint, typecheck, and build passed,
with 263 web tests.
OpenAPI and the generated web types include salary-period void and full
retail-sale void and omit a partial return route. A second schema export
matched the working tree. End-to-end browser tests were not run. The local
`make check` contract step still reports the schema files because this
increment is not committed. Phase 4 remains open.

Phase 5 Increment 1 derives `is_pt_client` from an effective same-gym active
trainer assignment.

Production deployment is not complete. [ADR 0010](docs/adr/0010-production-provider-readiness.md) selects the providers. Code readiness is in the repository. These live gates remain open:

- Resend at `POST https://api.resend.com/emails`, translating the application
  `X-Idempotency-Key` to Resend `Idempotency-Key` (1–256 characters, retained
  24 hours), before invitation, password-recovery, or membership-expiry
  delivery is production-ready. The client is implemented. A verified
  domain, API key, and live idempotency proof are still required. Password
  forgot and reset are implemented; live Resend delivery is not proven;
- AWS S3 in `ap-southeast-1` as the primary private bucket, with presigned
  access over direct HTTPS. Production MinIO is no longer planned. Local and
  end-to-end Compose still use digest-pinned `docker.io/pgsty/silo`, a
  test-only MinIO-compatible fork, not official MinIO. The end-to-end
  profile-photo journey is interoperability evidence only. The bucket, IAM,
  CORS, and a live upload and download are not proven;
- private ClamAV on the VPS. A deployed-host proof is not in the repository.

Block Public Access, SSE-S3, and versioning are planned only. Current-object
retention, noncurrent expiry, backup and replication, and jurisdiction or
legal approval stay open. Phase 5 code and test gate closed on 2026-10-19
without closing these live proofs. That closure is not production deployment.
The historical approximately $80 estimate is not an approved AWS price.

- Django REST Framework API for the published foundation and Phase 1B contracts;
- staff authentication, TOTP MFA, and rotating sessions;
- server-selected gym context;
- explicit Django tenant scoping and PostgreSQL forced RLS;
- permission and entitlement foundations;
- a Next.js browser BFF and application shell;
- platform administration, gym provisioning, owners, staff, roles, settings,
  and audit reads;
- automated security and contract checks.

Phase 3 attendance and authenticated member-self QR check-in are implemented.
Trainer and PT work, classes with remaining sessions, product inventory and
retail sales, reports, announcements and reminders, and the member mobile
application remain incomplete until their `docs/DEVELOPMENT_PLAN.md` phase
exit criteria pass. Documentation describes the approved target contract, not
proof that a later phase exists at runtime.

## Approved runtimes

- Python 3.14.7
- Django 5.2.17 LTS
- Django REST Framework 3.18.0
- Node.js 24.20.0 LTS
- Next.js 16.4.1 Active LTS
- PostgreSQL 18.6
- TypeScript 7.0.2

Use the exact versions in `.python-version`, `.nvmrc`, dependency locks, CI,
and container definitions. Next.js 16.4.1 is an approved security
compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable
historical baseline and still records Next.js 16.3.3.

## Local setup

1. Copy `.env.example` to `.env` and replace every placeholder.
2. Generate an RSA key pair under the ignored `secrets/` directory:

   ```sh
   mkdir -p secrets
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
     -out secrets/jwt-private.pem
   openssl rsa -pubout -in secrets/jwt-private.pem \
     -out secrets/jwt-public.pem
   chmod 600 secrets/jwt-private.pem secrets/jwt-public.pem
   ```

3. Start PostgreSQL 18.6 with `make db-up`.
4. Create a Python 3.14.7 virtualenv, then `make migrate`.
5. Verify RLS and ticket flows with `make backend-test-pg`.
6. Install the web workspace with Node.js 24.x (`24.20.0` in CI).

Django authentication routes accept only the trusted Next.js BFF. Production
Django listens on loopback and requires `BFF_SHARED_SECRET`.

`Secure` authentication cookies are mandatory outside isolated localhost
development. Browser JavaScript never receives authentication tokens.

## Browser smoke tests

`make e2e` runs the Playwright Chromium journeys against the real Next.js pages,
the same-origin BFF, and the Django API on PostgreSQL. The member journey also
uploads a profile photo through the test-only MinIO-compatible fork, ClamAV, and the Caddy object proxy,
then checks that member in and out and shows the rotating desk QR. It does not
redeem a scan.
It uses only the disposable `gym_platform_e2e` database. Each API start drops
and recreates that database as the PostgreSQL admin, then migrates and seeds
it. The application database is not dropped. `E2E_RESET=0` skips the drop for
debugging; after finance rows exist, `seed_e2e` still cannot delete those
immutable or restricted rows, and the original database error is reported.
Unit tests do not install or launch a browser.

Prerequisites are the local PostgreSQL roles from `make db-up`, Docker with
Compose v2, Python 3.14.7, Node.js 24.20.0, and `npm --prefix web ci`. The
script installs Chromium through Playwright, starts a separate `gym-e2e-files`
compose project for MinIO, ClamAV, and the object proxy, builds the web app,
migrates as `gym_migration`, seeds MFA-backed E2E identities, and serves Django
as `gym_runtime` on `127.0.0.1:8010`. Next.js listens on `localhost:3010`.
Login still requires the BFF secret, password, and a real TOTP code. Placeholder
passwords, TOTP secrets, and the local MinIO password live in `scripts/e2e-env.sh`
and can be overridden with `E2E_*` variables; they are not production secrets.
The file-service containers are removed when the command exits. Their volumes
stay so ClamAV does not download signatures again, and the application Postgres
volume is not touched.
Migrations run as `gym_migration` with ordinary `manage.py migrate`. After
migrate, only `gym_platform_e2e` grants `gym_runtime` `SELECT` on
`django_migrations` so Django's runserver migration check can read that table.
Application table and function privileges come from the migrations.

```sh
make e2e
```

`make e2e` and Playwright both use that drop. Set `E2E_RESET=0` only to keep
the current disposable database. Screenshots, traces, and video are off. A
failure log can include the obvious
E2E placeholder password or a one-time authenticator code when the failure is
on the sign-in form.

## API contract

The published contract is committed at `contracts/openapi.v1.json`. It includes
the Phase 1A foundation and the gate-closed Phase 1B routes: platform
administration, gym provisioning and lifecycle, owners, staff, settings, audit
reads, role create, update, and archive, permission replacement, the permission
catalog, and membership-role assignment. Generated TypeScript definitions are
committed at `web/src/types/api.generated.ts`. Run `make openapi-check` with
the approved Python and Node versions to regenerate both artifacts and fail on
contract drift.

Phase 2 routes are published: MembershipPlan and Member list, create, detail,
update, and archive; Membership list, create, detail, note update, cancel,
suspend, resume, and expiry buckets; the member summary; nested member payment
history; and profile-photo upload, complete, download-url, and delete.
Membership-expiry scheduling and delivery commands are implemented. Manual
check-in list, detail, create, and check-out, nested member check-in
history, and staff check-in challenge issuance are published. Member login, refresh,
and logout are published for one already linked member. The generic staff scan path
stays unpublished.
Staff announcement routes and member notification and device routes are
published; staff notification HTTP stays unpublished. Production expiry email
delivery follows [ADR 0010](docs/adr/0010-production-provider-readiness.md),
and push delivery follows [ADR 0012](docs/adr/0012-fcm-push-delivery.md).
Compose uses digest-pinned `docker.io/pgsty/silo`, a
test-only MinIO-compatible fork, not official MinIO and not production S3. Finance routes are published in Phase 4. PT, class, and trainer-scan routes are published by the Phase 5 code and test gate closed on 2026-10-19. Phase 6 Increment 1 publishes `GET /api/v1/dashboard/` and the tenant dashboard page. Phase 6 Increment 2 publishes `GET /api/v1/reports/daily/` and `GET /api/v1/reports/monthly/` and the tenant `/reports` page. Phase 6 Increment 3 is gate-clean as of 2026-10-19 and publishes `GET /api/v1/reports/cash-book/` and `GET /api/v1/reports/profit-loss/` on that page. Phase 6 Increment 4 is gate-clean as of 2026-10-20 and publishes `GET /api/v1/reports/membership-expiry/` and `GET /api/v1/reports/check-ins/` for `reports.view` without `reports.financial` or money fields. Phase 6 Increment 5 is gate-clean as of 2026-10-20 and publishes `GET /api/v1/reports/pt-sessions/` and `GET /api/v1/reports/classes/` for `reports.view` without money fields. Phase 6 Increment 6 is gate-clean as of 2026-10-20 and publishes `GET /api/v1/reports/product-sales/`, `GET /api/v1/reports/stock-movements/`, and `GET /api/v1/reports/inventory/`. Product sales is financial. Stock movements and inventory are operational and omit money. Phase 6 Increment 7 is independently verified and gate-clean as of 2026-10-20 and publishes `GET /api/v1/trainer/dashboard/` and `GET /api/v1/trainer/reports/sessions/`, plus `/pt/dashboard`, for protected TRAINER callers with `reports.view` and the `reports` and `pt` entitlements. Those reads are own-profile only and include no money. Phase 6 Increment 8 publishes immediate CSV downloads at `GET /api/v1/reports/{kind}/export/` for the eleven gym reports and is independently verified and gate-clean as of 2026-10-20. Export jobs stay unpublished. Trainer dashboard and session reports are not exportable. Phase 6 implementation and its local CI-equivalent gate are complete and closed. Phase 7 member routes and the bare React Native Android/iOS app are implemented. Production deployment is not complete; live FCM/APNs proof, mobile release signing and store submission, and the ADR 0010 live Resend, AWS, and ClamAV proofs remain open.

## Documentation

Start at the documentation index, [`docs/README.md`](docs/README.md). It lists the
seven authoritative contracts and states which document owns each rule.

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — boundaries, tenancy, deployment
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema, constraints, RLS, transactions
- [`docs/API.md`](docs/API.md) — versioned HTTP contracts and required permissions
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model, permission matrices, gates
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) — repository layout
- [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md) — phases, exit criteria, open decisions
- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — Burmese operational walkthrough

Accepted material decisions are recorded under [`docs/adr/`](docs/adr/).
