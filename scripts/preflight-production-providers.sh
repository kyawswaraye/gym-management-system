#!/usr/bin/env bash
set -euo pipefail

# Configuration-only by default. Live sockets require LIVE_PROVIDER_SMOKE=1
# and --live after real credentials exist.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ "${LIVE_PROVIDER_SMOKE:-}" == "1" ]]; then
  exec .venv/bin/python manage.py check_production_providers --live
fi

exec .venv/bin/python manage.py check_production_providers
