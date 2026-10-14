"""Supervise isolated category workers and the authenticated local gateway.

The original runtime remains its primary category. Added categories persist under
runtime/categories/ and always start with their own original nnInteractive state.
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
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import category_registry as registry

CATEGORIES = tuple(registry.BUILTIN_LABELS)


def category_roots(base, primary, custom_ids=()):
    if primary not in CATEGORIES:
        raise ValueError('MORPHOPILOT_CATEGORY must be neurite, spot, cell, or organ.')
    if any(not registry.CUSTOM_ID.fullmatch(category) for category in custom_ids):
        raise ValueError('Invalid custom category ID.')
    return {category: base if category == primary else base / 'categories' / category
            for category in (*CATEGORIES, *custom_ids)}


def worker_environments(env, roots, origins):
    result = {}
    for category in roots:
        child = dict(env)
        child.update(MORPHOPILOT_CATEGORY=category, MORPHOPILOT_DATA_DIR=str(roots[category]),
                     MORPHOPILOT_DATASET='uploads', MORPHOPILOT_BOOTSTRAP_DEMO='false',
                     MORPHOPILOT_RELEASE_GPU_ON_IDLE='1')
        child.pop('MORPHOPILOT_REPORT_DIR', None)
        for target, origin in origins.items():
            child[f'MORPHOPILOT_{target.upper()}_ORIGIN'] = origin
        result[category] = child
    return result


def main():
    env = dict(os.environ)
    if len(env.get('MORPHOPILOT_API_TOKEN', '')) < 32:
        raise SystemExit('Run deploy/configure.py and start with bash deploy/start.sh.')
    base = Path(env.get('MORPHOPILOT_DATA_DIR', str(ROOT / 'runtime'))).resolve()
    base.mkdir(parents=True, exist_ok=True)
    primary = env.get('MORPHOPILOT_CATEGORY') or ('spot' if env.get('MORPHOPILOT_DATASET') == 'legacy_spot' else 'neurite')
    roots = category_roots(base, primary)
    # Optional stable, authenticated API entry for a Sites HTTPS reverse proxy.
    # It stays loopback-only and separate from the password browser gateway.
    try:
        api_port = int(env.get('MORPHOPILOT_API_PORT', '0'))
    except ValueError:
        raise SystemExit('MORPHOPILOT_API_PORT must be an unused port from 1024 to 65535.')
    if api_port and (not 1024 <= api_port <= 65535 or api_port == int(env.get('MORPHOPILOT_PORT', '8787'))):
        raise SystemExit('MORPHOPILOT_API_PORT must be 1024–65535 and different from MORPHOPILOT_PORT.')
    env.setdefault('MORPHOPILOT_GPU_LOCK_PATH', str(base / 'gpu.lock'))
    # These files belong to this runtime; they never come from browser input.
    registry_file = base / 'category-registry.json'
    runtime_file = base / '.category-runtime.json'
    env.update(MORPHOPILOT_CATEGORY_REGISTRY=str(registry_file), MORPHOPILOT_CATEGORY_RUNTIME=str(runtime_file))
    runtime_lock = (base / '.category-services.lock').open('a+')
    try:
        fcntl.flock(runtime_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This runtime already has a category service running.')
    processes, pending, origins, listeners = {}, {}, {}, []
    retired = []
    gateway = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def publish(accepting=True):
        registry.atomic_json(runtime_file, {'version': 1, 'updated_at': time.time(),
                                           'accepting': accepting, 'origins': origins if accepting else {}})

    def terminate(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def fail_custom(category, error):
        process = processes.pop(category, None)
        if process:
            terminate(process)
            retired.append(process)
        pending.pop(category, None)
        origins.pop(category, None)
        publish()
        registry.update(category, path=registry_file, status='failed', error=error)

    def start_worker(category):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listeners.append(listener)
        try:
            # A pinned API port must reopen after graceful shutdown closes
            # keep-alive connections and leaves server sockets in TIME_WAIT.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', api_port if category == 'neurite' else 0))
            listener.listen(128)
            origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
            child = worker_environments(env, {category: roots[category]}, origins)[category]
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'app:app', '--app-dir', str(ROOT / 'backend'),
                 '--fd', str(listener.fileno()), '--workers', '1'],
                env=child, cwd=ROOT, pass_fds=(listener.fileno(),), start_new_session=True)
            processes[category] = process
            pending[category] = {'origin': origin, 'deadline': time.monotonic() + 90}
        finally:
            listener.close()
            listeners.remove(listener)

    def check_workers():
        for category, process in list(processes.items()):
            if process.poll() is not None:
                if category in CATEGORIES:
                    raise RuntimeError(f'The {category} worker stopped. See its log above.')
                fail_custom(category, 'This category worker stopped. Retry to restart it; saved data and models are preserved.')
        for category, start in list(pending.items()):
            ready = False
            try:
                request = Request(start['origin'] + '/health', headers={'Authorization': 'Bearer ' + env['MORPHOPILOT_API_TOKEN']})
                with urlopen(request, timeout=.3) as response:
                    status = json.load(response)
                    ready = status.get('status') == 'ok' and status.get('category_id') == category
            except (OSError, ValueError):
                pass
            if ready:
                origins[category] = start['origin']
                pending.pop(category)
                publish()
                if category not in CATEGORIES:
                    registry.update(category, path=registry_file, status='ready')
            elif time.monotonic() >= start['deadline']:
                if category in CATEGORIES:
                    raise RuntimeError(f'The {category} worker startup timed out. See its log above.')
                fail_custom(category, 'Category startup timed out. Check the server log and retry; existing categories are unaffected.')

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # A restart refreshes ports while preserving names, roots and model state.
        with registry.registry_lock(registry_file):
            saved = registry.entries(registry_file)
            for record in saved:
                record.update(status='starting', updated_at=time.time())
                record.pop('error', None)
            registry.atomic_json(registry_file, {'version': 1, 'categories': saved})
        roots.update(category_roots(base, primary, [record['id'] for record in saved]))
        publish()
        for category in CATEGORIES:
            start_worker(category)
        while set(CATEGORIES) - set(origins) and not stopping:
            check_workers()
            publish()
            time.sleep(.2)
        if stopping:
            return 0
        gateway_env = {**env, 'MORPHOPILOT_BACKEND_ORIGIN': origins['neurite']}
        gateway = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'standalone.gateway:app', '--host', env.get('MORPHOPILOT_HOST', '127.0.0.1'),
             '--port', env.get('MORPHOPILOT_PORT', '8787'), '--workers', '1', '--timeout-keep-alive', '30',
             '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'],
            env=gateway_env, cwd=ROOT, start_new_session=True)
        print('Category workers ready. Each category has independent images, masks and models.', flush=True)
        last_publish = 0
        while not stopping:
            if gateway.poll() is not None:
                raise RuntimeError('The gateway stopped. Shutting down this installation.')
            check_workers()
            for record in registry.entries(registry_file):
                category = record['id']
                if record['status'] != 'starting' or category in processes:
                    continue
                roots[category] = base / 'categories' / category
                try:
                    start_worker(category)
                except OSError as error:
                    print(f'Cannot start category worker {category}: {error}', file=sys.stderr, flush=True)
                    fail_custom(category, 'Could not start this category worker. Check server resources and retry.')
            if time.monotonic() - last_publish >= 2:
                publish()
                last_publish = time.monotonic()
            # Reap failed children without blocking healthy categories.
            retired[:] = [process for process in retired if process.poll() is None]
            time.sleep(.25)
        return 0
    finally:
        publish(False)
        for listener in listeners:
            listener.close()
        all_processes = [*processes.values(), *retired, *([gateway] if gateway else [])]
        for process in reversed(all_processes):
            terminate(process)
        deadline = time.monotonic() + 30
        for process in reversed(all_processes):
            try:
                process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        runtime_lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
