# Deploy MorphoPilot

MorphoPilot runs on your own server. Image volumes, masks, model versions and the SQLite index stay on that server. A new installation starts with an empty Data Library and separate Neurite, Spot, Cell and Organ categories. No research data or model weights are included in the repository.

Configuration uses the `MORPHOPILOT_*` environment variables written by `deploy/configure.py`.

## Requirements

- Linux is the supported deployment target. The CPU tests also run on macOS.
- Node.js 22.13+ within the 22.x line, or Node.js 24+, and npm, for building the browser app. Node.js 23 is not in the supported engine range.
- Python 3.10–3.12 with `venv`, plus Git. Python 3.10 is the verified GPU environment.
- Writable persistent storage. Allow space for uploaded images, masks, confirmation snapshots, model checkpoints and backups.
- Neural inference/training additionally needs a compatible NVIDIA driver, CUDA-capable PyTorch, and a GPU supporting BF16. An Ampere-or-newer GPU is recommended; the research workflow was exercised on an A100 40 GB. This is not a minimum VRAM guarantee.

CPU mode supports upload, tiling, viewing, manual painting, confirmation, merging and download. Point-based neural segmentation, Prompt training and Auto mode need the GPU installation and separately downloaded weights.

## 1. Build the browser app

From a fresh clone, enter the repository and run:

```bash
npm ci
npm run build
```

This creates `standalone/dist/`. A source checkout cannot serve the browser app until this build completes.

## 2. Install Python dependencies

For a CPU evaluation:

```bash
bash deploy/install.sh cpu
```

For a CUDA server:

```bash
bash deploy/install.sh gpu
```

The installer uses `python3.10` by default. To select another supported interpreter:

```bash
PYTHON_BIN=python3.12 bash deploy/install.sh cpu
```

Dependencies install into `.venv/`. The GPU dependency file is a pinned Linux x86_64 environment; it includes the exact upstream nnInteractive and nnU-Net revisions. Do not install a random newer nnInteractive version into that environment and assume checkpoint compatibility.

## 3. Configure your local installation

```bash
.venv/bin/python deploy/configure.py
```

Enter a workspace password privately when prompted. The command creates `deploy/local.env` with mode `0600`, a salted password hash and freshly generated API/session secrets. It refuses to overwrite an existing configuration. Never commit or share this file.

Defaults are `runtime/` for persistent data, `models/nnInteractive_v1.0/` for the official model and loopback port `8787`. To use a persistent server volume instead:

```bash
.venv/bin/python deploy/configure.py \
  --data-dir /data/morphopilot/runtime \
  --model-dir /data/morphopilot/models/nnInteractive_v1.0 \
  --port 8787
```

Choose either configuration command, not both. The account running the service must own these directories.

## 4. Download the original model (GPU only)

```bash
bash deploy/download-model.sh
bash deploy/doctor.sh
```

The downloader retrieves `MIC-DKFZ/nnInteractive` at the immutable revision in `deploy/model-manifest.json`. It checks SHA-256 hashes for the checkpoint, architecture metadata and license. It uses the `hf` CLI installed by the GPU dependency file. The official weights are **CC BY-NC-SA 4.0**, separately from MorphoPilot's source license. Read `deploy/THIRD_PARTY_NOTICES.md` and the downloaded license before use.

For an air-gapped server, download and verify the official model on an approved connected machine, transfer the complete `nnInteractive_v1.0` directory to the configured model path, then verify it again with `.venv/bin/python deploy/verify-model.py /absolute/path/to/nnInteractive_v1.0`. Never overwrite another model directory. `deploy/import-official-model.py` also accepts pinned archives produced by the `deploy.model_assets.export_archive` Python helper.

For CPU mode, skip weight download and check the installation with:

```bash
bash deploy/doctor.sh --cpu
.venv/bin/python deploy/smoke-cpu.py
```

The smoke test creates a disposable runtime, uploads a synthetic array, manually annotates eight edge-aware tiles, confirms them, verifies an exact merged TIFF, and exercises Trash/Restore. It does not touch the configured runtime or need weights.

## 5. Start and open the app

```bash
bash deploy/start.sh
```

