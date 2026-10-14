"""Disk-backed large-volume imports, tile index and exact-grid mask reconstruction.

ROI JSON / mask files remain canonical for edits. SQLite indexes collections and
uploads; every collection read reconciles tile revisions before exposing merges.
No GPU, reference labels, external paths, or pickle deserialization are used here.
"""
import base64
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import numpy as np
import tifffile
from fastapi import HTTPException
from volume_trash import VolumeTrashMixin

CHUNK_BYTES = 8 * 1024 * 1024
COPY_BYTES = 16 * 1024 * 1024


def atomic_json(value, path):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, indent=2))
    os.replace(tmp, path)


def load_json(path):
    return json.loads(Path(path).read_text())


def checked_id(value):
    if not value or not value.replace('_', '').isalnum():
        raise HTTPException(400, 'Invalid collection or upload ID.')
    return value


def inference_eligible(raw):
    """Mirror nonzero-bbox normalization requirements without importing torch."""
    raw = np.asarray(raw, dtype=np.float32)
    if raw.size < 2 or not np.isfinite(raw).all():
        return False
    nz = np.nonzero(raw)
    if not len(nz[0]):
        return False
    region = raw[tuple(slice(int(c.min()), int(c.max()) + 1) for c in nz)]
    return bool(region.size >= 2 and float(region.std(dtype=np.float64)) > 1e-8)


