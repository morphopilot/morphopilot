"""Curated, read-only views over saved experiment ROIs and their model.

The server-owned manifest contains IDs, never client-selected filesystem paths.
A collection references the canonical annotations rather than adding a second
training copy. Browsing a collection never changes the active model.
"""
import base64
import json
import math
import re
from pathlib import Path

import numpy as np
from fastapi import HTTPException

_ID = re.compile(r'^[A-Za-z0-9_-]{1,160}$')
_HISTORY_KEYS = {
    'experiment_id', 'round', 'input_model_id', 'output_model_id', 'training_steps',
    'cumulative_confirmed_count', 'positive_clicks', 'negative_clicks',
    'loss_first5', 'loss_last5', 'training_seconds', 'training_seed',
    'slice_indices', 'source_revision', 'imported_revision', 'source_snapshot_sha256',
}


def _id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def _history(value):
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if key in _HISTORY_KEYS}


class DemoStore:
    def __init__(self, root, category, sample_dir, model_info, image_for):
        self.root = Path(root)
        self.category = category
        self.sample_dir = sample_dir
        self.model_info = model_info
        self.image_for = image_for

    def _manifest(self):
        try:
            value = _read(self.root / 'demos.json', {'version': 1, 'category_id': self.category, 'demos': []})
            if not isinstance(value, dict) or value.get('version') != 1:
                raise ValueError('Unsupported demo manifest version.')
            if value.get('category_id') != self.category:
                raise ValueError('The demo manifest belongs to a different category.')
            rows = value.get('demos')
            if not isinstance(rows, list) or len(rows) > 20:
                raise ValueError('Invalid demo collection list.')
            ids = [row.get('id') for row in rows if isinstance(row, dict)]
            if len(ids) != len(rows) or not all(_id(item) for item in ids) or len(set(ids)) != len(ids):
                raise ValueError('Invalid or duplicate demo collection IDs.')
            return rows
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(503, f'The demo collection configuration needs attention: {error}') from error

    def _resolve(self, entry):
        ids = entry.get('sample_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(_id(sid) for sid in ids) or len(set(ids)) != len(ids):
            raise HTTPException(503, 'This demo collection has invalid sample IDs.')
        if not _id(entry.get('model_id')):
            raise HTTPException(503, 'This demo collection has an invalid model ID.')
        model = self.model_info(entry['model_id'])
        preferred = entry.get('preferred_z_by_sample', {})
        history_overrides = entry.get('round_history_by_sample', {})
        if not isinstance(preferred, dict) or not isinstance(history_overrides, dict):
            raise HTTPException(503, 'This demo collection has invalid sample metadata.')
        samples = []
        for index, sid in enumerate(ids):
            folder = self.sample_dir(sid)
            metadata = _read(folder / 'meta.json')
            shape = metadata.get('shape')
            if not isinstance(shape, list) or len(shape) != 3 or not all(type(n) is int and n > 0 for n in shape):
                raise HTTPException(503, 'A demo sample has invalid image dimensions.')
            history = _history(metadata.get('round_history') or history_overrides.get(sid))
            slices = (history or {}).get('slice_indices') or []
            suggested = preferred.get(sid, slices[0] if slices else shape[0] // 2)
            if type(suggested) is not int or not 0 <= suggested < shape[0]:
                raise HTTPException(503, 'A demo sample has an invalid preferred Z slice.')
            prompts = self._prompts(folder)
            split = metadata.get('split', 'annotation')
            mask_model_id = metadata.get('last_model')
            mask_model_label = 'Saved mask · model not recorded'
            mask_model_available = False
            if _id(mask_model_id):
                try:
                    mask_model_label = self.model_info(mask_model_id)['label']
                    mask_model_available = True
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                    mask_model_label = 'Saved model · unavailable'
            else:
                mask_model_id = None
                if any(event.get('tool') in ('brush', 'erase') for event in _read(folder / 'events.json', [])):
                    mask_model_label = 'Manual annotation'
            recorded_mask = bool(history and metadata.get('confirmed') and
                                 metadata.get('revision') == history.get('imported_revision') and
                                 mask_model_id is not None and mask_model_id == history.get('input_model_id'))
            if recorded_mask and not mask_model_available and type(history.get('round')) is int and history['round'] >= 1:
                mask_model_label = f"Recorded incoming model M{history['round'] - 1} (history)"
            sample = {'id': sid, 'title': metadata.get('title', sid), 'shape': shape,
                      'split': split, 'mask_model_id': mask_model_id, 'mask_model_label': mask_model_label,
                      'recorded_mask': recorded_mask,
                      'confirmed': bool(metadata.get('confirmed')), 'events': metadata.get('events', 0),
                      'revision': metadata.get('revision', 0), 'round': metadata.get('experiment_round') or (history or {}).get('round') or (index + 1 if split == 'annotation' else None),
                      'preferred_z': suggested, 'prompt_count': len(prompts),
                      'positive_clicks': sum(p['tool'] == 'point' and p['include'] for p in prompts),
                      'negative_clicks': sum(p['tool'] == 'point' and not p['include'] for p in prompts)}
            if history:
                sample['round_history'] = history
            samples.append(sample)
        cover_id = entry.get('preview_sample_id', ids[0])
        cover = next((sample for sample in samples if sample['id'] == cover_id), None)
        if not cover:
            raise HTTPException(503, 'The demo preview sample is not in this collection.')
        return {'id': entry['id'], 'category_id': self.category,
                'title': str(entry.get('title', entry['id']))[:200],
                'description': str(entry.get('description', ''))[:2000],
                'source_label': str(entry.get('source_label', 'Saved experiment ROIs'))[:300],
                'training_rounds': entry.get('training_rounds', len(samples)),
                'model': {key: model.get(key) for key in ('id', 'label', 'phase', 'auto_ready', 'confirmed_count')},
                'samples': samples, 'sample_ids': ids, 'preview_sample_id': cover_id,
                'preview_z': cover['preferred_z']}

    @staticmethod
    def _prompts(folder):
        # Explicit fields ensure sample paths, snapshots and other internal event
        # details cannot be exposed through the curated preview response.
        return [{'include': bool(event.get('include', True)), **{key: event[key] for key in ('tool', 'points', 'radius') if key in event}}
                for event in _read(folder / 'events.json', [])
                if event.get('tool') in ('point', 'bbox', 'scribble', 'lasso')]

    def list(self):
        return [self._resolve(entry) for entry in self._manifest()]

    def get(self, demo_id):
        if not _id(demo_id):
            raise HTTPException(400, 'Invalid demo ID.')
        entry = next((item for item in self._manifest() if item['id'] == demo_id), None)
        if entry is None:
            raise HTTPException(404, 'Demo collection not found in this category.')
        return self._resolve(entry)

    def selection(self, demo_id, sample_id=None):
        demo = self.get(demo_id)
        selected = sample_id or demo['preview_sample_id']
        sample = next((item for item in demo['samples'] if item['id'] == selected), None)
        if sample is None:
            raise HTTPException(404, 'This sample is not part of the selected demo collection.')
        return demo, sample

    def slice(self, demo_id, sample_id=None, z=None, max_size=1024):
        if max_size < 64 or max_size > 2048:
            raise HTTPException(422, 'Preview size must be between 64 and 2048.')
        demo, sample = self.selection(demo_id, sample_id)
        z = sample['preferred_z'] if z is None else z
        if z < 0 or z >= sample['shape'][0]:
            raise HTTPException(422, 'Z is outside this demo sample.')
        folder = self.sample_dir(sample['id'])
        raw = self.image_for(folder)
        mask = np.load(folder / 'mask.npy', allow_pickle=False)
        if list(raw.shape) != sample['shape'] or mask.shape != raw.shape:
            raise HTTPException(409, 'The saved demo image and mask dimensions do not match.')
        stride = max(1, math.ceil(max(raw.shape[1:]) / max_size))
        low, high = np.percentile(raw, [1, 99.6])
        image = np.clip((raw[z, ::stride, ::stride] - low) / max(high - low, 1e-8) * 255, 0, 255).astype(np.uint8)
        plane_mask = np.ascontiguousarray(mask[z, ::stride, ::stride], dtype=np.uint8)
        return {'category_id': self.category, 'demo_id': demo_id, 'sample': sample,
                'image': base64.b64encode(image.tobytes()).decode(),
                'mask': base64.b64encode(plane_mask.tobytes()).decode(),
                'shape': list(image.shape), 'source_shape': list(raw.shape),
                'stride': stride, 'z': z, 'tiles': [], 'partial': not sample['confirmed'],
                'revision': sample['revision'], 'prompts': self._prompts(folder)}
