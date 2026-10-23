# Phase 8 increment 6 — disposable staging rehearsal

This increment adds a local rehearsal of the production Compose stack. It uses
a separate project, generated credentials, and separate volumes. It does not
deploy to production, push to GHCR, create a protected environment, deliver
alerts, or prove offsite backup, RPO, or RTO. A completed local command is
not a temporary-VPS proof. Phase 8 is not complete.

`production_deploy_proven`, `ghcr_proven`, `protected_environment_proven`,
`offsite_backup_proven`, `external_provider_proven`, `rpo_proven`,
`rto_proven`, `alert_delivery_proven`, `staging_vps_proven`,
`release_attestation_proven`, `rollback_proven`, `backup_proven`,
`restore_proven`, and `smoke_proven` stay false. Config validation, local
command completion, and runtime checks are separate fields. This is local
evidence only.

## What it runs

The harness keeps `infra/compose/docker-compose.prod.yml` digest-only. The
overlay `infra/compose/docker-compose.staging-rehearsal.yml` is the only
staging difference:

- project `gym-staging-rehearsal`
- Caddy on loopback `127.0.0.1:8080` and `127.0.0.1:8443`
- Caddy local TLS for `localhost`, with trust limited to the rehearsal CA
- non-delivery locmem mail and push through `config.settings.staging_rehearsal`
- a private S3-compatible test service for the application
- a second private HTTPS object service for pgBackRest, with TLS verification
  left on
- no public database, API, object, or monitoring ports

Custom API, web, and PostgreSQL images are built from the production
Dockerfiles and pushed to an ephemeral loopback registry. Compose receives
`repository@sha256` references. Third-party images are pulled and recorded by
digest. The Caddy pull tag is `caddy:2.11.7`, the same explicit tag Increment
4 validated, and the rehearsal resolves it to a digest at run time. Those
digests are not GHCR and not release attestation proof. If a
third-party tag has no linux/arm64 manifest, the harness pulls that tag for
linux/amd64 and records the platform. Emulation is not production architecture
proof. The harness starts ClamAV with the data services and waits for a TCP
`PONG` before the other application services. The image healthcheck uses the
unix socket and can pass before TCP is listening, and loading signatures while
the rest of the stack is already up OOM-killed `clamav/clamav:1.4` at the
production 1152m limit on this 4GiB Docker VM. The production limit stays
1152m. The rehearsal overlay raises the disposable ClamAV budget and sets
`CLAMAV_NO_FRESHCLAMD=true` so live signature refresh cannot stall the daemon.
That skip is not production scanner freshness proof. Clamd conf-from-env cannot be used on this read-only rootfs. The TCP wait
uses newline `nPING` because BusyBox `nc` drops the null-terminated `zPING`
under emulation. After the API is up it also pings `clamav:3310` from that
container. An isolated container with the same tmpfs options reported uid 100, gid
101, mode 0755, and a successful log write.

The rehearsal refuses production project names, production directories,
production volumes, symlinks, path traversal, unsafe permissions, mutable
image references, digest substitution, a bad cleanup token, secret material in
evidence, occupied loopback ports, missing local TLS trust, and a second
concurrent run. It does not read production env files.

## Order

1. Offline config preflight.
2. Build images and resolve digests.
3. Start a fresh project and run migrations with the migration identity.
4. Check health, runtime-role separation, forced RLS, Caddy's staff and
   platform block, the member API route, request ID, and TLS.
5. Check the private object store and ClamAV without provider credentials.
6. Check that private services publish no host ports.
7. Record observability target status and local backup status where the
   containers complete. Alert delivery and offsite backup stay unproven.

The two-gym smoke uses `manage.py staging_isolation_smoke` as `gym_runtime`.
Seed rows are ephemeral. The command prints counts, not credentials.

Migration evidence is fresh-schema completion, migration ownership, no pending
migrations, runtime schema denial, and a duration at or under 600 seconds.
That duration is local evidence, not an RTO.

## Commands

Offline validation is part of `make validate-production`. The live rehearsal
is separate:

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py preflight --repo .
backend/.venv/bin/python scripts/staging_rehearsal.py run --repo .
```

State, secrets, and evidence stay under `.staging-rehearsal/` or
`/var/lib/gym-staging-rehearsal`. Both are owner-only. Generated files are
gitignored. Failure does not delete evidence.

Cleanup is explicit. Put `destroy-disposable-staging-rehearsal` in a mode
`0600` file and pass that file. Do not put the token on the command line.

```bash
backend/.venv/bin/python scripts/staging_rehearsal.py cleanup --repo . \
  --confirmation-file /path/to/token
```

Cleanup removes the rehearsal project, its volumes, and its secret files. It
does not remove production state. Evidence is retained.

## Open gates

- No production deploy, GHCR push, or release attestation is proven.
- No protected environment, temporary VPS, or live staging host is proven.
- No offsite backup, external provider, alert delivery, RPO, or RTO is proven.
- Third-party digests recorded by a local pull are not a published release.
- ClamAV and backup checks are local connectivity only.
- Load characterization is a separate opt-in command recorded in [Phase 8 increment 7](./phase8-increment-7.md). It is not part of this rehearsal command and it does not prove capacity or an SLO.
- Receipt allocation and cash close are a separate opt-in command recorded in [Phase 8 increment 8](./phase8-increment-8.md). That command does not prove a soak, an SLO, or a PT payout.
- PT sale, session, payout, and report are a separate opt-in command recorded in [Phase 8 increment 9](./phase8-increment-9.md). Cash commission payout and till close are a separate opt-in command recorded in [Phase 8 increment 10](./phase8-increment-10.md). Those commands do not prove a soak, an SLO, or financial reconciliation. Local bounded endurance is a separate opt-in command recorded in [Phase 8 increment 11](./phase8-increment-11.md). It does not prove a soak, an SLO, or financial reconciliation. Local security characterization is a separate opt-in command recorded in [Phase 8 increment 12](./phase8-increment-12.md). It does not prove a penetration test, a formal security review, production security, or tenant isolation. Local identity-security characterization is a separate opt-in command recorded in [Phase 8 increment 13](./phase8-increment-13.md). It does not prove a penetration test, a formal security review, production security, tenant isolation, or a live provider. Local tenant-isolation characterization is a separate opt-in command recorded in [Phase 8 increment 14](./phase8-increment-14.md). It does not prove independent tenant isolation, a penetration test, a formal security review, or production proof.

Phase 8 is not complete.
