from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy import ndimage
from skimage.draw import line
from skimage.morphology import convex_hull_image


PREV_SEG = 0
BBOX_LASSO_POS = 1
BBOX_LASSO_NEG = 2
POINT_POS = 3
POINT_NEG = 4
SCRIBBLE_POS = 5
SCRIBBLE_NEG = 6
NUM_INTERACTION_CHANNELS = 7
INTERACTION_DECAY = 0.98

CHANNEL_NAMES = (
    "previous segmentation",
    "positive bbox/lasso",
    "negative bbox/lasso",
    "positive point",
    "negative point",
    "positive scribble",
    "negative scribble",
)

SCENARIOS = ("point", "bbox2d", "scribble", "lasso", "prev_seg", "mixed")


@dataclass
class PromptSample:
    interactions: np.ndarray
    scenario: str
    events: list[dict]


def _decay_existing(interactions: np.ndarray) -> None:
    interactions[1:] *= INTERACTION_DECAY


def _sample_interior_coordinate(mask: np.ndarray, rng: np.random.Generator) -> Optional[tuple[int, int, int]]:
    if not np.any(mask):
        return None
    distance = ndimage.distance_transform_edt(mask)
    positive = distance[mask]
    threshold = np.quantile(positive, 0.75) if positive.size > 8 else positive.min()
    candidates = np.argwhere(mask & (distance >= threshold))
    return tuple(int(v) for v in candidates[rng.integers(len(candidates))])


def _sample_background_coordinate(
    target: np.ndarray, valid: np.ndarray, rng: np.random.Generator
) -> Optional[tuple[int, int, int]]:
    near = ndimage.binary_dilation(target, structure=np.ones((1, 3, 3), dtype=bool), iterations=12)
    candidates = near & ~target & valid
    if not np.any(candidates):
        candidates = ~target & valid
    if not np.any(candidates):
        return None
    coords = np.argwhere(candidates)
    return tuple(int(v) for v in coords[rng.integers(len(coords))])


def _point_kernel(radius: int = 4) -> np.ndarray:
    axes = np.ogrid[tuple(slice(-radius, radius + 1) for _ in range(3))]
    distance = np.sqrt(sum(axis.astype(np.float32) ** 2 for axis in axes))
    return np.clip(1.0 - distance / (radius + 0.5), 0.0, 1.0).astype(np.float32)


POINT_KERNEL = _point_kernel()


def _stamp_point(channel: np.ndarray, coordinate: tuple[int, int, int]) -> None:
    radius = POINT_KERNEL.shape[0] // 2
    dst_slices = []
    src_slices = []
    for center, size in zip(coordinate, channel.shape):
        start = max(0, center - radius)
        stop = min(size, center + radius + 1)
        dst_slices.append(slice(start, stop))
        src_slices.append(slice(start - (center - radius), stop - (center - radius)))
    destination = channel[tuple(dst_slices)]
    np.maximum(destination, POINT_KERNEL[tuple(src_slices)], out=destination)


def add_point(
    interactions: np.ndarray,
    mask: np.ndarray,
    include: bool,
    rng: np.random.Generator,
    event_name: str = "point",
) -> Optional[dict]:
    coordinate = (
        _sample_interior_coordinate(mask, rng)
        if include
        else _sample_interior_coordinate(mask, rng)
    )
    if coordinate is None:
        return None
    _decay_existing(interactions)
    channel = POINT_POS if include else POINT_NEG
    _stamp_point(interactions[channel], coordinate)
    return {
        "type": event_name,
        "include": bool(include),
        "channel": int(channel),
        "coordinate_zyx": list(coordinate),
    }


def _choose_foreground_slice(target: np.ndarray, rng: np.random.Generator) -> Optional[int]:
    counts = target.sum(axis=(1, 2))
    available = np.flatnonzero(counts > 0)
    if available.size == 0:
        return None
    weights = counts[available].astype(np.float64)
    weights /= weights.sum()
    return int(rng.choice(available, p=weights))


def add_bbox2d(
    interactions: np.ndarray, target: np.ndarray, valid: np.ndarray, rng: np.random.Generator
) -> Optional[dict]:
    z = _choose_foreground_slice(target, rng)
    if z is None:
        return None
    coords = np.argwhere(target[z] & valid[z])
    if coords.size == 0:
        return None
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    jitter = rng.integers(-6, 7, size=4)
    y0 = int(np.clip(y0 + jitter[0], 0, target.shape[1] - 1))
    y1 = int(np.clip(y1 + jitter[1], y0 + 1, target.shape[1]))
    x0 = int(np.clip(x0 + jitter[2], 0, target.shape[2] - 1))
    x1 = int(np.clip(x1 + jitter[3], x0 + 1, target.shape[2]))
    _decay_existing(interactions)
    interactions[BBOX_LASSO_POS, z, y0:y1, x0:x1] = 1.0
    return {
        "type": "bbox2d",
        "include": True,
        "channel": BBOX_LASSO_POS,
        "slice_z": z,
        "bbox_yx": [[y0, y1], [x0, x1]],
    }


