"""Self-hosted gateway: same-origin password session + isolated category APIs.

The backend is mounted in this process; its bearer token never reaches the browser.
Bind to loopback by default. Use SSH forwarding, or a TLS reverse proxy.
"""
import asyncio
import base64
from collections import defaultdict, deque
import hashlib
import hmac
import html
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(os.environ.get('MORPHOPILOT_WEB_DIR', str(ROOT / 'standalone/dist'))).resolve()
SESSION_SECRET = os.environ.get('MORPHOPILOT_SESSION_SECRET', '')
PASSWORD_HASH = os.environ.get('MORPHOPILOT_PASSWORD_HASH', '')
API_TOKEN = os.environ.get('MORPHOPILOT_API_TOKEN', '')
PUBLIC_ORIGIN = os.environ.get('MORPHOPILOT_PUBLIC_ORIGIN', '').rstrip('/')
COOKIE_SECURE = os.environ.get('MORPHOPILOT_COOKIE_SECURE', '0') == '1'
SESSION_SECONDS = 12 * 60 * 60
COOKIE = 'morphopilot_session'
if min(len(SESSION_SECRET), len(API_TOKEN)) < 32 or not PASSWORD_HASH.startswith('scrypt$'):
    raise RuntimeError('Run deploy/configure.py before starting. Missing session secret, password hash, or backend token.')
if not (DIST / 'index.html').is_file():
    raise RuntimeError('Missing standalone/dist/index.html. Run npm ci and npm run build before starting the gateway.')


def valid_session(value):
    try:
        payload, signature = value.rsplit('.', 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        expires = int(payload.split(':', 1)[0])
        return hmac.compare_digest(signature, expected) and time.time() < expires <= time.time() + SESSION_SECONDS + 5
    except (ValueError, AttributeError):
        return False


def issue_session():
    payload = f'{int(time.time()) + SESSION_SECONDS}:{secrets.token_urlsafe(24)}'
    return payload + '.' + hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def password_matches(password):
    try:
        _, salt, expected = PASSWORD_HASH.split('$')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def safe_return_to(value):
    """Preserve known SPA deep links without accepting external/auth redirects."""
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith('/') or value.startswith('//'):
        return '/'
    if '\\' in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return '/'
    try:
        target = urlsplit(value)
    except ValueError:
        return '/'
    if target.scheme or target.netloc or target.path not in {'/', '/library', '/library/'}:
        return '/'
    return value


def login_page(error='', return_to='/'):
    hidden_return = f'<input type="hidden" name="next" value="{html.escape(safe_return_to(return_to), quote=True)}">'
    return HTMLResponse('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MorphoPilot · Sign in</title><style>body{margin:0;background:#f5f7f9;color:#17212b;font:18px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}main{background:white;padding:40px;border:1px solid #dce3e8;border-radius:16px;width:min(400px,80vw)}h1{font-size:28px;margin-top:0}label{display:block;margin:24px 0 8px}input,button{box-sizing:border-box;width:100%;padding:14px;font:inherit;border-radius:8px;border:1px solid #bbc7cf}button{margin-top:18px;background:#0f766e;color:white;cursor:pointer}p{color:#516170;line-height:1.5}.error{color:#b42318}</style><main><h1>MorphoPilot</h1><p>Sign in to your local annotation workspace.</p>''' + (f'<p class="error" role="alert">{html.escape(error)}</p>' if error else '') + '''<form method="post" action="/login">''' + hidden_return + '''<label for="password">Workspace password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button type="submit">Sign in</button></form></main></html>''', status_code=401 if error else 200)


app = FastAPI(title='MorphoPilot · Self-hosted', docs_url=None, redoc_url=None, openapi_url=None)
attempts = defaultdict(deque)


@app.middleware('http')
async def session_gate(request, call_next):
    path = request.url.path
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('origin', '')
        expected = PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
        if origin != expected:
            return JSONResponse({'detail': 'This action requires the same-origin browser session.'}, status_code=403)
    if path not in {'/login', '/healthz'} and not valid_session(request.cookies.get(COOKIE)):
        if path.startswith('/api/') or path.startswith('/auth/'):
            return JSONResponse({'detail': 'Your sign-in session has expired. Reload this page to sign in again.'}, status_code=401)
        return_to = safe_return_to(path + ('?' + request.url.query if request.url.query else ''))
        return RedirectResponse('/login?' + urlencode({'next': return_to}), status_code=303)
    response = await call_next(request)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})
    return response


