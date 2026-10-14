#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"
if [[ -f deploy/local.env ]]; then
  set -a
  source deploy/local.env
  set +a
fi
MODEL_PATH="${MORPHOPILOT_MODEL_DIR:-$APP_ROOT/models/nnInteractive_v1.0}"
MODEL_PARENT="$(dirname -- "$MODEL_PATH")"
if [[ "$(basename -- "$MODEL_PATH")" != nnInteractive_v1.0 ]]; then
  echo 'MORPHOPILOT_MODEL_DIR must end in nnInteractive_v1.0 for this downloader.' >&2; exit 1
fi
echo 'Official model license: CC BY-NC-SA 4.0 (research / non-commercial). See deploy/THIRD_PARTY_NOTICES.md.'
.venv/bin/hf download MIC-DKFZ/nnInteractive --revision 3f308d751c00644e4fde6f09c600264b393b21b5 --include 'nnInteractive_v1.0/*' --local-dir "$MODEL_PARENT"
.venv/bin/python deploy/verify-model.py "$MODEL_PATH"
