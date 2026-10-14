#!/usr/bin/env python3
"""Package the current clean Git commit, excluding ignored runtime artifacts."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='Destination ZIP, outside the repository')
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        raise SystemExit('Choose an output path outside the repository.')
    if output.exists():
        raise SystemExit('Output already exists; use a new release filename.')
    changes = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if changes.strip():
        raise SystemExit('Commit or resolve source changes before packaging. Ignored local runtime files are excluded.')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='morphopilot-release-') as work:
        archive = Path(work) / 'source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--prefix=MorphoPilot/', '-o', str(archive), revision], cwd=ROOT, check=True)
        extracted = Path(work) / 'check'
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
            if package.testzip() is not None:
                raise SystemExit('Archive integrity check failed.')
        clean = extracted / 'MorphoPilot'
        subprocess.run([sys.executable, str(clean / 'scripts/check_publication.py')], cwd=clean, check=True)
        output.write_bytes(archive.read_bytes())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(f'{digest}  {output.name}\n')
    print(f'Commit: {revision}\nArchive: {output}\nSHA-256: {digest}')

if __name__ == '__main__':
    main()
