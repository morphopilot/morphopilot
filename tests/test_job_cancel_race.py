"""Real-process job locking and cancellation/finalization boundary tests.

No server import, model import, network call, or GPU work. Every job is temporary.
Use MORPHOPILOT_JOB_STORE_SOURCE to test another actual job_store.py implementation.
"""
from __future__ import annotations
import fcntl
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import tempfile
import unittest

CHECKOUT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('MORPHOPILOT_JOB_STORE_SOURCE', CHECKOUT / 'backend/job_store.py'))
DEADLINE = 10


def load_store(source):
    spec = importlib.util.spec_from_file_location('actual_job_store_under_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hold_progress_transaction(source, path, entered, release, results):
    try:
        store = load_store(source)
        with store.transaction(path) as job:
            entered.set()
            if not release.wait(DEADLINE): raise TimeoutError('Parent did not release transaction')
            job.update(status='running', step=1)
        results.put(('progress', True))
    except BaseException as error:
        results.put(('error', repr(error)))
        raise


def observed_cancel(source, path, lock_attempted, returned, results):
    try:
        store = load_store(source)
        actual_flock = store.fcntl.flock
        def observe_flock(file, flags):
            if flags & fcntl.LOCK_EX: lock_attempted.set()
            return actual_flock(file, flags)
        store.fcntl.flock = observe_flock
        result = store.request_cancel(path)
        returned.set()
        results.put(('cancel', result))
    except BaseException as error:
        results.put(('error', repr(error)))
        raise


def operation(source, path, name, ready, go, results):
    try:
        store = load_store(source)
        ready.set()
        if not go.wait(DEADLINE): raise TimeoutError('Operation was not started')
        result = getattr(store, name)(path)
        results.put((name, result))
    except BaseException as error:
        results.put(('error', repr(error)))
        raise


def write_progress(source, path, field, ready, go, results):
    try:
        store = load_store(source)
        ready.set()
        if not go.wait(DEADLINE): raise TimeoutError('Writer was not started')
        for i in range(30): store.update(path, **{field: i})
        results.put((field, True))
    except BaseException as error:
        results.put(('error', repr(error)))
        raise


class JobStoreProcesses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='morphopilot-real-job-processes-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'job.json'
        self.path.write_text(json.dumps({'id': 'unit', 'kind': 'train', 'status': 'running'}))
        self.ctx = mp.get_context('spawn')
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.is_alive(): child.terminate()
            child.join(DEADLINE)

    def start(self, fn, *args):
        child = self.ctx.Process(target=fn, args=(str(SOURCE), str(self.path), *args))
        child.start(); self.children.append(child)
        return child

    def joined(self, children):
        for child in children:
            child.join(DEADLINE)
            self.assertFalse(child.is_alive(), 'Child process did not finish')
            self.assertEqual(child.exitcode, 0, 'Child process failed')

    def state(self): return json.loads(self.path.read_text())

    def single(self, name):
        ready, go, results = self.ctx.Event(), self.ctx.Event(), self.ctx.Queue()
        child = self.start(operation, name, ready, go, results)
        self.assertTrue(ready.wait(DEADLINE)); go.set(); self.joined([child])
        key, value = results.get(timeout=DEADLINE)
        self.assertEqual(key, name)
        return value

    def test_cancellation_waits_for_other_process_transaction_and_survives_it(self):
        entered, release = self.ctx.Event(), self.ctx.Event()
        attempted, returned = self.ctx.Event(), self.ctx.Event()
        results = self.ctx.Queue()
        writer = self.start(hold_progress_transaction, entered, release, results)
        self.assertTrue(entered.wait(DEADLINE))
        # Independently prove an OS lock is held; a thread-only RLock cannot pass.
        with self.path.with_suffix('.lock').open('a+') as probe:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        canceller = self.start(observed_cancel, attempted, returned, results)
        self.assertTrue(attempted.wait(DEADLINE), 'Cancellation did not use the shared OS lock')
        self.assertFalse(returned.is_set(), 'Cancellation bypassed the held transaction')
        release.set(); self.joined([writer, canceller])
        outcomes = dict(results.get(timeout=DEADLINE) for _ in range(2))
        self.assertEqual(outcomes, {'progress': True, 'cancel': True})
        self.assertEqual(self.state()['step'], 1)
        self.assertIs(self.state()['cancel_requested'], True)

    def test_multiple_process_progress_updates_preserve_accepted_cancellation(self):
        self.assertTrue(self.single('request_cancel'))
        go, ready_a, ready_b = self.ctx.Event(), self.ctx.Event(), self.ctx.Event()
        results = self.ctx.Queue()
        a = self.start(write_progress, 'api_counter', ready_a, go, results)
        b = self.start(write_progress, 'trainer_counter', ready_b, go, results)
        self.assertTrue(ready_a.wait(DEADLINE)); self.assertTrue(ready_b.wait(DEADLINE))
        go.set(); self.joined([a, b])
        self.assertIs(self.state()['cancel_requested'], True)
        self.assertEqual(self.state()['api_counter'], 29)
        self.assertEqual(self.state()['trainer_counter'], 29)

    def test_cancel_accepted_before_finalization_prevents_commit(self):
        self.assertTrue(self.single('request_cancel'))
        self.assertFalse(self.single('begin_finalization'))
        self.assertEqual(self.state()['status'], 'cancelled')
        self.assertNotEqual(self.state().get('phase'), 'finalizing')

    def test_cancel_after_finalization_cutoff_is_rejected(self):
        self.assertTrue(self.single('begin_finalization'))
        self.assertFalse(self.single('request_cancel'))
        self.assertEqual(self.state()['phase'], 'finalizing')
        self.assertFalse(self.state().get('cancel_requested', False))

    def test_concurrent_cancel_and_finalization_have_exactly_one_winner(self):
        for trial in range(4):
            with self.subTest(trial=trial):
                self.path.write_text(json.dumps({'id': 'unit', 'kind': 'train', 'status': 'running'}))
                go, ready_a, ready_b = self.ctx.Event(), self.ctx.Event(), self.ctx.Event()
                results = self.ctx.Queue()
                a = self.start(operation, 'request_cancel', ready_a, go, results)
                b = self.start(operation, 'begin_finalization', ready_b, go, results)
                self.assertTrue(ready_a.wait(DEADLINE)); self.assertTrue(ready_b.wait(DEADLINE))
                go.set(); self.joined([a, b])
                outcomes = dict(results.get(timeout=DEADLINE) for _ in range(2))
                self.assertEqual(sum(outcomes.values()), 1, outcomes)
                if outcomes['request_cancel']:
                    self.assertEqual(self.state()['status'], 'cancelled')
                    self.assertNotEqual(self.state().get('phase'), 'finalizing')
                else:
                    self.assertEqual(self.state()['phase'], 'finalizing')
                    self.assertFalse(self.state().get('cancel_requested', False))

    def test_terminal_jobs_and_nontraining_jobs_cannot_accept_cancellation(self):
        store = load_store(SOURCE)
        for status in ('completed', 'failed', 'cancelled'):
            self.path.write_text(json.dumps({'kind': 'train', 'status': status}))
            self.assertFalse(store.request_cancel(self.path), status)
            self.assertFalse(self.state().get('cancel_requested', False))
        self.path.write_text(json.dumps({'kind': 'interaction', 'status': 'running'}))
        self.assertFalse(store.request_cancel(self.path))


if __name__ == '__main__': unittest.main(verbosity=2)
