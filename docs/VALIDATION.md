# Initial source-release validation

Recorded 2026-10-13 for the MorphoPilot `0.1.0` source release. This record distinguishes local functional checks, browser checks, and research-model evaluation. See [GitHub Actions](https://github.com/morphopilot/morphopilot/actions) for the checks on each published revision.

## Website correction before public launch

- **73 frontend tests and 107 Python tests with 64 subtests passed locally**, including nine new explorer regressions and five synthetic-asset regressions. TypeScript, ESLint and the production build passed.
- Both 25-slice examples have reference foreground on every plane, including the first and last. Compact geometric signal support matches the reference masks; all 37,223 bright voxels in the two website crops are covered. This is a synthetic consistency check, **not model accuracy**.
- Every raw/reference atlas frame is checked against the corresponding TIFF plane. Neurite overlays use 88% lime blending and Spot overlays use 92% orange blending so the difference is visible across the comparison slider.
- Chrome checks verified full-original/full-mask endpoints, both datasets' boundary slices, immediate playback, continuing frame changes, wrap from Z25 to Z1, and pause. Explicit view buttons complement the comparison slider.
- The website remains a prerecorded synthetic visualization. Its Play button does not run inference or training.

## Clean environment and automated checks

| Check | Result |
| --- | --- |
| Fresh Python 3.12 CPU installation using `deploy/install.sh cpu` | Passed; isolated `.venv`, pinned dependencies, `pip check` clean. |
| Python regression suite | **102 tests and 10 subtests passed** with `pytest tests/test_*.py deploy/test_gateway.py -q`. |
| CPU API smoke | Passed resumable upload/retry, eight edge-aware tiles, direct mask painting, explicit confirmation, exact merged TIFF dimensions/voxel placement, overview, trash, and restoration in disposable storage. |
| Fresh `npm ci` | Passed. |
| Frontend regression suite | **64 tests passed**, including a real Vite-to-gateway proxy integration test. |
| TypeScript and ESLint | Passed; zero lint errors or warnings. |
| Production browser build | Passed with Vite 8.3.4. |
| npm dependency audit | Zero reported vulnerabilities at the time of the check; this is time-specific and not a comprehensive security certification. |
| Synthetic fixture generation | Deterministic array comparison passed; four included TIFFs have a manifest with shape and SHA-256. |
| Public website | JavaScript syntax and local asset/document links checked; no external font/CDN dependency. |

The CPU test suite does not import a real neural model. Model-dependent paths are mocked or inspected where those tests state it. One upstream Starlette/httpx test-client deprecation warning was observed; it did not fail the suite.

## Actual Chrome checks

The freshly built standalone application was started on loopback with a disposable runtime and no weights. Checked in the real browser:

- Password sign-in, clean empty library, and original-model state.
- Loading an API-uploaded 25×384×384 synthetic image as four tiles.
- Tile selection, Z=13 navigation, visible synthetic image, direct mask painting, undo, and persistence after page reload.
- Whole-volume overview, global Z selection, and explicit partial-review coverage.
- Switching to Spot without exposing the Neurite upload.
- Creating a custom “Synthetic QA” category, opening its empty upload dialog, and seeing independent original-model state.
- Project website: synthetic spot selection, Z and overlay sliders, workflow tabs, Auto explanation, deployment dialog, and mobile navigation.
- Website at 390×844: no horizontal overflow; viewport override reset afterward. No website console errors or warnings were observed.
- Single-file HTML export: checked in Chrome over local HTTP; embedded images, Spot selection, Z playback, workflow switching and the deployment dialog worked. Local document targets were checked on disk. Direct `file:` navigation was not automated.

The automated browser file chooser was blocked by the Chrome extension's local-file permission. The browser upload dialog was inspected, but **file selection/upload was not completed through browser automation**. Upload behavior was exercised separately through the authenticated API and CPU smoke. No browser permission was weakened to work around this restriction.

The included [UI screenshot](images/studio-synthetic.jpg) shows a manual edit on a generated image. It is neither a pretrained-model output nor a biological result.

## GPU boundary

The predecessor application was previously exercised on an NVIDIA A100 40 GB with real native Prompt inference, short Prompt fine-tuning, conversion, Auto inference, and short Auto fine-tuning. Those runs informed this source extraction. **The complete new open-source checkout has not yet been installed and rerun on a fresh GPU server.** A new adopter should run `deploy/doctor.sh` and an isolated neural acceptance workflow before using its training output.

No GPU performance, memory minimum, accuracy improvement, multi-acquisition generalization, or clinical validation is established by this release check. The public synthetic references are not neural predictions. Private historical research data and checkpoints are intentionally absent.

## Reproduce the local checks

```bash
npm ci
npm run typecheck
npm test
npm run lint
npm run build
npm audit
PYTHON_BIN=python3.12 bash deploy/install.sh cpu
.venv/bin/python -m pip install -r deploy/requirements-test.lock
.venv/bin/python -m pytest tests/test_*.py deploy/test_gateway.py -q
.venv/bin/python deploy/smoke-cpu.py
.venv/bin/python examples/generate_synthetic.py --output-dir examples/generated
python3 scripts/check_publication.py
```

Run the publication scan on a clean source tree or archive without `deploy/local.env`; it deliberately reports local credentials as unsuitable for publication. The scan finds common accidental artifacts and strings, not every possible secret or legal issue.
