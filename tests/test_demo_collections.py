"""Curated demos reuse saved masks; viewing never selects or trains a model."""
import base64
import hashlib
import json
from pathlib import Path
import time
import types

import numpy as np
import pytest

from test_categories import category_apps


def create_demo(module):
    ids = ['round_one', 'round_two']
    for index, sid in enumerate(ids):
        folder = module.ROOT / 'samples' / sid
        folder.mkdir()
        raw = np.arange(3 * 80 * 160, dtype=np.float32).reshape(3, 80, 160) + index
        mask = np.zeros(raw.shape, np.uint8)
        mask[1, 10:30, 40:60] = 1
        np.save(folder / 'image.npy', raw)
        np.save(folder / 'mask.npy', mask)
        module.atomic({'id': sid, 'title': f'Round {index + 1}', 'shape': list(raw.shape),
                       'split': 'annotation', 'revision': 2, 'events': 2, 'confirmed': True,
                       'snapshot': '/private/training/snapshot.npy'}, folder / 'meta.json')
        module.atomic([{'tool': 'point', 'points': [[1, 12, 42]], 'include': True, 'radius': 2,
                        'private_path': '/private/should-not-appear'},
                       {'tool': 'point', 'points': [[1, 2, 3]], 'include': False}], folder / 'events.json')
    model = module.ROOT / 'models' / 'prompt_m10'
    model.mkdir()
    module.atomic({'id': model.name, 'label': 'M10', 'phase': 'prompt', 'confirmed_count': 10,
                   'auto_ready': False, 'checkpoint': '/private/checkpoint.pth'}, model / 'meta.json')
    manifest = {'version': 1, 'category_id': module.CATEGORY, 'demos': [{
        'id': 'demo_ten', 'title': 'Corrected 10 rounds', 'model_id': model.name,
        'sample_ids': ids, 'preview_sample_id': ids[1], 'preferred_z_by_sample': {sid: 1 for sid in ids},
        'training_rounds': 10, 'private_path': '/private/source',
        'round_history_by_sample': {sid: {'round': index + 1, 'slice_indices': [1],
                                         'private_path': '/private/history'} for index, sid in enumerate(ids)}
    }]}
    (module.ROOT / 'demos.json').write_text(json.dumps(manifest))
    return manifest, ids


def fingerprints(root):
    files = [root / 'state.json', root / 'demos.json'] + list((root / 'samples').glob('*/*'))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def test_demo_lists_existing_annotations_and_preview_without_mutation(category_apps):
    client, module = category_apps('neurite')
    manifest, ids = create_demo(module)
    before = fingerprints(module.ROOT)
    response = client.get('/categories/neurite/demos')
    assert response.status_code == 200, response.text
    demo = response.json()['demos'][0]
    assert demo['category_id'] == 'neurite'
    assert demo['model']['id'] == 'prompt_m10'
    assert demo['sample_ids'] == ids
    assert demo['preview_sample_id'] == ids[1]
    assert demo['preview_z'] == 1
    assert demo['samples'][0]['prompt_count'] == 2
    assert demo['samples'][0]['positive_clicks'] == demo['samples'][0]['negative_clicks'] == 1
    assert demo['samples'][0]['round_history'] == {'round': 1, 'slice_indices': [1]}
    assert '/private/' not in response.text
    preview = client.get('/demos/demo_ten/slice?max_size=64').json()
    assert preview['sample']['id'] == ids[1]
    assert preview['shape'] == [27, 54]
    assert preview['stride'] == 3
    assert preview['source_shape'] == [3, 80, 160]
    mask = np.frombuffer(base64.b64decode(preview['mask']), dtype=np.uint8).reshape(preview['shape'])
    saved = np.load(module.ROOT / 'samples' / ids[1] / 'mask.npy')
    np.testing.assert_array_equal(mask, saved[1, ::3, ::3])
    assert preview['prompts'][0]['points'] == [[1, 12, 42]]  # Unscaled ROI coordinates.
    assert '/private/' not in json.dumps(preview)
    assert client.get('/state').json()['active_model'] == 'official'
    assert fingerprints(module.ROOT) == before
    assert len(client.get('/state').json()['samples']) == 2  # No duplicate training data.


def test_demo_category_guards_membership_and_range(category_apps):
    client, module = category_apps('neurite')
    create_demo(module)
    spot, spot_module = category_apps('spot')
    assert spot.get('/demos').json()['demos'] == []
    assert spot.get('/demos/demo_ten/slice').status_code == 404
    for url in ['/demos/demo_ten/slice?sample_id=another_sample', '/demos/missing/slice']:
        assert client.get(url).status_code == 404
    assert client.get('/demos/demo_ten/slice?z=3').status_code == 422
    assert client.get('/demos/demo_ten/slice?z=-1').status_code == 422
    assert client.get('/demos/demo_ten/slice?max_size=1').status_code == 422
    assert client.post('/demos/demo_ten/load', json={'sample_id': 'another_sample'}).status_code == 404
    assert client.post('/demos/demo_ten/load', json={'category_id': 'spot'}).status_code == 422
    manifest = json.loads((module.ROOT / 'demos.json').read_text())
    manifest['category_id'] = 'spot'
    (module.ROOT / 'demos.json').write_text(json.dumps(manifest))
    assert client.get('/demos').status_code == 503


