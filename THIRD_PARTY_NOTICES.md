# Third-party notices and asset provenance

The Apache-2.0 license at the repository root covers project-owned code, documentation, and generated synthetic examples. Dependencies and model weights retain their own terms. The dependency manifests and lockfiles specify the installable versions.

| Component | Source and license | Distribution |
| --- | --- | --- |
| nnInteractive code | [MIC-DKFZ/nnInteractive](https://github.com/MIC-DKFZ/nnInteractive), Apache-2.0; pinned in the GPU lockfile. | Installed separately; upstream notices retained by the package. |
| nnU-Net code | [MIC-DKFZ/nnUNet](https://github.com/MIC-DKFZ/nnUNet), Apache-2.0; pinned in the GPU lockfile. | Installed separately. |
| Official nnInteractive weights | [MIC-DKFZ/nnInteractive model](https://huggingface.co/MIC-DKFZ/nnInteractive), **CC BY-NC-SA 4.0**. | Downloaded separately by the user. Revision and hashes are in `deploy/model-manifest.json`; no weights in Git. |
| shadcn stylesheet | MIT; copyright shadcn. | `vendor/shadcn-tailwind-4.13.0.css` with its adjacent license. |
| Browser/Python libraries | React, Vite, Lucide, Tailwind, FastAPI, NumPy, PyTorch and other declared dependencies. | Installed via lockfiles, not vendored wholesale. The browser build emits a dependency license inventory. |
| Public website graphics and examples | Generated for MorphoPilot with `examples/generate_synthetic.py`; see `website/README.md`. | Project-owned synthetic content under Apache-2.0. |

The dual-stem adapter, application workflow, storage layer, and training support were developed in the MorphoPilot research project. Their architecture relies on the upstream nnInteractive/nnU-Net model specification; this repository does not claim to have invented those upstream methods. No SAVAII source is bundled.

The public package excludes the private research volumes, measured experiment figures, fine-tuned research checkpoints, server credentials, hosted-service identities, and the predecessor's private Git history. Their presence in earlier internal demonstrations does not grant public redistribution rights.

When redistributing an installation or trained weights, check the licenses of the exact artifacts you distribute. Licensing this application under Apache-2.0 does not remove restrictions on the separately obtained official or derived model weights.