def _scribble_on_2d_mask(mask: np.ndarray, rng: np.random.Generator, thickness: int = 2) -> np.ndarray:
    coords = np.argwhere(mask)
    output = np.zeros_like(mask, dtype=bool)
    if coords.size == 0:
        return output
    start = coords[rng.integers(len(coords))]
    distances = np.linalg.norm(coords - start[None], axis=1)
    candidates = coords[(distances >= 8) & (distances <= 60)]
    end = candidates[rng.integers(len(candidates))] if len(candidates) else coords[rng.integers(len(coords))]
    rr, cc = line(int(start[0]), int(start[1]), int(end[0]), int(end[1]))
    output[rr, cc] = True
    output = ndimage.binary_dilation(output, iterations=thickness)
    return output & mask


def add_scribble(
    interactions: np.ndarray,
    mask: np.ndarray,
    include: bool,
    rng: np.random.Generator,
    event_name: str = "scribble",
) -> Optional[dict]:
    z = _choose_foreground_slice(mask, rng)
    if z is None:
        return None
    scribble = _scribble_on_2d_mask(mask[z], rng)
    if not np.any(scribble):
        return None
    _decay_existing(interactions)
    channel = SCRIBBLE_POS if include else SCRIBBLE_NEG
    interactions[channel, z, scribble] = 1.0
    return {
        "type": event_name,
        "include": bool(include),
        "channel": int(channel),
        "slice_z": z,
        "voxels": int(scribble.sum()),
    }


def add_lasso(
    interactions: np.ndarray, target: np.ndarray, valid: np.ndarray, rng: np.random.Generator
) -> Optional[dict]:
    z = _choose_foreground_slice(target, rng)
    if z is None:
        return None
    mask = target[z] & valid[z]
    eroded = ndimage.binary_erosion(mask, iterations=int(rng.integers(1, 4)))
    if eroded.sum() < 16:
        eroded = mask
    lasso = convex_hull_image(eroded) & mask
    if not np.any(lasso):
        return None
    _decay_existing(interactions)
    interactions[BBOX_LASSO_POS, z, lasso] = 1.0
    return {
        "type": "lasso",
        "include": True,
        "channel": BBOX_LASSO_POS,
        "slice_z": z,
        "voxels": int(lasso.sum()),
    }


def corrupt_segmentation(target: np.ndarray, valid: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    previous = target.copy()
    structure = np.ones((1, 3, 3), dtype=bool)
    iterations = int(rng.integers(2, 10))
    if rng.random() < 0.5:
        previous = ndimage.binary_erosion(previous, structure=structure, iterations=iterations)
    else:
        previous = ndimage.binary_dilation(previous, structure=structure, iterations=iterations)
    shift = (0, int(rng.integers(-8, 9)), int(rng.integers(-8, 9)))
    previous = ndimage.shift(previous.astype(np.uint8), shift=shift, order=0, mode="constant") > 0
    if rng.random() < 0.35 and np.any(previous):
        z = int(rng.integers(previous.shape[0]))
        y = int(rng.integers(previous.shape[1]))
        x = int(rng.integers(previous.shape[2]))
        previous[
            z,
            max(0, y - 8) : min(previous.shape[1], y + 9),
            max(0, x - 8) : min(previous.shape[2], x + 9),
        ] = True
    return previous & valid


def partial_annotation_segmentation(
    target: np.ndarray, valid: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, dict]:
    coordinates = np.argwhere(target & valid)
    if len(coordinates) == 0:
        return np.zeros_like(target, dtype=bool), {
            "source": "partial_annotation",
            "removed_fraction": 1.0,
            "retained_fraction": 0.0,
        }
    remove_fraction = 1.0 if rng.random() < 0.05 else float(rng.uniform(1 / 3, 0.95))
    if remove_fraction >= 1.0:
        previous = np.zeros_like(target, dtype=bool)
    else:
        # A random projection removes one contiguous part of the object. This
        # resembles an annotation that stopped early and avoids salt-and-pepper
        # holes that a user would never produce.
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction) + 1e-8
        scores = coordinates @ direction
        threshold = np.quantile(scores, remove_fraction)
        retained_coordinates = coordinates[scores > threshold]
        previous = np.zeros_like(target, dtype=bool)
        previous[tuple(retained_coordinates.T)] = True
    retained_fraction = float(previous.sum() / max(1, target.sum()))
    return previous, {
        "source": "partial_annotation",
        "removed_fraction": float(1 - retained_fraction),
        "retained_fraction": retained_fraction,
    }


