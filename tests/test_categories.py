"""Category isolation, routing, training scope and publication regressions.

Uses disposable directories and real APIs. Only GPU computation is substituted.
"""
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
TOKEN = 'category-test-token-not-production-123456'


@pytest.fixture
def category_apps(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BACKEND))
    for name in ('NEURITE', 'SPOT', 'CELL', 'ORGAN', 'LEGACY_SPOT'):
        monkeypatch.delenv(f'MORPHOPILOT_{name}_ORIGIN', raising=False)
    monkeypatch.setenv('MORPHOPILOT_API_TOKEN', TOKEN)
    monkeypatch.setenv('MORPHOPILOT_BOOTSTRAP_DEMO', 'false')
    monkeypatch.setenv('MORPHOPILOT_GPU_LOCK_PATH', str(tmp_path / 'gpu.lock'))
    monkeypatch.setenv('MORPHOPILOT_RELEASE_GPU_ON_IDLE', 'true')
    modules = {}
    clients = []

    def make(category):
        monkeypatch.setenv('MORPHOPILOT_CATEGORY', category)
        monkeypatch.setenv('MORPHOPILOT_DATASET', 'uploads')
        monkeypatch.setenv('MORPHOPILOT_DATA_DIR', str(tmp_path / category))
        spec = importlib.util.spec_from_file_location('morphopilot_' + category + '_test', BACKEND / 'app.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[category] = module
        client = TestClient(module.app, headers={'Authorization': 'Bearer ' + TOKEN})
        clients.append(client)
        return client, module

    yield make
    for module in modules.values():
        module.POOL.shutdown(wait=True)
        module.VOLUMES.pool.shutdown(wait=True)
    for client in clients:
        client.close()


def upload_volume(client, offset=0):
    image = np.arange(60, dtype=np.float32).reshape(3, 4, 5) + offset
    raw = io.BytesIO()
    np.save(raw, image)
    payload = raw.getvalue()
    upload = client.post('/volumes/uploads', json={'name': 'test.npy', 'size': len(payload),
                                                  'tile_shape': [3, 4, 5]}).json()['upload']
    assert client.post(f'/volumes/uploads/{upload["id"]}/chunk?offset=0', content=payload).status_code == 200
    volume = client.post(f'/volumes/uploads/{upload["id"]}/complete').json()['volume']
    for _ in range(200):
        detail = client.get('/volumes/' + volume['id']).json()
        if detail['volume']['status'] != 'importing':
            assert detail['volume']['status'] == 'ready', detail
            return detail
        time.sleep(.01)
    raise AssertionError('Import did not complete')


def test_canonical_category_alias_inside_and_outside_gateway_mount(category_apps):
    client, module = category_apps('neurite')
    response = client.get('/categories/neurite/state')
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['category'] == {'id': 'neurite', 'label': 'Neurite'}
    assert [item['id'] for item in state['capabilities']['categories']] == ['neurite', 'spot', 'cell', 'organ']
    assert state['models'][0]['category_id'] == 'neurite'
    assert state['active_model'] == 'official'
    assert client.get('/categories').json()['current_category'] == 'neurite'
    assert client.get('/categories/cell/state').status_code == 503
    assert client.get('/categories/unknown/state').status_code == 404
    assert client.get('/state', headers={'X-MorphoPilot-Category': 'spot'}).status_code == 409
    mounted = FastAPI()
    mounted.mount('/api/gpu', module.app)
    with TestClient(mounted, headers={'Authorization': 'Bearer ' + TOKEN}) as outer:
        assert outer.get('/api/gpu/categories/neurite/state').json()['category_id'] == 'neurite'
        assert outer.get('/api/gpu/state').json()['category_id'] == 'neurite'
        assert outer.get('/api/gpu/categories/neurite/state', headers={'Authorization': ''}).status_code == 401


def test_legacy_and_canonical_spot_routes_preserve_separate_root(category_apps, monkeypatch):
    main, module = category_apps('neurite')
    spot, spot_module = category_apps('spot')
    monkeypatch.setenv('MORPHOPILOT_LEGACY_SPOT_ORIGIN', 'http://spot.test')
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.ASGITransport(app=spot_module.app), **kwargs))
    for prefix in ['/categories/spot', '/workspaces/legacy_spot']:
        response = main.get(prefix + '/state')
        assert response.status_code == 200, response.text
        assert response.json()['category_id'] == 'spot'
    assert main.get('/categories/spot/categories/neurite/state').status_code == 400
    assert main.get('/categories/spot/workspaces/legacy_spot/state').status_code == 400
    assert main.get('/categories').json()['categories'][1]['available']
    assert module.ROOT != spot_module.ROOT
    # A mistaken port/origin cannot quietly serve another category's state.
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.ASGITransport(app=module.app), **kwargs))
    assert main.get('/categories/spot/state').status_code == 409


