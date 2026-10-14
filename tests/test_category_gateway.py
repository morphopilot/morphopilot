"""Self-hosted category routing keeps browser authentication and data roots separate."""
import hashlib
import importlib.util
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_category_workers_preserve_legacy_root_and_isolate_models(tmp_path):
    serve = load_module('category_serve_test', ROOT / 'standalone/serve.py')
    roots = serve.category_roots(tmp_path, 'spot')
    assert roots['spot'] == tmp_path
    assert len(set(roots.values())) == 4
    assert roots['neurite'] == tmp_path / 'categories/neurite'
    origins = {category: f'http://127.0.0.1:{9000+i}' for i, category in enumerate(serve.CATEGORIES)}
    env = {'MORPHOPILOT_API_TOKEN': 'secret', 'MORPHOPILOT_GPU_LOCK_PATH': str(tmp_path / 'gpu.lock'),
           'MORPHOPILOT_DATA_DIR': str(tmp_path), 'MORPHOPILOT_BOOTSTRAP_DEMO': 'true'}
    children = serve.worker_environments(env, roots, origins)
    for category, child in children.items():
        assert child['MORPHOPILOT_CATEGORY'] == category
        assert child['MORPHOPILOT_DATA_DIR'] == str(roots[category])
        assert child['MORPHOPILOT_BOOTSTRAP_DEMO'] == 'false'
        assert child['MORPHOPILOT_GPU_LOCK_PATH'] == env['MORPHOPILOT_GPU_LOCK_PATH']
        assert child['MORPHOPILOT_RELEASE_GPU_ON_IDLE'] == '1'
        for target, origin in origins.items():
            assert child[f'MORPHOPILOT_{target.upper()}_ORIGIN'] == origin
    assert env['MORPHOPILOT_BOOTSTRAP_DEMO'] == 'true'
    with pytest.raises(ValueError):
        serve.category_roots(tmp_path, '../spot')


@pytest.fixture
def category_gateway(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('<html>Category fixture</html>')
    salt = bytes.fromhex('b1' * 16)
    digest = hashlib.scrypt(b'category-test-password', salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    for key, value in {
        'MORPHOPILOT_WEB_DIR': str(tmp_path), 'MORPHOPILOT_API_TOKEN': 'a' * 48,
        'MORPHOPILOT_SESSION_SECRET': 'b' * 48, 'MORPHOPILOT_PASSWORD_HASH': f'scrypt${salt.hex()}${digest}',
        'MORPHOPILOT_PUBLIC_ORIGIN': 'http://testserver', 'MORPHOPILOT_BACKEND_ORIGIN': 'http://127.0.0.1:9000',
    }.items():
        monkeypatch.setenv(key, value)
    module = load_module('category_gateway_test', ROOT / 'standalone/gateway.py')
    with TestClient(module.app, follow_redirects=False) as client:
        yield client, module


def test_category_proxy_auth_headers_upload_and_streaming(category_gateway, monkeypatch):
    client, module = category_gateway
    seen = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first'
            yield b'second'

    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=Stream(), headers={'Content-Type': 'image/tiff', 'Content-Disposition': 'attachment; filename=mask.tif'})

    constructor = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: constructor(transport=httpx.MockTransport(handler), **kwargs))
    assert client.get('/api/gpu/categories/spot/state').status_code == 401
    assert not seen
    client.cookies.set(module.COOKIE, module.issue_session())
    response = client.post('/api/gpu/categories/spot/volumes/uploads/u/chunk?offset=4', content=b'raw-bytes',
                           headers={'Origin': 'http://testserver', 'Content-Type': 'application/octet-stream', 'Authorization': 'Bearer browser-override'})
    assert response.status_code == 200
    assert response.content == b'firstsecond'
    assert response.headers['content-disposition'] == 'attachment; filename=mask.tif'
    assert seen[0].url.path == '/categories/spot/volumes/uploads/u/chunk'
    assert seen[0].url.query == b'offset=4'
    assert seen[0].content == b'raw-bytes'
    assert seen[0].headers['authorization'] == 'Bearer ' + module.API_TOKEN
    assert module.API_TOKEN not in response.text
    assert client.post('/api/gpu/categories/cell/train', json={}, headers={'Origin': 'https://elsewhere.example'}).status_code == 403
    assert len(seen) == 1


def test_category_proxy_rejects_redirects(category_gateway, monkeypatch):
    client, module = category_gateway
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={'Location': 'https://elsewhere.example'})
    constructor = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: constructor(transport=httpx.MockTransport(handler), **kwargs))
    client.cookies.set(module.COOKIE, module.issue_session())
    assert client.get('/api/gpu/categories/organ/state').status_code == 502
    assert len(requests) == 1
