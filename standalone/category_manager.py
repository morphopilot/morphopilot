"""Add custom workers beside four existing services without owning those services.

For an existing deployment, set the registry/runtime paths in all four fixed
workers and this manager. Start with the same trusted environment/token/model
configuration as those workers. No browser gateway or fixed worker is started,
stopped or moved here. The self-hosted ZIP continues to use standalone/serve.py.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import category_registry as registry


def fixed_origins(env):
    result = {}
    for category in registry.BUILTIN_LABELS:
        value = env.get(f'MORPHOPILOT_{category.upper()}_ORIGIN')
        if category == 'spot':
            value = value or env.get('MORPHOPILOT_LEGACY_SPOT_ORIGIN')
        try:
            parsed = urlsplit(value or '')
            valid = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port and
                     not parsed.username and not parsed.password and parsed.path in {'', '/'} and
                     not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f'Configure MORPHOPILOT_{category.upper()}_ORIGIN as a fixed loopback HTTP origin.')
        result[category] = value.rstrip('/')
    if len(set(result.values())) != len(result):
        raise ValueError('Every fixed category must have a different worker origin.')
    return result


def healthy(origin, category, token):
    try:
        request = Request(origin + '/health', headers={'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=.4) as response:
            status = json.load(response)
            return status.get('status') == 'ok' and status.get('category_id') == category
    except (OSError, ValueError):
        return False


def main():
    env = dict(os.environ)
    token = env.get('MORPHOPILOT_API_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('The manager requires the same strong MORPHOPILOT_API_TOKEN as the fixed workers.')
    required = ('MORPHOPILOT_CATEGORY_REGISTRY', 'MORPHOPILOT_CATEGORY_RUNTIME', 'MORPHOPILOT_CUSTOM_CATEGORY_ROOT')
    if any(not env.get(key) for key in required):
        raise SystemExit('Configure MORPHOPILOT_CATEGORY_REGISTRY, MORPHOPILOT_CATEGORY_RUNTIME and MORPHOPILOT_CUSTOM_CATEGORY_ROOT.')
    registry_file, runtime_file, custom_root = (Path(env[key]).resolve() for key in required)
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    custom_root.mkdir(parents=True, exist_ok=True)
    fixed = fixed_origins(env)
    env['MORPHOPILOT_CATEGORY_EXTERNAL_MANAGER'] = '1'
    # One manager owns this registry's child processes and ephemeral routes.
    owner = registry_file.with_name(registry_file.name + '.manager.lock').open('a+')
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This registry already has a custom category manager running.')
    processes, pending, origins = {}, {}, {}
    retired = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def publish(accepting=True):
        registry.atomic_json(runtime_file, {'version': 1, 'mode': 'external', 'updated_at': time.time(),
                                           'accepting': accepting, 'origins': origins if accepting else {}})

    def terminate(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def fail(category, message):
        process = processes.pop(category, None)
        if process:
            terminate(process)
            retired.append(process)
        pending.pop(category, None)
        origins.pop(category, None)
        publish()
        registry.update(category, path=registry_file, status='failed', error=message)

    def start(category):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(128)
            origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
            child = {**env, 'MORPHOPILOT_CATEGORY': category,
                     'MORPHOPILOT_DATA_DIR': str(custom_root / category / 'data'),
                     'MORPHOPILOT_DATASET': 'uploads', 'MORPHOPILOT_BOOTSTRAP_DEMO': 'false',
                     'MORPHOPILOT_RELEASE_GPU_ON_IDLE': '1'}
            # Existing report roots are category-specific and must not leak into
            # newly created categories inherited from an administrator's env.
            child.pop('MORPHOPILOT_REPORT_DIR', None)
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'app:app', '--app-dir', str(ROOT / 'backend'),
                 '--fd', str(listener.fileno()), '--workers', '1'],
                env=child, cwd=ROOT, pass_fds=(listener.fileno(),), start_new_session=True)
            processes[category] = process
            pending[category] = {'origin': origin, 'deadline': time.monotonic() + 90}

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with registry.registry_lock(registry_file):
            saved = registry.entries(registry_file)
            for record in saved:
                record.update(status='starting', updated_at=time.time())
                record.pop('error', None)
            registry.atomic_json(registry_file, {'version': 1, 'categories': saved})
        last_probe = last_publish = 0
        while not stopping:
            # Probe existing services, but never take ownership of their PIDs.
            if time.monotonic() - last_probe >= 3:
                for category, origin in fixed.items():
                    if healthy(origin, category, token):
                        origins[category] = origin
                    else:
                        origins.pop(category, None)
                last_probe = time.monotonic()
                publish()
            for category, process in list(processes.items()):
                if process.poll() is not None:
                    fail(category, 'This category worker stopped. Retry to restart it; saved data and models are preserved.')
            for category, setup in list(pending.items()):
                if healthy(setup['origin'], category, token):
                    origins[category] = setup['origin']
                    pending.pop(category)
                    publish()
                    registry.update(category, path=registry_file, status='ready')
                elif time.monotonic() >= setup['deadline']:
                    fail(category, 'Category startup timed out. Check the server log and retry; existing categories are unaffected.')
            for record in registry.entries(registry_file):
                category = record['id']
                if record['status'] != 'starting' or category in processes:
                    continue
                try:
                    start(category)
                except OSError as error:
                    print(f'Cannot start custom category {category}: {error}', file=sys.stderr, flush=True)
                    fail(category, 'Could not start this category worker. Check server resources and retry.')
            if time.monotonic() - last_publish >= 2:
                publish()
                last_publish = time.monotonic()
            retired[:] = [process for process in retired if process.poll() is None]
            time.sleep(.25)
        return 0
    finally:
        publish(False)
        owned = [*processes.values(), *retired]
        for process in owned:
            terminate(process)
        deadline = time.monotonic() + 30
        for process in owned:
            try:
                process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        owner.close()


if __name__ == '__main__':
    raise SystemExit(main())
