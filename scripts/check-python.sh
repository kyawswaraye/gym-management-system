#!/usr/bin/env bash
set -euo pipefail
required="3.14.7"
root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "${root}/backend/.venv/bin/python" ]]; then
  interpreter="${root}/backend/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  interpreter="$(command -v python3)"
else
  echo "Python ${required} is required; python3 was not found." >&2
  exit 1
fi
actual="$("${interpreter}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if [[ "${actual}" != "${required}" ]]; then
  echo "Python ${required} is required; ${interpreter} reports ${actual}." >&2
  echo "Recreate backend/.venv with Python ${required} before continuing." >&2
  exit 1
fi
