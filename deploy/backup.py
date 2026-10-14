#!/usr/bin/env python3
"""Offline backup with the embedded SQLite database and volume files together."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tarfile

parser = argparse.ArgumentParser(description='Stop the service first; this command does not stop it for you.')
parser.add_argument('--data-dir', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--service-stopped', action='store_true', required=True)
args = parser.parse_args()
source, output = args.data_dir.resolve(), args.output.resolve()
if not source.is_dir() or output.is_relative_to(source):
    raise SystemExit('Data directory must exist; output must be outside it.')
if output.exists():
    raise SystemExit('Refusing to overwrite an existing backup.')
job_paths = list((source / 'jobs').glob('*.json'))
for category_root in (source / 'categories').glob('*'):
    job_paths.extend((category_root / 'jobs').glob('*.json'))
for path in job_paths:
    try:
        if json.loads(path.read_text()).get('status') in {'queued', 'running'}:
            raise SystemExit('A task record is still running/queued. Let it finish or cancel it before an orderly backup.')
    except json.JSONDecodeError:
        raise SystemExit(f'Cannot read job record: {path.name}')
output.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, 'wb') as file, tarfile.open(fileobj=file, mode='w:gz', dereference=False) as archive:
        archive.add(source, arcname='runtime')
except BaseException:
    output.unlink(missing_ok=True)
    raise
print(f'Created {output}. Restore to the SAME absolute data path to preserve checkpoint/snapshot references.')
print('Separately back up deploy/local.env securely. Official model weights can be downloaded again.')
