#!/bin/sh
# Validated on prom/alertmanager:v0.34.1, which contains /bin/sh, /bin/tr,
# and /bin/alertmanager. A distroless tag cannot run this check. Missing
# tools fail closed. This process does not prove that an alert was delivered.
set -eu
if ! command -v tr >/dev/null 2>&1 || ! command -v alertmanager >/dev/null 2>&1; then
  echo "alertmanager image is missing tr or alertmanager; delivery is not claimed" >&2
  exit 1
fi
sink_file="${GYM_ALERT_SINK_FILE:-/run/secrets/alert_sink_url}"
expected_file="${GYM_ALERT_SINK_EXPECTED_FILE:-/etc/alertmanager/non-production-sink.url}"
url=$(tr -d '\r\n' < "$sink_file")
expected=$(tr -d '\r\n' < "$expected_file")
if [ "$url" != "$expected" ]; then
  echo "alert sink is not the non-production sink; delivery is not claimed" >&2
  exit 1
fi
exec alertmanager \
  --config.file=/etc/alertmanager/alertmanager.yml \
  --storage.path=/alertmanager \
  --web.listen-address=0.0.0.0:9093 \
  --cluster.listen-address=
