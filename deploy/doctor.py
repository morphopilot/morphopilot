#!/usr/bin/env python3
"""Read-only installation diagnostics. Never load or modify a user checkpoint."""
import argparse
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--cpu', action='store_true', help='Only check storage/annotation requirements')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
failures = []

def report(label, ok, detail):
    print(('PASS' if ok else 'FAIL') + f' {label}: {detail}')
    if not ok:
        failures.append(label)

report('Python', (3, 10) <= sys.version_info[:2] <= (3, 12), sys.version.split()[0])
for name in ['numpy', 'tifffile', 'imagecodecs', 'pillow', 'fastapi', 'uvicorn', 'httpx']:
    try:
        report(name, True, importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        report(name, False, 'not installed')
report('Frontend', (root / 'standalone/dist/index.html').exists(), str(root / 'standalone/dist'))
report('Configuration', (root / 'deploy/local.env').exists(), 'deploy/local.env present' if (root / 'deploy/local.env').exists() else 'run deploy/configure.py')
data = Path(os.environ.get('MORPHOPILOT_DATA_DIR', str(root / 'runtime')))
nearest = data
while not nearest.exists():
    nearest = nearest.parent
usage = shutil.disk_usage(nearest)
report('Storage', os.access(nearest, os.W_OK), f'{data}; {usage.free / 2**30:.1f} GiB free')
if not args.cpu:
    for name in ['torch', 'nnInteractive', 'nnunetv2']:
        try:
            report(name, True, importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            report(name, False, 'install GPU requirements')
    try:
        import torch
        tensor = torch.randn(16, 16, device='cuda')
        output = tensor @ tensor
        torch.cuda.synchronize()
        report('Actual CUDA computation', bool(torch.isfinite(output).all()), torch.cuda.get_device_name(0))
        report('BF16', torch.cuda.is_bf16_supported(), 'training uses bfloat16; an Ampere-or-newer NVIDIA GPU is recommended')
    except Exception as exc:
        report('Actual CUDA computation', False, str(exc))
    model = Path(os.environ.get('MORPHOPILOT_MODEL_DIR', str(root / 'models/nnInteractive_v1.0')))
    check = subprocess.run([sys.executable, str(root / 'deploy/verify-model.py'), str(model)], capture_output=True, text=True)
    report('Official model checksums', check.returncode == 0, check.stdout.strip() or check.stderr.strip().splitlines()[-1])
    nvml = subprocess.run(['bash', '-c', 'command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader'], capture_output=True, text=True)
    if nvml.returncode:
        print('WARN NVML / nvidia-smi: ' + (nvml.stderr.strip() or nvml.stdout.strip() or 'unavailable') + '. This is separate from the actual CUDA computation check above.')
    else:
        print('INFO GPU driver: ' + nvml.stdout.strip())
print('Diagnostics failed.' if failures else 'All requested checks passed.')
raise SystemExit(bool(failures))
