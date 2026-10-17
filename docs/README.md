# Documentation index

This directory holds the authoritative Phase 0 contracts for the gym management platform. Documentation describes the approved target contract; it is never evidence that a feature exists at runtime. Phase scope and exit criteria stay in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). That file is the immutable roadmap and was not edited when the Phase 2 implementation gate closed. The closure record and the remaining production-only gates are in the repository [`README.md`](../README.md) and in [`API.md`](./API.md).

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
