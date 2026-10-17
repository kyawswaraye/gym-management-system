# ADR 0009: Finance foundation defaults

- **Status:** Accepted
- **Decision date:** 2026-10-18
- **Applies from:** Phase 4 finance foundation

## Context

Phase 4 still has to post payments, receipts, expenses, retail sales, and cash movements. Those commands need shared money, idempotency, sequence, and permission rules before any of their HTTP routes exist. Decision 15 in `DEVELOPMENT_PLAN.md` blocked custom expense-category changes because the catalog had no code that was narrower than general finance permissions. Jurisdiction and fiscal receipt rules remain a production blocker.

## Decision

Phase 4 receipts are tax-neutral, non-fiscal, internal documents. They are not tax invoices and they do not calculate jurisdiction-specific tax. Decision 8 in the approval register stays open: jurisdiction, fiscal receipt behavior, privacy, retention, and data residency remain a production blocker.

Money is an exact `Decimal` quantized once to two decimal places with `ROUND_HALF_UP`. Floats, NaN, infinities, exponent notation, and values that do not fit `NUMERIC(18, 2)` are rejected.

Receipt rendering is escaped and server-generated from the immutable receipt snapshot. Clients do not supply receipt numbers, totals, or markup.

Posted finance rows, issued receipts, and expense evidence are not deleted automatically. Idempotency response ciphertext expires 24 hours after the claim is created. Only a privileged maintenance identity may purge expired `IdempotencyRecord` rows. `gym_runtime` can select, insert, and update those rows, and cannot delete them.

A cash session `register_code` is an uppercase token of 1 to 32 characters matching `[A-Z0-9_-]+`. There is no separate register catalog.

Expense evidence reuses the private file pipeline. The purpose is `EXPENSE_RECEIPT`. The file is image-only and at most 5 MiB.

Decision 15 is resolved. Custom expense-category create and update require `expense_categories.manage`. That code is never satisfied by `expenses.view`, `expenses.create`, `expenses.update`, `expenses.submit`, `expenses.approve`, `expenses.void`, or any other finance permission. Protected `system_key` categories stay immutable and cannot be deleted. The protected-template defaults are:

| Role         | `expense_categories.manage` |
| ------------ | --------------------------- |
| OWNER        | Allowed                     |
| MANAGER      | Conditional                 |
| ACCOUNTANT   | Conditional                 |
| RECEPTIONIST | Denied                      |
| TRAINER      | Denied                      |

Conditional means the protected template includes the code and the service still enforces same-gym scope, protected-category immutability, and audit. The code is not an approval permission and does not post an expense.

The finance idempotency key is `FINANCE_IDEMPOTENCY_ENCRYPTION_KEY`. It is distinct from `MFA_ENCRYPTION_KEY`. Stored responses are Fernet ciphertext. A claim lives 30 seconds. The same key and request hash replay a `COMPLETED` or `FAILED` response only while `expires_at` is still in the future. An expired row, whether `COMPLETED`, `FAILED`, or `IN_PROGRESS`, is never decrypted and returns `409 IDEMPOTENCY_KEY_EXPIRED` until a privileged maintenance identity deletes it. A different hash on a live row is `409 IDEMPOTENCY_KEY_REUSED`. A live claim is `409 IDEMPOTENCY_IN_PROGRESS`. A stale unexpired claim can be reclaimed under the row lock. Business writes and the completion write commit in one transaction. `mutate()` is database-only and must not perform irreversible external I/O. Completion is fenced by `locked_until`: a claimant that resumes after the lease was reclaimed rolls its business transaction back.

Only a finance-domain exception marked `cacheable_idempotent_failure` is stored as `FAILED`. Its body is the API exception handler envelope for the live request, including that request id. Authentication, authorization, throttling, and serializer validation happen before the claim and are never stored. A callback result is `COMPLETED` only for HTTP 200–299. `FAILED` stores HTTP 400–499. Deadlocks, connection failures, permission or throttle errors, and other unexpected errors roll the business transaction back and release the claim so the same key can retry. They are not cached for 24 hours.

`ATOMIC_REQUESTS` is enabled. An idempotent command view must use `non_atomic_requests`. The idempotency runner owns the claim transaction and the business transaction. Approval and posting helpers run inside `mutate()` and must not start another durable idempotency transaction.

Ciphertext must match a Fernet token and `octet_length(response_body_ciphertext) <= 87480`. That bound is the base64 length of a Fernet token for a 65,536-byte plaintext: PKCS7 padding adds 16 bytes, the binary token is 65,609 bytes, and URL-safe base64 expands that to 87,480 characters.

Receipt numbers come from `TenantSequence` with `SELECT … FOR UPDATE` inside that same transaction. The runtime role may increment `next_value` by exactly one and touch `updated_at`. Rollback does not consume a number.

No payment, receipt, expense, inventory, retail, or cash HTTP route is published by this foundation.

## Security consequences

A stolen idempotency key still cannot be replayed across a gym, actor, method, or route. Response bodies are encrypted at rest and are not logged. Maintenance purge is the only delete path for expired claims. Custom roles do not receive `expense_categories.manage` unless an Owner grants that exact code. Reception and trainers cannot rename the expense catalog. Protected salary and commission categories remain undeletable.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` section 15 item 15 is resolved by this record. The rest of that file stays the historical phase baseline. Phase 4 HTTP workflows, receipt documents, cash sessions, and expense-file upload acceptance are not implemented yet. Jurisdiction remains open.

## Alternatives considered

### Reuse `expenses.update` for custom categories

Rejected. A receptionist who can edit a draft expense would also be able to change the catalog. Decision 15 forbids falling back to a broader finance permission.

### Reuse the MFA encryption key

Rejected. A finance-response key and an authenticator-secret key must rotate separately. Compromise of one store must not decrypt the other.

### Delete expired idempotency rows from the runtime role

Rejected. Posted finance is not deleted by the application role. Expiry limits how long a response can be replayed. Removal stays with the maintenance identity.
