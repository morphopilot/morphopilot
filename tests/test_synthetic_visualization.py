"""Regression checks for complete, correctly aligned synthetic website masks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image
import tifffile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('synthetic_generator', ROOT / 'examples/generate_synthetic.py')
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class SyntheticVisualizationRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / 'examples/fixtures'
        cls.manifest = json.loads((fixtures / 'manifest.json').read_text())
        cls.datasets = {}
        for name in ('neurite', 'spot'):
            raw = tifffile.imread(fixtures / f'{name}s.tif')
            truth = tifffile.imread(fixtures / f'{name}s_reference.tif')
            if name == 'neurite':
                raw, truth = raw[:, 96:288, 96:288], truth[:, 96:288, 96:288]
            cls.datasets[name] = raw, truth

    def test_primitive_has_no_signal_outside_reference_including_volume_boundary(self):
        for z in (0, 3, 6):
            field = np.zeros((7, 20, 20), dtype=np.float32)
            truth = np.zeros_like(field, dtype=bool)
            GENERATOR.stamp(field, truth, z, 10, 10, 1.3, 1.55, 2500)
            np.testing.assert_array_equal(field > 0, truth)

    def test_all_25_planes_have_masked_bright_structures(self):
        for name, (raw, truth) in self.datasets.items():
            with self.subTest(dataset=name):
                report = GENERATOR.coverage_report(raw, truth)
                self.assertEqual(len(report['reference_voxels_per_z']), 25)
                self.assertEqual(report['unmasked_bright_voxels'], 0)
                self.assertTrue(np.all((raw >= GENERATOR.BRIGHT_SIGNAL_THRESHOLD).any(axis=(1, 2))))

    def test_raw_and_mask_atlases_align_with_every_source_slice(self):
        for name, (raw, truth) in self.datasets.items():
            prefix = '' if name == 'neurite' else 'spot-'
            source = GENERATOR.grey(raw)
            expected_raw = (source * 255).astype(np.uint8)
            color = (182, 242, 120) if name == 'neurite' else GENERATOR.SPOT_OVERLAY_COLOR
            opacity = .88 if name == 'neurite' else GENERATOR.SPOT_OVERLAY_OPACITY
            expected_mask = GENERATOR.overlay(source, truth, color, opacity)
            raw_atlas = np.asarray(Image.open(ROOT / f'website/assets/{prefix}stack-raw.webp').convert('L'))
            mask_atlas = np.asarray(Image.open(ROOT / f'website/assets/{prefix}stack-mask.webp').convert('RGB'))
            for z in range(25):
                y, x = z // 5 * 192, z % 5 * 192
                actual_raw = raw_atlas[y:y+192, x:x+192]
                actual_mask = mask_atlas[y:y+192, x:x+192]
                with self.subTest(dataset=name, z=z):
                    np.testing.assert_array_equal(actual_raw, expected_raw[z])
                    np.testing.assert_array_equal(actual_mask, expected_mask[z])
                    self.assertTrue(np.any(actual_mask[..., 0] != actual_mask[..., 2]))
                    if name == 'spot':
                        foreground = actual_mask[truth[z] > 0].astype(int)
                        self.assertTrue(np.all(foreground[:,0]-foreground[:,2] > 150))

    def test_z_sequence_contains_different_geometry(self):
        for name, (_, truth) in self.datasets.items():
            with self.subTest(dataset=name):
                for z in range(24):
                    self.assertFalse(np.array_equal(truth[z], truth[z+1]))

    def test_checked_in_fixture_hashes_match_manifest(self):
        for entry in self.manifest['files']:
            path = ROOT / 'examples/fixtures' / entry['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])


if __name__ == '__main__':
    unittest.main()
