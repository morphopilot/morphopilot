#!/usr/bin/env python3
"""Offline import of a trusted MorphoPilot dual-stem checkpoint. Does not activate it."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('--checkpoint', type=Path, required=True)
parser.add_argument('--sha256', required=True, help='Obtain this checksum from the researcher separately')
parser.add_argument('--data-dir', type=Path, required=True)
parser.add_argument('--label', required=True)
parser.add_argument('--parent', required=True, help='Recorded parent model ID or provenance label')
parser.add_argument('--service-stopped', action='store_true', required=True)
parser.add_argument('--trust-checkpoint', action='store_true', required=True, help='PyTorch checkpoints are executable trusted input, not image uploads')
args = parser.parse_args()
source = args.checkpoint.resolve()
digest = hashlib.sha256()
with source.open('rb') as stream:
    for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
        digest.update(chunk)
if digest.hexdigest() != args.sha256.lower():
    raise SystemExit('Checkpoint checksum does not match. Nothing imported.')
import torch
payload = torch.load(source, map_location='cpu', weights_only=False)
if not isinstance(payload, dict) or not all(key in payload for key in ['prompt_weights', 'auto_stem_weights', 'auto_ready']):
    raise SystemExit('This is not a supported dual-stem checkpoint. Do not use an arbitrary model file.')
saved = payload.get('meta', {})
if saved.get('parent') and saved['parent'] != args.parent:
    raise SystemExit(f'Parent mismatch: checkpoint records {saved["parent"]!r}; nothing imported.')
mid = 'import_' + uuid.uuid4().hex[:16]
target = args.data_dir.resolve() / 'models' / mid
target.mkdir(parents=True)
try:
    shutil.copy2(source, target / 'checkpoint.pth')
    meta = {'id': mid, 'label': args.label, 'phase': 'auto' if payload['auto_ready'] else 'prompt', 'auto_ready': bool(payload['auto_ready']), 'confirmed_count': int(saved.get('confirmed_count', 0)), 'parent': args.parent, 'created_at': time.time(), 'checkpoint': str(target / 'checkpoint.pth'), 'source_sha256': args.sha256.lower(), 'imported': True}
    (target / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')
except BaseException:
    shutil.rmtree(target)
    raise
print(f'Imported inactive model {mid}. The active model was not changed. An administrator can activate it through the authenticated /models/activate API.')
