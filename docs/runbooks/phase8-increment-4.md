# Phase 8 increment 4 — private observability baseline

This increment checks in logs, metrics, alert rules, and dashboard JSON. It
does not deploy them, scrape a live host, deliver a notification, or approve
an SLO. Phase 8 is not complete.

## Request logs

Production Django writes one JSON object per diagnostic line to stdout.
`JsonLogFormatter` is the production formatter. A request completion event
from `gym.http` includes the request ID, the resolver route template, method,
status, duration, and service. Actor and gym IDs are included only after
authentication has set UUID values. The raw path, query string, headers,
cookies, and body are not fields.

Severity is ERROR for 5xx, WARNING for 429 and for other 4xx except 401, 403,
and 404, and INFO for 401, 403, 404, and success. Gunicorn's access log is
off because that line contains the raw request target. `django.request` and
`django.server` stay at CRITICAL so they do not print the raw path. The audit
logger remains `apps.audit`. An audit row is not a diagnostic log.

The Next.js BFF uses `web/src/lib/log/structured.ts` for meaningful upstream
and authentication failures. It stamps `X-Request-ID` on the browser response.
Caddy accepts one header matching `^[A-Za-z0-9._:-]{1,128}$` and uses the
captured value. A regexp map output is not a Caddy placeholder, so the matcher
returns `$1`. Otherwise Caddy generates an ID, forwards that ID to the Next.js
or member Django hop, and returns it. Caddy access logs are JSON on stdout. The query string,
`Authorization`, `Cookie`, `X-Gym-BFF-Secret`, `Proxy-Authorization`, and
`Set-Cookie` are removed before the line is written. Docker json-file
rotation is `10m` and 3 files on every Compose service.

Redaction is recursive and covers authorization, cookies, tokens, tickets,
MFA, reset and invitation material, the BFF secret, provider keys, presigned
signatures, control characters, and bodies. It is not a substitute for keeping
those values out of the log call.

## Private metrics

Prometheus, Alertmanager, Grafana, node exporter, blackbox exporter, and
postgres exporter are on internal Compose networks. Caddy publishes only 80
and 443. Metrics listen on Caddy `:2020` inside the observe network. There is
no public `/metrics` route and no Django business metrics route. The admin
listener stays off.

Labels and scrape targets are fixed. They do not include a tenant, user,
member, request ID, raw path, query, email, token, or object key. Blackbox
probes only `http://web:3000/login`, `http://api:8000/api/v1/health/`, and
`caddy:443`. The API probe sends `X-Forwarded-Proto: https`. TLS verification
for the certificate probe is not skipped. `prom/blackbox-exporter:v0.29.0`
has no server-name flag and does not expand its config file. The checked-in
probe stays on private `caddy:443`. tls_connect server_name is only the
example PUBLIC_HOST. Render it to the deployed PUBLIC_HOST before start.
This is a pre-deployment rendered-config gate.

node exporter reads `/proc`, `/sys`, and `/` read-only and a read-only
textfile directory. It does not use the host network, host PID, or the Docker
socket. postgres exporter v0.20.1 reads `DATA_SOURCE_URI` from
`POSTGRES_DB` with no user or password in the URI,
`DATA_SOURCE_USER=gym_monitor`, and `DATA_SOURCE_PASS_FILE`. It does not accept
`--datasource.pass-file`. The checked-in config is `auth_modules: {}` and has
no password. `--extend.query-path` loads one custom numeric query. Default
collectors that can expose query text, relation names, settings, or role names
are disabled. `sslmode=disable` is the private Docker network, not a TLS claim.

Backup jobs write `backup.prom` atomically. The conversion allowlists the
result label and numeric ages. Error text, extra keys, and a true
`rpo_proven` input cannot change the gauges. `gym_backup_rpo_proven`,
`gym_backup_rto_proven`, and `gym_backup_production_proof` stay 0. A stale,
busy, or failed status is still written when the command exits nonzero.
Deploy and restore evidence are recording rules whose expression is
`vector(0)`.

Prometheus keeps 15 days or 512MB, whichever comes first. The admin API and
lifecycle API are off. There is no remote write. Third-party image values in
the example env are fixture digests. This increment does not add a custom
image and does not add those images to the release workflow.

API, web, Caddy, PostgreSQL, ClamAV, and migrate do not wait for a monitoring
container. node exporter does not wait for PostgreSQL, so host metrics stay
available during a database outage. postgres exporter waits until PostgreSQL
is healthy. If `gym_monitor` is absent, that exporter restarts and the API
still starts. The backup textfile directory is created by the PostgreSQL
image. Until that volume exists, backup gauges can be absent while host
metrics continue.

## Planning budget

These limits are a planning fit for 4 vCPU and 8 GiB. They are not a load or
soak result. The operating-system reserve is 1024 MiB and the backup reserve
is 1024 MiB. Steady use must leave that 2048 MiB.

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

