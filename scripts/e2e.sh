#!/usr/bin/env bash
# Run the browser smoke tests, including the Phase 2 member lifecycle.
# scripts/e2e-backend.sh recreates gym_platform_e2e before each API start.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
E2E_ENV_ROOT="${ROOT}"
# shellcheck disable=SC1091
source "${ROOT}/scripts/e2e-env.sh"
# shellcheck disable=SC1091
source "${ROOT}/scripts/e2e-files.sh"
e2e_prepare_env

e2e_cleanup() {
  local code=$?
  if [[ "${E2E_FILE_CLEANUP_DONE:-}" == "1" ]]; then
    return
  fi
  E2E_FILE_CLEANUP_DONE=1
  e2e_stop_file_services || true
  exit "${code}"
}
trap e2e_cleanup EXIT

bash "${ROOT}/scripts/check-node.sh"
bash "${ROOT}/scripts/check-python.sh"

if [[ ! -d "${ROOT}/web/node_modules/@playwright/test" ]]; then
  echo "Install the web workspace first: npm --prefix web ci" >&2
  exit 1
fi

if [[ "$(uname -s)" == "Linux" ]]; then
  npm --prefix "${ROOT}/web" exec -- playwright install --with-deps chromium
else
  npm --prefix "${ROOT}/web" exec -- playwright install chromium
fi

e2e_start_file_services &
file_services_pid=$!
build_code=0
npm --prefix "${ROOT}/web" run build || build_code=$?
file_services_code=0
wait "${file_services_pid}" || file_services_code=$?
if [[ "${build_code}" -ne 0 ]]; then
  exit "${build_code}"
fi
if [[ "${file_services_code}" -ne 0 ]]; then
  exit "${file_services_code}"
fi
npm --prefix "${ROOT}/web" run test:e2e