def test_demo_explicit_load_activates_bound_model_without_editing_samples(category_apps, monkeypatch):
    client, module = category_apps('neurite')
    _, ids = create_demo(module)
    before = fingerprints(module.ROOT)
    loaded = []
    monkeypatch.setattr(module, 'require_neural_runtime', lambda: None)
    monkeypatch.setattr(module, 'ENGINE', types.SimpleNamespace(load=loaded.append, release=lambda: None))
    response = client.post('/demos/demo_ten/load', json={'sample_id': ids[0]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['sample_id'] == ids[0]
    assert result['model_id'] == 'prompt_m10'
    assert result['z'] == 1
    assert result['sample_ids'] == ids
    job_id = result['job']['id']
    for _ in range(200):
        job = client.get('/jobs/' + job_id).json()
        if job['status'] not in ['running', 'queued']:
            break
        time.sleep(.01)
    assert job['status'] == 'completed', job
    assert loaded == ['prompt_m10']
    assert client.get('/state').json()['active_model'] == 'prompt_m10'
    same = client.post('/demos/demo_ten/load', json={}).json()
    assert 'job' not in same
    assert same['sample_id'] == ids[1]
    after = fingerprints(module.ROOT)
    assert {k:v for k,v in after.items() if k!='state.json'} == {k:v for k,v in before.items() if k!='state.json'}


def test_demo_load_respects_busy_and_failed_activation(category_apps, monkeypatch):
    client, module = category_apps('neurite')
    create_demo(module)
    module.atomic({'id': 'busy', 'status': 'running', 'created_at': 1}, module.ROOT / 'jobs' / 'busy.json')
    assert client.post('/demos/demo_ten/load', json={}).status_code == 409
    (module.ROOT / 'jobs' / 'busy.json').unlink()
    monkeypatch.setattr(module, 'require_neural_runtime', lambda: None)
    def fail(mid):
        raise RuntimeError('Checkpoint could not load')
    monkeypatch.setattr(module, 'ENGINE', types.SimpleNamespace(load=fail, release=lambda: None))
    job_id = client.post('/demos/demo_ten/load', json={}).json()['job']['id']
    for _ in range(200):
        job = client.get('/jobs/' + job_id).json()
        if job['status'] not in ['running', 'queued']:
            break
        time.sleep(.01)
    assert job['status'] == 'failed'
    assert client.get('/state').json()['active_model'] == 'official'


def test_demo_current_mask_provenance_does_not_mislabel_changed_or_heldout_masks(category_apps):
    client, module = category_apps('neurite')
    _, ids = create_demo(module)
    first = module.ROOT / 'samples' / ids[0] / 'meta.json'
    original = module.read(first)
    module.atomic({**original, 'last_model': 'official', 'round_history': {
        'round': 1, 'imported_revision': 2, 'input_model_id': 'official'}}, first)
    saved = client.get('/demos').json()['demos'][0]['samples'][0]
    assert saved['recorded_mask']
    assert saved['mask_model_id'] == 'official'
    assert saved['mask_model_label'] == 'Original pretrained model'
    module.atomic({**module.read(first), 'last_model': 'prompt_historical_m0', 'round_history': {
        'round': 1, 'imported_revision': 2, 'input_model_id': 'prompt_historical_m0'}}, first)
    history_only = client.get('/demos').json()['demos'][0]['samples'][0]
    assert history_only['recorded_mask']
    assert history_only['mask_model_label'] == 'Recorded incoming model M0 (history)'
    module.atomic({**module.read(first), 'revision': 3, 'last_model': 'prompt_m10'}, first)
    changed = client.get('/demos').json()['demos'][0]['samples'][0]
    assert not changed['recorded_mask']
    assert changed['mask_model_label'] == 'M10'
    # Test records without historical rounds must not become numbered training rounds.
    manifest = module.read(module.ROOT / 'demos.json')
    manifest['demos'][0].pop('round_history_by_sample')
    (module.ROOT / 'demos.json').write_text(json.dumps(manifest))
    second = module.ROOT / 'samples' / ids[1] / 'meta.json'
    module.atomic({**module.read(second), 'split': 'evaluation', 'last_model': 'missing_checkpoint'}, second)
    heldout = client.get('/demos').json()['demos'][0]['samples'][1]
    assert heldout['round'] is None
    assert heldout['mask_model_label'] == 'Saved model · unavailable'
    assert not heldout['recorded_mask']
