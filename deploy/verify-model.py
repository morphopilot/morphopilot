#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
model = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'models/nnInteractive_v1.0'
manifest = json.loads((root / 'deploy/model-manifest.json').read_text())
for relative, expected in manifest['sha256'].items():
    path = model / relative
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit(f'Checksum mismatch: {relative}')
    print('Verified:', relative)
print('Official weights match the pinned nnInteractive model manifest.')
