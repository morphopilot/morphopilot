#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"
if [[ -f deploy/local.env ]]; then
  set -a
  source deploy/local.env
  set +a
fi
exec "${MORPHOPILOT_PYTHON:-$APP_ROOT/.venv/bin/python}" deploy/doctor.py "$@"
