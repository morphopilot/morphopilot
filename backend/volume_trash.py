"""Recoverable collection deletion with a durable, replayable filesystem journal.

Training snapshots and models deliberately stay in place: existing checkpoints
keep their provenance, while removing sample folders excludes future training.
A trash operation uses renames on the runtime filesystem, never recursive erase.
"""
import json
import os
import time
import uuid

from fastapi import HTTPException


class VolumeTrashMixin:
    def _trash_public(self, record):
        volume = record['volume']
        return {'category_id': self.category, 'volume_id': volume['id'],
                'trash_id': record['id'], 'name': volume['name'],
                'total_tiles': volume['total_tiles'],
                'confirmed_tiles': volume.get('confirmed_tiles', 0),
                'status': record['status'], 'deleted_at': record['deleted_at'],
                'preserved_training_snapshots': True, 'retains_disk_space': True}

    def _trash_record(self, tid):
        # _get performs ID and category validation before reading the journal.
        return self._get('volume_trash', tid)

    def list_trash(self):
        with self.lock:
            return sorted([self._trash_public(r) for r in self._all('volume_trash')
                           if r['status'] == 'trashed'],
                          key=lambda r: r['deleted_at'], reverse=True)

    def _journal_put(self, record):
        self._put('volume_trash', record)
        # Human-readable recovery copy; the SQLite row is authoritative. No
        # absolute paths are stored, so moving a deployment remains supported.
        folder = self.trash / record['id']
        folder.mkdir(exist_ok=True)
        from volume_store import atomic_json
        atomic_json(record, folder / 'manifest.json')

    def _owned_path(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise HTTPException(409, 'Volume storage contains an unsafe path. No files were deleted.')
        return path

    def _move_owned(self, source, destination):
        source, destination = self._owned_path(source), self._owned_path(destination)
        if source.exists() and destination.exists():
            raise HTTPException(409, 'Storage collision. Existing files were not overwritten.')
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            # Persist directory entries as well as the SQLite intent before
            # reporting success, including a machine power loss after rename.
            for parent in {source.parent, destination.parent}:
                descriptor = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        elif not destination.exists():
            raise HTTPException(409, 'A volume file is missing. Restore from a backup before continuing.')
        # If only the destination exists, a prior attempt already moved it.

    def _finish_trash(self, record):
        for move in record['moves']:
            self._move_owned(move['live'], move['trash'])
        record['status'] = 'trashed'
        with self.connection() as db:
            db.execute('DELETE FROM tiles WHERE volume_id=?', (record['volume']['id'],))
            db.execute('DELETE FROM volumes WHERE id=?', (record['volume']['id'],))
            for upload in record['uploads']:
                db.execute('DELETE FROM uploads WHERE id=?', (upload['id'],))
            db.execute('UPDATE volume_trash SET data=? WHERE id=?', (json.dumps(record), record['id']))
        self._journal_put(record)

    def _finish_restore(self, record):
        for move in record['moves']:
            self._move_owned(move['trash'], move['live'])
        volume = record['volume']
        record['status'] = 'restored'
        record['restored_at'] = time.time()
        with self.connection() as db:
            # Restores reserve the volume row before moving files. Replace only
            # that exact reservation, never an unrelated live volume.
            row = db.execute('SELECT data FROM volumes WHERE id=?', (volume['id'],)).fetchone()
            if not row or json.loads(row['data']).get('restoring_trash_id') != record['id']:
                raise HTTPException(409, 'The restore reservation changed. Existing data was not overwritten.')
            db.execute('UPDATE volumes SET data=? WHERE id=?', (json.dumps(volume), volume['id']))
            db.executemany('INSERT INTO tiles(id,volume_id,ordinal,revision,confirmed) VALUES (?,?,?,?,?)',
                           [(t['id'], t['volume_id'], t['ordinal'], t['revision'], t['confirmed']) for t in record['tiles']])
            for upload in record['uploads']:
                db.execute('INSERT INTO uploads(id,data) VALUES (?,?)', (upload['id'], json.dumps(upload)))
            db.execute('UPDATE volume_trash SET data=? WHERE id=?', (json.dumps(record), record['id']))
        self._journal_put(record)

    def _recover_trash(self):
        # Finish durable intent before normal import recovery or exposing APIs.
        for record in self._all('volume_trash'):
            if record['status'] == 'delete_pending':
                self._finish_trash(record)
            elif record['status'] == 'restore_pending':
                self._finish_restore(record)

    def trash_volume(self, vid, name, revision):
        with self.lock:
            detail = self.detail(vid)
            volume = detail['volume']
            if volume['status'] == 'importing' or volume.get('merge_status') == 'merging':
                raise HTTPException(409, 'Wait for this volume import or merge to finish before deleting it.')
            if volume['status'] not in {'ready', 'failed'}:
                raise HTTPException(409, 'This volume is already being changed. Reload the library.')
            if name != volume['name']:
                raise HTTPException(409, 'The volume name does not match. Reload it before deleting.')
            if revision != volume['revision']:
                raise HTTPException(409, 'This volume has changed. Reload it before deleting.')
            with self.connection() as db:
                tiles = [dict(r) for r in db.execute('SELECT * FROM tiles WHERE volume_id=? ORDER BY ordinal', (vid,))]
            uploads = [u for u in self._all('uploads') if u.get('volume_id') == vid]
            tid = 'trash_' + uuid.uuid4().hex[:20]
            owned = ['volumes/' + vid]
            for tile in tiles:
                sid = tile['id']
                if not sid.startswith(vid + '_tile_') or '/' in sid or '\\' in sid:
                    raise HTTPException(409, 'The tile ownership record is invalid. No files were deleted.')
                folder = self._owned_path('samples/' + sid)
                meta = json.loads((folder / 'meta.json').read_text())
                self._category_record(meta)
                if meta.get('volume_id') != vid or meta.get('id') != sid:
                    raise HTTPException(409, 'The tile belongs to another volume. No files were deleted.')
                owned.append('samples/' + sid)
            for upload in uploads:
                relative = 'uploads/' + upload['id'] + '.part'
                if self._owned_path(relative).exists():
                    owned.append(relative)
            for relative in owned:
                if not self._owned_path(relative).exists():
                    raise HTTPException(409, 'A volume file is missing. No files were deleted.')
            record = {'id': tid, 'category_id': self.category, 'status': 'delete_pending',
                      'deleted_at': time.time(), 'volume': volume, 'tiles': tiles,
                      'uploads': uploads,
                      'moves': [{'live': p, 'trash': f'trash/{tid}/{p}'} for p in owned]}
            # Commit the journal and hidden state together before any move.
            with self.connection() as db:
                db.execute('INSERT INTO volume_trash(id,data) VALUES (?,?)', (tid, json.dumps(record)))
                db.execute('UPDATE volumes SET data=? WHERE id=?',
                           (json.dumps({**volume, 'status': 'deleting'}), vid))
            self._journal_put(record)
            try:
                self._finish_trash(record)
            except OSError as exc:
                raise HTTPException(503, 'Deletion was interrupted. Data remains recoverable; restart the service to finish safely.') from exc
            return self._trash_public(record)

    def restore_volume(self, tid):
        with self.lock:
            record = self._trash_record(tid)
            if record['status'] == 'restored':
                return {'category_id': self.category, 'restored': True,
                        'volume': self.detail(record['volume']['id'])['volume']}
            if record['status'] != 'trashed':
                raise HTTPException(409, 'This volume is still being changed. Restart the service to complete recovery.')
            vid = record['volume']['id']
            with self.connection() as db:
                if db.execute('SELECT 1 FROM volumes WHERE id=?', (vid,)).fetchone():
                    raise HTTPException(409, 'A volume with this ID already exists. Nothing was overwritten.')
                for tile in record['tiles']:
                    if db.execute('SELECT 1 FROM tiles WHERE id=?', (tile['id'],)).fetchone():
                        raise HTTPException(409, 'A tile with this ID already exists. Nothing was overwritten.')
                for upload in record['uploads']:
                    if db.execute('SELECT 1 FROM uploads WHERE id=?', (upload['id'],)).fetchone():
                        raise HTTPException(409, 'An upload with this ID already exists. Nothing was overwritten.')
            for move in record['moves']:
                if self._owned_path(move['live']).exists():
                    raise HTTPException(409, 'Existing volume files would be overwritten. Restore was cancelled.')
                if not self._owned_path(move['trash']).exists():
                    raise HTTPException(409, 'A file is missing from Trash. Restore was cancelled.')
            record['status'] = 'restore_pending'
            with self.connection() as db:
                db.execute('INSERT INTO volumes(id,data) VALUES (?,?)',
                           (vid, json.dumps({**record['volume'], 'status': 'restoring', 'restoring_trash_id': tid})))
                db.execute('UPDATE volume_trash SET data=? WHERE id=?', (json.dumps(record), tid))
            self._journal_put(record)
            try:
                self._finish_restore(record)
            except OSError as exc:
                raise HTTPException(503, 'Restore was interrupted. Restart the service to finish safely.') from exc
            return {'category_id': self.category, 'restored': True, 'volume': record['volume']}
