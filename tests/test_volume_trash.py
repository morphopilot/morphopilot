"""Whole-volume removal and restore preserve data and training provenance."""
import hashlib

import numpy as np
import pytest

from test_large_volumes import api, upload, npy_bytes, wait_merge
from test_categories import category_apps, upload_volume


def tree_hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}


def trash(client, detail, **override):
    volume = detail['volume']
    return client.post('/volumes/' + volume['id'] + '/trash', json={
        'name': volume['name'], 'revision': volume['revision'], **override})


def test_whole_volume_round_trip_excludes_training_keeps_snapshots_models_and_exports(api, monkeypatch):
    client, module = api
    vid, detail, uid = upload(api, npy_bytes(np.arange(120).reshape(3, 4, 10)))
    sid = detail['tiles'][0]['id']
    assert client.post('/samples/' + sid + '/interaction', json={
        'tool': 'brush', 'points': [[1, 2, 2]], 'radius': 1, 'revision': 0}).status_code == 200
    assert client.post('/samples/' + sid + '/confirm', json={
        'revision': 1, 'reviewed': True}).status_code == 200
    assert client.post('/volumes/' + vid + '/merge', json={'draft': True}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'
    detail = client.get('/volumes/' + vid).json()
    model_folder = module.ROOT / 'models' / 'trained_model'
    model_folder.mkdir()
    (model_folder / 'checkpoint.pth').write_bytes(b'existing-checkpoint-must-not-change')
    module.atomic({'id': 'trained_model', 'phase': 'prompt', 'label': 'Previously trained',
                   'created_at': 1, 'confirmed_count': 1, 'auto_ready': False,
                   'checkpoint': str(model_folder / 'checkpoint.pth')}, model_folder / 'meta.json')
    historical = {'samples': [{'snapshot': str(p)} for p in (module.ROOT / 'snapshots').glob('*')]}
    module.atomic(historical, module.ROOT / 'models' / 'training_history.json')
    saved = {part: tree_hashes(module.ROOT / part) for part in ['models', 'snapshots', 'samples', 'volumes']}
    result = trash(client, detail)
    assert result.status_code == 200, result.text
    record = result.json()
    assert record['status'] == 'trashed'
    assert record['total_tiles'] == 2
    assert record['confirmed_tiles'] == 1
    assert record['retains_disk_space'] and record['preserved_training_snapshots']
    assert client.get('/volumes').json()['volumes'] == []
    assert client.get('/volumes/trash').json()['items'] == [record]
    assert client.get('/state').json()['samples'] == []
    assert client.get('/state').json()['confirmed_count'] == 0
    assert client.get('/state').json()['trainable_count'] == 0
    for url in ['/volumes/' + vid, '/volumes/' + vid + '/slice',
                '/volumes/' + vid + '/export?draft=true', '/volumes/uploads/' + uid,
                '/samples/' + sid + '/volume', '/samples/' + sid + '/export']:
        assert client.get(url).status_code == 404, url
    for suffix in ['/merge', '/trash']:
        assert client.post('/volumes/' + vid + suffix, json={'draft': True, 'name': detail['volume']['name'], 'revision': detail['volume']['revision']}).status_code == 404
    for action, body in [('interaction', {'tool': 'brush', 'points': [[1, 2, 2]], 'revision': 1}),
                         ('reopen', {'revision': 1}), ('confirm', {'revision': 1, 'reviewed': True}),
                         ('auto', {'revision': 1})]:
        assert client.post('/samples/' + sid + '/' + action, json=body).status_code == 404, action
    monkeypatch.setattr(module, 'require_neural_runtime', lambda: None)
    assert client.post('/train', json={'steps': 1}).status_code == 400
    assert client.post('/train', json={'steps': 1, 'volume_id': vid}).status_code == 404
    assert module.model_info('trained_model')['checkpoint'] == str(model_folder / 'checkpoint.pth')
    assert {m['id'] for m in client.get('/state').json()['models']} == {'official', 'trained_model'}
    assert tree_hashes(module.ROOT / 'models') == saved['models']
    assert tree_hashes(module.ROOT / 'snapshots') == saved['snapshots']
    restored = client.post('/volumes/trash/' + record['trash_id'] + '/restore', json={})
    assert restored.status_code == 200, restored.text
    assert restored.json()['volume']['id'] == vid
    assert client.get('/volumes/trash').json()['items'] == []
    assert client.get('/state').json()['confirmed_count'] == 1
    assert client.get('/volumes/uploads/' + uid).json()['upload']['volume_id'] == vid
    assert client.get('/volumes/' + vid + '/export?draft=true').status_code == 200
    for part, hashes in saved.items():
        assert tree_hashes(module.ROOT / part) == hashes, part
    assert client.post('/volumes/trash/' + record['trash_id'] + '/restore', json={}).status_code == 200


def test_confirmation_busy_import_merge_and_restore_collision_guards(api):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    assert trash(client, detail, name='wrong.npy').status_code == 409
    assert trash(client, detail, revision=99).status_code == 409
    assert trash(client, detail, category='spot').status_code == 422
    module.atomic({'id': 'running', 'status': 'running', 'created_at': 1}, module.ROOT / 'jobs' / 'running.json')
    assert trash(client, detail).status_code == 409
    (module.ROOT / 'jobs' / 'running.json').unlink()
    for change in [{'status': 'importing'}, {'merge_status': 'merging'}]:
        module.VOLUMES._put('volumes', {**detail['volume'], **change})
        assert trash(client, detail).status_code == 409
    module.VOLUMES._put('volumes', detail['volume'])
    record = trash(client, detail).json()
    collision = module.ROOT / 'samples' / detail['tiles'][0]['id']
    collision.mkdir()
    (collision / 'keep.txt').write_text('keep this unrelated data')
    result = client.post('/volumes/trash/' + record['trash_id'] + '/restore', json={})
    assert result.status_code == 409
    assert (collision / 'keep.txt').read_text() == 'keep this unrelated data'
    assert client.get('/volumes/trash').json()['items'][0]['status'] == 'trashed'
    assert client.get('/volumes').json()['volumes'] == []


@pytest.mark.parametrize('operation', ['delete', 'restore'])
def test_restart_finishes_interrupted_transaction_without_partial_visibility(api, monkeypatch, operation):
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(120).reshape(3, 4, 10)))
    expected = {part: tree_hashes(module.ROOT / part) for part in ['samples', 'volumes']}
    method = module.VOLUMES._move_owned
    if operation == 'restore':
        record = trash(client, detail).json()
    calls = 0
    def interrupt(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated process interruption')
        return method(source, destination)
    monkeypatch.setattr(module.VOLUMES, '_move_owned', interrupt)
    if operation == 'delete':
        result = trash(client, detail)
    else:
        result = client.post('/volumes/trash/' + record['trash_id'] + '/restore', json={})
    assert result.status_code == 503, result.text
    assert client.get('/state').json()['samples'] == []
    assert client.get('/volumes').json()['volumes'] == []
    # During interruption a tile folder may still exist; old tile IDs must not
    # bypass the deleted/restoring parent-volume guard.
    for meta in detail['tiles']:
        assert client.get('/samples/' + meta['id'] + '/volume').status_code == 404
    module.VOLUMES.pool.shutdown(wait=True)
    module.VOLUMES = module.VolumeStore(module.ROOT, module.LOCK, category=module.CATEGORY)
    if operation == 'delete':
        assert client.get('/volumes').json()['volumes'] == []
        record = client.get('/volumes/trash').json()['items'][0]
        assert client.post('/volumes/trash/' + record['trash_id'] + '/restore', json={}).status_code == 200
    assert len(client.get('/state').json()['samples']) == 2
    for part, hashes in expected.items():
        assert tree_hashes(module.ROOT / part) == hashes
    assert client.get('/volumes/trash').json()['items'] == []


def test_deleted_volume_isolated_by_category(category_apps):
    neurite, neuron_module = category_apps('neurite')
    spot, spot_module = category_apps('spot')
    detail = upload_volume(neurite)
    vid = detail['volume']['id']
    assert trash(spot, detail).status_code == 404
    record = trash(neurite, detail).json()
    assert spot.get('/volumes/trash').json()['items'] == []
    assert spot.post('/volumes/trash/' + record['trash_id'] + '/restore', json={}).status_code == 404
    assert neurite.post('/volumes/trash/' + record['trash_id'] + '/restore', json={'category': 'spot'}).status_code == 422


def test_failed_import_upload_input_is_removed_and_restored_without_loss(api):
    client, module = api
    vid, detail, uid = upload(api, npy_bytes(np.full((2, 3, 4), np.nan)))
    assert detail['volume']['status'] == 'failed'
    original = (module.ROOT / 'uploads' / (uid + '.part')).read_bytes()
    record = trash(client, detail)
    assert record.status_code == 200, record.text
    assert not (module.ROOT / 'uploads' / (uid + '.part')).exists()
    assert client.get('/volumes/uploads/' + uid).status_code == 404
    result = client.post('/volumes/trash/' + record.json()['trash_id'] + '/restore', json={})
    assert result.status_code == 200, result.text
    assert result.json()['volume']['status'] == 'failed'
    assert (module.ROOT / 'uploads' / (uid + '.part')).read_bytes() == original
    assert client.get('/volumes/uploads/' + uid).json()['upload']['status'] == 'failed'


def test_download_already_opened_completes_after_move_to_trash(api):
    import asyncio
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    assert client.post('/volumes/' + vid + '/merge', json={'draft': True}).status_code == 200
    assert wait_merge(client, vid)['merge_status'] == 'ready'
    expected = client.get('/volumes/' + vid + '/export?draft=true').content
    response = module.export_collection(vid, draft=True)
    detail = client.get('/volumes/' + vid).json()
    assert trash(client, detail).status_code == 200
    async def download():
        body = b''.join([block async for block in response.body_iterator])
        await response.background()
        return body
    assert asyncio.run(download()) == expected
    assert int(response.headers['content-length']) == len(expected)


def test_state_snapshot_cannot_mix_pre_delete_samples_with_post_delete_volumes(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    captured = threading.Event()
    release = threading.Event()
    deleting = threading.Event()
    original = module.all_samples
    def pause_state():
        samples = original()
        captured.set()
        assert release.wait(3)
        return samples
    monkeypatch.setattr(module, 'all_samples', pause_state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        state = pool.submit(client.get, '/state')
        assert captured.wait(3)
        def remove():
            deleting.set()
            return trash(client, detail)
        deletion = pool.submit(remove)
        assert deleting.wait(3)
        try:
            # State has already read its sample list. Deletion must wait until
            # it also reads volume metadata, otherwise the response mixes epochs.
            import time
            time.sleep(.05)
            assert not deletion.done()
        finally:
            release.set()
        before = state.result(timeout=3)
        assert before.status_code == 200, before.text
        assert {v['id'] for v in before.json()['volumes']} == {vid}
        assert {s['volume_id'] for s in before.json()['samples']} == {vid}
        assert deletion.result(timeout=3).status_code == 200
    after = client.get('/state').json()
    assert after['samples'] == [] and after['volumes'] == []


def test_library_list_snapshot_cannot_lose_a_row_during_delete(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    client, module = api
    vid, detail, _ = upload(api, npy_bytes(np.arange(60).reshape(3, 4, 5)))
    captured = threading.Event()
    release = threading.Event()
    deleting = threading.Event()
    original = module.VOLUMES._all
    def pause_ids(table):
        rows = original(table)
        if table == 'volumes' and not captured.is_set():
            captured.set()
            assert release.wait(3)
        return rows
    monkeypatch.setattr(module.VOLUMES, '_all', pause_ids)
    with ThreadPoolExecutor(max_workers=2) as pool:
        listing = pool.submit(client.get, '/volumes')
        assert captured.wait(3)
        def remove():
            deleting.set()
            return trash(client, detail)
        deletion = pool.submit(remove)
        assert deleting.wait(3)
        try:
            # The list has captured the row IDs but has not read their details.
            # Trash must wait; otherwise GET /volumes raises a spurious 404.
            time.sleep(.05)
            assert not deletion.done()
        finally:
            release.set()
        before = listing.result(timeout=3)
        assert before.status_code == 200, before.text
        assert [volume['id'] for volume in before.json()['volumes']] == [vid]
        assert deletion.result(timeout=3).status_code == 200
    assert client.get('/volumes').json()['volumes'] == []
    assert len(client.get('/volumes/trash').json()['items']) == 1
