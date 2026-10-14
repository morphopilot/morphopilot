# Third-party notices and model terms

MorphoPilot source licensing is described in the repository's `LICENSE`. That license does not replace the separate licenses of dependencies or downloaded model weights. This public repository contains no research images, saved biological annotations or pretrained/fine-tuned weights. The example generator creates synthetic data from mathematical functions and a fixed random seed.

- **nnInteractive code:** MIC-DKFZ, Apache-2.0, installed from [the official repository](https://github.com/MIC-DKFZ/nnInteractive) at commit `bbe12fdccc876cb2d4e0a47133811e362608e000`. The client package uses the same commit's `client/` subdirectory. Upstream license files remain in the installed distribution.
- **nnU-Net code:** MIC-DKFZ, Apache-2.0, installed from [the official repository](https://github.com/MIC-DKFZ/nnUNet) at commit `0e495086eb108ff79afe106291e8c15bd2f2bc3a`.
- **Original pretrained model v1.0 weights:** downloaded separately from [MIC-DKFZ/nnInteractive on Hugging Face](https://huggingface.co/MIC-DKFZ/nnInteractive), revision `3f308d751c00644e4fde6f09c600264b393b21b5`. The model license is **CC BY-NC-SA 4.0**. Read the downloaded license for non-commercial use and derivative-model terms. The downloader verifies the license, model metadata and checkpoint against the pinned SHA-256 manifest. MorphoPilot's source license does not grant commercial rights to these weights.
- **Python libraries:** exact package versions are listed in `deploy/requirements-cpu.lock` and `deploy/requirements-gpu.lock`. Each package retains its own license.
- **Browser libraries:** versions are pinned in `package-lock.json`. The Vite build writes dependency license text to `standalone/dist/.vite/license.md`.

`backend/model_build.py` constructs the upstream nnInteractive architecture and loads the official checkpoint mapping. `backend/training_utils.py` and `backend/prompt_simulation.py` contain this project's training and prompt-simulation utilities; no separate private checkout is required. Prompt training preserves the inactive Auto stem. Auto training updates its own stem and the shared network.

MorphoPilot provides research annotation and training tools. The supplied synthetic examples and interface screenshots are workflow demonstrations, not clinical validation or segmentation-quality benchmarks. Third-party hosting plugins, accounts and credentials are not bundled or required to run the local app.