def simulate_previous_segmentation(
    target: np.ndarray, valid: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, dict]:
    if rng.random() < 0.7:
        return partial_annotation_segmentation(target, valid, rng)
    previous = corrupt_segmentation(target, valid, rng)
    return previous, {
        "source": "boundary_corruption",
        "removed_fraction": float(np.logical_and(target, ~previous).sum() / max(1, target.sum())),
        "retained_fraction": float(np.logical_and(target, previous).sum() / max(1, target.sum())),
        "false_positive_voxels": int(np.logical_and(previous, ~target).sum()),
    }


def add_correction_prompt(
    interactions: np.ndarray,
    prediction: np.ndarray,
    target: np.ndarray,
    valid: np.ndarray,
    rng: np.random.Generator,
    prefer_scribble: bool = False,
) -> Optional[dict]:
    prediction = prediction.astype(bool) & valid
    interactions[PREV_SEG] = prediction.astype(np.float32)
    false_negative = target & ~prediction & valid
    false_positive = prediction & ~target & valid
    fn_count = int(false_negative.sum())
    fp_count = int(false_positive.sum())
    if fn_count == 0 and fp_count == 0:
        return None
    include = fn_count >= fp_count
    error = false_negative if include else false_positive
    if prefer_scribble and error.sum() >= 20:
        return add_scribble(interactions, error, include, rng, event_name="corrective_scribble")
    return add_point(interactions, error, include, rng, event_name="corrective_point")


def create_prompt_sample(
    target: np.ndarray,
    valid: np.ndarray,
    rng: np.random.Generator,
    scenario: Optional[str] = None,
) -> PromptSample:
    target = target.astype(bool) & valid
    interactions = np.zeros((NUM_INTERACTION_CHANNELS, *target.shape), dtype=np.float32)
    scenario = scenario or str(rng.choice(SCENARIOS))
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown prompt scenario: {scenario}")
    events: list[dict] = []

    def record(event: Optional[dict]) -> None:
        if event is not None:
            events.append(event)

    background = ~target & valid
    if scenario == "point":
        record(add_point(interactions, target, True, rng))
        if rng.random() < 0.4:
            near_background = ndimage.binary_dilation(target, iterations=12) & background
            record(add_point(interactions, near_background, False, rng))
    elif scenario == "bbox2d":
        record(add_bbox2d(interactions, target, valid, rng))
    elif scenario == "scribble":
        record(add_scribble(interactions, target, True, rng))
        if rng.random() < 0.35:
            near_background = ndimage.binary_dilation(target, iterations=8) & background
            record(add_scribble(interactions, near_background, False, rng))
    elif scenario == "lasso":
        record(add_lasso(interactions, target, valid, rng))
    elif scenario == "prev_seg":
        previous, previous_metadata = simulate_previous_segmentation(target, valid, rng)
        interactions[PREV_SEG] = previous.astype(np.float32)
        events.append({"type": "previous_segmentation", **previous_metadata})
        record(add_correction_prompt(interactions, previous, target, valid, rng, prefer_scribble=rng.random() < 0.5))
    else:
        choices = list(rng.choice(("point", "bbox2d", "scribble", "lasso"), size=3, replace=False))
        for choice in choices:
            if choice == "point":
                record(add_point(interactions, target, True, rng))
            elif choice == "bbox2d":
                record(add_bbox2d(interactions, target, valid, rng))
            elif choice == "scribble":
                record(add_scribble(interactions, target, True, rng))
            else:
                record(add_lasso(interactions, target, valid, rng))
        if rng.random() < 0.6:
            previous, previous_metadata = simulate_previous_segmentation(target, valid, rng)
            interactions[PREV_SEG] = previous.astype(np.float32)
            events.append({"type": "previous_segmentation", **previous_metadata})
            record(add_correction_prompt(interactions, previous, target, valid, rng))

    if not events and np.any(target):
        record(add_point(interactions, target, True, rng))
    return PromptSample(interactions=interactions, scenario=scenario, events=events)
