# Notification scheduler

This runbook describes the production Compose service. It does not deploy that
service, and a file in the repository is not evidence that the service is
running. Do not treat a container start as proof that email was accepted,
that FCM accepted a push, or that a handset displayed one.

The service name is `scheduler`. It is one container, using the same API image
and `config.settings.production` as the API. It is not started inside the API
workers. Caddy does not publish a port for it, and it does not listen for HTTP.
The API and Caddy do not wait for it, so a scheduler failure does not restart
them.

The staging rehearsal overlay forces this service onto
`config.settings.staging_rehearsal` with the locmem mail and push backends.
That override is not the production configuration.

## Schedule

The loop runs the commands in this order, one at a time. A command is due
again this many seconds after its previous attempt finishes. The first loop
after a start runs each command once, sequentially, so an outage can catch up.
The commands keep their own deduplication, row locks, and idempotency keys.

| Command | Interval |
| --- | ---: |
| `schedule_membership_expiry_emails` | 15 minutes |
| `deliver_membership_expiry_emails` | 5 minutes |
| `schedule_membership_expiry_pushes` | 15 minutes |
| `deliver_push_notifications` | 2 minutes |
| `publish_scheduled_announcements` | 1 minute |
| `expire_announcements` | 5 minutes |
| `refresh_membership_statuses` | 15 minutes |
| `expire_profile_photo_uploads` | 60 minutes |

`refresh_membership_statuses` moves future memberships to ACTIVE and finished
memberships to EXPIRED using the gym-local date. `expire_profile_photo_uploads`
removes only expired quarantine and unattached upload objects. It does not
delete a photo that a member profile still uses.

Scheduling still creates a reminder only on the gym-local warning day. A later
delivery attempt uses the rules already in the notification engine. A second
schedule pass does not insert a second row for the same gym, channel, and
deduplication key.

## Start, stop, and restart

Production Compose starts `scheduler` with the stack, after PostgreSQL's
health check has passed once. `depends_on` does not mean PostgreSQL stays
available. If the database is down, the process stays up, logs
`error_type=OperationalError`, marks its heartbeat degraded, and tries again
on the next interval.

```bash
docker compose --project-directory infra/compose \
  --env-file infra/environments/production/compose.env \
  -f infra/compose/docker-compose.prod.yml \
  up -d scheduler
```

Stop only the scheduler:

```bash
docker compose --project-directory infra/compose \
  --env-file infra/environments/production/compose.env \
  -f infra/compose/docker-compose.prod.yml \
  stop scheduler
```

Restart it without restarting the API:

```bash
docker compose --project-directory infra/compose \
  --env-file infra/environments/production/compose.env \
  -f infra/compose/docker-compose.prod.yml \
  restart scheduler
```

Compose uses `restart: unless-stopped`. Keep that policy. It is the
production recovery rule:

| Event | What happens |
| --- | --- |
| The Python process exits on its own | Docker starts the container again, with Docker's backoff. The new process runs all eight commands once. |
| A command raises | The process stays up, logs `command_failed`, and continues with the later commands. |
| The command registry is invalid | The process logs `scheduler_configuration_invalid`, writes `fatal`, and waits. It does not exit, so Docker does not tight-loop. |
| `docker stop` or SIGTERM | This is an operator stop. The current command is allowed to finish. `stop_grace_period` is 60 seconds, then Docker sends SIGKILL. The container stays stopped. |
| `docker kill` or SIGKILL | The process dies immediately. PostgreSQL drops the session and releases the advisory lock. On this Docker, `docker kill` is an operator kill: the container stayed exited and the restart count stayed 0. `docker start` runs it again. |
| Docker daemon restart or host reboot | Containers that were running come back. A container that was manually stopped stays stopped. |
| Health status `unhealthy` | Docker does not restart the container. An external check has to notice and start or restart it. |

Do not change the policy to `always`. That would start a container again after an operator stop when the daemon restarts.

Do not print the environment. Do not `docker compose exec scheduler env`.

## Logs

```bash
docker compose --project-directory infra/compose \
  --env-file infra/environments/production/compose.env \
  -f infra/compose/docker-compose.prod.yml \
  logs --since 2h scheduler
```

