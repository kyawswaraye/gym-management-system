# Phase 8 increment 5 — deployment control baseline

This increment checks in a manual deployment workflow, a checksummed bundle,
and host orchestration for temporary staging and the single production VPS.
It does not deploy. It does not create a protected GitHub environment, push
to GHCR, or prove SSH, smoke, backup, restore, or rollback on a live host.
Phase 8 is not complete.

`deploy_proven`, `ghcr_push_proven`, `protected_environment_proven`,
`backup_proven`, `restore_proven`, `rpo_proven`, `rto_proven`,
`rollback_proven`, `smoke_proven`, `attestation_proven`, and
`staging_proven` stay false. `commands_completed` only means this process
exited the planned steps. It is not proof.

## Operator order

1. Publish an image with the Increment 2 workflow and keep that successful
   run id, tag, and commit. This repository has not done that.
2. On the temporary staging VPS or the production VPS, create
   `/var/lib/gym-deploy/incoming/staging` or
   `/var/lib/gym-deploy/incoming/production`, `/etc/gym/staging` or
   `/etc/gym/production`, and a mode `0600` `compose.env`. The env file and
   every secret file must be owned by the deployment user or by root. Secret
   files are absolute host paths, mode `0600`, and are not the committed
   examples. Configure a host Docker credential helper for `ghcr.io` with
   read access and point `DOCKER_CONFIG` at that owner-only directory. Do not
   put a registry token in the workflow, an artifact, or a command-line
   password. This helper is a prerequisite, and it is not proven.
3. Store `DEPLOY_SSH_PRIVATE_KEY` and `DEPLOY_SSH_HOST_KEY` in the GitHub
   environment. `DEPLOY_SSH_HOST_KEY` is the pinned public key
   (`ssh-ed25519` or another allowlisted type), verified out of band.
   `DEPLOY_SSH_HOST` and `DEPLOY_SSH_USER` are environment variables.
   Required reviewers for `gym-staging` and `gym-production` are a GitHub
   setting. This file does not create them and they are not proven.
4. Run the Deploy workflow manually from the repository default branch.
   Choose `staging` or `production`, `deploy` or `rollback-app`, the release
   run id, the release tag, and the release commit. `deploy` requires
   `schema_compatibility=not-applicable`. `rollback-app` requires
   `schema_compatibility=reviewed-authorized`. A dispatch from any other ref
   is refused. The GitHub environment branch policy is still an open gate.
5. The workflow asks the GitHub API for the default branch and binds the
   caller SHA to that branch head through `git/ref/heads`. It resolves the
   release tag through `git/ref/tags`, and peels an annotated tag to its
   commit. It does not use the commits API, which can resolve a branch of
   the same name. The release manifest must match that commit, the
   image-release run, and the attestations. Deploy jobs check the default
   branch head again and check out exactly the bound control SHA. If the
   default branch moved after resolve, the job stops instead of following
   the new head. The release checkout stays at the release SHA. The bundle
   uses that commit's infrastructure files and the trusted control scripts.
   `StrictHostKeyChecking=yes` is required. The known_hosts entry is the
   pinned key. The workflow caller SHA is not the release SHA unless those
   commits are actually the same.

Staging uses project `gym-staging`, directory `/etc/gym/staging`, state
`/var/lib/gym-deploy/staging`, and incoming
`/var/lib/gym-deploy/incoming/staging`. Production uses `gym-production`,
`/etc/gym/production`, `/var/lib/gym-deploy/production`, and
`/var/lib/gym-deploy/incoming/production`. A host-global lock is acquired
before either target reads its incoming bundle. The two do not share
volumes, env files, credentials, or incoming files. A host that still has
the other environment directory, or a running project for it, is refused.
Temporary staging is not a permanent stack on the production VPS. Published
ports stay 80 and 443, so the two stacks cannot run on one host.

## What the host does

The host takes the host-global lock, then the target lock, and exits 75
when either lock is held. It verifies the bundle, rejects extra, missing,
or changed files, repeated checksum paths, path traversal, symlinks, and
modes other than `0644`. It renders `blackbox.rendered.yml` from the
tracked blackbox file using `PUBLIC_HOST`. Certificate verification stays
enabled. The tracked `infra/observability/blackbox/blackbox.yml` is not
edited. Compose mounts `./blackbox.rendered.yml`.

