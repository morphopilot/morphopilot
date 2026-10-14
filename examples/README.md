# Reproducible synthetic 3D examples

**All files here are synthetic. They are not biological acquisitions, neural-network predictions, or an accuracy benchmark.** Inputs and geometric reference masks are separate files.

| Checked-in file in `fixtures/` | Shape (Z, Y, X) | Type | Purpose |
| --- | --- | --- | --- |
| `neurites.tif` | 25 × 384 × 384 | uint16 | Noisy branching input; four default 25 × 192 × 192 tiles |
| `neurites_reference.tif` | 25 × 384 × 384 | uint8 | Known geometric foreground; reference only |
| `spots.tif` | 25 × 192 × 192 | uint16 | Noisy punctate input; one default tile |
| `spots_reference.tif` | 25 × 192 × 192 | uint8 | Known geometric foreground; reference only |

## Try the files

1. Start MorphoPilot using [the deployment guide](../docs/DEPLOYMENT.md).
2. Upload `fixtures/neurites.tif` into a Neurite or new category. Upload the input image, not its reference mask.
3. Inspect the four tiles and scroll through Z. Positive prompts belong on bright structures; negative prompts belong on clear background.
4. Review and confirm each tile explicitly. Fine-tuning and Auto inference require installed, compatible model weights and a working GPU backend; the synthetic fixture does not supply any weights.
5. Merge/export the reviewed tiles and verify the result has the input volume's 25 × 384 × 384 dimensions.
6. Use the separately named reference files only to test analysis/export code. Do not present reference overlays as model predictions.

The four checked-in fixture files total approximately **5.6 MB**. They are included in a clean source checkout, so website download links do not depend on local generation.

## Regenerate

From the project root, in the installed environment:

```bash
python examples/generate_synthetic.py --output-dir examples/generated
```

Dependencies: NumPy, tifffile, Pillow. A minimal standalone generation environment can install them with `python -m pip install numpy tifffile pillow`.

To regenerate both fixtures and the website illustrations:

```bash
python examples/generate_synthetic.py \
  --output-dir examples/generated \
  --website-assets website/assets
```

The generator uses seed `20261013`, compact Gaussian tube/spot primitives, explicit geometric foreground and additive noise. Each primitive fades to zero at its reference boundary, so its bright structure has no unlabelled halo. Every one of the 25 Z planes, including both endpoints, contains labelled structures. These are complete procedural reference masks, not model output.

Array hashes, per-slice foreground counts and a bright-voxel coverage check are in `fixtures/manifest.json`. Regenerating writes an independent local copy and manifest under the ignored `generated/` directory; it does not overwrite the checked-in fixtures. Values reproduce with a fixed NumPy version; encoded file bytes may differ with TIFF/image-library versions. The website's branching comparison is a 25 × 192 × 192 crop at origin `(0, 96, 96)` of the four-tile input. Its hero is a separate 25 × 448 × 512 generated volume projected across Z.

These fixtures validate mechanics and UI behavior. They do not establish generalization to microscopy, medical imaging, or clinical use.

## License

The generator, generated volumes, geometric references and project-generated website illustrations are licensed under **Apache-2.0**, as covered by the project [LICENSE](../LICENSE). Upstream nnInteractive code and separately downloaded model weights keep their own licenses.
