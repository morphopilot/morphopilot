"""Persistent names and ephemeral, supervisor-owned routes for local categories.

Only opaque IDs determine paths. Browser input can supply a display name, never a
directory, model, origin or worker command. Writers lock across worker processes.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit
import uuid

BUILTIN_LABELS = {'neurite': 'Neurite', 'spot': 'Spot', 'cell': 'Cell', 'organ': 'Organ'}
CUSTOM_ID = re.compile(r'^cat_[0-9a-f]{16}$')
MAX_CUSTOM = 12  # Each ready category owns a CPU worker; GPU allocation stays lazy.


class CategoryError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def registry_path():
    value = os.environ.get('MORPHOPILOT_CATEGORY_REGISTRY')
    return Path(value) if value else None


def runtime_path():
    value = os.environ.get('MORPHOPILOT_CATEGORY_RUNTIME')
    return Path(value) if value else None


def read_json(path, default):
    try:
        return json.loads(path.read_text()) if path else default
    except FileNotFoundError:
        return default


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def registry_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + '.lock').open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def normalize_label(value):
    if not isinstance(value, str):
        raise CategoryError('Enter a category name.', 422)
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise CategoryError('Category names cannot contain control or invisible characters.', 422)
    value = ' '.join(unicodedata.normalize('NFKC', value).split())
    if not 1 <= len(value) <= 48:
        raise CategoryError('Use a category name between 1 and 48 characters.', 422)
    return value


def entries(path=None):
    path = path or registry_path()
    document = read_json(path, {'version': 1, 'categories': []})
    if not isinstance(document, dict) or document.get('version') != 1 or not isinstance(document.get('categories'), list):
        raise RuntimeError('The category registry is invalid. Restore its last valid backup.')
    result = document['categories']
    seen_ids, seen_names = set(), {label.casefold() for label in BUILTIN_LABELS.values()}
    for record in result:
        if not isinstance(record, dict) or not CUSTOM_ID.fullmatch(str(record.get('id', ''))):
            raise RuntimeError('The category registry contains an invalid category ID.')
        label = normalize_label(record.get('label'))
        if record['id'] in seen_ids or label.casefold() in seen_names:
            raise RuntimeError('The category registry contains duplicate categories.')
        if record.get('status') not in {'starting', 'ready', 'failed'}:
            raise RuntimeError('The category registry contains an invalid status.')
        seen_ids.add(record['id'])
        seen_names.add(label.casefold())
    return result


def runtime_state(path=None):
    value = read_json(path or runtime_path(), {})
    return value if isinstance(value, dict) else {}


def supervisor_available():
    state = runtime_state()
    return bool(registry_path() and state.get('accepting') is True and
                0 <= time.time() - state.get('updated_at', 0) < 15)


def runtime_origins():
    state = runtime_state()
    if not state.get('accepting') or time.time() - state.get('updated_at', 0) >= 15:
        return {}
    origins = {}
    for key, value in state.get('origins', {}).items():
        if key not in BUILTIN_LABELS and not CUSTOM_ID.fullmatch(key):
            continue
        try:
            parsed = urlsplit(value)
            if (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port and
                    not parsed.username and not parsed.password and parsed.path in {'', '/'} and
                    not parsed.query and not parsed.fragment):
                origins[key] = value.rstrip('/')
        except (TypeError, ValueError):
            continue
    return origins


def create(label):
    path = registry_path()
    if not supervisor_available():
        raise CategoryError('Adding categories requires the local installation started with deploy/start.sh.', 503)
    label = normalize_label(label)
    with registry_lock(path):
        records = entries(path)
        if label.casefold() in {name.casefold() for name in BUILTIN_LABELS.values()} | {r['label'].casefold() for r in records}:
            raise CategoryError('A category with this name already exists. Choose that category or use another name.', 409)
        if len(records) >= MAX_CUSTOM:
            raise CategoryError(f'This installation supports {MAX_CUSTOM} custom categories in addition to the four built-in categories.', 409)
        record = {'id': 'cat_' + uuid.uuid4().hex[:16], 'label': label,
                  'status': 'starting', 'created_at': time.time()}
        atomic_json(path, {'version': 1, 'categories': [*records, record]})
    return record


def update(category, *, path=None, status, error=None):
    path = path or registry_path()
    with registry_lock(path):
        records = entries(path)
        record = next((record for record in records if record['id'] == category), None)
        if not record:
            raise CategoryError('Unknown data category.', 404)
        record.update(status=status, updated_at=time.time())
        record.pop('error', None)
        if error:
            record['error'] = error
        atomic_json(path, {'version': 1, 'categories': records})
    return record


def retry(category):
    if not supervisor_available():
        raise CategoryError('The local category supervisor is unavailable. Restart the local installation.', 503)
    path = registry_path()
    with registry_lock(path):
        records = entries(path)
        record = next((record for record in records if record['id'] == category), None)
        if not record:
            raise CategoryError('Unknown data category.', 404)
        if record['status'] != 'failed':
            raise CategoryError('Only a failed category can be restarted.', 409)
        record.update(status='starting', updated_at=time.time())
        record.pop('error', None)
        atomic_json(path, {'version': 1, 'categories': records})
    return record


def public_record(record):
    return {key: record[key] for key in ('id', 'label', 'status', 'error', 'created_at') if key in record}
