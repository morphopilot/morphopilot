"""Official asset integrity, archive boundaries, and non-destructive installation."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/model_assets.py'
spec = importlib.util.spec_from_file_location('model_assets', SCRIPT)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def fixture(tmp_path):
    source = tmp_path / 'source'
    files = {'plans.json': b'{"plan":true}', 'dataset.json': b'{}',
             'inference_session_class.json': b'{}', 'LICENSE': b'fixture license',
             'fold_0/checkpoint_final.pth': b'fixture bytes; must never be unpickled'}
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {'model': 'nnInteractive_v1.0', 'revision': 'pinned-fixture',
                'sha256': {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    return source, files, manifest


def write_archive(path, files, manifest, extra=None):
    content = {'MANIFEST.json': json.dumps({'format':'morphopilot-official-model-v1', **manifest}).encode(), **files}
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in content.items():
            member = tarfile.TarInfo('nnInteractive_v1.0/' + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return path


def test_export_and_import_preserve_all_required_official_assets(tmp_path):
    source, files, manifest = fixture(tmp_path)
    archive = tmp_path / 'model.tar.gz'
    assets.export_archive(source, archive, manifest)
    destination = tmp_path / 'configured models' / 'nnInteractive_v1.0'
    report = assets.import_archive(archive, destination, manifest)
    assert report == manifest
    assert {p.relative_to(destination).as_posix(): p.read_bytes() for p in destination.rglob('*') if p.is_file()} == files
    assert not list(destination.parent.glob('.official-model-*'))


@pytest.mark.parametrize('nonempty', [False, True])
def test_import_refuses_even_an_empty_existing_directory(tmp_path, nonempty):
    source, files, manifest = fixture(tmp_path)
    archive = write_archive(tmp_path / 'model.tar.gz', files, manifest)
    destination = tmp_path / 'model'
    destination.mkdir()
    if nonempty:
        (destination / 'keep').write_text('existing model')
    with pytest.raises(ValueError, match='already exists'):
        assets.import_archive(archive, destination, manifest)
    assert destination.is_dir()
    assert list(destination.iterdir()) == ([destination / 'keep'] if nonempty else [])


def test_export_refuses_existing_archive_and_changed_source(tmp_path):
    source, files, manifest = fixture(tmp_path)
    archive = tmp_path / 'model.tar.gz'
    assets.export_archive(source, archive, manifest)
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        assets.export_archive(source, archive, manifest)
    assert archive.read_bytes() == original
    (source / 'plans.json').write_text('changed')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        assets.export_archive(source, tmp_path / 'new.tar.gz', manifest)
    assert not (tmp_path / 'new.tar.gz').exists()


@pytest.mark.parametrize('problem', ['corrupt', 'missing', 'manifest', 'traversal', 'link', 'duplicate', 'extra'])
def test_invalid_archive_never_creates_a_partial_model(tmp_path, problem):
    source, files, manifest = fixture(tmp_path)
    archive_manifest = manifest
    extra = None
    if problem == 'corrupt':
        files['fold_0/checkpoint_final.pth'] += b'tamper'
    elif problem == 'missing':
        del files['dataset.json']
    elif problem == 'manifest':
        archive_manifest = {**manifest, 'revision': 'untrusted'}
    elif problem == 'traversal':
        extra = tarfile.TarInfo('nnInteractive_v1.0/../../outside')
    elif problem == 'link':
        extra = tarfile.TarInfo('nnInteractive_v1.0/linked')
        extra.type = tarfile.SYMTYPE
        extra.linkname = '/tmp/outside'
    elif problem == 'duplicate':
        extra = tarfile.TarInfo('nnInteractive_v1.0/dataset.json')
    else:
        extra = tarfile.TarInfo('nnInteractive_v1.0/private-token.txt')
    archive = write_archive(tmp_path / 'bad.tar.gz', files, archive_manifest, extra)
    destination = tmp_path / 'models' / 'nnInteractive_v1.0'
    with pytest.raises(ValueError):
        assets.import_archive(archive, destination, manifest)
    assert not destination.exists()
    assert not list(destination.parent.glob('.official-model-*'))
    assert not (tmp_path / 'outside').exists()


def test_size_limit_applies_before_writing_large_file(tmp_path, monkeypatch):
    source, files, manifest = fixture(tmp_path)
    archive = write_archive(tmp_path / 'model.tar.gz', files, manifest)
    monkeypatch.setattr(assets, 'MAX_UNPACKED_BYTES', 10)
    with pytest.raises(ValueError, match='size limit'):
        assets.import_archive(archive, tmp_path / 'model', manifest)


def test_destination_symlink_is_never_followed(tmp_path):
    source, files, manifest = fixture(tmp_path)
    archive = write_archive(tmp_path / 'model.tar.gz', files, manifest)
    target = tmp_path / 'real-model'
    target.mkdir()
    link = tmp_path / 'model'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='already exists'):
        assets.import_archive(archive, link, manifest)
    assert link.is_symlink() and list(target.iterdir()) == []
