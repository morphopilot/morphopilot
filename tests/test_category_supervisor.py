"""Real CPU-only four-process installation tests, with temporary data and sockets.

These tests launch the actual supervisor, category services and password gateway.
They never use production credentials, model weights or research data.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ('neurite', 'spot', 'cell', 'organ')


def children_of(parent):
    output = subprocess.run(['ps', '-axo', 'pid=,ppid='], capture_output=True, text=True, check=True).stdout
    return [int(pid) for line in output.splitlines() if len(parts := line.split()) == 2
            for pid, ppid in [parts] if int(ppid) == parent]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_until(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('Timed out waiting for the isolated service lifecycle condition.')


@pytest.mark.parametrize(('primary', 'stop_mode'), [('neurite', 'sigterm'), ('spot', 'worker-loss')])
def test_real_supervisor_category_routing_auth_storage_and_cleanup(tmp_path, primary, stop_mode):
    if os.name != 'posix':
        pytest.skip('The deployment supervisor uses POSIX process groups and sockets.')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    # Reproduce an already initialized installation, including a saved legacy ROI.
    (runtime / 'category.json').write_text(json.dumps({'id': primary}))
    (runtime / 'state.json').write_text(json.dumps({'active_model': 'official'}))
    (runtime / 'initialized.json').write_text(json.dumps({'source': 'isolated lifecycle fixture'}))
    legacy = runtime / 'samples/legacy_roi'
    legacy.mkdir(parents=True)
    (legacy / 'meta.json').write_text(json.dumps({
        'id': 'legacy_roi', 'title': 'Preserved existing image', 'shape': [3, 4, 5],
        'confirmed': False, 'split': 'annotation', 'revision': 0, 'events': 0}))
    (legacy / 'events.json').write_text('[]')
    np.save(legacy / 'image.npy', np.arange(60, dtype=np.float32).reshape(3, 4, 5))
    np.save(legacy / 'mask.npy', np.zeros((3, 4, 5), np.uint8))
    existing_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in legacy.iterdir()}
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<!doctype html><title>Isolated category gateway test</title>')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with socket.socket() as api_reservation:
        api_reservation.bind(('127.0.0.1', 0))
        api_port = api_reservation.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    password = 'test-category-password-' + secrets.token_hex(8)
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    # Remove inherited application configuration so tests cannot contact any
    # configured research category or accidentally use its runtime directory.
    env = {key: value for key, value in os.environ.items() if not key.startswith('MORPHOPILOT_')}
    env.update(MORPHOPILOT_DATA_DIR=str(runtime), MORPHOPILOT_CATEGORY=primary,
               MORPHOPILOT_API_TOKEN=secrets.token_urlsafe(48), MORPHOPILOT_SESSION_SECRET=secrets.token_urlsafe(48),
               MORPHOPILOT_PASSWORD_HASH=f'scrypt${salt.hex()}${password_hash}',
               MORPHOPILOT_WEB_DIR=str(dist), MORPHOPILOT_HOST='127.0.0.1', MORPHOPILOT_PORT=str(port),
               MORPHOPILOT_PUBLIC_ORIGIN=url, MORPHOPILOT_COOKIE_SECURE='0', MORPHOPILOT_API_PORT=str(api_port), PYTHONUNBUFFERED='1')
    log_path = tmp_path / 'supervisor.log'
    known_children = []
    with log_path.open('w') as log:
        supervisor = subprocess.Popen([sys.executable, str(ROOT / 'standalone/serve.py')],
                                      cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True)
        try:
            with httpx.Client(base_url=url, timeout=5, follow_redirects=False,
                              headers={'Origin': url}) as client:
                def ready():
                    if supervisor.poll() is not None:
                        raise AssertionError('Supervisor exited during startup: ' + log_path.read_text()[-2500:])
                    try:
                        return client.get('/healthz').status_code == 200
                    except httpx.HTTPError:
                        return False

                wait_until(ready)
                api_url = f'http://127.0.0.1:{api_port}'
                assert httpx.get(api_url + '/state').status_code == 401
                api_state = httpx.get(api_url + '/state', headers={'Authorization': 'Bearer ' + env['MORPHOPILOT_API_TOKEN']})
                assert api_state.status_code == 200 and api_state.json()['category_id'] == 'neurite'
                routes = json.loads((runtime / '.category-runtime.json').read_text())['origins']
                assert routes['neurite'] == api_url
                known_children = children_of(supervisor.pid)
                assert len(known_children) == 5, (known_children, log_path.read_text()[-2500:])
                assert client.get('/api/gpu/categories/neurite/state').status_code == 401
                logged_in = client.post('/login', data={'password': password})
                assert logged_in.status_code == 303, logged_in.text
                assert 'HttpOnly' in logged_in.headers['set-cookie']
                assert 'SameSite=strict' in logged_in.headers['set-cookie']
                assert client.get('/').status_code == 200
                for category in CATEGORIES:
                    response = client.get(f'/api/gpu/categories/{category}/state')
                    assert response.status_code == 200, response.text
                    state = response.json()
                    assert state['category_id'] == category
                    assert state['active_model'] == 'official'
                    assert len(state['models']) == 1
                    assert state['models'][0]['category_id'] == category
                    assert {entry['id'] for entry in state['capabilities']['categories'] if entry['available']} == set(CATEGORIES)
                    assert [item['id'] for item in state['samples']] == (['legacy_roi'] if category == primary else [])
                    assert env['MORPHOPILOT_API_TOKEN'] not in response.text
                    category_root = runtime if category == primary else runtime / 'categories' / category
                    assert json.loads((category_root / 'category.json').read_text())['id'] == category
                assert client.get('/api/gpu/workspaces/legacy_spot/state').json()['category_id'] == 'spot'
                assert client.get('/api/gpu/categories/unknown/state').status_code == 404
                assert client.post('/api/gpu/categories/cell/train', json={'steps': 1},
                                   headers={'Origin': 'https://untrusted.example'}).status_code == 403
                assert client.post('/api/gpu/categories/cell/volumes/uploads',
                                   json={'name': 'bad.npy', 'size': 128, 'category': 'spot'}).status_code == 422

                # Round-trip bytes through the real gateway and cross-process
                # category proxy, then confirm only the chosen category sees them.
                raw = io.BytesIO()
                np.save(raw, np.arange(60, dtype=np.float32).reshape(3, 4, 5))
                payload = raw.getvalue()
                endpoint = '/api/gpu/categories/cell'
                response = client.post(endpoint + '/volumes/uploads', json={
                    'name': 'cell-gateway-test.npy', 'size': len(payload), 'category': 'cell',
                    'tile_shape': [3, 4, 5]})
                assert response.status_code == 200, response.text
                upload = response.json()['upload']
                assert upload['category_id'] == 'cell'
                assert client.post(endpoint + f'/volumes/uploads/{upload["id"]}/chunk?offset=0',
                                   content=payload, headers={'Content-Type': 'application/octet-stream'}).status_code == 200
                volume = client.post(endpoint + f'/volumes/uploads/{upload["id"]}/complete').json()['volume']

                def imported():
                    detail = client.get(endpoint + '/volumes/' + volume['id']).json()
                    assert detail['volume']['status'] != 'failed', detail
                    return detail['volume']['status'] == 'ready'

                wait_until(imported, timeout=10)
                for category in CATEGORIES:
                    volumes = client.get(f'/api/gpu/categories/{category}/volumes').json()['volumes']
                    assert [item['id'] for item in volumes] == ([volume['id']] if category == 'cell' else [])
                    if category != 'cell':
                        assert client.get(f'/api/gpu/categories/{category}/volumes/{volume["id"]}').status_code == 404

                # A duplicate supervisor must fail without disturbing the live one.
                duplicate = subprocess.run([sys.executable, str(ROOT / 'standalone/serve.py')],
                                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
                assert duplicate.returncode != 0
                assert 'already has a category service running' in duplicate.stderr
                assert client.get('/api/gpu/categories/cell/state').status_code == 200
                if stop_mode == 'sigterm':
                    supervisor.send_signal(signal.SIGTERM)
                else:
                    # All direct children are owned processes of this temporary
                    # supervisor. Losing one must close the entire installation.
                    os.kill(known_children[0], signal.SIGTERM)
                code = supervisor.wait(timeout=40)
                assert code == 0 if stop_mode == 'sigterm' else code != 0
                wait_until(lambda: not any(alive(pid) for pid in known_children), timeout=10)
                with socket.socket() as probe:
                    probe.settimeout(.5)
                    assert probe.connect_ex(('127.0.0.1', port)) != 0
                assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in legacy.iterdir()} == existing_hashes
        finally:
            # Always clean up this test's subprocess groups, including on a
            # failed assertion; never leave local category workers running.
            if supervisor.poll() is None:
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


def test_pinned_api_port_restarts_immediately_after_server_closes_keepalive(tmp_path):
    """A stable proxy endpoint must survive ordinary supervisor restarts."""
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<!doctype html><title>Restart fixture</title>')
    reservations = [socket.socket(), socket.socket()]
    try:
        for reservation in reservations:
            reservation.bind(('127.0.0.1', 0))
        port, api_port = [reservation.getsockname()[1] for reservation in reservations]
    finally:
        for reservation in reservations:
            reservation.close()
    url, api_url = f'http://127.0.0.1:{port}', f'http://127.0.0.1:{api_port}'
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(b'isolated-restart-password', salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    env = {key: value for key, value in os.environ.items() if not key.startswith('MORPHOPILOT_')}
    env.update(MORPHOPILOT_DATA_DIR=str(tmp_path / 'runtime'), MORPHOPILOT_CATEGORY='neurite',
               MORPHOPILOT_API_TOKEN=secrets.token_urlsafe(48), MORPHOPILOT_SESSION_SECRET=secrets.token_urlsafe(48),
               MORPHOPILOT_PASSWORD_HASH=f'scrypt${salt.hex()}${digest}', MORPHOPILOT_WEB_DIR=str(dist),
               MORPHOPILOT_HOST='127.0.0.1', MORPHOPILOT_PORT=str(port), MORPHOPILOT_API_PORT=str(api_port),
               MORPHOPILOT_PUBLIC_ORIGIN=url, MORPHOPILOT_COOKIE_SECURE='0', PYTHONUNBUFFERED='1')
    log_path = tmp_path / 'pinned-restart.log'
    processes, known_children = [], []
    with log_path.open('w') as log:
        def start():
            process = subprocess.Popen([sys.executable, str(ROOT / 'standalone/serve.py')], cwd=ROOT,
                                       env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            def ready():
                if process.poll() is not None:
                    raise AssertionError('Pinned API restart failed: ' + log_path.read_text()[-2500:])
                try:
                    return httpx.get(url + '/healthz', timeout=.5).status_code == 200
                except httpx.HTTPError:
                    return False
            wait_until(ready)
            known_children.extend(children_of(process.pid))
            return process
        try:
            first = start()
            # Keep this HTTP connection open until uvicorn closes it. Closing the
            # client first would move TIME_WAIT to the client and hide the bug.
            with httpx.Client(base_url=api_url, headers={'Authorization': 'Bearer ' + env['MORPHOPILOT_API_TOKEN']}) as connection:
                assert connection.get('/state').json()['category_id'] == 'neurite'
                first.send_signal(signal.SIGTERM)
                assert first.wait(timeout=35) == 0
            second = start()
            result = httpx.get(api_url + '/state', headers={'Authorization': 'Bearer ' + env['MORPHOPILOT_API_TOKEN']})
            assert result.status_code == 200 and result.json()['category_id'] == 'neurite'
            second.send_signal(signal.SIGTERM)
            assert second.wait(timeout=35) == 0
            wait_until(lambda: not any(alive(pid) for pid in known_children))
        finally:
            for process in processes:
                if process.poll() is None:
                    known_children.extend(children_of(process.pid))
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            for pid in set(known_children):
                if alive(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
