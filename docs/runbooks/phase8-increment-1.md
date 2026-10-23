# Phase 8 increment 1 — production artifact baseline

This increment checks in a production API image, a production Next.js image,
a single-VPS Compose baseline, a Caddy site, environment name inventories,
and offline validation. It does not deploy. It does not publish images. It
does not close Phase 8.

The image digests in `infra/environments/production/compose.env.example` are
format fixtures. They are not registry digests. Replacing them is a live gate.

## Topology

Caddy is the only service with host ports, and those ports are 80 and 443.
Caddy joins `edge` and `app`. It proxies `/api/v1/member-auth/` and
`/api/v1/member/`, including descendants, to private Django at `api:8000` and
keeps the caller's host and scheme. Every other `/api/v1` path, including
staff `/api/v1/auth/` and paths that only share a prefix such as
`/api/v1/members/`, is 404. Browser routes go to Next.js. Next.js calls
`http://api:8000` on the private `app` network and sends
`X-Forwarded-Proto: https` only when cookies are secure.

The API publishes no host port. Gunicorn binds `0.0.0.0:8000` inside the
container network namespace. That bind is not a host listener. Django accepts
the public host, `api`, and `127.0.0.1`. `DJANGO_LISTEN_HOST` is not a
production control.

PostgreSQL is only on the internal `data` network. ClamAV is only on the
internal `scan` network. The API already has provider egress on `app`, so
ClamAV does not get external egress through `scan`. Caddy is not on `data` or
`scan`. Production objects are AWS S3 in `ap-southeast-1` through the existing
`MINIO_*` settings. There is no production MinIO service.

The API container receives `DATABASE_ROLE_MODE=runtime` and
`RUNTIME_DB_PASSWORD`. The one-shot `migrate` profile uses
`config.settings.migration`. That module requires
`DATABASE_ROLE_MODE=MIGRATION`, the `gym_migration` identity,
`MIGRATION_DB_PASSWORD`, and `DJANGO_SECRET_KEY`. It does not require JWT,
Resend, FCM, push, S3, ClamAV, or BFF secrets, and Compose does not pass them.
PostgreSQL init still reads `POSTGRES_MIGRATION_PASSWORD` and
`POSTGRES_RUNTIME_PASSWORD` inside the database container. Compose maps those
from the single canonical values `MIGRATION_DB_PASSWORD` and
`RUNTIME_DB_PASSWORD`. The host file does not define a second copy of either
password. The admin password and the two role passwords must be different
values in the host secret file. The committed example uses the same
placeholder for every secret so the repository does not contain credentials.

## Planning limits

These limits fit the small end of the approved 4 vCPU / 8 GiB host with
headroom. They are not load-test results. Increment 4 recalculated the steady
budget so private monitoring fits the same host, with a 1024 MiB operating-system
reserve and a 1024 MiB backup reserve. The current planning table is:

| Service | CPU | Memory |
| --- | ---: | ---: |
| Caddy | 0.15 | 128 MiB |
| Web | 0.40 | 512 MiB |
| API | 0.70 | 768 MiB |
| PostgreSQL | 1.25 | 2560 MiB |
| ClamAV | 0.30 | 1152 MiB |
| Prometheus | 0.10 | 256 MiB |
| Alertmanager | 0.05 | 64 MiB |
| Grafana | 0.10 | 128 MiB |
| node exporter | 0.05 | 48 MiB |
| blackbox exporter | 0.05 | 32 MiB |
| postgres exporter | 0.05 | 64 MiB |
| Migrate, one shot | 0.50 | 512 MiB |

Steady CPU is 3.20 of 4. Steady memory is 5712 MiB. `5712 + 2048` is 7760,
inside 8192. The migrate job is not part of that sum and should run while
traffic is paused.

PostgreSQL `max_connections` is 40. Two sync Gunicorn workers hold one
connection each when `DB_CONN_MAX_AGE` is 60. The monitor connection is 1.
The operations reserve is 8 and `max_wal_senders` is 3. `2 + 1 + 8 + 3 < 40`.
Gunicorn refuses `WEB_CONCURRENCY` above 4. Raising workers, threads, or
`max_connections` without a new measurement fails validation or process start.

API, web, and migrate drop all capabilities and run as uid 10001. Their home directory is `/tmp`, which Compose mounts as tmpfs. Caddy drops
all capabilities except `NET_BIND_SERVICE`. PostgreSQL's official entrypoint
and ClamAV's signature-volume setup are not compatible with dropping every
capability, so those two services keep the image default and still set
`no-new-privileges`, a read-only root, tmpfs, and no host port.

## Validation

Static checks, Django's security deployment check with offline placeholders,
and `docker compose config` when Docker is installed:

```bash
make validate-production
```

Build both images locally without pushing:

```bash
make production-images
```

CI runs the same validation and both image builds. It does not log in to a
registry and it does not deploy.

The Django check uses obvious offline strings. A passing check means the
production settings import and the security deploy checks are clean. It does
not mean Resend, AWS, FCM, or ClamAV answered.

## Open gates

- Replace the fixture digests with GHCR digests from a real push.
- Live Resend domain, API key, and idempotency proof.
- AWS bucket, IAM, CORS, Block Public Access, SSE-S3, versioning, and a live
  upload and download. Retention, noncurrent expiry, backup, replication, and
  legal approval stay open.
- ClamAV signature updates and a deployed-host scan proof.
- FCM project, service account, and live Android and iOS delivery.
- Host firewall, SSH, Cloudflare origin certificate, and real client IP.
- WAL archiving, offsite backup, restore drill, RPO, and RTO. Increment 3
  checks in the repository baseline. These live gates stay open.
- Load, soak, rollout, rollback, and penetration test. Increment 4 checks in
  private metrics and alert rules. Live monitoring, external uptime, and alert
  delivery stay open.
- Jurisdiction and retention approval.

Phase 8 is not complete.
