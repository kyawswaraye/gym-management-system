# ADR 0003: Reception Cash-Session Authority

- **Status:** Accepted
- **Decision date:** 2026-09-02
- **Applies from:** Phase 4 finance

## Context

Reception must operate and close a front-desk till without receiving financial approval, posting, or manual-adjustment authority. A blanket denial of cashbook access prevents normal shift operation, while unrestricted cashbook permissions would expose another receptionist's till and weaken separation of duties.

The approved `CashSession` model already records `opened_by_membership_id`. Tenant isolation already requires explicit Django gym scope and forced PostgreSQL RLS. The remaining decision is the exact object condition within one gym.

## Decision

The protected RECEPTIONIST template changes these defaults from Deny to Conditional:

- `cashbook.view`: C;
- `cashbook.open`: C;
- `cashbook.reconcile`: C.

`cashbook.adjust` remains D.

After normal active-gym Django scoping and forced RLS, the conditional object scope is exactly:

```text
CashSession.opened_by_membership_id = current Reception GymMembership.id
```

Reception may open a same-gym session and may view, operate, reconcile, and close only that own-opened session. “Operate” means use the session for ordinary front-desk draft preparation and allow automatic movements created later by an authorized posting service. Reception cannot append a manual adjustment.

Reception may prepare, edit, and submit permitted Payment, Expense, Salary, and later PT Sale drafts. Reception never approves or posts a financial transaction. The hard-reserved Payment, Expense, Salary, and PT Sale approval permissions remain limited to a different authorized Manager or Owner, and posting remains a server-side consequence of successful approval.

Another Reception membership's session is denied even if it belongs to the same user, register, shift, or gym. Every cross-gym session is denied by both Django scope and forced RLS before the object condition is evaluated.

Reconcile is the close command. The server locks the open session, recomputes expected totals from immutable posted cash transactions, records actual cash and any explained difference, and closes once. Clients never supply authoritative totals. Closed sessions and their transactions are immutable and are never reopened; later corrections use an authorized new-session workflow.

## Security consequences

The rule grants the minimum till access needed for Reception operations while preserving separation between preparation, approval, and posting. It does not grant financial reports, another till, manual adjustment, approval, posting, void, refund, or correction authority.

Permission checks, object scope, entitlement, record state, Django gym scope, forced RLS, same-gym composite foreign keys, idempotency, and audit all remain required. UI visibility is not authority.

Required security tests cover:

- own-opened same-gym session view/operation/reconcile/close succeeds;
- another Reception membership's session is denied;
- every cross-gym session is denied;
- all finance approval/posting and manual adjustment attempts are denied;
- close totals are server-calculated;
- closed sessions and transactions are immutable.

## Concurrency consequences

Open uses the existing partial uniqueness rule for one open session per gym/register. Operation and close lock the session. A transaction racing with close must serialize so either the authorized posted movement is included before close or fails against the closed state; no movement may appear after close.

Concurrent reconcile/close requests produce one committed close through row locking and idempotency. The server recalculates totals inside that same transaction and does not trust a previously displayed expected amount.

## Compatibility consequences

No schema field is added because `opened_by_membership_id` already exists. The permission template/default data changes for Reception, while API response fields and stable permission codes remain unchanged. Existing gyms receive the change only through the explicit, audited role-template upgrade/backfill mechanism; tenant-customized roles are not silently rewritten.

The approved runtime pins remain Python 3.14.7, Django 5.2.17 LTS, DRF 3.18.0, Node.js 24.20.0 LTS, Next.js 16.4.1 Active LTS, PostgreSQL 18.6, and TypeScript 7.0.2. Next.js 16.4.1 is an approved security compatibility update. `docs/DEVELOPMENT_PLAN.md` remains the immutable historical baseline and still records Next.js 16.3.3. PostgreSQL 18.6 lock, RLS, immutability, and race tests are compatibility evidence for this decision.

## Alternatives considered

### Deny Reception all cashbook access

Rejected because Reception could not independently run and close the till it opens.

### Allow every same-gym cash session

Rejected because it exposes other Reception sessions and weakens accountability.

### Scope by register code, shift, or user ID

Rejected because those values are broader or less authoritative than the exact gym-membership actor recorded at open. A global user may hold multiple gym memberships.

### Grant manual adjustment or posting

Rejected because it collapses operation/preparation into correction or posting authority and breaks the approved separation of duties.

## Rollback and migration

Before Phase 4 implementation there is no data migration. During implementation, the protected template and tests are created with the conditional defaults.

For existing gyms, an explicit audited upgrade command may change only the Reception template mappings for `cashbook.view/open/reconcile`; it must preserve tenant customizations unless an owner approves the diff. Rollback removes those three grants from the protected Reception template and active upgraded roles after operational impact review. It does not delete or rewrite sessions, transactions, closes, audit evidence, or permission codes.

Any future widening of Reception scope, adjustment authority, or financial approval/posting requires a new security review, ADR, permission migration, and concurrency tests.
