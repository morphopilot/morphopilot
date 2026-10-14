#!/usr/bin/env python3
"""Generate installation-local secrets and absolute runtime paths. Never overwrite."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path
import secrets
import shlex

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', type=Path, default=root / 'runtime')
parser.add_argument('--model-dir', type=Path, default=root / 'models/nnInteractive_v1.0')
parser.add_argument('--port', type=int, default=8787)
parser.add_argument('--public-origin', default='', help='For TLS reverse proxy, e.g. https://morphopilot.example.org')
args = parser.parse_args()
config = root / 'deploy/local.env'
if config.exists():
    raise SystemExit('deploy/local.env already exists; edit it deliberately or move it to a private backup first.')
if not 1024 <= args.port <= 65535:
    raise SystemExit('Choose an unprivileged port from 1024 to 65535.')
if args.public_origin and not args.public_origin.startswith('https://'):
    raise SystemExit('--public-origin must use HTTPS. For SSH forwarding omit it.')
password = getpass.getpass('Choose a workspace password (at least 12 characters): ')
if len(password) < 12 or password != getpass.getpass('Confirm password: '):
    raise SystemExit('Passwords must match and contain at least 12 characters.')
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
args.data_dir = args.data_dir.expanduser().resolve()
args.model_dir = args.model_dir.expanduser().resolve()
args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
values = {
    'MORPHOPILOT_DATA_DIR': str(args.data_dir),
    'MORPHOPILOT_MODEL_DIR': str(args.model_dir),
    'MORPHOPILOT_DATASET': 'uploads',
    'MORPHOPILOT_CATEGORY': 'neurite',
    'MORPHOPILOT_GPU_LOCK_PATH': str(args.data_dir / 'gpu.lock'),
    'MORPHOPILOT_API_TOKEN': secrets.token_urlsafe(48),
    'MORPHOPILOT_SESSION_SECRET': secrets.token_urlsafe(48),
    'MORPHOPILOT_PASSWORD_HASH': f'scrypt${salt.hex()}${digest}',
    'MORPHOPILOT_HOST': '127.0.0.1',
    'MORPHOPILOT_PORT': str(args.port),
    'MORPHOPILOT_PUBLIC_ORIGIN': args.public_origin.rstrip('/'),
    'MORPHOPILOT_COOKIE_SECURE': '1' if args.public_origin else '0',
}
fd = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('# Local secrets; never share this file or put it in a release ZIP.\n')
    for key, value in values.items():
        stream.write(key + '=' + shlex.quote(value) + '\n')
print('Wrote protected deploy/local.env. Password is stored as a salted hash.')
print('Next: bash deploy/start.sh')