def test_uploads_masks_model_ids_and_active_selection_are_isolated(category_apps):
    neuron, neuron_module = category_apps('neurite')
    spot, spot_module = category_apps('spot')
    detail = upload_volume(neuron)
    sid = detail['tiles'][0]['id']
    vid = detail['volume']['id']
    assert detail['volume']['category_id'] == 'neurite'
    assert detail['tiles'][0]['category_id'] == 'neurite'
    assert spot.get('/volumes/' + vid).status_code == 404
    assert spot.get('/samples/' + sid + '/volume').status_code == 404
    assert spot.get('/volumes').json()['volumes'] == []
    assert neuron.post('/volumes/uploads', json={'name': 'x.npy', 'size': 100, 'category': 'spot'}).status_code == 422
    assert neuron.post('/train', json={'category_id': 'spot'}).status_code == 422
    assert neuron.post('/samples/' + sid + '/interaction', json={
        'category': 'spot', 'tool': 'brush', 'points': [[1, 2, 2]], 'revision': 0}).status_code == 422
    model_dir = neuron_module.ROOT / 'models' / 'prompt_neurite_only'
    model_dir.mkdir()
    neuron_module.atomic({'id': model_dir.name, 'checkpoint': str(model_dir / 'checkpoint.pth'),
                          'phase': 'prompt', 'auto_ready': False, 'confirmed_count': 1}, model_dir / 'meta.json')
    neuron_module.atomic({'active_model': model_dir.name}, neuron_module.ROOT / 'state.json')
    assert neuron.get('/state').json()['active_model'] == model_dir.name
    assert spot.get('/state').json()['active_model'] == 'official'
    assert spot.post('/models/activate', json={'model_id': model_dir.name}).status_code == 404
    assert {m['id'] for m in spot.get('/state').json()['models']} == {'official'}
    assert neuron.post('/samples/' + sid + '/interaction', json={
        'tool': 'brush', 'points': [[1, 2, 2]], 'radius': 1, 'revision': 0}).status_code == 200
    saved = neuron.get('/samples/' + sid + '/volume').json()
    assert np.frombuffer(base64.b64decode(saved['mask']), np.uint8).any()
    assert spot.get('/state').json()['samples'] == []


@pytest.mark.parametrize('mode', ['prompt', 'auto'])
def test_category_training_uses_two_volumes_and_legacy_roi_excludes_heldout_and_activates(category_apps, monkeypatch, mode):
    client, module = category_apps('cell')
    details = [upload_volume(client, offset=i) for i in range(2)]
    for detail in details:
        sid = detail['tiles'][0]['id']
        assert client.post('/samples/' + sid + '/confirm', json={
            'revision': 0, 'reviewed': True, 'empty_background': True}).status_code == 200
    # A confirmed legacy annotation participates; even a preexisting confirmed
    # evaluation record must never be selected as a training input.
    legacy = dict(module.read(module.ROOT / 'samples' / details[0]['tiles'][0]['id'] / 'meta.json'))
    legacy.pop('volume_id')
    for name, split in [('legacy_annotation', 'annotation'), ('held_out', 'evaluation')]:
        folder = module.ROOT / 'samples' / name
        folder.mkdir()
        module.atomic({**legacy, 'id': name, 'split': split}, folder / 'meta.json')
    captured = {}
    parent = 'official'
    if mode == 'auto':
        parent = 'dual_cell_before'
        folder = module.ROOT / 'models' / parent
        folder.mkdir()
        module.atomic({'id': parent, 'phase': 'dual', 'auto_ready': True, 'confirmed_count': 0,
                       'created_at': 0, 'checkpoint': str(folder / 'checkpoint.pth')}, folder / 'meta.json')
        module.atomic({'active_model': parent}, module.ROOT / 'state.json')
    monkeypatch.setattr(module, 'require_neural_runtime', lambda: None)
    monkeypatch.setattr(module, 'ENGINE', types.SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(module, 'save_native_export', lambda model, path: None)
    monkeypatch.setitem(sys.modules, 'model', types.SimpleNamespace(load_dual=lambda *a, **kw: object()))

    class Process:
        def __init__(self, args, **kwargs):
            cfg = json.loads(Path(args[-1]).read_text())
            captured.update(cfg)
            module.atomic({'auto_ready': mode == 'auto'}, Path(cfg['out']) / 'training.json')
            module.atomic({**module.read(cfg['job_path']), 'training_finished': True}, Path(cfg['job_path']))

        def wait(self):
            return 0

    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    def submit(kind, work, **extra):
        module.atomic({'id': 'testtrain', 'status': 'running', 'created_at': time.time()}, module.ROOT / 'jobs' / 'testtrain.json')
        return {'job': {**extra, **work('testtrain')}}
    monkeypatch.setattr(module, 'submit', submit)
    response = client.post('/train', json={'mode': mode, 'steps': 1})
    assert response.status_code == 200, response.text
    assert len(captured['samples']) == 3
    assert {s['id'] for s in captured['samples']} == {
        details[0]['tiles'][0]['id'], details[1]['tiles'][0]['id'], 'legacy_annotation'}
    assert captured['category_id'] == 'cell'
    assert captured['training_scope'] == 'category'
    state = client.get('/state').json()
    assert state['confirmed_count'] == 3
    assert state['trainable_confirmed_count'] == 3
    assert state['active_model'] == mode + '_testtrain'
    assert state['models'][-1]['category_id'] == 'cell'
    assert state['models'][-1]['parent'] == parent


def test_runtime_root_cannot_be_reassigned_to_another_category(category_apps, monkeypatch):
    client, module = category_apps('neurite')
    monkeypatch.setenv('MORPHOPILOT_CATEGORY', 'spot')
    monkeypatch.setenv('MORPHOPILOT_DATA_DIR', str(module.ROOT))
    spec = importlib.util.spec_from_file_location('morphopilot_incorrect_category_test', BACKEND / 'app.py')
    other = importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError, match='different category'):
        spec.loader.exec_module(other)
    assert client.get('/state').json()['category_id'] == 'neurite'


def test_gpu_engine_released_on_job_failure_before_next_category_can_run(category_apps, monkeypatch):
    client, module = category_apps('organ')
    released = []
    monkeypatch.setattr(module, 'ENGINE', types.SimpleNamespace(release=lambda: released.append(True)))

    def fail(job_id):
        raise ValueError('Intentional isolated test failure')

    job = module.submit('test', fail)['job']
    for _ in range(100):
        final = client.get('/jobs/' + job['id']).json()
        if final['status'] == 'failed':
            break
        time.sleep(.01)
    assert final['status'] == 'failed'
    assert released == [True]
    assert final['category_id'] == 'organ'
