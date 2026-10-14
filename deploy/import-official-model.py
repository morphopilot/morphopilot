#!/usr/bin/env python3
"""Import bundled official weights into a new model directory using only stdlib."""
import argparse
from pathlib import Path
import shlex

from model_assets import APP_ROOT, import_archive


def configured_model_directory():
    config = APP_ROOT / 'deploy/local.env'
    if config.is_file():
        for line in config.read_text().splitlines():
            if line.startswith('MORPHOPILOT_MODEL_DIR='):
                values = shlex.split(line.split('=', 1)[1])
                if len(values) == 1:
                    return Path(values[0])
                raise ValueError('Invalid MORPHOPILOT_MODEL_DIR in deploy/local.env.')
    return APP_ROOT / 'models/nnInteractive_v1.0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=APP_ROOT / 'assets/nninteractive-v1.0.tar.gz')
    parser.add_argument('--model-dir', type=Path)
    args = parser.parse_args()
    destination = args.model_dir or configured_model_directory()
    report = import_archive(args.archive, destination)
    print(f'Imported {len(report["sha256"])} verified official nnInteractive assets to {destination.resolve()}')
    print('Model license: CC BY-NC-SA 4.0. No runtime or category models were changed.')


if __name__ == '__main__':
    main()
