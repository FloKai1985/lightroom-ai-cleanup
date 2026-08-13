"""Technical sharpness / probable-blur estimation.

See docs/algorithms.md §3. Four classic, complementary focus measures are
computed on a dimension-normalized grayscale image and combined into two
headline numbers:

- `sharpness_score` (0..1, higher = sharper)
- `blur_confidence` (0..1, higher = more likely blurry)

These are technical estimates, not ground truth — always surface them with
hedged language (`probable_blur`, `high_confidence_blur_candidate`), never
as a certain verdict.

The calibration constants (`_CALIBRATION`) and per-metric weights
(`_WEIGHTS`) below were originally MVP placeholders tuned by eye against a
handful of synthetic checkerboard sharp/blurry images, then recalibrated
against real photos after a real-world false negative: a visibly,
severely out-of-focus outdoor photo (fence/bench/grass scene) scored
blur_confidence=0.055 — read as confidently sharp. Root cause, confirmed
by recomputing raw metrics for that photo plus six known-sharp real
photos through the identical plugin-export pipeline (1600px long edge,
JPEG q85, then 768px working resize): `tenengrad` and `edge_density`
were saturating to their calibration ceiling for *every* real photo
tested, sharp or blurry alike, because those ceilings were tuned against
low-detail synthetic patterns, not real outdoor scenes with dappled
sunlight/foliage (which generate strong gradient energy independent of
focus). With every real photo landing at ~1.0 on those two metrics, they
contributed nothing but a constant "sharp" vote, silently diluting the
unweighted mean and letting laplacian_variance — the metric that *did*
correctly separate the two (blurry: 175.8, sharp range: 392.9-1468.4) —
get outvoted 3-to-1.

Fix: raised the tenengrad/edge_density ceilings so they stop saturating
on real photos and start contributing real signal again, and switched
from an unweighted mean to `_WEIGHTS` favoring laplacian_variance — the
classic, most texture-robust focus measure (Pech-Pacheco et al., 2000) —
since it showed the cleanest separation in the real sample. Verified
against that real photo (0.055 -> 0.559, now correctly reads as
"probably blurry") and the six known-sharp real photos (all stayed in
0.02-0.21, so no regressions). See
`tests/unit/test_sharpness.py::test_dappled_background_out_of_focus_scores_above_probable_blur`
for the synthetic regression fixture (the real photo can't be committed
— it's a personal photo, not a redistributable test asset).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from lr_cleanup.analysis._imaging import load_bgr, resize_long_edge, to_gray


@dataclass(frozen=True)
class SharpnessMetrics:
    laplacian_variance: float
    tenengrad: float
    edge_density: float
    local_contrast: float
    sharpness_score: float
    blur_confidence: float


# Rough (low, high) ranges used to map each raw metric onto 0..1 before
# averaging. Values below `low` clamp to 0.0, above `high` clamp to 1.0.
# Calibrated for grayscale images normalized to ~768px long edge.
_CALIBRATION = {
    "laplacian_variance": (5.0, 400.0),
    "tenengrad": (50.0, 20000.0),
    "edge_density": (0.01, 0.20),
    "local_contrast": (3.0, 35.0),
}

# How much each normalized metric contributes to `sharpness_score`. Must
# sum to 1.0 (enforced by test_sharpness.py). laplacian_variance and
# tenengrad get the most weight -- they showed the cleanest real-photo
# separation once their calibration ceilings were fixed (see module
# docstring); local_contrast showed the weakest separation (dappled
# textured backgrounds keep it elevated regardless of focus), so it's
# weighted lowest.
_WEIGHTS = {
    "laplacian_variance": 0.45,
    "tenengrad": 0.25,
    "edge_density": 0.20,
    "local_contrast": 0.10,
}


def _normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _variance_of_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx**2 + gy**2))


def _edge_density(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges > 0))


def _local_contrast(gray: np.ndarray, tile_size: int = 32) -> float:
    h, w = gray.shape
    tile_stds: list[float] = []
    for y in range(0, h - tile_size + 1, tile_size):
        for x in range(0, w - tile_size + 1, tile_size):
            tile = gray[y : y + tile_size, x : x + tile_size]
            tile_stds.append(float(tile.std()))
    if not tile_stds:
        return float(gray.std())
    return float(np.mean(tile_stds))


def _combine_metrics(
    laplacian_variance: float, tenengrad: float, edge_density: float, local_contrast: float
) -> tuple[float, float]:
    """Maps the four raw metrics onto `(sharpness_score, blur_confidence)`
    via `_CALIBRATION` + `_WEIGHTS`. Split out from `compute_sharpness_from_gray`
    so it can be unit-tested directly against real recorded metric values
    (tests/unit/test_sharpness.py) without needing an actual image on disk."""
    normalized = {
        "laplacian_variance": _normalize(laplacian_variance, *_CALIBRATION["laplacian_variance"]),
        "tenengrad": _normalize(tenengrad, *_CALIBRATION["tenengrad"]),
        "edge_density": _normalize(edge_density, *_CALIBRATION["edge_density"]),
        "local_contrast": _normalize(local_contrast, *_CALIBRATION["local_contrast"]),
    }
    sharpness_score = float(sum(_WEIGHTS[key] * value for key, value in normalized.items()))
    blur_confidence = float(np.clip(1.0 - sharpness_score, 0.0, 1.0))
    return sharpness_score, blur_confidence


def compute_sharpness_from_gray(gray: np.ndarray) -> SharpnessMetrics:
    """Compute sharpness metrics from an already normalized grayscale image."""
    laplacian_variance = _variance_of_laplacian(gray)
    tenengrad = _tenengrad(gray)
    edge_density = _edge_density(gray)
    local_contrast = _local_contrast(gray)

    sharpness_score, blur_confidence = _combine_metrics(
        laplacian_variance, tenengrad, edge_density, local_contrast
    )

    return SharpnessMetrics(
        laplacian_variance=laplacian_variance,
        tenengrad=tenengrad,
        edge_density=edge_density,
        local_contrast=local_contrast,
        sharpness_score=sharpness_score,
        blur_confidence=blur_confidence,
    )


def compute_sharpness(path: str, working_long_edge: int = 768) -> SharpnessMetrics:
    """Load an image from disk and compute its sharpness metrics."""
    image = load_bgr(path)
    image = resize_long_edge(image, working_long_edge)
    gray = to_gray(image)
    return compute_sharpness_from_gray(gray)


def is_high_confidence_blur(metrics: SharpnessMetrics, threshold: float = 0.75) -> bool:
    """Whether `metrics` clears the bar for `high_confidence_blur_candidate`."""
    return metrics.blur_confidence >= threshold


def is_probable_blur(metrics: SharpnessMetrics, threshold: float = 0.5) -> bool:
    """Whether `metrics` clears the lower bar for `probable_blur`."""
    return metrics.blur_confidence >= threshold