class VolumeStore(VolumeTrashMixin):
    def __init__(self, root, mutation_lock=None, category=None):
        self.root = Path(root)
        self.category = category
        self.collections = self.root / 'volumes'
        self.uploads = self.root / 'uploads'
        self.trash = self.root / 'trash'
        self.trash.mkdir(exist_ok=True)
        self.collections.mkdir(exist_ok=True)
        self.uploads.mkdir(exist_ok=True)
        self.db_path = self.root / 'volumes.sqlite3'
        self.lock = mutation_lock or threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='volume-io')
        self.max_upload = int(os.environ.get('MORPHOPILOT_MAX_UPLOAD_BYTES', 20 * 1024**3))
        self.max_decoded = int(os.environ.get('MORPHOPILOT_MAX_DECODED_BYTES', 20 * 1024**3))
        self.max_tiles = int(os.environ.get('MORPHOPILOT_MAX_TILES', 20000))
        self.max_page = int(os.environ.get('MORPHOPILOT_MAX_TIFF_PAGE_BYTES', 512 * 1024**2))
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS volumes (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS volume_trash (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tiles (
                    id TEXT PRIMARY KEY, volume_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, revision INTEGER NOT NULL,
                    confirmed INTEGER NOT NULL,
                    FOREIGN KEY (volume_id) REFERENCES volumes(id)
                );
                CREATE INDEX IF NOT EXISTS tile_volume ON tiles(volume_id, ordinal);
            ''')
        self.recover()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, table, key):
        checked_id(key)
        with self.connection() as db:
            row = db.execute(f'SELECT data FROM {table} WHERE id=?', (key,)).fetchone()
        if not row:
            raise HTTPException(404, 'Upload not found.' if table == 'uploads' else 'Volume not found.')
        value = self._category_record(json.loads(row['data']))
        if table == 'volumes' and value.get('status') in {'deleting', 'restoring'}:
            raise HTTPException(404, 'Volume not found in the active library.')
        return value

    def _category_record(self, value):
        if self.category is not None:
            if value.get('category_id', self.category) != self.category:
                raise HTTPException(409, 'This record belongs to a different data category.')
            value['category_id'] = self.category
        return value

    def _put(self, table, value):
        self._category_record(value)
        with self.connection() as db:
            db.execute(f'INSERT INTO {table}(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (value['id'], json.dumps(value)))

    def _all(self, table):
        with self.connection() as db:
            return [self._category_record(json.loads(r['data'])) for r in db.execute(f'SELECT data FROM {table}')]

    def capabilities(self):
        return {'large_volume_upload': True, 'chunk_size': CHUNK_BYTES,
                'default_tile_shape': [25, 192, 192],
                'max_upload_bytes': self.max_upload, 'max_decoded_bytes': self.max_decoded,
                'supported_formats': ['tif', 'tiff', 'npy', 'npz'],
                'empty_background_confirmation': True, 'volume_trash': True}

    def recover(self):
        # Imports are transactional at collection level. A crash cannot expose a
        # half-imported collection or silently claim that a partial merge is final.
        with self.lock:
            self._recover_trash()
            for volume in self._all('volumes'):
                for temporary_merge in (self.collections / volume['id']).glob('merge_*'):
                    if temporary_merge.is_dir():
                        shutil.rmtree(temporary_merge, ignore_errors=True)
                if volume['status'] == 'importing':
                    volume.update(status='failed', error='Import interrupted by a service restart. Re-upload the source to try again.')
                    self._put('volumes', volume)
                    self._cleanup_incomplete(volume)
                if volume.get('merge_status') == 'merging':
                    volume.update(merge_status='failed', merge_error='Merge interrupted by a service restart. Run Merge again.')
                    self._put('volumes', volume)
            for upload in self._all('uploads'):
                path = self.uploads / (upload['id'] + '.part')
                if upload['status'] == 'uploading' and path.exists():
                    upload['offset'] = path.stat().st_size
                    self._put('uploads', upload)

    def create_upload(self, name, size, tile_shape, voxel_spacing=None):
        name = str(name).replace('\\', '/').split('/')[-1][:180]
        if Path(name).suffix.lower() not in {'.tif', '.tiff', '.npy', '.npz'}:
            raise HTTPException(422, 'Use a single-channel TIFF/BigTIFF, NPY, or NPZ containing image.')
        if size < 1 or size > self.max_upload:
            raise HTTPException(413, f'The upload limit is {self.max_upload} bytes.')
        if len(tile_shape) != 3 or any(type(n) is not int or n < 1 or n > cap for n, cap in zip(tile_shape, (96, 512, 512))):
            raise HTTPException(422, 'Tile shape must be Z,Y,X with positive dimensions up to 96,512,512.')
        if voxel_spacing is not None and (len(voxel_spacing) != 3 or not all(math.isfinite(x) and x > 0 for x in voxel_spacing)):
            raise HTTPException(422, 'Voxel spacing must contain three positive finite Z,Y,X values.')
        with self.lock:
            # Account for outstanding uploads, without reserving sparse files.
            pending = sum(max(0, u['size'] - u.get('offset', 0)) for u in self._all('uploads') if u['status'] == 'uploading')
            if shutil.disk_usage(self.root).free < pending + size + 256 * 1024**2:
                raise HTTPException(507, 'Not enough free disk space for this upload.')
            uid = 'up_' + uuid.uuid4().hex[:20]
            value = {'id': uid, 'name': name, 'size': size, 'offset': 0, 'status': 'uploading',
                     'tile_shape': list(tile_shape), 'voxel_spacing': voxel_spacing,
                     'chunk_size': CHUNK_BYTES, 'created_at': time.time(), 'volume_id': None}
            (self.uploads / (uid + '.part')).touch(exist_ok=False)
            self._put('uploads', value)
            return value

    def upload_status(self, uid):
        with self.lock:
            upload = self._get('uploads', uid)
            if upload.get('volume_id'):
                volume = self._get('volumes', upload['volume_id'])
                upload['volume_status'] = volume['status']
                if volume.get('error'):
                    upload['error'] = volume['error']
            return upload

    def append_chunk(self, uid, offset, body):
        if not body or len(body) > CHUNK_BYTES:
            raise HTTPException(413, f'Each chunk must contain 1 to {CHUNK_BYTES} bytes.')
        with self.lock:
            upload = self._get('uploads', uid)
            if upload['status'] != 'uploading':
                raise HTTPException(409, 'This upload is already being imported.')
            if offset < 0 or offset + len(body) > upload['size']:
                raise HTTPException(422, 'Chunk is outside the declared upload size.')
            path = self.uploads / (uid + '.part')
            actual = path.stat().st_size
            if actual != upload['offset']:
                upload['offset'] = actual
                self._put('uploads', upload)
            if offset < actual:
                with path.open('rb') as f:
                    f.seek(offset)
                    same = offset + len(body) <= actual and f.read(len(body)) == body
                if not same:
                    raise HTTPException(409, f'Conflicting chunk. Resume at offset {actual}.')
                return upload
            if offset != actual:
                raise HTTPException(409, f'Unexpected chunk offset. Resume at offset {actual}.')
            with path.open('ab') as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            upload['offset'] += len(body)
            self._put('uploads', upload)
            return upload

    def complete_upload(self, uid):
        with self.lock:
            upload = self._get('uploads', uid)
            if upload.get('volume_id'):
                return self._get('volumes', upload['volume_id'])
            if upload['offset'] != upload['size']:
                raise HTTPException(409, 'Upload is incomplete. Resume from the saved offset.')
            vid = 'vol_' + uuid.uuid4().hex[:16]
            volume = {'id': vid, 'name': upload['name'], 'status': 'importing',
                      'created_at': time.time(), 'upload_id': uid,
                      'tile_shape': upload['tile_shape'], 'voxel_spacing': upload['voxel_spacing'],
                      'shape': None, 'total_tiles': 0, 'confirmed_tiles': 0,
                      'revision': 0, 'merge_status': 'missing', 'merge_revision': None,
                      'progress': 0, 'message': 'Inspecting the uploaded volume.'}
            (self.collections / vid).mkdir(exist_ok=False)
            upload.update(status='importing', volume_id=vid)
            # One SQLite transaction links the immutable completed upload to one
            # volume. Retrying complete after a lost response returns this volume.
            with self.connection() as db:
                db.execute('INSERT INTO volumes(id,data) VALUES (?,?)', (vid, json.dumps(volume)))
                db.execute('UPDATE uploads SET data=? WHERE id=?', (json.dumps(upload), uid))
            self.pool.submit(self._import, vid)
            return volume

    def _validate_array(self, shape, dtype, volume):
        dtype = np.dtype(dtype)
        if len(shape) != 3 or any(n < 1 for n in shape):
            raise ValueError('Use a single-channel 3D Z,Y,X array. Color, channel and time axes are not supported.')
        if dtype.kind not in 'uibf' or dtype.hasobject:
            raise ValueError('The volume must contain real numeric or boolean pixels; object/complex arrays are unsupported.')
        size = math.prod(int(n) for n in shape) * dtype.itemsize
        if size > self.max_decoded:
            raise ValueError(f'The decoded image exceeds the {self.max_decoded}-byte limit.')
        count = math.prod(math.ceil(n / t) for n, t in zip(shape, volume['tile_shape']))
        if count > self.max_tiles:
            raise ValueError(f'This image creates {count} tiles, above the configured {self.max_tiles} limit. Use a larger tile shape.')
        # raw array + empty masks + temporary decode overhead. Snapshots are made
        # only when tiles are reviewed, not eagerly duplicated during import.
        needed = size * 2 + math.prod(shape) + 256 * 1024**2
        if shutil.disk_usage(self.root).free < needed:
            raise ValueError('Not enough disk space for the decoded image and tile masks.')
        return tuple(map(int, shape)), dtype

    def _npy_header(self, path):
        with Path(path).open('rb') as f:
            version = np.lib.format.read_magic(f)
            if version == (1, 0):
                shape, _, dtype = np.lib.format.read_array_header_1_0(f)
            elif version in [(2, 0), (3, 0)]:
                shape, _, dtype = np.lib.format.read_array_header_2_0(f)
            else:
                raise ValueError('Unsupported NPY version.')
        return shape, dtype

    def _decode(self, source, volume, folder):
        ext = Path(volume['name']).suffix.lower()
        extracted = None
        if ext == '.npz':
            with zipfile.ZipFile(source) as archive:
                names = archive.namelist()
                if names.count('image.npy') != 1:
                    raise ValueError('NPZ must contain exactly one image array named image.npy.')
                entry = archive.getinfo('image.npy')
                if entry.file_size > self.max_decoded + 65536:
                    raise ValueError('The NPZ image exceeds the decoded size limit.')
                extracted = folder / 'extracted.npy'
                # Zip member path is never used as a filesystem path.
                with archive.open(entry) as src, extracted.open('wb') as dst:
                    copied = 0
                    while True:
                        block = src.read(COPY_BYTES)
                        if not block:
                            break
                        copied += len(block)
                        if copied > self.max_decoded + 65536:
                            raise ValueError('The decompressed NPZ image exceeds the decoded size limit.')
                        dst.write(block)
            source = extracted
            ext = '.npy'
        dest = folder / 'raw.npy'
        if ext == '.npy':
            shape, dtype = self._npy_header(source)
            shape, dtype = self._validate_array(shape, dtype, volume)
            incoming = np.load(source, mmap_mode='r', allow_pickle=False)
            if incoming.shape != shape or incoming.dtype != dtype:
                raise ValueError('Array metadata changed while reading the upload.')
            output = np.lib.format.open_memmap(dest, mode='w+', dtype=dtype, shape=shape)
            # nditer buffers also bound memory for Fortran-order arrays.
            with np.nditer([incoming, output], flags=['external_loop', 'buffered'],
                           op_flags=[['readonly'], ['writeonly']], order='C',
                           buffersize=max(1, COPY_BYTES // dtype.itemsize)) as it:
                for src, dst in it:
                    if not np.isfinite(src).all() or (dtype.kind == 'f' and np.max(np.abs(src), initial=0) > np.finfo(np.float32).max):
                        raise ValueError('The image contains NaN, infinity, or values outside the float32 model range.')
                    dst[...] = src
            output.flush()
            del output, incoming
        else:
            with tifffile.TiffFile(source) as tf:
                if len(tf.series) != 1:
                    raise ValueError('Upload one single-channel TIFF series. Multi-series images are not supported.')
                series = tf.series[0]
                if len(series.axes) != 3 or series.axes[-2:] != 'YX' or series.axes[0] not in 'ZQI':
                    raise ValueError('Use a single-channel ZYX TIFF stack. Color, channel and time axes are not supported.')
                shape, dtype = self._validate_array(series.shape, series.dtype, volume)
                if any(math.prod(page.shape) * page.dtype.itemsize > self.max_page for page in series.pages):
                    raise ValueError('A TIFF page exceeds the configured decode-buffer limit. Use tiled TIFF or NPY instead.')
                output = np.lib.format.open_memmap(dest, mode='w+', dtype=dtype, shape=shape)
                series.asarray(out=output, maxworkers=1, buffersize=COPY_BYTES)
                flat = output.reshape(-1)
                step = max(1, COPY_BYTES // dtype.itemsize)
                for start in range(0, flat.size, step):
                    block = flat[start:start + step]
                    if not np.isfinite(block).all() or (dtype.kind == 'f' and np.max(np.abs(block), initial=0) > np.finfo(np.float32).max):
                        raise ValueError('The image contains NaN, infinity, or values outside the float32 model range.')
                output.flush()
                del flat, output
        if extracted:
            extracted.unlink(missing_ok=True)
        return shape, dtype

    def _cleanup_incomplete(self, volume):
        folder = self.collections / volume['id']
        # IDs have a collection-specific prefix. Only new, incomplete import
        # files are eligible; legacy/user-created ROIs cannot match this prefix.
        for p in (self.root / 'samples').glob(volume['id'] + '_tile_*'):
            shutil.rmtree(p, ignore_errors=True)
        with self.connection() as db:
            db.execute('DELETE FROM tiles WHERE volume_id=?', (volume['id'],))
        for path in [folder / 'staging', folder / 'raw.npy', folder / 'extracted.npy']:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        # Input survives a failed import for diagnosis; no existing annotations
        # or models are part of this cleanup.

    def _import(self, vid):
        try:
            volume = self._get('volumes', vid)
            folder = self.collections / vid
            source = self.uploads / (volume['upload_id'] + '.part')
            source_hash = hashlib.sha256()
            with source.open('rb') as stream:
                for block in iter(lambda: stream.read(COPY_BYTES), b''):
                    source_hash.update(block)
            volume['source_sha256'] = source_hash.hexdigest()
            shape, dtype = self._decode(source, volume, folder)
            grid = [list(range(0, n, t)) for n, t in zip(shape, volume['tile_shape'])]
            total = math.prod(len(g) for g in grid)
            volume.update(shape=list(shape), dtype=str(dtype), total_tiles=total, message='Creating annotation tiles.')
            self._put('volumes', volume)
            staging = folder / 'staging'
            staging.mkdir()
            raw = np.load(folder / 'raw.npy', mmap_mode='r', allow_pickle=False)
            rows = []
            # Deterministic bounded intensity sample for consistent full-volume display.
            display_step = max(1, math.ceil(raw.size / 1_000_000))
            sampled = np.asarray(raw.reshape(-1)[::display_step], dtype=np.float64)
            lo, hi = np.percentile(sampled, [1, 99.6])
            del sampled
            for i, start in enumerate(itertools.product(*grid)):
                stop = [min(n, a + t) for n, a, t in zip(shape, start, volume['tile_shape'])]
                sl = tuple(slice(a, b) for a, b in zip(start, stop))
                tile = raw[sl]
                sid = f'{vid}_tile_{i + 1:05d}'
                dest = staging / sid
                dest.mkdir()
                eligible = inference_eligible(tile)
                meta = {'id': sid, 'title': f'Tile {i + 1:03d} · Z{start[0]} Y{start[1]} X{start[2]}',
                        'shape': list(tile.shape), 'confirmed': False, 'split': 'annotation',
                        'revision': 0, 'events': 0, 'volume_id': vid, 'tile_index': i,
                        'source_group': vid, 'origin_zyx': list(start),
                        'bounds_zyx': [[a, b] for a, b in zip(start, stop)],
                        'inference_eligible': eligible, 'training_eligible': eligible,
                        'empty_background': False,
                        'normalization_note': None if eligible else 'Constant or insufficient nonzero data; review and confirm empty background, or paint manually. This tile is excluded from model training.'}
                np.save(dest / 'mask.npy', np.zeros(tile.shape, np.uint8), allow_pickle=False)
                atomic_json([], dest / 'events.json')
                atomic_json(self._category_record(meta), dest / 'meta.json')
                rows.append((sid, vid, i, 0, 0))
                if (i + 1) % 20 == 0 or i + 1 == total:
                    volume['progress'] = (i + 1) / total
                    self._put('volumes', volume)
            del raw
            with self.lock:
                # Move generated tile folders first; all_samples filters out an
                # importing collection, so they become visible only after commit.
                for dest in staging.iterdir():
                    os.replace(dest, self.root / 'samples' / dest.name)
                staging.rmdir()
                volume.update(status='ready', progress=1, imported_at=time.time(),
                              display_range=[float(lo), float(hi)],
                              message='Ready for tile annotation.')
                upload = self._get('uploads', volume['upload_id'])
                upload.update(status='complete')
                with self.connection() as db:
                    db.executemany('INSERT INTO tiles(id,volume_id,ordinal,revision,confirmed) VALUES (?,?,?,?,?)', rows)
                    db.execute('UPDATE volumes SET data=? WHERE id=?', (json.dumps(volume), vid))
                    db.execute('UPDATE uploads SET data=? WHERE id=?', (json.dumps(upload), upload['id']))
                atomic_json({'volume_id': vid, 'source_name': volume['name'], 'shape': shape,
                             'dtype': str(dtype), 'source_sha256': volume['source_sha256'], 'tile_shape': volume['tile_shape'],
                             'voxel_spacing': volume.get('voxel_spacing'),
                             'layout': 'non-overlapping ZYX cores; edge dimensions retained; binary semantic mask'},
                            folder / 'source.json')
                # raw.npy is the durable, exact decoded source. Avoid retaining a
                # second multi-GB uploaded copy after a committed successful import.
                source.unlink(missing_ok=True)
        except Exception as exc:
            with self.lock:
                volume = self._get('volumes', vid)
                volume.update(status='failed', error=str(exc), message='Import failed.')
                self._put('volumes', volume)
                upload = self._get('uploads', volume['upload_id'])
                upload.update(status='failed', error=str(exc))
                self._put('uploads', upload)
                self._cleanup_incomplete(volume)

    def image_for(self, meta):
        volume = self._get('volumes', meta['volume_id'])
        if volume['status'] != 'ready':
            raise HTTPException(409, 'This volume is not ready.')
        raw = np.load(self.collections / volume['id'] / 'raw.npy', mmap_mode='r', allow_pickle=False)
        sl = tuple(slice(a, a + n) for a, n in zip(meta['origin_zyx'], meta['shape']))
        return np.array(raw[sl], dtype=np.float32, copy=True)

    def _tiles(self, vid):
        with self.connection() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM tiles WHERE volume_id=? ORDER BY ordinal', (vid,))]
        return [self._category_record(load_json(self.root / 'samples' / sid / 'meta.json')) for sid in ids]

    def sync_sample(self, meta):
        vid = meta.get('volume_id')
        if not vid:
            return
        with self.lock:
            volume = self._get('volumes', vid)
            with self.connection() as db:
                row = db.execute('SELECT revision,confirmed FROM tiles WHERE id=?', (meta['id'],)).fetchone()
                if not row or (row['revision'], bool(row['confirmed'])) == (meta['revision'], bool(meta['confirmed'])):
                    return
                db.execute('UPDATE tiles SET revision=?,confirmed=? WHERE id=?', (meta['revision'], int(meta['confirmed']), meta['id']))
                volume['revision'] += 1
                volume['confirmed_tiles'] = db.execute('SELECT count(*) AS n FROM tiles WHERE volume_id=? AND confirmed=1', (vid,)).fetchone()['n']
                if volume.get('merge_status') == 'ready':
                    volume['merge_status'] = 'stale'
                db.execute('UPDATE volumes SET data=? WHERE id=?', (json.dumps(volume), vid))

    def detail(self, vid):
        with self.lock:
            volume = self._get('volumes', vid)
            tiles = self._tiles(vid) if volume['status'] == 'ready' else []
            for meta in tiles:
                self.sync_sample(meta)
            volume = self._get('volumes', vid)
            return {'volume': volume, 'tiles': tiles, 'category_id': self.category}

    def list(self):
        # Keep the ID snapshot and all reconciled metadata in one read window.
        # A concurrent trash operation must not remove a row between these reads.
        with self.lock:
            return sorted([self.detail(v['id'])['volume'] for v in self._all('volumes')
                           if v.get('status') not in {'deleting', 'restoring'}],
                          key=lambda v: v['created_at'], reverse=True)

    def slice(self, vid, z, max_size=1024):
        if max_size < 64 or max_size > 2048:
            raise HTTPException(422, 'Preview size must be between 64 and 2048.')
        with self.lock:
            detail = self.detail(vid)
            volume, tiles = detail['volume'], detail['tiles']
            if volume['status'] != 'ready':
                raise HTTPException(409, 'This volume is not ready.')
            shape = volume['shape']
            if z < 0 or z >= shape[0]:
                raise HTTPException(422, 'Z is outside this volume.')
            step = max(1, math.ceil(max(shape[1:]) / max_size))
            ys = np.arange(0, shape[1], step)
            xs = np.arange(0, shape[2], step)
            raw = np.load(self.collections / vid / 'raw.npy', mmap_mode='r', allow_pickle=False)
            plane = np.asarray(raw[z, ::step, ::step], dtype=np.float64)
            lo, hi = volume['display_range']
            image = np.clip((plane - lo) / max(hi - lo, 1e-8) * 255, 0, 255).astype(np.uint8)
            mask = np.zeros(image.shape, np.uint8)
            bounds = []
            for meta in tiles:
                (z0, z1), (y0, y1), (x0, x1) = meta['bounds_zyx']
                if not z0 <= z < z1:
                    continue
                iy = np.flatnonzero((ys >= y0) & (ys < y1))
                ix = np.flatnonzero((xs >= x0) & (xs < x1))
                if len(iy) and len(ix):
                    tile = np.load(self.root / 'samples' / meta['id'] / 'mask.npy', mmap_mode='r', allow_pickle=False)
                    mask[np.ix_(iy, ix)] = tile[z - z0][np.ix_(ys[iy] - y0, xs[ix] - x0)]
                bounds.append({'id': meta['id'], 'tile_index': meta['tile_index'], 'bounds_zyx': meta['bounds_zyx'],
                               'confirmed': meta['confirmed'], 'empty_background': meta.get('empty_background', False),
                               'events': meta.get('events', 0), 'revision': meta['revision']})
            return {'category_id': self.category, 'image': base64.b64encode(image.tobytes()).decode(),
                    'mask': base64.b64encode(mask.tobytes()).decode(), 'shape': list(image.shape),
                    'source_shape': shape, 'z': z, 'stride': step, 'tiles': bounds,
                    'partial': volume['confirmed_tiles'] != volume['total_tiles'],
                    'confirmed_tiles': volume['confirmed_tiles'], 'total_tiles': volume['total_tiles'],
                    'revision': volume['revision'], 'voxel_spacing': volume.get('voxel_spacing')}

    def merge(self, vid, draft=False):
        with self.lock:
            detail = self.detail(vid)
            volume = detail['volume']
            if volume['status'] != 'ready':
                raise HTTPException(409, 'This volume is not ready.')
            if not draft and volume['confirmed_tiles'] != volume['total_tiles']:
                raise HTTPException(409, 'Review and confirm every tile before creating a final merged mask. Use an explicit draft export for partial results.')
            if volume.get('merge_status') == 'merging':
                return volume
            if volume.get('merge_status') == 'ready' and volume.get('merge_revision') == volume['revision'] and volume.get('merge_draft') == draft:
                return volume
            volume.update(merge_status='merging', merge_error=None, merge_draft=draft)
            self._put('volumes', volume)
            # Immutable mask copies under the edit lock guarantee that all slices
            # in this export represent the same collection revision.
            folder = self.collections / vid
            staging = folder / ('merge_' + uuid.uuid4().hex[:12])
            staging.mkdir()
            try:
                for meta in detail['tiles']:
                    shutil.copyfile(self.root / 'samples' / meta['id'] / 'mask.npy', staging / (meta['id'] + '.npy'))
                self.pool.submit(self._merge, vid, volume['revision'], draft, detail['tiles'], staging)
            except Exception as exc:
                shutil.rmtree(staging, ignore_errors=True)
                volume.update(merge_status='failed', merge_error=str(exc))
                self._put('volumes', volume)
                raise HTTPException(507, 'Unable to create a consistent merge snapshot. Check free disk space and retry.')
            return volume

    def _merge(self, vid, revision, draft, tiles, staging):
        try:
            volume = self._get('volumes', vid)
            shape = tuple(volume['shape'])
            out = staging / 'merged.npy'
            merged = np.lib.format.open_memmap(out, mode='w+', dtype=np.uint8, shape=shape)
            for meta in tiles:
                sl = tuple(slice(a, a + n) for a, n in zip(meta['origin_zyx'], meta['shape']))
                tile = np.load(staging / (meta['id'] + '.npy'), mmap_mode='r', allow_pickle=False)
                merged[sl] = tile
            merged.flush()
            tif = staging / 'mask.tif'
            metadata = {'axes': 'ZYX', 'volume_id': vid, 'revision': revision,
                        'draft': draft, 'voxel_spacing_zyx': volume.get('voxel_spacing'),
                        'confirmed_tiles': sum(bool(m['confirmed']) for m in tiles), 'total_tiles': len(tiles)}
            # tifffile writes the memmap without materializing a full-volume copy.
            tifffile.imwrite(tif, merged, bigtiff=math.prod(shape) > 3_500_000_000,
                             photometric='minisblack', metadata=metadata)
            del merged
            with self.lock:
                current = self.detail(vid)['volume']
                final = self.collections / vid / ('draft_mask.tif' if draft else 'final_mask.tif')
                os.replace(tif, final)
                atomic_json({**metadata, 'shape': shape,
                             'tiles': [{k: m.get(k) for k in ['id', 'origin_zyx', 'shape', 'revision', 'confirmed', 'empty_background']} for m in tiles]},
                            final.with_suffix('.json'))
                current.update(merge_status='ready' if current['revision'] == revision else 'stale',
                               merge_revision=revision, merge_draft=draft, merge_error=None,
                               merged_at=time.time(), merge_filename=final.name)
                self._put('volumes', current)
        except Exception as exc:
            with self.lock:
                current = self._get('volumes', vid)
                current.update(merge_status='failed', merge_error=str(exc))
                self._put('volumes', current)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def export_path(self, vid, draft=False):
        with self.lock:
            volume = self.detail(vid)['volume']
            if volume.get('merge_status') != 'ready' or volume.get('merge_revision') != volume['revision'] or volume.get('merge_draft') != draft:
                raise HTTPException(409, 'Create a current merged mask first. A changed tile makes the previous export stale.')
            if not draft and volume['confirmed_tiles'] != volume['total_tiles']:
                raise HTTPException(409, 'The final export requires every tile to be reviewed.')
            return self.collections / vid / ('draft_mask.tif' if draft else 'final_mask.tif')
