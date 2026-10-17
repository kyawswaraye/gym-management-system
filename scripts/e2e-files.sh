#!/usr/bin/env bash
# Start and stop the disposable E2E file services. Source this file.
# The compose project is gym-e2e-files. It never includes PostgreSQL.

e2e_compose() {
  docker compose \
    --project-name gym-e2e-files \
    --project-directory "${E2E_ENV_ROOT}" \
    --file "${E2E_ENV_ROOT}/docker-compose.e2e.yml" \
    "$@"
}

e2e_stop_file_services() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  e2e_compose down --remove-orphans
}

e2e_probe_url() {
  local url="$1"
  local host_header="${2:-}"
  local attempts=30
  local code=""
  local try
  for ((try = 1; try <= attempts; try++)); do
    if [[ -n "${host_header}" ]]; then
      code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 2 -H "Host: ${host_header}" "${url}" || true)"
    else
      code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 2 "${url}" || true)"
    fi
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "E2E probe failed for ${url} (last HTTP ${code:-none})." >&2
  return 1
}

e2e_start_file_services() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to start MinIO, ClamAV, and the object proxy for E2E." >&2
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required for the E2E file services." >&2
    return 1
  fi
  echo "Starting E2E MinIO, ClamAV, and the object proxy."
  e2e_compose up -d --wait --wait-timeout 600 minio clamav object-proxy
  e2e_compose run --rm --no-deps minio-init
  e2e_probe_url "http://127.0.0.1:${E2E_MINIO_PORT}/minio/health/live"
  e2e_probe_url \
    "http://127.0.0.1:${E2E_OBJECT_PROXY_PORT}/minio/health/live" \
    "${OBJECT_PUBLIC_HOST}:${E2E_OBJECT_PROXY_PORT}"
  python3 - "${E2E_CLAMAV_PORT}" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.create_connection(("127.0.0.1", port), 10):
    pass
PY
  echo "E2E file services are ready on ${MINIO_PUBLIC_URL}."
}
