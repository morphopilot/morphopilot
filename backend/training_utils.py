"""Unchanged training primitives extracted from the 2026-09-16 experiment.

Bundled so deployment does not depend on another research checkout.
"""
from __future__ import annotations
import numpy as np
import torch
import torch.nn.functional as F

def crop_with_padding(
    array: np.ndarray, center_zyx: tuple[int, int, int], shape: tuple[int, int, int], fill
) -> np.ndarray:
    output = np.full(shape, fill, dtype=array.dtype)
    source_slices = []
    target_slices = []
    for center, requested, available in zip(center_zyx, shape, array.shape):
        start = center - requested // 2
        stop = start + requested
        source_start = max(0, start)
        source_stop = min(available, stop)
        target_start = source_start - start
        target_stop = target_start + source_stop - source_start
        source_slices.append(slice(source_start, source_stop))
        target_slices.append(slice(target_start, target_stop))
    output[tuple(target_slices)] = array[tuple(source_slices)]
    return output


def apply_augmentation(
    image: np.ndarray,
    label: np.ndarray,
    valid: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if rng.random() < 0.5:
        image, label, valid = image[:, ::-1, :], label[:, ::-1, :], valid[:, ::-1, :]
    if rng.random() < 0.5:
        image, label, valid = image[:, :, ::-1], label[:, :, ::-1], valid[:, :, ::-1]
    rotations = int(rng.integers(4))
    if rotations:
        image = np.rot90(image, rotations, axes=(1, 2))
        label = np.rot90(label, rotations, axes=(1, 2))
        valid = np.rot90(valid, rotations, axes=(1, 2))
    scale = float(rng.uniform(0.88, 1.12))
    shift = float(rng.uniform(-0.12, 0.12))
    image = image * scale + shift
    if rng.random() < 0.35:
        image += rng.normal(0, rng.uniform(0.01, 0.05), size=image.shape).astype(np.float32)
    return np.ascontiguousarray(image), np.ascontiguousarray(label), np.ascontiguousarray(valid)


def segmentation_loss(logits: torch.Tensor, target: torch.Tensor) -> tuple[torch.Tensor, dict]:
    class_weights = torch.tensor((0.25, 0.75), device=logits.device, dtype=torch.float32)
    cross_entropy = F.cross_entropy(logits.float(), target, weight=class_weights, ignore_index=-1)
    valid = target >= 0
    foreground = (target == 1) & valid
    probability = torch.softmax(logits.float(), dim=1)[:, 1]
    intersection = (probability * foreground).flatten(1).sum(1)
    denominator = (probability * valid).flatten(1).sum(1) + foreground.flatten(1).sum(1)
    dice_score = (2 * intersection + 1) / (denominator + 1)
    dice_loss = 1 - dice_score.mean()
    loss = cross_entropy + dice_loss
    return loss, {
        "ce": float(cross_entropy.detach()),
        "dice_loss": float(dice_loss.detach()),
        "soft_dice": float(dice_score.mean().detach()),
    }
