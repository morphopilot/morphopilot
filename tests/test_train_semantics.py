"""CPU behavioral regressions against the real train.py run() function.

Only torch/model computation is mocked. Actual sampling, job writes, cancellation,
and checkpoint completion branches execute from the selected source file.
"""
from __future__ import annotations
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import numpy as np

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / 'backend/train.py'
SOURCE = Path(os.environ.get('MORPHOPILOT_TRAIN_SOURCE', DEFAULT_SOURCE))


class Tensor:
    def __init__(self, value=None): self.value = value
    def cuda(self): return self
    def backward(self): pass
    def detach(self): return 1.0


class Stem:
    def requires_grad_(self, value): return self
    def parameters(self): return []


class Model:
    def __init__(self):
        self.prompt = Stem()
        self.auto_stem = Stem()
        self.auto_ready = False
    def train(self): return self
    def __call__(self, *args): return Tensor()
    def payload(self, meta): return {'meta': meta}


class Harness:
    def __init__(self, directory, steps, callback=None, depth=23):
        self.root = Path(directory)
        self.out = self.root / 'model'; self.out.mkdir()
        self.job_path = self.root / 'job.json'
        self.steps = steps; self.optimizer_steps = 0
        self.centers = []; self.callback = callback
        self.raw = np.arange(depth * 8 * 8, dtype=np.float32).reshape(depth, 8, 8)
        self.label = np.zeros(self.raw.shape, dtype=np.uint8)
        self.label[2, 4, 4] = 1
        self.label[-3, 4, 4] = 1
        sample = self.root / 'snapshot.npz'
        np.savez_compressed(sample, image=self.raw, mask=self.label)
        self.job_path.write_text(json.dumps({'id': 'test', 'kind': 'train', 'status': 'running'}))
        self.cfg = {'mode': 'prompt', 'checkpoint': None, 'parent': 'official',
                    'samples': [{'snapshot': str(sample), 'id': 'roi'}],
                    'out': str(self.out), 'job_path': str(self.job_path),
                    'steps': steps, 'seed': 260101}
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(self.cfg))

    def optimizer_step(self):
        self.optimizer_steps += 1
        if self.callback: self.callback(self)

    def crop(self, array, center, shape, pad_value):
        # This deliberately records the *real sampler's* request before doing a
        # generic centered crop. No sampler condition is reproduced here.
        if array.dtype.kind == 'f': self.centers.append(tuple(center))
        result = np.full(shape, pad_value, array.dtype)
        origin = np.asarray(center) - np.asarray(shape) // 2
        lo = np.maximum(origin, 0); hi = np.minimum(origin + shape, array.shape)
        src = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
        dst = tuple(slice(int(a), int(b)) for a, b in zip(lo-origin, hi-origin))
        result[dst] = array[src]
        return result

    def run(self):
        fake_torch = types.ModuleType('torch')
        fake_torch.manual_seed = lambda seed: None
        fake_torch.set_num_threads = lambda n: None
        fake_torch.backends = types.SimpleNamespace(cudnn=types.SimpleNamespace(benchmark=False))
        fake_torch.optim = types.SimpleNamespace(AdamW=lambda *a, **kw: types.SimpleNamespace(
            zero_grad=lambda **kw: None, step=self.optimizer_step))
        fake_torch.nn = types.SimpleNamespace(utils=types.SimpleNamespace(clip_grad_norm_=lambda *a: None))
        fake_torch.autocast = lambda *a, **kw: contextlib.nullcontext()
        fake_torch.bfloat16 = 'mock-bfloat16'
        fake_torch.from_numpy = Tensor
        fake_torch.isfinite = lambda value: True
        fake_torch.save = lambda value, path: Path(path).write_bytes(b'mocked-checkpoint')
        fake_model = types.ModuleType('model')
        fake_model.load_dual = lambda *a, **kw: Model()
        fake_model.tensor_hash = lambda stem: 'unchanged'
        fake_model.normalize = lambda raw: raw
        fake_model.PATCH = (32, 8, 8)
        fake_model.crop_with_padding = self.crop
        fake_model.apply_augmentation = lambda x, y, valid, rng: (x, y, valid)
        fake_model.segmentation_loss = lambda *a: (Tensor(),)
        fake_model.create_prompt_sample = lambda label, valid, rng, scenario: types.SimpleNamespace(
            interactions=np.zeros((7, *label.shape), np.float32))
        spec = importlib.util.spec_from_file_location('morphopilot_cpu_train_under_test', SOURCE)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'torch': fake_torch, 'model': fake_model}), \
                patch.object(sys, 'path', [str(SOURCE.parent), *sys.path]):
            spec.loader.exec_module(module)
            module.time = types.SimpleNamespace(time=lambda: 100_000, monotonic=time.monotonic)
            module.run(str(self.config_path))
        return json.loads(self.job_path.read_text())


class TrainingSemantics(unittest.TestCase):
    def test_foreground_at_both_z_extremes_can_enter_training_crops(self):
        with tempfile.TemporaryDirectory(prefix='morphopilot-crop-qa-') as directory:
            h = Harness(directory, steps=30, depth=96)
            h.run()
            zs = [center[0] for center in h.centers]
            self.assertTrue(any(abs(z-2) <= 15 for z in zs),
                            f'Low-Z foreground was excluded from all crops: {zs}')
            self.assertTrue(any(abs(z-93) <= 15 for z in zs),
                            f'High-Z foreground was excluded from all crops: {zs}')

    def test_cancellation_during_final_optimizer_step_prevents_completion(self):
        def cancel(h):
            if h.optimizer_steps == h.steps:
                j = json.loads(h.job_path.read_text())
                j['cancel_requested'] = True
                h.job_path.write_text(json.dumps(j))
                (h.out / 'cancel').touch()
        with tempfile.TemporaryDirectory(prefix='morphopilot-late-cancel-qa-') as directory:
            h = Harness(directory, steps=2, callback=cancel)
            final = h.run()
            self.assertEqual(final['status'], 'cancelled', final)
            self.assertFalse(final.get('training_finished'), final)

    def test_external_cancellation_flag_survives_subprocess_progress_writes(self):
        def external_api_write(h):
            if h.optimizer_steps == 1:
                # Reproduce the API writing its flag before it touches the marker.
                j = json.loads(h.job_path.read_text())
                j['cancel_requested'] = True
                j['api_audit_marker'] = 'must-survive'
                h.job_path.write_text(json.dumps(j))
        with tempfile.TemporaryDirectory(prefix='morphopilot-job-write-qa-') as directory:
            h = Harness(directory, steps=2, callback=external_api_write)
            final = h.run()
            self.assertIs(final.get('cancel_requested'), True, final)
            self.assertEqual(final.get('api_audit_marker'), 'must-survive', final)


if __name__ == '__main__': unittest.main(verbosity=2)
