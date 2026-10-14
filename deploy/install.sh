#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"
MODE="${1:-gpu}"
if [[ "$MODE" != gpu && "$MODE" != cpu ]]; then
  echo 'Usage: bash deploy/install.sh [gpu|cpu]' >&2; exit 2
fi
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
command -v "$PYTHON_BIN" >/dev/null || { echo 'Install Python 3.10 + venv (see docs/DEPLOYMENT.md), or set PYTHON_BIN.' >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,12), "Use Python 3.10–3.12; 3.10 is the verified environment."'
[[ -x .venv/bin/python ]] || "$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install 'pip==26.2.1' 'setuptools==84.0.0' 'wheel==0.48.0'
.venv/bin/python -m pip install -r "deploy/requirements-$MODE.lock"
.venv/bin/python -m pip check
echo 'Dependencies installed. Next: .venv/bin/python deploy/configure.py'
if [[ "$MODE" == gpu ]]; then
  echo 'Next download and verify the official model with bash deploy/download-model.sh (see docs/DEPLOYMENT.md).'
else
  echo 'CPU mode supports upload, tiling, manual mask painting, confirmation and merge. Neural inference and training require the GPU installation.'
fi
