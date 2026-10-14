"""CPU-only security and routing regression tests; uses a disposable workspace."""
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
dist = Path(temporary.name) / 'dist'
dist.mkdir()
(dist / 'index.html').write_text('<!doctype html><title>MorphoPilot gateway test</title>')
# Never connect this unit test to a developer's configured live workspace.
for key in list(os.environ):
    if key.startswith('MORPHOPILOT_'):
        os.environ.pop(key)
password = 'test-only-password-42'
salt = secrets.token_bytes(16)
os.environ.update({
    'MORPHOPILOT_DATA_DIR': str(Path(temporary.name) / 'runtime'),
    'MORPHOPILOT_DATASET': 'uploads',
    'MORPHOPILOT_CATEGORY': 'neurite',
    'MORPHOPILOT_WEB_DIR': str(dist),
    'MORPHOPILOT_SESSION_SECRET': secrets.token_urlsafe(48),
    'MORPHOPILOT_API_TOKEN': secrets.token_urlsafe(48),
    'MORPHOPILOT_PASSWORD_HASH': 'scrypt$' + salt.hex() + '$' + hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex(),
    'MORPHOPILOT_PUBLIC_ORIGIN': '',
    'MORPHOPILOT_COOKIE_SECURE': '0',
})
spec = importlib.util.spec_from_file_location('gateway', ROOT / 'standalone/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(gateway.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()

    def login(self):
        return self.client.post('/login', data={'password': password}, headers={'origin': 'http://testserver'})

    def test_protected_routes_and_valid_login(self):
        self.assertEqual(self.client.get('/').status_code, 303)
        self.assertEqual(self.client.get('/api/gpu/state').status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/').status_code, 200)
        state = self.client.get('/api/gpu/state')
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()['samples'], [])
        self.assertNotIn(os.environ['MORPHOPILOT_API_TOKEN'], state.text)

    def test_wrong_password_and_cross_origin_are_rejected(self):
        self.assertEqual(self.client.post('/login', data={'password': password}, headers={'origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/login', data={'password': 'wrong'}, headers={'origin': 'http://testserver'}).status_code, 401)
        self.login()
        self.assertEqual(self.client.post('/api/gpu/train', json={'steps': 1}, headers={'origin': 'https://evil.example'}).status_code, 403)

    def test_logout_and_tampered_cookie(self):
        self.login()
        self.assertEqual(self.client.post('/auth/logout', headers={'origin': 'http://testserver'}).status_code, 303)
        self.assertEqual(self.client.get('/api/gpu/state').status_code, 401)
        self.client.cookies.set(gateway.COOKIE, '99999999999:forged.bad')
        self.assertEqual(self.client.get('/api/gpu/state').status_code, 401)


if __name__ == '__main__':
    unittest.main()
