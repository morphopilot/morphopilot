#!/usr/bin/env python3
"""Check the source tree/archive for obvious accidental private artifacts.

This is a publication hygiene check, not a comprehensive secret/security audit.
It deliberately does not echo matching values.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache',
        'runtime', 'dist', 'generated', 'coverage', 'qa-output', 'release-artifacts'}
DENIED_DIRS = {'.openai', '.sites-runtime', '.wrangler', '.codex', '.agents'}
DENIED_SUFFIXES = {'.pth', '.pt', '.ckpt', '.safetensors', '.sqlite', '.sqlite3', '.db', '.pem', '.key'}
TEXT_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.mjs', '.json', '.md', '.txt', '.html', '.css', '.yml', '.yaml', '.sh', '.toml', '.env', '.example', '.cff'}
PATTERNS = {
    'private absolute path': re.compile(r'/Users/[^/\s]+/|/media/volume/[^/\s]+/'),
    'account-specific hosted site': re.compile(r'[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.chatgpt\.site'),
    'GitHub access token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'OpenAI key': re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}

def scan():
    issues = []
    count = 0
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP:
            continue
        if set(rel.parts) & DENIED_DIRS:
            issues.append((str(rel), 'private tool state'))
            continue
        if not path.is_file():
            continue
        count += 1
        if path.is_symlink():
            issues.append((str(rel), 'symlink: verify target before publication'))
        if path.name in {'.env', 'local.env'} or path.suffix in DENIED_SUFFIXES:
            issues.append((str(rel), 'runtime/secret/model artifact'))
        if path.stat().st_size > 15 * 1024 * 1024:
            issues.append((str(rel), 'file exceeds public source size limit'))
        if path.suffix in TEXT_SUFFIXES and path != Path(__file__).resolve():
            content = path.read_text(errors='replace')
            for label, pattern in PATTERNS.items():
                if pattern.search(content):
                    issues.append((str(rel), label))
    for name, reason in issues:
        print(f'FAIL {name}: {reason}')
    print(f'Checked {count} source/assets files; {len(issues)} issue(s).')
    return 1 if issues else 0

if __name__ == '__main__':
    sys.exit(scan())
