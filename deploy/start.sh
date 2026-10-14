#!/usr/bin/env bash
set -euo pipefail
umask 077
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"
if [[ ! -f deploy/local.env ]]; then
  echo 'Missing deploy/local.env. Run python3 deploy/configure.py first.' >&2
  exit 1
fi
set -a
source deploy/local.env
set +a
PYTHON_BIN="${MORPHOPILOT_PYTHON:-$APP_ROOT/.venv/bin/python}"
exec "$PYTHON_BIN" standalone/serve.py
