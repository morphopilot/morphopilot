"""CPU regression for the real normalize() AST, without importing GPU modules."""
from __future__ import annotations
import ast
import os
from pathlib import Path
import unittest
import warnings

import numpy as np

LOCAL_SOURCE = Path(__file__).resolve().parents[1] / 'backend/model.py'
SOURCE = Path(os.environ.get('MORPHOPILOT_MODEL_SOURCE', LOCAL_SOURCE))


def actual_normalize():
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    node, = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'normalize']
    if node.decorator_list: raise AssertionError('Unexpected normalization decorator needs review')
    scope = {'np': np}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['normalize']


class NormalizeRegression(unittest.TestCase):
    def setUp(self): self.normalize = actual_normalize()

    def test_all_zero_volume_reports_clear_english_error(self):
        raw = np.zeros((3, 4, 5), np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            with self.assertRaisesRegex(ValueError, r'^The image contains only zeros\.$'):
                self.normalize(raw)
        np.testing.assert_array_equal(raw, np.zeros_like(raw))

    def test_single_nonzero_voxel_fails_before_nan_or_runtime_warning(self):
        for location, value in [((0, 0, 0), 7), ((2, 3, 4), -7), ((1, 2, 3), 1234)]:
            with self.subTest(location=location, value=value):
                raw = np.zeros((3, 4, 5), np.float32)
                raw[location] = value
                original = raw.copy()
                with warnings.catch_warnings():
                    warnings.simplefilter('error')
                    with self.assertRaisesRegex(ValueError, r'^The volume needs at least two nonzero voxels for normalization\.$'):
                        self.normalize(raw)
                np.testing.assert_array_equal(raw, original)

    def test_valid_3d_volume_keeps_prior_bbox_sample_std_values_and_input(self):
        # Fixed, known bounding box; interior zeros must remain in the statistics,
        # while the larger volume's zero border must remain outside statistics.
        for dtype in (np.uint16, np.float32, np.float64):
            with self.subTest(dtype=dtype):
                raw = np.zeros((5, 6, 7), dtype=dtype)
                region = np.arange(1, 46, dtype=dtype).reshape(3, 3, 5)
                region[1, 1, 2] = 0
                raw[1:4, 2:5, 1:6] = region
                original = raw.copy()
                # This is the pre-fix contract: float32 input, entire nonzero
                # bounding box, float64 statistics, sample standard deviation.
                region32 = region.astype(np.float32)
                mean = float(region32.mean(dtype=np.float64))
                std = float(region32.std(dtype=np.float64, ddof=1))
                expected = (raw.astype(np.float32) - mean) / std
                actual = self.normalize(raw)
                np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(raw, original)
                self.assertEqual(actual.dtype, np.float32)
                self.assertTrue(np.isfinite(actual).all())
                self.assertFalse(np.allclose(actual, (raw.astype(np.float32) - mean) / region32.std(ddof=0)))


if __name__ == '__main__': unittest.main(verbosity=2)
