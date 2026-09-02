# ADR 0001: Single-VPS Budget Production Topology

- **Status:** Accepted for Phase 0 baseline
- **Decision date:** 2026-08-15
- **Budget target:** approximately USD 80 per month

## Context

The earlier managed-cloud design used separate hosted web, API, database, object-storage, secret-management, registry, and monitoring services. Its several-hundred-dollar monthly production estimate is above the approved initial operating budget.

The initial rollout targets a small deployment of approximately ten gyms, 1,000 monthly active users, bounded file retention, and roughly one million API requests per month. Tenant isolation, PostgreSQL forced RLS, financial integrity, encrypted offsite backups, and restore testing cannot be weakened to meet the budget.

## Decision

Production will run on one hardened Linux VPS using Docker Compose:

- Cloudflare provides DNS, edge TLS, WAF, and rate controls.
- Caddy is the only public origin container and exposes ports 80/443.
- Next.js and Django/Gunicorn run as separate non-root containers. Caddy is the only container with published host ports (80/443). Django must not publish a host port; it is reachable only from the Next.js BFF on the private Docker network. The BFF shared secret authenticates that hop and does not replace this isolation.
- PostgreSQL runs on a private Docker network with persistent local storage, separate runtime/migration roles, and forced RLS.
- MinIO provides private S3-compatible object storage on the private network.
- GitHub Actions publishes digest-pinned images to GHCR and invokes a protected Compose deployment.
- PostgreSQL base/logical backups, continuous WAL archives, and MinIO object copies are encrypted and stored with an independent offsite provider.
- External monitoring covers HTTPS availability, certificates, host capacity, PostgreSQL, backup/WAL/object-copy freshness, and deployment failures.
- Staging runs locally or on an ephemeral temporary VPS with separate credentials and volumes; permanent staging does not share the production VPS.

## Budget

The monthly planning allocation is:

- VPS: approximately USD 50;
- encrypted offsite backup/object storage: approximately USD 10;
- Cloudflare/domain/monitoring/limited email allowance: approximately USD 20;
- estimated monthly total: approximately USD 80.

This is a planning estimate, not a provider quote. Provider, region, taxes, bandwidth, backup capacity, and measured load can change the actual price.

## Security and recovery consequences

Application security remains unchanged: explicit Django gym scoping and PostgreSQL forced RLS are independent mandatory controls. PostgreSQL and MinIO are not publicly exposed. Secrets do not enter images, Compose files, source control, logs, or shell history.

One VPS is a single point of failure. This topology provides restore capability rather than high availability. The initial RPO target of 15 minutes depends on healthy offsite WAL archiving; the RTO target of four hours depends on a rehearsed replacement-VPS build and restore. Both targets must be demonstrated by quarterly drills before they can be treated as operational commitments.

## Alternatives considered

### Managed cloud services

Separate managed web, API, database, object, secret, registry, and monitoring services provide stronger availability and lower operational burden but do not fit the approved initial budget.

### Separate application and database VPSs

This improves resource isolation and permits independent maintenance, but two appropriately sized hosts plus backup capacity may exceed the target. It is the preferred first structural upgrade after vertical resizing.

### Kubernetes

Kubernetes adds cost and operational complexity without evidence of a current scaling requirement. It is not approved for the baseline.

## Upgrade triggers

Architecture and budget must be reviewed when any of these occur:

- more than ten active gyms or 1,000 monthly active users;
- sustained CPU, RAM, disk, I/O, or database-connection pressure outside tested headroom;
- database/object growth or backup windows threaten the approved RPO/RTO;
- repeated availability/SLO failures caused by the shared host;
- security, data residency, contractual, or regulatory requirements demand stronger isolation or availability;
- restore drills fail the approved recovery targets.

The upgrade sequence is vertical VPS resizing, then separation or management of PostgreSQL/object storage, then multiple application nodes/load balancing. Every step must preserve tenant isolation, financial semantics, audit evidence, and recoverability.

## Rollback and compatibility

This decision changes deployment and operations only. Public API, database schema, IDs, RLS policy, and domain contracts remain provider-neutral. The system can move later to managed PostgreSQL, object storage, and multi-node application hosting by restoring the same PostgreSQL data and object keys behind the existing service interfaces.