@app.get('/healthz')
def healthz():
    return {'status': 'ok', 'service': 'morphopilot-standalone'}


@app.get('/login')
def login(request: Request):
    return login_page(return_to=request.query_params.get('next', '/'))


@app.post('/login')
async def sign_in(request: Request):
    ip = request.client.host if request.client else 'local'
    now = time.monotonic()
    if len(attempts) > 1000:
        for key in list(attempts):
            if not attempts[key] or now - attempts[key][-1] > 300:
                del attempts[key]
    recent = attempts[ip]
    while recent and now - recent[0] > 300:
        recent.popleft()
    if len(recent) >= 10:
        return JSONResponse({'detail': 'Too many sign-in attempts. Wait five minutes.'}, status_code=429)
    recent.append(now)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            return JSONResponse({'detail': 'Sign-in request too large.'}, status_code=413)
    try:
        fields = parse_qs(body.decode(), max_num_fields=4)
        password = fields.get('password', [''])[0]
        return_to = safe_return_to(fields.get('next', ['/'])[0])
    except (ValueError, UnicodeDecodeError):
        return login_page('Invalid sign-in request.')
    if not await asyncio.to_thread(password_matches, password):
        return login_page('Incorrect password.', return_to=return_to)
    recent.clear()
    response = RedirectResponse(return_to, status_code=303)
    response.set_cookie(COOKIE, issue_session(), max_age=SESSION_SECONDS, httponly=True, secure=COOKIE_SECURE, samesite='strict', path='/')
    return response


@app.post('/auth/logout')
def sign_out():
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(COOKIE, path='/')
    return response


class BackendToken:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope['headers'] = [(k, v) for k, v in scope.get('headers', []) if k.lower() != b'authorization']
        scope['headers'].append((b'authorization', ('Bearer ' + API_TOKEN).encode()))
        await self.application(scope, receive, send)


BACKEND_ORIGIN = os.environ.get('MORPHOPILOT_BACKEND_ORIGIN', '').rstrip('/')
if BACKEND_ORIGIN:
    parsed = urlsplit(BACKEND_ORIGIN)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError('The standalone category backend must use a local loopback HTTP origin.')

    @app.api_route('/api/gpu/{path:path}', methods=['GET', 'POST'])
    async def category_backend(path: str, request: Request):
        if any(part in {'.', '..'} or '\\' in part for part in path.split('/')):
            return JSONResponse({'detail': 'Invalid API path.'}, status_code=400)
        import httpx
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 64 * 1024 * 1024:
                return JSONResponse({'detail': 'The request size limit is 64 MB.'}, status_code=413)
        client = httpx.AsyncClient(timeout=90, follow_redirects=False)
        target = BACKEND_ORIGIN + '/' + path + ('?' + request.url.query if request.url.query else '')
        try:
            upstream = await client.send(client.build_request(request.method, target, content=bytes(body),
                headers={'Authorization': 'Bearer ' + API_TOKEN, 'Content-Type': request.headers.get('content-type', 'application/json'), 'Accept-Encoding': 'identity'}), stream=True)
            if 300 <= upstream.status_code < 400:
                await upstream.aclose()
                await client.aclose()
                return JSONResponse({'detail': 'Unexpected category backend redirect.'}, status_code=502)
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse({'detail': 'The category service is unavailable. Check the server log.'}, status_code=502)

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        headers = {key: value for key, value in upstream.headers.items() if key in {'content-type', 'content-disposition'}}
        return StreamingResponse(chunks(), status_code=upstream.status_code, headers=headers)
else:
    # Compatibility for direct, single-worker embedding and existing auth tests.
    sys.path.insert(0, str(ROOT / 'backend'))
    from app import app as backend_app
    app.mount('/api/gpu', BackendToken(backend_app))


@app.get('/{path:path}')
def frontend(path: str):
    target = (DIST / path).resolve()
    if target.is_relative_to(DIST) and target.is_file():
        return FileResponse(target)
    if path and '.' in Path(path).name:
        return JSONResponse({'detail': 'File not found'}, status_code=404)
    return FileResponse(DIST / 'index.html')
