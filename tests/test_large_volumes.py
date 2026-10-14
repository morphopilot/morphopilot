"""CPU API tests: real uploads, disk-backed import, review, and exact reassembly."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1] / 'backend'


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('MORPHOPILOT_DATA_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('MORPHOPILOT_API_TOKEN', 'test-token-only-not-for-production-123')
    monkeypatch.setenv('MORPHOPILOT_BOOTSTRAP_DEMO', 'false')
    monkeypatch.setenv('MORPHOPILOT_GPU_LOCK_PATH', str(tmp_path / 'gpu.lock'))
    monkeypatch.syspath_prepend(str(BACKEND))
    spec = importlib.util.spec_from_file_location('morphopilot_volume_test_app', BACKEND / 'app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.app, headers={'Authorization': 'Bearer test-token-only-not-for-production-123'})
    yield client, module
    module.VOLUMES.pool.shutdown(wait=True)
    module.POOL.shutdown(wait=True)
    client.close()


def upload(api, raw, name='image.npy', tile_shape=(3, 4, 5)):
    client, module = api
    r = client.post('/volumes/uploads', json={'name': name, 'size': len(raw), 'tile_shape': tile_shape})
    assert r.status_code == 200, r.text
    uid = r.json()['upload']['id']
    for offset in range(0, len(raw), 1024 * 1024):
        r = client.post(f'/volumes/uploads/{uid}/chunk?offset={offset}', content=raw[offset:offset + 1024 * 1024])
        assert r.status_code == 200, r.text
    r = client.post(f'/volumes/uploads/{uid}/complete')
    assert r.status_code == 200, r.text
    vid = r.json()['volume']['id']
    for _ in range(500):
        detail = client.get(f'/volumes/{vid}').json()
        if detail['volume']['status'] != 'importing':
            return vid, detail, uid
        time.sleep(.01)
    raise AssertionError('Import timed out')


def npy_bytes(image):
    stream = io.BytesIO()
    np.save(stream, image, allow_pickle=False)
    return stream.getvalue()


def wait_merge(client, vid):
    for _ in range(500):
        detail = client.get(f'/volumes/{vid}').json()
        if detail['volume']['merge_status'] != 'merging':
            return detail['volume']
        time.sleep(.01)
    raise AssertionError('Merge timed out')


def test_fresh_workspace_and_auth(api):
    client, module = api
    assert client.get('/state').json()['samples'] == []
    assert client.get('/state').json()['capabilities']['default_tile_shape'] == [25, 192, 192]
    assert client.get('/state').json()['capabilities']['workspaces'][0]['id'] == 'neurite'
    assert client.get('/state', headers={'Authorization': ''}).status_code == 401


def test_resumable_idempotent_chunk_and_complete(api):
    client, module = api
    raw = npy_bytes(np.arange(60, dtype=np.uint16).reshape(3, 4, 5))
    uid = client.post('/volumes/uploads', json={'name': '../image.npy', 'size': len(raw)}).json()['upload']['id']
    first = raw[:100]
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=first).status_code == 200
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=first).json()['upload']['offset'] == 100
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=b'x' * 100).status_code == 409
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=120', content=b'abc').status_code == 409
    assert client.post(f'/volumes/uploads/{uid}/complete').status_code == 409
    assert client.get(f'/volumes/uploads/{uid}').json()['upload']['offset'] == 100
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=100', content=raw[100:]).status_code == 200
    first_complete = client.post(f'/volumes/uploads/{uid}/complete').json()['volume']['id']
    second_complete = client.post(f'/volumes/uploads/{uid}/complete').json()['volume']['id']
    assert first_complete == second_complete
    assert client.get(f'/volumes/uploads/{uid}').json()['upload']['volume_id'] == first_complete


def test_odd_edges_exact_mask_merge_and_stale_revision(api):
    client, module = api
    raw = np.arange(7 * 9 * 11, dtype=np.uint16).reshape(7, 9, 11)
    vid, detail, uid = upload(api, npy_bytes(raw))
    assert detail['volume']['status'] == 'ready', detail
    assert detail['volume']['total_tiles'] == 27
    assert detail['tiles'][-1]['shape'] == [1, 1, 1]
    np.testing.assert_array_equal(np.load(module.ROOT / 'volumes' / vid / 'raw.npy'), raw)
    assert client.post(f'/volumes/{vid}/merge', json={}).status_code == 409
    expected = ((raw % 7) < 2).astype(np.uint8)
    for meta in detail['tiles']:
        sid = meta['id']
        sl = tuple(slice(a, a + n) for a, n in zip(meta['origin_zyx'], meta['shape']))
        # Exercise canonical save_mask -> automatic SQLite revision sync.
        module.save_mask(module.ROOT / 'samples' / sid, expected[sl], {'tool': 'qa'})
        response = client.post(f'/samples/{sid}/confirm', json={'revision': 1, 'reviewed': True, 'empty_background': not bool(expected[sl].any())})
        assert response.status_code == 200, response.text
    detail = client.get(f'/volumes/{vid}').json()
    assert detail['volume']['confirmed_tiles'] == 27
    assert client.post(f'/volumes/{vid}/merge', json={}).status_code == 200
    volume = wait_merge(client, vid)
    assert volume['merge_status'] == 'ready', volume
    downloaded = client.get(f'/volumes/{vid}/export')
    assert downloaded.status_code == 200
    merged = tifffile.imread(io.BytesIO(downloaded.content))
    np.testing.assert_array_equal(merged, expected)
    assert merged.shape == raw.shape
    preview = client.get(f'/volumes/{vid}/slice?z=6&max_size=64').json()
    mask = np.frombuffer(__import__('base64').b64decode(preview['mask']), np.uint8).reshape(preview['shape'])
    np.testing.assert_array_equal(mask, expected[6])
    assert not preview['partial']
    sid = detail['tiles'][0]['id']
    assert client.post(f'/samples/{sid}/reopen', json={'revision': 1}).status_code == 200
    assert client.get(f'/volumes/{vid}').json()['volume']['merge_status'] == 'stale'
    assert client.get(f'/volumes/{vid}/export').status_code == 409


def test_empty_review_requires_explicit_flag_and_constant_tiles_skip_training(api):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.zeros((3, 4, 5), np.float32)))
    sid = detail['tiles'][0]['id']
    assert not detail['tiles'][0]['inference_eligible']
    assert client.post(f'/samples/{sid}/confirm', json={'revision': 0, 'reviewed': True}).status_code == 422
    interaction = {'revision': 0, 'tool': 'point', 'include': True, 'points': [[1, 2, 3]]}
    assert client.post(f'/samples/{sid}/interaction', json=interaction).status_code == 422
    assert client.post(f'/samples/{sid}/confirm', json={'revision': 0, 'reviewed': True, 'empty_background': True}).status_code == 200
    assert client.post('/train', json={'volume_id': vid, 'steps': 1}).status_code == 400
    assert client.post(f'/volumes/{vid}/merge', json={}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'
    np.testing.assert_array_equal(tifffile.imread(io.BytesIO(client.get(f'/volumes/{vid}/export').content)), np.zeros((3, 4, 5), np.uint8))


def test_training_scope_and_empty_nonconstant_labels(api, monkeypatch):
    client, module = api
    ids = []
    for i in range(2):
        vid, detail, _ = upload(api, npy_bytes(np.arange(60, dtype=np.float32).reshape(3, 4, 5) + i))
        sid = detail['tiles'][0]['id']
        assert client.post(f'/samples/{sid}/confirm', json={'revision': 0, 'reviewed': True, 'empty_background': True}).status_code == 200
        ids.append(vid)
    captured = []
    monkeypatch.setattr(module, 'require_neural_runtime', lambda: None)
    monkeypatch.setattr(module, 'submit', lambda kind, fn, **extra: captured.append(extra) or {'job': extra})
    category_training = client.post('/train', json={'steps': 1})
    assert category_training.status_code == 200, category_training.text
    assert captured[0]['confirmed_count'] == 2
    assert captured[0]['training_scope'] == 'category'
    assert captured[0]['volume_id'] is None
    result = client.post('/train', json={'volume_id': ids[0], 'steps': 1})
    assert result.status_code == 200, result.text
    assert captured[1]['confirmed_count'] == 1
    assert captured[1]['volume_id'] == ids[0]
    assert captured[1]['training_scope'] == 'volume'
    assert captured[1]['skipped_normalization_tiles'] == []


@pytest.mark.parametrize('fmt', ['npy', 'npz', 'tiff'])
def test_format_decode_and_axis_validation(api, fmt):
    image = np.asfortranarray(np.arange(5 * 6 * 7, dtype=np.float32).reshape(5, 6, 7))
    stream = io.BytesIO()
    if fmt == 'npy': np.save(stream, image)
    elif fmt == 'npz': np.savez_compressed(stream, image=image)
    else: tifffile.imwrite(stream, image, metadata={'axes': 'ZYX'}, photometric='minisblack', compression='zlib')
    vid, detail, _ = upload(api, stream.getvalue(), name='input.' + fmt)
    assert detail['volume']['status'] == 'ready', detail
    np.testing.assert_array_equal(np.load(api[1].ROOT / 'volumes' / vid / 'raw.npy'), image)


@pytest.mark.parametrize('image', [np.zeros((2, 3, 4, 5)), np.full((2, 3, 4), np.nan), np.full((2, 3, 4), complex(1, 2)), np.full((2, 3, 4), 1e100)])
def test_invalid_inputs_fail_without_publishing_tiles(api, image):
    vid, detail, _ = upload(api, npy_bytes(image))
    assert detail['volume']['status'] == 'failed', detail
    assert detail['volume']['error']
    assert detail['tiles'] == []
    assert api[0].get('/state').json()['samples'] == []
    assert not (api[1].ROOT / 'volumes' / vid / 'raw.npy').exists()


def test_draft_export_explicit_and_unreviewed_preview(api):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    assert client.get(f'/volumes/{vid}/slice?z=0').json()['partial']
    assert client.post(f'/volumes/{vid}/merge', json={'draft': True}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'
    assert client.get(f'/volumes/{vid}/export').status_code == 409
    assert client.get(f'/volumes/{vid}/export?draft=true').status_code == 200


def test_preview_exposes_saved_tile_progress_without_confirming(api):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    sid = detail['tiles'][0]['id']

    def preview_tile():
        preview = client.get(f'/volumes/{vid}/slice?z=0').json()
        # Progress is tile-wide, including a slice with no saved foreground.
        assert not any(__import__('base64').b64decode(preview['mask']))
        return preview, preview['tiles'][0]

    preview, tile = preview_tile()
    assert (tile['events'], tile['revision'], tile['confirmed']) == (0, 0, False)
    assert preview['partial']

    saved = np.zeros((3, 4, 5), np.uint8)
    saved[1, 2, 3] = 1
    module.save_mask(module.ROOT / 'samples' / sid, saved, {'tool': 'qa'})
    preview, tile = preview_tile()
    assert (tile['events'], tile['revision'], tile['confirmed']) == (1, 1, False)
    assert preview['confirmed_tiles'] == 0
    assert preview['partial']
    assert client.post(f'/volumes/{vid}/merge', json={}).status_code == 409

    assert client.post(f'/samples/{sid}/confirm', json={'revision': 1, 'reviewed': True}).status_code == 200
    preview, tile = preview_tile()
    assert (tile['events'], tile['revision'], tile['confirmed']) == (1, 1, True)
    assert not preview['partial']

    assert client.post(f'/samples/{sid}/reopen', json={'revision': 1}).status_code == 200
    assert client.post(f'/samples/{sid}/reset', json={'revision': 2}).status_code == 200
    preview, tile = preview_tile()
    assert (tile['events'], tile['revision'], tile['confirmed']) == (0, 3, False)
    assert preview['partial']


def test_restart_keeps_ready_collection_and_upload_offset(api):
    from volume_store import VolumeStore
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    uid = client.post('/volumes/uploads', json={'name': 'other.npy', 'size': 1000}).json()['upload']['id']
    client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=b'123456')
    reloaded = VolumeStore(module.ROOT)
    try:
        assert reloaded.detail(vid)['volume']['status'] == 'ready'
        assert reloaded.upload_status(uid)['offset'] == 6
    finally:
        reloaded.pool.shutdown(wait=True)


def test_merge_snapshot_disk_failure_is_recoverable(api, monkeypatch):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    import volume_store
    with monkeypatch.context() as m:
        def disk_full(*args, **kwargs): raise OSError('Disk full')
        m.setattr(volume_store.shutil, 'copyfile', disk_full)
        failed = client.post(f'/volumes/{vid}/merge', json={'draft': True})
        assert failed.status_code == 507
    assert client.get(f'/volumes/{vid}').json()['volume']['merge_status'] == 'failed'
    assert not list((module.ROOT / 'volumes' / vid).glob('merge_*'))
    assert client.post(f'/volumes/{vid}/merge', json={'draft': True}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'


def test_changed_tile_during_merge_marks_result_stale(api, monkeypatch):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    store = module.VOLUMES
    pending = []
    with monkeypatch.context() as m:
        m.setattr(store.pool, 'submit', lambda fn, *args: pending.append((fn, args)))
        assert client.post(f'/volumes/{vid}/merge', json={'draft': True}).status_code == 200
    meta = detail['tiles'][0]
    module.save_mask(module.ROOT / 'samples' / meta['id'], np.ones(meta['shape'], np.uint8), {'tool': 'qa'})
    fn, args = pending[0]
    fn(*args)
    assert client.get(f'/volumes/{vid}').json()['volume']['merge_status'] == 'stale'
    assert client.get(f'/volumes/{vid}/export?draft=true').status_code == 409


def test_incomplete_import_restart_cleans_only_its_tiles(api, monkeypatch):
    client, module = api
    raw = npy_bytes(np.arange(60).reshape(3, 4, 5))
    uid = client.post('/volumes/uploads', json={'name': 'restart.npy', 'size': len(raw)}).json()['upload']['id']
    client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=raw)
    with monkeypatch.context() as m:
        m.setattr(module.VOLUMES.pool, 'submit', lambda *args: None)
        vid = client.post(f'/volumes/uploads/{uid}/complete').json()['volume']['id']
    orphan = module.ROOT / 'samples' / (vid + '_tile_00001')
    orphan.mkdir()
    legacy = module.ROOT / 'samples' / 'legacy_user_sample'
    legacy.mkdir()
    from volume_store import VolumeStore
    reloaded = VolumeStore(module.ROOT)
    try:
        assert reloaded.detail(vid)['volume']['status'] == 'failed'
        assert not orphan.exists()
        assert legacy.exists()
    finally: reloaded.pool.shutdown(wait=True)


def test_color_tiff_rejected(api):
    stream = io.BytesIO()
    tifffile.imwrite(stream, np.zeros((5, 6, 3), np.uint8), photometric='rgb')
    _, detail, _ = upload(api, stream.getvalue(), name='rgb.tif')
    assert detail['volume']['status'] == 'failed'
    assert 'single-channel' in detail['volume']['error']


def test_declared_and_actual_chunk_limits(api):
    client, module = api
    uid = client.post('/volumes/uploads', json={'name': 'limit.npy', 'size': 10 * 1024**2}).json()['upload']['id']
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=b'x', headers={'Content-Length': str(9 * 1024**2)}).status_code == 413
    assert client.post(f'/volumes/uploads/{uid}/chunk?offset=0', content=b'x' * (8 * 1024**2 + 1)).status_code == 413
    assert client.get(f'/volumes/uploads/{uid}').json()['upload']['offset'] == 0


def test_cpu_review_mode_reports_neural_runtime_unavailable_without_queuing(api, monkeypatch):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60, dtype=np.float32).reshape(3, 4, 5)))
    sid = detail['tiles'][0]['id']
    # This must also pass on developer machines that happen to have torch.
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda name: None)
    response = client.post(f'/samples/{sid}/interaction', json={'revision': 0, 'tool': 'point', 'points': [[1, 2, 3]], 'include': True})
    assert response.status_code == 503
    assert 'CPU-only installation' in response.json()['detail']
    assert 'Install the GPU requirements' in response.json()['detail']
    assert module.all_jobs() == []
    # Manual painting, confirmation and mask merging are still real operations.
    painted = client.post(f'/samples/{sid}/interaction', json={'revision': 0, 'tool': 'brush', 'points': [[1, 2, 3]], 'include': True, 'radius': 1})
    assert painted.status_code == 200, painted.text
    assert client.post(f'/samples/{sid}/confirm', json={'revision': 1, 'reviewed': True}).status_code == 200
    trained = client.post('/train', json={'volume_id': vid, 'steps': 1})
    assert trained.status_code == 503
    assert module.all_jobs() == []
    assert client.post('/models/activate', json={'model_id': 'official'}).status_code == 503
    # Metadata-only fake parent lets conversion and Auto reach the availability
    # guard. No model compute or fake prediction is performed.
    monkeypatch.setattr(module, 'state_config', lambda: {'active_model': 'test_dual'})
    monkeypatch.setattr(module, 'model_info', lambda _: {'auto_ready': True, 'confirmed_count': 1})
    assert client.post('/models/convert').status_code == 503
    assert client.post(f'/samples/{sid}/reopen', json={'revision': 1}).status_code == 200
    assert client.post(f'/samples/{sid}/auto', json={'revision': 2}).status_code == 503
    assert module.all_jobs() == []
    assert client.post(f'/samples/{sid}/confirm', json={'revision': 2, 'reviewed': True}).status_code == 200
    assert client.post(f'/volumes/{vid}/merge', json={}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'
    assert client.get(f'/volumes/{vid}/export').status_code == 200