Steady CPU is 3.20. Steady memory is 5712 MiB. `5712 + 2048` is 7760, inside
8192. The one-shot migrate job is 0.50 CPU and 512 MiB and is not in the sum.

`max_connections` stays 40. The planning sum is 2 API workers, 1 monitor
connection, an operations reserve of 8, and 3 WAL senders. `14 < 40`.

## Monitor role

`gym_monitor` is `LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
NOBYPASSRLS` with `CONNECT` and `pg_monitor`. It is not the admin, migration,
or runtime role, and it does not receive table `SELECT` or
`pg_read_all_data`. Its password is a separate secret file and must differ
from the other three database passwords. Fresh initialization passes those
passwords through PostgreSQL 18 `psql \getenv`. They are not process arguments.

`infra/postgres/init/001-create-roles.sh` runs only when the PostgreSQL data
directory is empty. It does not alter an existing volume. When
`/run/secrets/monitor_db_password` is absent, the script still creates the
migration, runtime, and auth bootstrap roles and skips `gym_monitor`. Local
Compose and the backup rehearsal do not mount that secret, so a fresh local
database still initializes. Production Compose mounts the secret, so a fresh
production volume creates `gym_monitor`. For a volume that already exists, run
`scripts/bootstrap_monitor_role.py` with the admin password, the monitor
password file, and `--apply` only after the confirmation token
`monitor-role-bootstrap`. Rollout order is: create the secret file, bootstrap
the role on an existing database, then start the exporter. Do not expect the
init script to do that second step.

## Alerts and dashboards

`infra/observability/prometheus/rules/gym-baseline.yml` records availability,
latency, 5xx, 429, host CPU, memory, disk, and the two evidence gauges. HTTP
5xx and 429 use `caddy_http_request_duration_seconds_count`, which carries the
`code` label. `caddy_http_requests_total` does not. Alert
rules cover private probe failure and slowness, 5xx, 429, host pressure,
PostgreSQL, backup age and status, WAL age, certificate age, and unproven
deploy or restore evidence. Every alert has a bounded `for`, a severity, the
runbook label, and `threshold: provisional`. Annotations are static. The
unproven-evidence alert is `info`, not a page.

Grafana anonymous access and sign-up are off. The admin password comes from
its secret file. Dashboards are provisioned from
`infra/observability/grafana/dashboards/gym-baseline.json`. No host port is
published. Opening Grafana through an SSH tunnel is an open gate, not a
completed access path.

Alertmanager's entrypoint was checked against `prom/alertmanager:v0.34.1`,
which provides `/bin/sh`, `/bin/tr`, and `/bin/alertmanager`. The script exits
when either tool is missing. It also exits unless the secret file matches
`http://127.0.0.1:9/gym-observability-non-production-sink`. That address is a
sink that is not a notification service. Clustering gossip is disabled. `python scripts/observability_canary.py` prints one synthetic alert
and the line `alert_delivery=not_claimed`. It does not open a socket.

```bash
python scripts/observability_canary.py --repo .
```

## Validation

`make validate-production` includes this baseline. It fails closed on a public
monitoring port, a mutable image reference, a Docker socket, privileged mode,
host PID or host network, a broad database credential on the exporter, a
high-cardinality label, a secret-bearing log or config, unbounded retention, a
weak `for`, a false delivery or SLO claim, backup metric injection, and a core
service that waits for monitoring.

```bash
make validate-production
```

## Open gates

- Fixture image digests are not the digests of images that were pulled or
  built here.
- `gym_monitor` has not been created on a live database.
- External uptime is not configured.
- No live alert receiver is configured, and no delivery has been observed.
- SLO numbers are not approved. The `for` durations and thresholds above are
  provisional.
- Log and metric retention has no legal approval.
- Production Grafana access is not set up.
- tls_connect server_name is only the example PUBLIC_HOST. Render it to the
  deployed PUBLIC_HOST before start. This is a pre-deployment rendered-config
  gate. Certificate verification stays enabled, and the probe target stays
  `caddy:443`. Increment 5 renders `blackbox.rendered.yml` on the host and
  leaves this tracked file unchanged. That render is not a live certificate
  proof.
- No load or soak test has measured this budget.
- Deploy and restore evidence remains 0. No drill has changed that.
- Explicit version tags were used to validate Caddy, Prometheus,
  Alertmanager, blackbox, Grafana, node exporter, and postgres exporter
  configuration. Those tags are not the fixture digests in Compose, and this
  is not release digest proof. Read-only roots and dropped capabilities were
  not proven on a VPS. Grafana's healthcheck calls `/usr/bin/curl`, which is
  present in `grafana/grafana:13.2.4`. A failed exporter does not stop the API.

Phase 8 is not complete.
