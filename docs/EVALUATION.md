# Evaluate improvement, not just successful execution

A completed training job proves that an optimization run finished. It does not prove that segmentation improved. The synthetic example is a deterministic functional fixture; its generated reference is not a biological benchmark.

## Freeze a test protocol before training

Use independent acquisitions when available. If only one acquisition exists, separate training and test regions spatially, document the separation, and avoid claiming cross-acquisition generalization. Make sure adjacent overlapping crops cannot leak the same structures between splits.

Keep test images and references outside eligible training snapshots. The application excludes samples marked as test; users must still avoid uploading a duplicate of a test image as training data. Set aside references for scoring, not for selecting supposedly raw-only prompts.

## Prompt comparison

Compare the official model and each trained version using the exact same image, ordered prompts, starting mask, preprocessing, and inference configuration. Save the complete prompt history and intermediate 3D masks. Label a result with the model that actually generated it: a mask annotated during round r typically comes from the incoming model M(r−1), while Mr is created only after that round's training.

Inspect negative-point placement in the relevant neighborhood across Z. Dim foreground is not necessarily background. Review the whole propagated mask before confirmation; sparse points do not establish dense label correctness.

## Auto comparison

Compare at least:

1. Auto immediately after conversion from a chosen Prompt checkpoint.
2. Auto after explicit training on the same allowed confirmed image–mask pairs.
3. A held-out reference, with a documented source and quality limitation.

Keep the same initialization, train/test split, preprocessing, and inference coverage. Record steps, optimizer settings, random seed, checkpoint lineage, and all eligible snapshot hashes. Conversion is a structural initialization, not an additional training run.

## Report results

- Per-volume Dice/IoU for semantic masks, with the foreground definition and empty-mask handling specified.
- Precision and recall when false positives and missed thin structures have different consequences.
- For spot detection, object/point matching with a fixed physical distance tolerance and one-to-one assignment; voxel Dice alone may obscure detection errors.
- Annotation effort: prompts, reviewed slices, corrections, elapsed time, and rejected masks.
- Raw images, visible positive/negative prompts, model outputs, and references at matching slices. Use identical display scaling for comparisons.
- Per-case values and failure examples, not only an aggregate. Report uncertainty only when the sampling design supports it.

An earlier internal MorphoPilot experiment motivated this platform. Its private data and results are not included in the public release, and no benchmark improvement is asserted by the README. Public evaluations should be reproducible from distributable data and frozen manifests.
