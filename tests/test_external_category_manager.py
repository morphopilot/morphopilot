"""An optional custom worker manager leaves existing fixed services untouched."""
import importlib.util
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
import pytest

from test_category_supervisor import alive, children_of, wait_until

ROOT = Path(__file__).resolve().parents[1]


def load_manager():
    spec = importlib.util.spec_from_file_location('category_manager_test', ROOT / 'standalone/category_manager.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manager_rejects_nonlocal_duplicate_fixed_origins():
    manager = load_manager()
    env = {f'MORPHOPILOT_{key.upper()}_ORIGIN': f'http://127.0.0.1:{9000+i}'
           for i, key in enumerate(('neurite', 'spot', 'cell', 'organ'))}
    assert len(manager.fixed_origins(env)) == 4
    for bad in ('https://example.com', 'http://127.0.0.1:9000/path', 'http://user@127.0.0.1:9000'):
        with pytest.raises(ValueError):
            manager.fixed_origins({**env, 'MORPHOPILOT_SPOT_ORIGIN': bad})
    with pytest.raises(ValueError, match='different'):
        manager.fixed_origins({**env, 'MORPHOPILOT_SPOT_ORIGIN': env['MORPHOPILOT_NEURITE_ORIGIN']})


def test_external_manager_preserves_fixed_workers_and_custom_restart(tmp_path):
    if os.name != 'posix':
        pytest.skip('Deployment workers use POSIX sockets and process groups.')
    paths = {key: tmp_path / ('legacy-neurites' if key == 'neurite' else f'experiment-{key}/data')
             for key in ('neurite', 'spot', 'cell', 'organ')}
    manager_root = tmp_path / 'management'
    token = secrets.token_urlsafe(48)
    env = {key: value for key, value in os.environ.items() if not key.startswith('MORPHOPILOT_')}
    env.update(MORPHOPILOT_API_TOKEN=token, MORPHOPILOT_CATEGORY_REGISTRY=str(manager_root / 'category-registry.json'),
               MORPHOPILOT_CATEGORY_RUNTIME=str(manager_root / '.category-runtime.json'),
               MORPHOPILOT_CUSTOM_CATEGORY_ROOT=str(tmp_path / 'categories'),
               MORPHOPILOT_CATEGORY_EXTERNAL_MANAGER='1', MORPHOPILOT_BOOTSTRAP_DEMO='false',
               MORPHOPILOT_GPU_LOCK_PATH=str(tmp_path / 'gpu.lock'), PYTHONUNBUFFERED='1')
    processes, listeners, origins = {}, {}, {}
    manager = None
    known_custom = []
    logfile = tmp_path / 'manager-test.log'
    for category in paths:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        listeners[category] = listener
        origins[category] = f'http://127.0.0.1:{listener.getsockname()[1]}'
        env[f'MORPHOPILOT_{category.upper()}_ORIGIN'] = origins[category]
    with logfile.open('w') as log, httpx.Client(base_url=origins['neurite'], timeout=5,
          headers={'Authorization': 'Bearer ' + token}) as client:
        def begin_manager():
            nonlocal manager
            manager = subprocess.Popen([sys.executable, str(ROOT / 'standalone/category_manager.py')],
                env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            wait_until(lambda: client.get('/categories').json()['can_create'])

        def stop_manager():
            known_custom.extend(children_of(manager.pid))
            manager.send_signal(signal.SIGTERM)
            assert manager.wait(timeout=35) == 0
            wait_until(lambda: not any(alive(pid) for pid in known_custom))

        try:
            for category, listener in listeners.items():
                child = {**env, 'MORPHOPILOT_CATEGORY': category, 'MORPHOPILOT_DATASET': 'uploads',
                         'MORPHOPILOT_DATA_DIR': str(paths[category])}
                processes[category] = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app',
                    '--app-dir', str(ROOT / 'backend'), '--fd', str(listener.fileno())],
                    env=child, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    pass_fds=(listener.fileno(),), start_new_session=True)
                listener.close()
            def fixed_ready():
                try:
                    return all(client.get(origin + '/health').status_code == 200 for origin in origins.values())
                except httpx.HTTPError:
                    return False
            wait_until(fixed_ready)
            fixed_pids = {category: process.pid for category, process in processes.items()}
            originals = {category: (path / 'state.json').read_bytes() for category, path in paths.items()}
            assert not client.get('/categories').json()['can_create']
            begin_manager()
            wait_until(lambda: all(item['available'] for item in client.get('/categories').json()['categories']))
            response = client.post('/categories', json={'label': 'Vascular branches'})
            assert response.status_code == 202, response.text
            category = response.json()['category']['id']
            def record():
                return next(item for item in client.get('/categories').json()['categories'] if item['id'] == category)
            wait_until(lambda: record()['available'])
            assert client.get(f'/categories/{category}/state').json()['active_model'] == 'official'
            custom_path = tmp_path / 'categories' / category / 'data'
            assert json.loads((custom_path / 'category.json').read_text())['id'] == category
            assert len(children_of(manager.pid)) == 1
            assert all(process.poll() is None for process in processes.values())
            assert {category: process.pid for category, process in processes.items()} == fixed_pids
            # Persisted model choice belongs to this custom root and survives a
            # manager restart; no model files from another root are copied.
            custom_marker = custom_path / 'preservation-fixture.txt'
            custom_marker.write_text('custom data stays in place')
            duplicate = subprocess.run([sys.executable, str(ROOT / 'standalone/category_manager.py')],
                env=env, cwd=ROOT, capture_output=True, text=True, timeout=10)
            assert duplicate.returncode != 0 and 'already has a custom category manager' in duplicate.stderr
            stop_manager()
            assert not client.get('/categories').json()['can_create']
            assert all(process.poll() is None for process in processes.values())
            # Static category routing continues while the optional manager is off.
            for fixed in paths:
                assert client.get(f'/categories/{fixed}/state').json()['category_id'] == fixed
            assert client.get(f'/categories/{category}/state').status_code == 503
            begin_manager()
            wait_until(lambda: record()['available'])
            assert custom_marker.read_text() == 'custom data stays in place'
            assert client.get(f'/categories/{category}/state').json()['active_model'] == 'official'
            assert {key: (path / 'state.json').read_bytes() for key, path in paths.items()} == originals
            # A fixed service that actually stops is marked unavailable, without
            # the manager starting, stopping, or restarting that external service.
            processes['spot'].send_signal(signal.SIGTERM)
            processes['spot'].wait(timeout=10)
            wait_until(lambda: not next(item for item in client.get('/categories').json()['categories'] if item['id'] == 'spot')['available'])
            assert client.get('/categories/spot/state').status_code == 503
            assert client.get('/categories/cell/state').status_code == 200
            stop_manager()
        finally:
            if manager and manager.poll() is None:
                known_custom.extend(children_of(manager.pid))
                manager.send_signal(signal.SIGTERM)
                try:
                    manager.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    manager.kill()
                    manager.wait(timeout=5)
            for process in processes.values():
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
            for process in processes.values():
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            for pid in known_custom:
                if alive(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for listener in listeners.values():
                listener.close()
