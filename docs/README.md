# Documentation index

This directory holds the authoritative Phase 0 contracts for the gym management platform. Documentation describes the approved target contract; it is never evidence that a feature exists at runtime. Phase scope and exit criteria stay in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). That file is the immutable roadmap and was not edited when the Phase 2 or Phase 3 implementation gates closed. Phase 2 implementation is complete as of 2026-10-17. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. Phase 4 increments 1–7 and Increment 8A are published: the finance foundation in [ADR 0009](./adr/0009-finance-foundation-defaults.md), the cash book, payments/receipts/daily sales, expense categories plus approval, salary periods and lines, the product catalog with append-only stock movements, retail-sale drafts through approval, and payment void/refund, receipt void, and manual expense void. Salary void and retail void/returns remain unpublished because a cash-origin correction may need an open destination session after the original till is closed, and that public contract is still only `reason`. The Phase 4 gate is not closed. Increment 9 is unchanged. The approved QR path is authenticated member-self scan. Trainer scan remains a Phase 5 assignment-scoped extension and is not implemented. Scoped DISPLAY, NFC, and BIOMETRIC credentials and dedicated ingestion abstractions are implemented. No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Vendor-specific integration remains optional. Public member identity linking, multi-member selection, the React Native app, and broader Phase 7 mobile APIs remain deferred and are not prerequisites for the already-linked member QR path. The closure evidence and the remaining production-only provider and storage gates are in the repository [`README.md`](../README.md) and in [`API.md`](./API.md).

## The seven authoritative documents

| Document                                             | Owns                                                                                    |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------- |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md)               | System boundaries, tenancy, deployment, reliability, observability, and scale           |
| [`DATABASE.md`](./DATABASE.md)                       | Schema, ownership, constraints, RLS, transactions, and migrations                       |
| [`API.md`](./API.md)                                 | Versioned HTTP contracts, permission requirements per endpoint, and phase delivery map  |
| [`SECURITY.md`](./SECURITY.md)                       | Threat model, permission matrices, isolation layers, and release gates                  |
| [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md)     | Repository, application, test, and infrastructure layout                                |
| [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md)       | Phase sequence, dependencies, checks, exit criteria, cost estimate, and open decisions   |
| [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md)               | Burmese user journeys and operational explanations derived from the contracts above      |

These seven documents plus every accepted ADR must stay internally consistent. A change to one of them that affects another is incomplete until both are updated in the same change.

## Accepted decision records

- [ADR 0001 — Single-VPS budget production topology](./adr/0001-single-vps-production-topology.md)
- [ADR 0002 — Next.js BFF browser authentication](./adr/0002-nextjs-bff-browser-authentication.md)
- [ADR 0003 — Reception cash-session authority](./adr/0003-reception-cash-session-authority.md)
- [ADR 0004 — Do not publish a staff check-in scan route](./adr/0004-defer-check-in-scan.md)
- [ADR 0005 — Member session foundation](./adr/0005-member-session-foundation.md)
- [ADR 0006 — Member check-in scan](./adr/0006-member-check-in-scan.md)
- [ADR 0007 — Display-device credentials](./adr/0007-display-device-credentials.md)
- [ADR 0008 — NFC and biometric ingestion](./adr/0008-device-ingestion.md)
- [ADR 0009 — Finance foundation defaults](./adr/0009-finance-foundation-defaults.md)

## Where a rule lives

When two documents appear to disagree, the owning document wins and the other is a defect to fix:

| Subject                                             | Authoritative source                        |
| --------------------------------------------------- | ------------------------------------------- |
| Stable permission catalog (exact code list)         | `DATABASE.md` section 3                     |
| Role defaults and conditional scopes for each code  | `SECURITY.md` section 6                     |
| Which permission each endpoint requires             | `API.md` section 6                          |
| Table columns, constraints, and RLS policies        | `DATABASE.md` sections 4 and 6              |
| Transaction-local context ordering                  | `ARCHITECTURE.md` section 6                 |
| Phase scope and exit criteria                       | `DEVELOPMENT_PLAN.md`                       |
| Open and blocking decisions                         | `DEVELOPMENT_PLAN.md` section 15            |

## Reference material (not authoritative)

These files are background input captured before the Phase 0 contracts were written. They are retained for history and are superseded by the Markdown documents above wherever they disagree:

- `Gym-Management-Platform.pdf` — original product brief.
- `How-It-Works.pdf` — earlier walkthrough, superseded by `HOW_IT_WORKS.md`.
- `Gym_Management_System_Monthly_Cost_Estimate.pdf` — earlier cost workbook, superseded by `DEVELOPMENT_PLAN.md` section 14.