Open `http://127.0.0.1:8787` on the server and sign in with the workspace password. The supervisor starts one isolated worker per category and a password-protected browser gateway. GPU work is serialized across categories.

For a remote server, keep the app bound to loopback and forward the port from your own computer:

```bash
ssh -N -L 8787:127.0.0.1:8787 your-user@your-server
```

Then open `http://127.0.0.1:8787` on your computer. This needs no hosted service or external login provider. Stop the foreground app with Ctrl+C. A system service example is in `deploy/morphopilot.service.example`; replace its user, group and `/opt/morphopilot` path before installation.

For HTTPS access, configure a domain you control, put a TLS reverse proxy in front of the loopback gateway, and set `MORPHOPILOT_PUBLIC_ORIGIN=https://your-domain` and `MORPHOPILOT_COOKIE_SECURE=1` in `deploy/local.env`. `deploy/Caddyfile.example` shows the proxy shape. Restart after configuration changes. Expose only the browser gateway, not internal category workers. The workspace password is shared by visitors to this installation; this is not a per-user access-control system.

## Try synthetic data

```bash
.venv/bin/python examples/generate_synthetic.py --output-dir examples/generated
```

Upload `neurites.tif` or `spots.tif` through Data Library. Files ending in `_reference.tif` are generated truth masks for external comparisons; do not upload them as raw images. Synthetic structures are a workflow fixture, not a biological dataset or a model-quality benchmark. See `docs/USER_GUIDE.md`.

## Verify a source checkout

```bash
.venv/bin/python -m pip install -r deploy/requirements-test.lock
.venv/bin/python -m pytest tests/test_*.py deploy/test_gateway.py -q
.venv/bin/python deploy/smoke-cpu.py
npm run typecheck
```

The Python tests use temporary storage. They exercise upload validation, category isolation, merge integrity, authentication, recoverable deletion and job cancellation. They mock model computation where stated; passing these tests alone does not validate a GPU installation or segmentation accuracy. `doctor.sh` checks actual CUDA/BF16 and original-model checksums, but a real annotation and a short training round on your own data remain the final GPU acceptance check.

## Back up and upgrade

Finish or cancel jobs, stop the service, and take an offline backup:

```bash
.venv/bin/python deploy/backup.py \
  --data-dir /data/morphopilot/runtime \
  --output /data/backups/morphopilot-runtime.tar.gz \
  --service-stopped
```

Use your configured paths. Back up `deploy/local.env` separately to protected storage. Restore the runtime to the **same absolute path** because saved snapshot and checkpoint references currently contain absolute paths. Never start two services against the same runtime. Keep runtime data, downloaded models, configuration and backup archives outside Git commits. Rebuild the frontend after upgrading source.

## Troubleshooting

- **Missing frontend:** run `npm ci && npm run build` in this checkout.
- **Neural actions unavailable in CPU mode:** install GPU requirements, download weights, and run `bash deploy/doctor.sh`.
- **Cannot connect remotely:** keep the server process running and use the SSH tunnel above. A browser's `127.0.0.1` refers to that browser's computer.
- **Same-origin error behind HTTPS:** the public URL must exactly match `MORPHOPILOT_PUBLIC_ORIGIN`, including scheme and port.
- **Failed category:** check server logs, correct the cause, then use Retry in the category dialog. Other categories keep their own data and model state.
- **Interrupted import or merge:** the UI reports it as failed. Retry upload or merge; unfinished outputs are not presented as complete.
- **Model training failed:** inspect the corresponding `runtime/models/<model-id>/train.log` and job record. Completed previous models and confirmation snapshots are preserved.

Supported image inputs are single-channel 3D TIFF/BigTIFF stacks, NPY and NPZ containing an `image` array, with Z,Y,X axes. Multichannel/color/time-series volumes and DICOM/NIfTI ingestion are not implemented. Default limits are 20 GiB uploaded/decoded, 20,000 tiles and a 512 MiB TIFF page decode limit; administrators may set `MORPHOPILOT_MAX_UPLOAD_BYTES`, `MORPHOPILOT_MAX_DECODED_BYTES`, `MORPHOPILOT_MAX_TILES` and `MORPHOPILOT_MAX_TIFF_PAGE_BYTES` after considering storage and memory capacity.
