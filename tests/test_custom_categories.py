"""Named categories persist safely and start from isolated original weights."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

from test_categories import category_apps, upload_volume

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))
import category_registry as registry


@pytest.fixture
def managed_registry(tmp_path, monkeypatch):
    path = tmp_path / 'category-registry.json'
    runtime = tmp_path / '.category-runtime.json'
    monkeypatch.setenv('MORPHOPILOT_CATEGORY_REGISTRY', str(path))
    monkeypatch.setenv('MORPHOPILOT_CATEGORY_RUNTIME', str(runtime))
    registry.atomic_json(path, {'version': 1, 'categories': []})
    registry.atomic_json(runtime, {'accepting': True, 'updated_at': time.time(), 'origins': {}})
    return path, runtime


def test_names_unique_casefolded_concurrent_and_opaque(managed_registry):
    def create():
        try:
            return registry.create('  Blood   Vessel  ')
        except registry.CategoryError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create(), range(8)))
    successful = [value for value in results if isinstance(value, dict)]
    assert len(successful) == 1
    assert results.count(409) == 7
    record = successful[0]
    assert record['label'] == 'Blood Vessel'
    assert registry.CUSTOM_ID.fullmatch(record['id'])
    assert len(registry.entries()) == 1
    for label in ('NEURITE', 'blood vessel', 'ＢＬＯＯＤ Vessel'):
        with pytest.raises(registry.CategoryError) as error:
            registry.create(label)
        assert error.value.status_code == 409
    for label in ('  ', 'a' * 49, 'bad\x00label', 'hidden\u200bname'):
        with pytest.raises(registry.CategoryError) as error:
            registry.create(label)
        assert error.value.status_code == 422
    assert registry.create('微血管')['label'] == '微血管'


def test_limit_offline_routes_and_retry(managed_registry):
    path, runtime = managed_registry
    for number in range(registry.MAX_CUSTOM):
        registry.create(f'Tissue {number}')
    with pytest.raises(registry.CategoryError) as error:
        registry.create('One too many')
    assert error.value.status_code == 409
    record = registry.entries()[0]
    registry.update(record['id'], status='failed', error='Startup failed.')
    retried = registry.retry(record['id'])
    assert retried['status'] == 'starting' and 'error' not in retried
    assert retried['id'] == record['id']
    with pytest.raises(registry.CategoryError) as error:
        registry.retry(record['id'])
    assert error.value.status_code == 409
    registry.atomic_json(runtime, {'accepting': True, 'updated_at': time.time(), 'origins': {
        record['id']: 'http://127.0.0.1:9876', 'neurite': 'https://remote.example',
        'spot': 'http://127.0.0.1:9877/path', 'cell': 'http://user@127.0.0.1:9878',
        '../bad': 'http://127.0.0.1:9879'}})
    assert registry.runtime_origins() == {record['id']: 'http://127.0.0.1:9876'}
    registry.atomic_json(runtime, {'accepting': True, 'updated_at': time.time() - 20, 'origins': {record['id']: 'http://127.0.0.1:9876'}})
    assert not registry.supervisor_available()
    assert registry.runtime_origins() == {}
    with pytest.raises(registry.CategoryError) as error:
        registry.create('Offline')
    assert error.value.status_code == 503


def test_custom_api_defaults_to_official_and_isolates_images_models(category_apps, managed_registry):
    main, main_module = category_apps('neurite')
    response = main.post('/categories', json={'label': 'Blood vessel'})
    assert response.status_code == 202, response.text
    entry = response.json()['category']
    category = entry['id']
    assert entry['status'] == 'starting' and not entry['available']
    assert main.get('/categories').json()['can_create']
    custom, custom_module = category_apps(category)
    state = custom.get('/state').json()
    assert state['category'] == {'id': category, 'label': 'Blood vessel'}
    assert state['active_model'] == 'official'
    assert [model['id'] for model in state['models']] == ['official']
    assert not state['samples']
    assert custom.get(f'/categories/{category}/state').json()['category_id'] == category
    assert custom.post('/volumes/uploads', json={'name': 'bad.npy', 'size': 100, 'category': 'neurite'}).status_code == 422
    detail = upload_volume(custom)
    assert detail['volume']['category_id'] == category
    sample = detail['tiles'][0]['id']
    assert custom.post(f'/samples/{sample}/interaction', json={'category': category,
        'tool': 'brush', 'points': [[1, 2, 2]], 'radius': 1, 'revision': 0}).status_code == 200
    assert main.get('/volumes').json()['volumes'] == []
    assert main.get(f'/samples/{sample}/volume').status_code == 404
    registry.update(category, status='failed', error='Temporary failure')
    assert main.post(f'/categories/{category}/retry').status_code == 202
    assert main.post('/categories', json={'label': 'blood vessel'}).status_code == 409
    registry.atomic_json(managed_registry[1], {'accepting': True, 'updated_at': time.time(),
                                               'origins': {category: 'http://127.0.0.1:9876'}})
    registry.update(category, status='ready')
    listing = main.get('/categories').json()
    assert next(record for record in listing['categories'] if record['id'] == category)['available']
    assert '9876' not in json.dumps(listing)
    assert custom_module.ROOT != main_module.ROOT


def test_real_supervisor_add_retry_and_restart_preserve_custom_data(tmp_path):
    """Use real worker processes and HTTP; simulate a worker loss, not GPU work."""
    import hashlib
    import secrets
    import signal
    import socket
    import subprocess
    import httpx
    from test_category_supervisor import alive, children_of, wait_until

    root = BACKEND.parent
    runtime = tmp_path / 'runtime'
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<!doctype html><title>Category lifecycle test</title>')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    password = 'isolated-category-lifecycle-test'
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    env = {key: value for key, value in os.environ.items() if not key.startswith('MORPHOPILOT_')}
    env.update(MORPHOPILOT_DATA_DIR=str(runtime), MORPHOPILOT_CATEGORY='neurite',
               MORPHOPILOT_API_TOKEN=secrets.token_urlsafe(48), MORPHOPILOT_SESSION_SECRET=secrets.token_urlsafe(48),
               MORPHOPILOT_PASSWORD_HASH=f'scrypt${salt.hex()}${digest}', MORPHOPILOT_WEB_DIR=str(dist),
               MORPHOPILOT_HOST='127.0.0.1', MORPHOPILOT_PORT=str(port), MORPHOPILOT_PUBLIC_ORIGIN=url,
               MORPHOPILOT_COOKIE_SECURE='0', PYTHONUNBUFFERED='1')
    supervisor = None
    known_children = []
    logfile = tmp_path / 'supervisor.log'
    with logfile.open('w') as log, httpx.Client(base_url=url, timeout=5, headers={'Origin': url}) as client:
        def start():
            nonlocal supervisor
            supervisor = subprocess.Popen([sys.executable, str(root / 'standalone/serve.py')], cwd=root,
                                          env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            def ready():
                if supervisor.poll() is not None:
                    raise AssertionError(logfile.read_text()[-4000:])
                try:
                    return client.get('/healthz').status_code == 200
                except httpx.HTTPError:
                    return False
            wait_until(ready)
            assert client.post('/login', data={'password': password}).status_code == 303

        def stop():
            known_children.extend(children_of(supervisor.pid))
            supervisor.send_signal(signal.SIGTERM)
            assert supervisor.wait(timeout=35) == 0
            wait_until(lambda: not any(alive(pid) for pid in known_children))

        try:
            start()
            original_children = children_of(supervisor.pid)
            assert len(original_children) == 5
            response = client.post('/api/gpu/categories', json={'label': 'Vessels'})
            assert response.status_code == 202, response.text
            category = response.json()['category']['id']
            endpoint = '/api/gpu/categories/' + category
            def category_status():
                return next(record for record in client.get('/api/gpu/categories').json()['categories']
                            if record['id'] == category)
            wait_until(lambda: category_status()['available'])
            state = client.get(endpoint + '/state').json()
            assert state['active_model'] == 'official' and state['samples'] == []
            assert state['category']['label'] == 'Vessels'
            custom_process = next(pid for pid in children_of(supervisor.pid) if pid not in original_children)
            assert all(alive(pid) for pid in original_children)
            # Real upload/paint/confirm persists only inside this new category.
            import io
            import numpy as np
            raw = io.BytesIO()
            np.save(raw, np.arange(60, dtype=np.float32).reshape(3, 4, 5))
            payload = raw.getvalue()
            upload = client.post(endpoint + '/volumes/uploads', json={'name': 'vessel.npy',
                'size': len(payload), 'category': category, 'tile_shape': [3, 4, 5]}).json()['upload']
            assert client.post(endpoint + f'/volumes/uploads/{upload["id"]}/chunk?offset=0', content=payload).status_code == 200
            volume = client.post(endpoint + f'/volumes/uploads/{upload["id"]}/complete').json()['volume']
            wait_until(lambda: client.get(endpoint + '/volumes/' + volume['id']).json()['volume']['status'] == 'ready')
            sid = client.get(endpoint + '/volumes/' + volume['id']).json()['tiles'][0]['id']
            assert client.post(endpoint + f'/samples/{sid}/interaction', json={'tool': 'brush',
                'category': category, 'points': [[1, 2, 2]], 'radius': 1, 'revision': 0}).status_code == 200
            assert client.post(endpoint + f'/samples/{sid}/confirm', json={'revision': 1, 'reviewed': True}).status_code == 200
            saved_mask = client.get(endpoint + f'/samples/{sid}/volume').json()['mask']
            assert client.get('/api/gpu/categories/neurite/volumes').json()['volumes'] == []
            assert client.get('/api/gpu/categories/spot/volumes/' + volume['id']).status_code == 404
            # A custom worker loss does not terminate original workers or gateway.
            os.kill(custom_process, signal.SIGTERM)
            wait_until(lambda: category_status()['status'] == 'failed')
            assert all(alive(pid) for pid in original_children)
            assert client.get('/api/gpu/categories/neurite/state').status_code == 200
            # A bad storage marker makes retry fail visibly without reassigning data.
            marker = runtime / 'categories' / category / 'category.json'
            marker.write_text(json.dumps({'id': 'neurite'}))
            assert client.post(endpoint + '/retry').status_code == 202
            wait_until(lambda: category_status()['status'] == 'failed')
            assert json.loads(marker.read_text())['id'] == 'neurite'
            marker.write_text(json.dumps({'id': category, 'label': 'Vessels'}))
            assert client.post(endpoint + '/retry').status_code == 202
            wait_until(lambda: category_status()['available'])
            assert client.get(endpoint + f'/samples/{sid}/volume').json()['mask'] == saved_mask
            assert client.get(endpoint + '/state').json()['confirmed_count'] == 1
            stop()
            start()
            wait_until(lambda: category_status()['available'])
            assert category_status()['label'] == 'Vessels'
            assert client.get(endpoint + f'/samples/{sid}/volume').json()['mask'] == saved_mask
            assert client.get(endpoint + '/state').json()['active_model'] == 'official'
            assert client.post('/api/gpu/categories', json={'label': 'VESSELS'}).status_code == 409
            assert json.loads((runtime / 'category-registry.json').read_text())['categories'][0]['id'] == category
            stop()
        finally:
            if supervisor and supervisor.poll() is None:
                known_children.extend(children_of(supervisor.pid))
                supervisor.send_signal(signal.SIGTERM)
                try:
                    supervisor.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    supervisor.kill()
                    supervisor.wait(timeout=5)
            for pid in set(known_children):
                if alive(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
