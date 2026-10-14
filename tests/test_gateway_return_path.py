"""Login preserves SPA links without allowing redirects outside the workspace."""
import hashlib
import html
import importlib.util
from pathlib import Path
import re
import sys
import types
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>workspace fixture</html>')
    password = 'gateway-test-password'
    salt = bytes.fromhex('a1' * 16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    monkeypatch.setenv('MORPHOPILOT_WEB_DIR', str(dist))
    monkeypatch.setenv('MORPHOPILOT_API_TOKEN', 'api-fixture-token-' + 'a' * 32)
    monkeypatch.setenv('MORPHOPILOT_SESSION_SECRET', 'session-fixture-' + 'b' * 32)
    monkeypatch.setenv('MORPHOPILOT_PASSWORD_HASH', f'scrypt${salt.hex()}${digest}')
    monkeypatch.setenv('MORPHOPILOT_PUBLIC_ORIGIN', 'http://testserver')
    monkeypatch.setenv('MORPHOPILOT_COOKIE_SECURE', '0')
    # Auth tests never import or initialize a real annotation runtime.
    fake_backend = types.ModuleType('app')
    fake_backend.app = FastAPI()
    monkeypatch.setitem(sys.modules, 'app', fake_backend)
    original_path = list(sys.path)
    spec = importlib.util.spec_from_file_location('gateway_return_path_test', Path(__file__).resolve().parents[1] / 'standalone/gateway.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.app, follow_redirects=False)
    yield client, module, password
    client.close()
    sys.path[:] = original_path


def hidden_next(response):
    match = re.search(r'<input type="hidden" name="next" value="([^"]*)">', response.text)
    assert match, response.text
    return html.unescape(match.group(1))


@pytest.mark.parametrize('target', ['/library', '/library?dataset=neurite', '/?volume=vol_first&tile=vol_first_tile_00009&z=12'])
def test_expired_session_login_returns_to_exact_deep_link(gateway, target):
    client, module, password = gateway
    client.cookies.set(module.COOKIE, 'expired.invalid')
    redirect = client.get(target)
    assert redirect.status_code == 303
    location = redirect.headers['location']
    assert urlsplit(location).path == '/login'
    assert parse_qs(urlsplit(location).query)['next'] == [target]
    page = client.get(location)
    assert hidden_next(page) == target
    # A typo must not lose the selected collection.
    invalid = client.post('/login', data={'password': 'wrong', 'next': target}, headers={'Origin': 'http://testserver'})
    assert invalid.status_code == 401
    assert hidden_next(invalid) == target
    success = client.post('/login', data={'password': password, 'next': target}, headers={'Origin': 'http://testserver'})
    assert success.status_code == 303
    assert success.headers['location'] == target
    assert 'httponly' in success.headers['set-cookie'].lower()
    assert 'samesite=strict' in success.headers['set-cookie'].lower()
    assert client.get(target).status_code == 200


@pytest.mark.parametrize('target', [
    'https://outside.example/library', '//outside.example/library', '///library',
    '/\\outside.example', 'javascript:alert(1)', '/%2f%2foutside.example',
    '/login', '/login?next=/library', '/auth/logout', '/api/gpu/state',
    '/healthz', '/library/../login', '/library?x=\nheader', ' /library',
])
def test_unsafe_or_recursive_return_path_falls_back_to_root(gateway, target):
    client, module, password = gateway
    assert module.safe_return_to(target) == '/'
    page = client.get('/login', params={'next': target})
    assert hidden_next(page) == '/'
    response = client.post('/login', data={'password': password, 'next': target}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 303
    assert response.headers['location'] == '/'


def test_return_path_html_escaping_and_same_origin_policy(gateway):
    client, module, password = gateway
    target = '/library?name="<img src=x onerror=alert(1)>'
    page = client.get('/login', params={'next': target})
    assert hidden_next(page) == target
    assert '<img ' not in page.text
    response = client.post('/login', data={'password': password, 'next': '/library'}, headers={'Origin': 'https://outside.example'})
    assert response.status_code == 403
    assert 'set-cookie' not in response.headers
    assert client.get('/api/gpu/state').status_code == 401
