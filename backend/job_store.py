"""Process-safe job transitions shared by the API and training subprocess."""
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def transaction(path):
    path = Path(path)
    with path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = json.loads(path.read_text())
        yield job
        temporary = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        os.replace(temporary, path)

def update(path, **values):
    with transaction(path) as job:
        job.update(values)
        return dict(job)

def cancel_requested(path):
    return bool(json.loads(Path(path).read_text()).get('cancel_requested'))

def request_cancel(path):
    with transaction(path) as job:
        if job['kind'] != 'train' or job['status'] not in ('queued', 'running') or job.get('phase') == 'finalizing':
            return False
        job['cancel_requested'] = True
        job['message'] = 'Cancellation requested. Finishing the current operation…'
        return True

def begin_finalization(path):
    with transaction(path) as job:
        if job.get('cancel_requested') or job['status'] == 'cancelled':
            job.update(status='cancelled', message='Training cancelled. No new model was published.')
            return False
        job.update(phase='finalizing', message='Saving the trained model. Cancellation is no longer available.')
        return True
