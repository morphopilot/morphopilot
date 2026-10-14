"""Independent data categories and safe aliases for older workspace URLs."""
import os
from category_registry import BUILTIN_LABELS, entries, runtime_origins, public_record, supervisor_available

CATEGORY_LABELS = BUILTIN_LABELS


def category_labels():
    return {**CATEGORY_LABELS, **{record['id']: record['label'] for record in entries()}}


def category_id(dataset, configured=None):
    value = configured or ('spot' if dataset == 'legacy_spot' else 'neurite')
    if value not in category_labels():
        raise RuntimeError('MORPHOPILOT_CATEGORY must identify a registered data category.')
    return value


def category_origins():
    origins = {key: os.environ.get(f'MORPHOPILOT_{key.upper()}_ORIGIN') or
            (os.environ.get('MORPHOPILOT_LEGACY_SPOT_ORIGIN') if key == 'spot' else None)
            for key in CATEGORY_LABELS}
    # Managed installations route through one supervisor-owned runtime file;
    # immutable child environments cannot discover workers added later.
    if os.environ.get('MORPHOPILOT_CATEGORY_RUNTIME'):
        live = runtime_origins()
        # Existing systemd services are not owned by the optional external
        # manager. Its restart must not disconnect the four fixed services.
        if os.environ.get('MORPHOPILOT_CATEGORY_EXTERNAL_MANAGER') == '1' and not supervisor_available():
            live = {**origins, **live}
        return {key: live.get(key) for key in category_labels()}
    return origins


def category_list(current):
    origins = category_origins()
    result = [{'id': key, 'label': label, 'available': key == current or bool(origins.get(key)),
               'status': 'ready' if key == current or origins.get(key) else 'unavailable', 'builtin': True}
              for key, label in CATEGORY_LABELS.items()]
    for record in entries():
        available = record['status'] == 'ready' and bool(origins.get(record['id']))
        result.append({**public_record(record), 'available': available, 'builtin': False,
                       'status': record['status'] if available or record['status'] != 'ready' else 'unavailable'})
    return result


class CurrentCategoryAlias:
    """Route this category's canonical URL directly, including inside an ASGI mount.

    This avoids a loopback HTTP request and keeps uploads/exports streaming.
    Other category URLs are handled by the explicit upstream proxy route.
    """
    def __init__(self, app, category):
        self.app = app
        self.category = category

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            root = scope.get('root_path', '').rstrip('/')
            path = scope['path']
            local = path[len(root):] if root and path.startswith(root + '/') else path
            prefix = '/categories/' + self.category + '/'
            if local.startswith(prefix):
                scope = dict(scope)
                scope['path'] = root + '/' + local[len(prefix):]
                scope['raw_path'] = scope['path'].encode()
        await self.app(scope, receive, send)
