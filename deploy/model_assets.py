"""Portable, pinned official nnInteractive assets; never deserialize checkpoints."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile

APP_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = 'nnInteractive_v1.0'
MAX_UNPACKED_BYTES = 1024 ** 3
MAX_MANIFEST_BYTES = 64 * 1024


def pinned_manifest():
    return json.loads((APP_ROOT / 'deploy/model-manifest.json').read_text())


def sha256(path):
    state = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 ** 2), b''):
            state.update(chunk)
    return state.hexdigest()


def checked_relative(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Invalid official model archive path.')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name:
        raise ValueError('Unsafe official model archive path.')
    return relative


def verify_directory(directory, expected=None):
    expected = expected or pinned_manifest()
    directory = Path(directory)
    for name, checksum in expected['sha256'].items():
        checked_relative(name)
        path = directory / name
        if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents if parent != directory.parent):
            raise ValueError('Missing or linked official model asset: ' + name)
        if sha256(path) != checksum:
            raise ValueError('Checksum mismatch: ' + name)
    return expected


def export_archive(source, archive_path, expected=None):
    """Export only files in the pinned manifest, with a manifest as first entry."""
    expected = verify_directory(source, expected)
    archive_path = Path(archive_path)
    payload = json.dumps({'format': 'morphopilot-official-model-v1', **expected}, indent=2).encode()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing a previous verified export.
    with archive_path.open('xb') as output:
        try:
            with tarfile.open(fileobj=output, mode='w:gz') as archive:
                member = tarfile.TarInfo(ARCHIVE_ROOT + '/MANIFEST.json')
                member.size = len(payload)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(payload))
                for name in sorted(expected['sha256']):
                    path = Path(source) / name
                    member = tarfile.TarInfo(ARCHIVE_ROOT + '/' + name)
                    member.size = path.stat().st_size
                    member.mode = 0o644
                    with path.open('rb') as source_file:
                        archive.addfile(member, source_file)
        except BaseException:
            archive_path.unlink(missing_ok=True)
            raise
    return expected


def extract_verified(archive_path, target, expected=None):
    """Extract an exact pinned file set in one pass with size and path guards."""
    expected = expected or pinned_manifest()
    manifest = None
    seen = set()
    total = 0
    with tarfile.open(archive_path, 'r|*') as archive:
        for member in archive:
            name = member.name.rstrip('/') if member.isdir() else member.name
            checked_relative(name)
            if member.isdir():
                if name not in {ARCHIVE_ROOT, ARCHIVE_ROOT + '/fold_0'}:
                    raise ValueError('Unexpected official model directory.')
                continue
            if not member.isfile() or member.name in seen:
                raise ValueError('Official model archive contains a link, special file or duplicate.')
            seen.add(member.name)
            total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError('Official model archive exceeds the unpacked size limit.')
            if manifest is None:
                if member.name != ARCHIVE_ROOT + '/MANIFEST.json' or member.size > MAX_MANIFEST_BYTES:
                    raise ValueError('First official model entry must be MANIFEST.json.')
                manifest = json.load(archive.extractfile(member))
                if manifest != {'format': 'morphopilot-official-model-v1', **expected}:
                    raise ValueError('Official model archive does not match the pinned model manifest.')
                continue
            prefix = ARCHIVE_ROOT + '/'
            relative = member.name.removeprefix(prefix)
            if not member.name.startswith(prefix) or relative not in expected['sha256']:
                raise ValueError('Unexpected official model asset: ' + member.name)
            output = Path(target).joinpath(*checked_relative(relative).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            state = hashlib.sha256()
            with archive.extractfile(member) as stream, output.open('xb') as destination:
                for chunk in iter(lambda: stream.read(8 * 1024 ** 2), b''):
                    state.update(chunk)
                    destination.write(chunk)
            if state.hexdigest() != expected['sha256'][relative]:
                raise ValueError('Checksum mismatch: ' + relative)
    required = {ARCHIVE_ROOT + '/MANIFEST.json', *(ARCHIVE_ROOT + '/' + name for name in expected['sha256'])}
    if manifest is None or seen != required:
        raise ValueError('Official model archive is missing required files.')
    return expected


def import_archive(archive_path, destination, expected=None):
    """Commit only a fully verified directory, without replacing any existing one."""
    destination = Path(destination).expanduser()
    if destination.is_symlink() or destination.exists():
        raise ValueError('Model directory already exists; refusing to overwrite it.')
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.official-model-', dir=destination.parent))
    try:
        report = extract_verified(archive_path, stage, expected)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Model directory appeared during import; refusing to overwrite it.')
        os.rename(stage, destination)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)
