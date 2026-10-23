# Phase 8 increment 3 — backup and restore baseline

This increment checks in an encrypted PostgreSQL backup and isolated restore
rehearsal. It is a repository baseline. It is not a live backup-provider proof,
not a measured freshness result, and not a production deployment.

pgBackRest 2.60.0 is built from its published release archive inside
`infra/postgres/Dockerfile`. The base is `postgres:18.6`. The archive checksum
is verified during the image build. The release workflow publishes
`gym-postgres` by digest with the API and web images. Compose selects that
digest through `POSTGRES_IMAGE`. The official `postgres:18.6` image can run
`archive_command`. It does not include the pinned pgBackRest binary, so the
image build adds that binary.

## Repository

Production repository mode is S3-compatible object storage. The endpoint,
region, bucket, and credentials are separate from the application object
store. The committed example uses `https://backup-storage.example`,
`replace-with-*` region, bucket, and path values, and placeholder secret
files. A started production container or backup job refuses those unresolved
values before pgBackRest runs. Production MinIO is not a backup target.

`BACKUP_REPO_MODE=rehearsal-posix` is the local filesystem mode. It is labeled
non-production in `infra/compose/docker-compose.backup-rehearsal.yml`. The
production Compose file does not select it. Its repository identity is
`posix:rehearsal`.

An S3 repository identity binds the mode, the normalized endpoint host and
port, the region, the bucket, the URI style, and the repository path:

`s3://<host>:<port>/<region>/<bucket>/<uri-style><path>`

For endpoint `https://backup.object-store.test`, region `ap-southeast-1`,
bucket `gym-backup-bucket`, URI style `host`, and path `/gym-platform`, the
identity is
`s3://backup.object-store.test:443/ap-southeast-1/gym-backup-bucket/host/gym-platform`.
Another endpoint with the same bucket and path is a different identity.
Restore refuses that mismatch.

The cipher passphrase and the two S3 keys are Docker secret files. They are
not process environment variables, command arguments, image layers, or release
manifest fields. The rendered pgBackRest config is written to
`/run/pgbackrest/pgbackrest.conf` at mode 0600. The placeholder
`replace-from-secret-store` is refused.

## Operations

`archive_mode=on` and `archive_command` run inside the production PostgreSQL
container. The command name is `pgbackrest-archive-push` because pgBackRest
rejects an archive command that does not contain that token. The command is
still the repository wrapper: it checks the WAL path, bounds the timeout, and
then calls the pinned pgBackRest binary. PostgreSQL passes a `pg_wal/` path.

On a fresh cluster, archive commands can fail until a stanza exists. That is
expected and is not a successful backup. Run the one-shot `gym-backup
bootstrap` command, Compose service `backup-bootstrap`, profile `bootstrap`,
before enabling timers. The database entrypoint does not call it, and the
`backup` profile does not start it. Under the shared non-overlap lock,
bootstrap creates the stanza when it is missing, takes the initial full
backup, runs pgBackRest check and WAL validation, and prints
`gym-backup-status/v1` JSON. `rpo_proven` and `rto_proven` stay false. Do not
advance deployment or enable the full and differential timers until bootstrap
exits 0.

Later full and differential jobs require an existing healthy stanza. They do
not create one. A missing stanza tells the operator to run bootstrap. Those
jobs use Compose profile `backup` and do not start with the steady stack. A
non-blocking lock stops a second full, differential, or bootstrap from
overlapping. Archive push does not take that lock. Timeouts are bounded.
Retention is 14 full backups, 14 differential backups, and 14 archive sets
tied to full backups. That retention is provisional and has no legal approval.

`gym-backup check` prints the same status JSON. A missing, stale, or failed
archive or backup exits nonzero. A busy lock exits 75. The 900-second WAL
alarm and 86400-second backup alarm are operational thresholds, not recovery
evidence.

`infra/backup/examples/` contains host timer and service examples, including a
one-shot bootstrap service with no timer. They are not installed. They do not
mount the Docker socket. Backup status is converted to an allowlisted textfile
by [Phase 8 increment 4](./phase8-increment-4.md). Alert delivery is not claimed.

## Restore

`scripts/restore_rehearsal.py` and `infra/compose/docker-compose.restore.yml`
require a disposable project, a volume that does not already exist, the
expected stanza and repository identity, and the confirmation token
`restore-disposable`. PostgreSQL and API images must be exact
`repository@sha256:<64 lowercase hex>` references with no tag. The database
password and Django secret are read from `--db-password-file` and
`--django-secret-file`. The cipher file is always required. S3 restore also
requires both storage key files and mounts them only on the one-shot
container. A filesystem rehearsal refuses those key files. The command does
not accept secret values as arguments.
The data path is `/var/lib/gym-restore`. The production project
`gym-production` and the volume `postgres_data` are refused. The restored
server sets `archive_mode=off`.

The script refuses a target volume that already exists, creates the absent
volume with a fresh ownership label, and inspects that label before changing
ownership or mounting it. The container still requires that data directory to
be empty. A filesystem rehearsal requires the source repository volume to
exist already and mounts it only on the one-shot restore container. S3
restore does not mount a local repository volume. That one-shot container may
use temporary egress while it fetches from S3 and finishes recovery. It is
the only process that receives repository credentials.

The disposable Compose network is internal. The restored PostgreSQL service
uses the official `docker-entrypoint.sh` entrypoint, keeps `archive_mode=off`,
and receives no pgBackRest configuration or repository secrets. The verifier
stays on that same internal network and also receives none.

`python manage.py verify_restore_rehearsal` then checks reachability, recovery,
database identity and version, the migration graph, roles and grants, forced
row-level security, missing tenant context, two-gym isolation, constraints,
triggers, finance reconciliation, audit rows, and a file-reference checksum
inventory. It does not download object bytes and does not mutate business
data. Fewer than two gyms, or an empty tenant audit log, fails the command.
The JSON field `live_evidence` stays separate and unproven.

`scripts/backup_rehearsal.py` can exercise a local filesystem bootstrap,
WAL archive, differential backup, and marker restore when the image has been
built. The restored server in that exercise uses the official entrypoint and
does not receive repository credentials. That script does not run application
verification and does not upload WAL offsite.

## Open gates

- Backup provider, endpoint, region, bucket, and credentials are not approved
  or installed.
- Retention and legal approval remain open.
- No live credential has been used.
- No WAL upload to offsite storage has succeeded.
- Freshness against a live repository has not been measured.
- A full restore drill of a migrated production backup has not been run.
- Recovery-point and recovery-time planning targets have no drill evidence.
- Alert delivery is not connected. Increment 4 adds a non-production sink and
  a synthetic canary. Neither delivers a notification.
- Production rollout has not started.

Phase 8 is not complete.