Startup contains `scheduler_started`. Shutdown contains `scheduler_stopped`.
Each attempt logs `command_started` and then one of `command_finished`,
`command_failed`, or `command_skipped`. `duration_ms` and `exit_status` are on
the finished and failed lines. `error_type` is the exception class name.
`reason=lock_held` means another session already holds that command's lock.

These lines do not include API keys, service-account JSON, device tokens,
email addresses, or message bodies. Do not add a log query that prints
`payload`, `token_ciphertext`, or `Member.email`.

The health check reads `/tmp/gym-scheduler-heartbeat` inside the container.
Prometheus rule `GymSchedulerHeartbeatStale` reads
`gym_scheduler_heartbeat_timestamp_seconds` and `gym_scheduler_heartbeat_ok`
from the node-exporter textfile directory. That rule is the external report.
A failed Docker health check does not restart the container. The rule does
not page a responder until the scheduler image that writes the metric file
and the Prometheus config that targets Alertmanager are both running.
Deploy copies the new Prometheus files onto the bind mount. The process
has no lifecycle reload flag. `check-prometheus` runs `promtool check config`
before migration and does not start a container. `reload-prometheus` runs
that check again and, after it succeeds, recreates only the Prometheus
container. The step then reads `/api/v1/alertmanagers` from that process.
The word must be `ok` and the timestamp must be less than 15 minutes old.
`degraded` means the database could not be reached. `fatal` means the command
registry failed validation. A missing file means the process has not started
or has stopped writing. A failed health check does not by itself restart the
container. Use `docker inspect` on the `scheduler` health state, then the
logs.

The main thread rewrites that file about every 5 seconds while a command is
still inside a 60-minute watchdog, without releasing the advisory lock. The
staging host needed about 48 seconds to finish importing Django and about 73
seconds before a probe passed. Probes at a 5-second timeout failed while the
0.05 CPU limit was saturated. The Compose check therefore waits 15 seconds
for one probe, repeats every 30 seconds, and ignores failures for the first
120 seconds. With that check, a later disposable start imported Django in 44
to 49 seconds, finished each probe in 4 to 8 seconds, and became healthy
about 50 seconds after the container started. Memory peaked at 90 MiB.
After the watchdog, the thread stops rewriting. The same
15-minute age check then fails, so a command that never returns cannot stay
healthy. A dead process stops rewriting immediately because that thread dies
with the process.

## Duplicate execution

Each command has its own PostgreSQL session advisory lock:
`pg_try_advisory_lock(1647384801, command_id)`. `command_id` is a signed
32-bit prefix of SHA-256 over `gympilot.notification-scheduler.v1:` plus the
command name. The ids, one per command in `PRODUCTION_SCHEDULER_COMMANDS`, are distinct. This two-integer lock space is not
the one-integer lock used for class locations, and it does not lock
notification rows. Ordinary API queries are not blocked by it.

The lock is released after the command returns, including when the command
raises. Closing the session releases it as well, so a killed process does not
leave a permanent lock. The same process will not start that command a second
time until the current attempt returns. A second scheduler container logs
`command_skipped` and waits until the next interval.

Restarting the container runs the commands again. That is safe because the
existing unique key `(gym, channel, deduplication_key)`, `select_for_update`
row locks, and the Resend idempotency header reject a second successful send.
FCM acceptance is still not handset delivery.

## Missed reminders, failed pushes, and announcements

Confirm `command_finished` for `schedule_membership_expiry_emails` and
`schedule_membership_expiry_pushes` around the gym-local warning day. A gap
longer than the interval, with no `command_failed` line, means the container
was stopped. Start it again. The next loop schedules anything that is still
due. It does not recreate a reminder that was already sent.

`deliver_membership_expiry_emails` and `deliver_push_notifications` log a
failure with `error_type` and `exit_status=1`. The notification row keeps a
safe code such as `PROVIDER_UNAVAILABLE` or `DELIVERY_REJECTED`. Do not read
the provider body out of the logs. A push row can be `PENDING` while one
device is retried and another was already accepted. `INVALID_TOKEN` revokes
only that device.

`publish_scheduled_announcements` promotes a due scheduled announcement once.
`expire_announcements` marks a published announcement expired once and cancels
its pending pushes. A repeated run does not publish or expire the same row
again. Cancelled and expired announcements leave the active member inbox and
remain in history.

After an outage, `restart scheduler` is enough. Do not send a reminder by
hand from a shell, and do not point this process at locmem or an E2E script
in production.
