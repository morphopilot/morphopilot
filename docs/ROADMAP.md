# Roadmap

The goal is a practical open research workflow from expert correction to reusable volumetric models. This list separates shipped functionality from future work; it is not a delivery promise.

## Available in the initial source release

- Browser-based 3D annotation, display controls, and native Prompt inference.
- Resumable volume upload, tiling, a persistent library, mask reconstruction, and export.
- Independent category models and explicit incremental training.
- Additional Auto stem, conversion, and prompt-free training/inference workflow.
- Standalone server deployment and CPU-only functional examples.

## Near-term contributions

| Priority | Work | Completion evidence |
| --- | --- | --- |
| Installation | Test documented setup on additional Linux/CUDA combinations. | Fresh-host command logs, dependency validation, and an isolated synthetic smoke run. |
| Evaluation | Publish a redistributable multi-volume benchmark protocol. | Frozen split, prompt manifest, per-case metrics, and reproducible figures. |
| Usability | Improve accessible controls and review coverage explanations. | Keyboard/browser regression checks and synthetic screenshots. |
| Robustness | Expand upload interruption, restart, disk-pressure, and checkpoint failure checks. | Tests that reproduce failure and verify recovery without data loss. |
| Data formats | Add explicit channel/axis selection and larger-volume strategies. | Known-shape fixtures with verified source-coordinate reconstruction. |

## Research and longer-term directions

- Fine-grained branch analysis, including a proposed 3D mean-shift approach. **Not implemented in this release.**
- Better uncertainty and disagreement cues to help decide what should be reviewed next.
- Category adapters and independent benchmarks for cells and organs; category names alone do not validate performance.
- Multi-user ownership, permissions, audit trails, and stronger deployment isolation.
- More efficient model loading, memory management, and support for smaller GPUs after measurement.

Codex-assisted development can help with issue reproduction, test generation, documentation, and code review. Any future agent automation should act on public/synthetic fixtures and produce reviewable changes, not silently modify scientific annotations or publish results.
