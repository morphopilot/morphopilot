#!/usr/bin/env python3
"""Exercise the real storage API in a disposable runtime, without model weights."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

import numpy as np
import tifffile
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix='morphopilot-cpu-smoke-') as directory:
        runtime = Path(directory) / 'runtime'
        token = secrets.token_urlsafe(48)
        os.environ.update(MORPHOPILOT_DATA_DIR=str(runtime), MORPHOPILOT_API_TOKEN=token,
                          MORPHOPILOT_CATEGORY='neurite', MORPHOPILOT_DATASET='uploads',
                          MORPHOPILOT_GPU_LOCK_PATH=str(Path(directory) / 'gpu.lock'))
        for key in ['MORPHOPILOT_CATEGORY_REGISTRY', 'MORPHOPILOT_CATEGORY_RUNTIME']:
            os.environ.pop(key, None)
        sys.path.insert(0, str(APP / 'backend'))
        spec = importlib.util.spec_from_file_location('morphopilot_smoke_api', APP / 'backend/app.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = TestClient(module.app, headers={'Authorization': 'Bearer ' + token})
        try:
            def call(method, path, **kwargs):
                response = getattr(client, method)(path, **kwargs)
                assert response.status_code == 200, (path, response.status_code, response.text)
                return response

            def wait_volume(vid, operation):
                for _ in range(500):
                    detail = call('get', '/volumes/' + vid).json()
                    value = detail['volume']
                    status = value['status'] if operation == 'import' else value['merge_status']
                    if status not in {'importing', 'merging'}:
                        assert status == 'ready', value
                        return detail
                    time.sleep(.01)
                raise AssertionError('Timed out waiting for ' + operation)

            state = call('get', '/state').json()
            assert state['samples'] == [] and state['active_model'] == 'official'
            assert client.get('/state', headers={'Authorization': ''}).status_code == 401
            image = np.arange(7 * 21 * 23, dtype=np.uint16).reshape(7, 21, 23)
            payload = io.BytesIO()
            np.save(payload, image, allow_pickle=False)
            raw = payload.getvalue()
            upload = call('post', '/volumes/uploads', json={
                'name': 'SYNTHETIC_smoke.npy', 'size': len(raw), 'tile_shape': [4, 12, 12]}).json()['upload']
            uid = upload['id']
            call('post', '/volumes/uploads/' + uid + '/chunk?offset=0', content=raw)
            # A retry after a lost reply is idempotent, not a duplicate upload.
            call('post', '/volumes/uploads/' + uid + '/chunk?offset=0', content=raw)
            vid = call('post', '/volumes/uploads/' + uid + '/complete').json()['volume']['id']
            detail = wait_volume(vid, 'import')
            assert len(detail['tiles']) == 8
            expected = np.zeros(image.shape, np.uint8)
            for tile in detail['tiles']:
                sid = tile['id']
                center = [size // 2 for size in tile['shape']]
                call('post', '/samples/' + sid + '/interaction', json={
                    'tool': 'brush', 'include': True, 'points': [center], 'radius': 1, 'revision': 0})
                result = call('get', '/samples/' + sid + '/volume').json()
                mask = np.frombuffer(base64.b64decode(result['mask']), np.uint8).reshape(result['shape'])
                bounds = tuple(slice(a, a + size) for a, size in zip(tile['origin_zyx'], tile['shape']))
                expected[bounds] = mask
                call('post', '/samples/' + sid + '/confirm', json={'revision': 1, 'reviewed': True})
            assert call('get', '/state').json()['trainable_count'] == 8
            call('post', '/volumes/' + vid + '/merge', json={})
            detail = wait_volume(vid, 'merge')
            merged = tifffile.imread(io.BytesIO(call('get', '/volumes/' + vid + '/export').content))
            np.testing.assert_array_equal(merged, expected)
            np.testing.assert_array_equal(np.load(runtime / 'volumes' / vid / 'raw.npy'), image)
            preview = call('get', '/volumes/' + vid + '/slice?z=6').json()
            assert not preview['partial']
            record = call('post', '/volumes/' + vid + '/trash', json={
                'name': detail['volume']['name'], 'revision': detail['volume']['revision']}).json()
            assert call('get', '/state').json()['samples'] == []
            call('post', '/volumes/trash/' + record['trash_id'] + '/restore', json={})
            restored = tifffile.imread(io.BytesIO(call('get', '/volumes/' + vid + '/export').content))
            np.testing.assert_array_equal(restored, expected)
            print(json.dumps({'status': 'passed', 'tiles': 8, 'mode': 'CPU manual annotation',
                              'checks': ['empty startup', 'authentication', 'resumable upload',
                                         'odd-edge tiling', 'manual annotation', 'confirmation',
                                         'exact merged TIFF', 'global preview', 'trash and restore'],
                              'model_weights_used': False, 'persistent_user_data_modified': False}, indent=2))
        finally:
            client.close()
            module.VOLUMES.pool.shutdown(wait=True)
            module.POOL.shutdown(wait=True)


if __name__ == '__main__':
    run()
