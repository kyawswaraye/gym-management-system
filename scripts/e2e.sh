#!/usr/bin/env bash
# Run the Super Admin and tenant-settings browser smoke tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
E2E_ENV_ROOT="${ROOT}"
# shellcheck disable=SC1091
source "${ROOT}/scripts/e2e-env.sh"
e2e_prepare_env

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

npm --prefix "${ROOT}/web" run build
npm --prefix "${ROOT}/web" run test:e2e
