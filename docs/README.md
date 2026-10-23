# Documentation index

This directory holds the authoritative Phase 0 contracts for the gym management platform. Documentation describes the approved target contract; it is never evidence that a feature exists at runtime. Phase scope and exit criteria stay in [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md). That file is the immutable roadmap and was not edited when the Phase 2 or Phase 3 implementation gates closed. Phase 2 implementation is complete as of 2026-10-17. Phase 3 implementation is complete as of 2026-10-18 under ADRs 0004–0008. Phase 4 increments 1–7, Increment 8A, and Increment 8B are published: the finance foundation in [ADR 0009](./adr/0009-finance-foundation-defaults.md), the cash book, payments/receipts/daily sales, expense categories plus approval, salary periods and lines, the product catalog with append-only stock movements, retail-sale drafts through approval, payment void/refund, receipt void, manual expense void, salary-period void, and full retail-sale void. The partial-return command is published. Its contract, immutable return tables, and database guards are recorded in [ADR 0009](./adr/0009-finance-foundation-defaults.md), [API.md](./API.md), and [DATABASE.md](./DATABASE.md). The retail staff page exposes it for a posted sale. Increment 8C is that publication. A cash correction names an open destination session and does not reopen the original till ([ADR 0009](./adr/0009-finance-foundation-defaults.md)). Phase 4 implementation is complete as of 2026-10-18 against the code and test gates. Manager finance and security evidence review is complete. Production deployment is not complete, and the email and object-storage gates remain open. Increment 9 staff pages include the partial return. The approved QR path is authenticated member-self scan. Trainer scan is published at `POST /api/v1/trainer/check-ins/scan/`. Scoped DISPLAY, NFC, and BIOMETRIC credentials and dedicated ingestion abstractions are implemented. No vendor-specific adapter is implemented, and no biometric template, raw capture, or vendor secret is stored. Vendor-specific integration remains optional. Public member identity linking, multi-gym member selection, and member MFA remain deferred and are not prerequisites for the already-linked member QR path. [ADR 0011](./adr/0011-bare-react-native-member-app.md) and [ADR 0012](./adr/0012-fcm-push-delivery.md) approve bare React Native CLI and FCM. Those records specify the Phase 7 contracts. The app and the published member routes are implemented; staff `/api/v1/notifications/` stays unpublished. The closure evidence is in the repository [`README.md`](../README.md) and in [`API.md`](./API.md). [ADR 0010](./adr/0010-production-provider-readiness.md) selects Resend and AWS S3 `ap-southeast-1` and records the password-recovery contract. Password recovery and the Resend client are implemented. Live Resend, AWS, and ClamAV proofs remain open, and the legal retention policy stays draft-only. Phase 5 waits when those live gates must be closed first.

Phase 7 code and the 2026-10-21 local test gate are complete: the member-self
and announcement APIs, the FCM delivery pipeline, and the bare React Native
Android/iOS workspace are present. Live Firebase and APNs credentials, release
signing, store submission, a production device proof, and Phase 8 production
deployment remain open. This is not a production deployment.

Phase 8 increment 1 adds the production image and Compose baseline recorded in
[the increment 1 runbook](./runbooks/phase8-increment-1.md). Phase 8 increment 2
adds the release supply chain recorded in
[the increment 2 runbook](./runbooks/phase8-increment-2.md). Phase 8 increment 3
adds the encrypted backup and isolated restore baseline recorded in
[the increment 3 runbook](./runbooks/phase8-increment-3.md). Phase 8 increment 4
adds the private observability baseline recorded in
[the increment 4 runbook](./runbooks/phase8-increment-4.md). Phase 8 increment 5
adds the manual deployment-control baseline recorded in
[the increment 5 runbook](./runbooks/phase8-increment-5.md). Phase 8 increment 6
adds the disposable local staging rehearsal recorded in
[the increment 6 runbook](./runbooks/phase8-increment-6.md). Phase 8 increment 7
adds the opt-in local load characterization recorded in
[the increment 7 runbook](./runbooks/phase8-increment-7.md). That profile is
not a load-test proof, a soak, a production-capacity proof, or an SLO. Phase 8
increment 8 adds the opt-in receipt and cash-close characterization recorded in
[the increment 8 runbook](./runbooks/phase8-increment-8.md). It does not prove a
soak, capacity, an SLO, or financial reconciliation. PT payout stays uncovered.
Phase 8 increment 9 adds the opt-in PT sale, session, payout, and report
characterization recorded in
[the increment 9 runbook](./runbooks/phase8-increment-9.md). It does not prove a
soak, capacity, an SLO, or financial reconciliation. Phase 8 increment 10 adds
the opt-in cash commission payout and till characterization recorded in
[the increment 10 runbook](./runbooks/phase8-increment-10.md). It does not prove a
soak, capacity, an SLO, or financial reconciliation. Phase 8 increment 11 adds
the opt-in local bounded endurance characterization recorded in
[the increment 11 runbook](./runbooks/phase8-increment-11.md). It does not prove a
soak, capacity, an SLO, or financial reconciliation. Phase 8 increment 12 adds
the opt-in local security characterization recorded in
[the increment 12 runbook](./runbooks/phase8-increment-12.md). It does not prove a
penetration test, a formal security review, production security, or tenant
isolation. Phase 8 increment 13 adds the opt-in local identity-security
characterization recorded in
[the increment 13 runbook](./runbooks/phase8-increment-13.md). It does not prove a
penetration test, a formal security review, production security, tenant
isolation, or a live provider. Phase 8 increment 14 adds the opt-in local
tenant-isolation characterization recorded in
[the increment 14 runbook](./runbooks/phase8-increment-14.md). It does not prove
independent tenant isolation, a penetration test, a formal security review, or
production proof. No GHCR push has
been performed, protections are unproven, and images stay unpublished until
the release workflow runs. The deployment workflow has not been run. The local
rehearsal is not a production deploy, a temporary-VPS proof, or an offsite
backup proof. Phase 8
is not complete. None of these increments is a deployment, a live alert proof,
or a live provider proof.

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
- [ADR 0010 — Production provider readiness](./adr/0010-production-provider-readiness.md)
- [ADR 0011 — Bare React Native member application](./adr/0011-bare-react-native-member-app.md)
- [ADR 0012 — FCM push delivery](./adr/0012-fcm-push-delivery.md)

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
