#!/usr/bin/env bash
set -euo pipefail
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 24.x is required; node was not found." >&2
  exit 1
fi
major="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "${major}" != "24" ]]; then
  echo "Node.js 24.x is required (CI uses 24.20.0); found $(node -v)." >&2
  exit 1
fi
