#!/bin/sh
# Validated on prom/alertmanager:v0.34.1, which contains /bin/sh, /bin/cat,
# /bin/tr, and /bin/alertmanager. A distroless tag cannot run this check.
# Production SMTP credentials stay in a Docker secret file.
set -eu

for command in alertmanager cat tr; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "alertmanager image is missing ${command}; delivery is not claimed" >&2
    exit 1
  fi
done

mode="${GYM_ALERT_DELIVERY_MODE:-}"
config=/etc/alertmanager/alertmanager.yml

if [ "$mode" = "resend-email" ]; then
  from="${GYM_ALERT_EMAIL_FROM:-}"
  to="${GYM_ALERT_EMAIL_TO:-}"
  password_file="${GYM_ALERT_SMTP_PASSWORD_FILE:-/run/secrets/alert_smtp_password}"

  for address in "$from" "$to"; do
    safe=$(printf '%s' "$address" | tr -d 'A-Za-z0-9@._+-')
    case "$address" in
      ""|*"@"*"@"*|*@|@*) safe=invalid ;;
      *"@"*) ;;
      *) safe=invalid ;;
    esac
    if [ -n "$safe" ]; then
      echo "alert email address is invalid; delivery is not claimed" >&2
      exit 1
    fi
  done
  if [ ! -r "$password_file" ]; then
    echo "alert SMTP password file is missing; delivery is not claimed" >&2
    exit 1
  fi
  password=$(tr -d '\r\n' < "$password_file")
  if [ -z "$password" ] || [ "$password" = "replace-from-secret-store" ]; then
    echo "alert SMTP password is empty or a placeholder; delivery is not claimed" >&2
    exit 1
  fi

  config=/tmp/alertmanager.yml
  cat > "$config" <<EOF
global:
  resolve_timeout: 5m
  smtp_from: ${from}
  smtp_smarthost: smtp.resend.com:587
  smtp_auth_username: resend
  smtp_auth_password_file: ${password_file}
  smtp_require_tls: true

route:
  receiver: resend-email
  group_by:
    - alertname
    - severity
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: resend-email
    email_configs:
      - to: ${to}
        send_resolved: true
EOF
elif [ "$mode" != "non-production" ]; then
  echo "alert delivery mode must be resend-email or non-production" >&2
  exit 1
fi

exec alertmanager \
  --config.file="$config" \
  --storage.path=/alertmanager \
  --web.listen-address=0.0.0.0:9093 \
  --cluster.listen-address=