It then checks free disk and available memory against the planning reserve.
Private GHCR pulls use the host credential helper. A missing helper or a
failed digest pull stops before migration. The pull is not proof that the
helper has read-only scope. Production runs `backup-full` before the
one-shot `migrate` service. Staging records `skip-backup-checkpoint` and
still migrates only after that decision. Migration uses the existing
migration identity and `--noinput`. A failed checkpoint does not migrate.
Startup is `docker compose up -d --wait` without the migrate or backup
profiles. After smoke, the host inspects each running service and requires
`Config.Image` to be the digest reference from the overlay. A missing,
duplicate, or substituted container fails closed.

Smoke checks are bounded: public `/login`, Caddy's 404 for staff
`/api/v1/auth/login/` and `/api/v1/health/`, an unauthenticated member-auth
response, API runtime role, web `/login`, migration and forced-RLS posture,
ClamAV's Compose health status, and private observability readiness.
Prometheus readiness is probed with BusyBox `/bin/wget -T` from
`prom/prometheus:v3.15.0`. Blackbox readiness is `/-/healthy` on
`prom/blackbox-exporter:v0.29.0`. Those tag checks are not digest proof.
Probes do not send application credentials.

A successful forward deploy then writes an owner-only, checksummed active
deployment state. A failed deploy does not replace that file.

Evidence stays under the state directory. It records the release SHA, the
caller SHA, digests, and the blackbox checksum. It does not record secret
values.

## Rollback boundary

App rollback is `rollback-app` after a reviewed schema compatibility
decision, and only when the active deployment state checksum and owner
match. App rollback only changes API/web. Caddy may restart for config but
its image digest is preserved. PostgreSQL, ClamAV, and the observability
image digests are preserved from the active deployment state. A stale or
malicious host image value is not used. The running PostgreSQL container
must keep the same id and digest. Rollback does not migrate, restore, pull,
or restart PostgreSQL. After those checks pass, the active deployment state
is replaced atomically so its release and API/web identity match the
rollback while the preserved digests stay. A failed rollback does not
replace that file. A tampered active deployment state is refused.

Any other rollback stops with `stop-forward-fix-or-restore`. A failed smoke
or migration does not automatically start an older app image on a migrated
schema. The next change is a forward fix, an authorized app rollback, or a
restore decision. `rollback_proven` stays false.

## Teardown

Temporary staging teardown is a host command, not a workflow input:

```bash
python3 scripts/deployment_control.py teardown-staging \
  --confirmation-file /path/to/token \
  --compose-file /var/lib/gym-deploy/staging/src/infra/compose/docker-compose.prod.yml
```

The token file contains `destroy-temporary-staging`. Anything else exits
before `docker compose down -v`. The command is only for project
`gym-staging`. It does not delete `/etc/gym/production` or production
volumes. Removing the staging env directory afterward is a separate operator
action and is required before that machine can be used for production.

## Failure

The workflow and the host fail closed. A bad manifest, digest substitution,
attestation mismatch, mutable image, wrong host-key policy, secret example
in the bundle, lock conflict, low disk, failed checkpoint, failed migration,
or failed smoke stops the run. Evidence is kept. Nothing in this increment
sets `rpo_proven`.

## Open gates

- No protected `gym-staging` or `gym-production` environment is proven.
- No GHCR push, attestation, or digest pull has been performed here.
- No VPS, SSH host key, staging stack, or production deploy has been run.
- Smoke, isolation, backup checkpoint, rollback, RPO, and RTO are not proven.
- Third-party image digests in the host env are still operator-supplied
  digests. Fixture values in the example env are not registry proof.
  Checking `/bin/wget` or `/-/healthy` against an image tag is not digest
  proof.
- The host GHCR credential helper has not been logged in or scoped here.
- No protected environment has been limited to the default branch. The
  workflow refuses other refs only when this workflow file is the one that
  runs.
- Live certificate names, Cloudflare, and provider credentials remain open.

Phase 8 is not complete.
