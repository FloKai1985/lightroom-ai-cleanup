"""Technical sharpness / probable-blur estimation.

See docs/algorithms.md §3. Four classic, complementary focus measures are
computed on a dimension-normalized grayscale image and combined into two
headline numbers:

- `sharpness_score` (0..1, higher = sharper)
- `blur_confidence` (0..1, higher = more likely blurry)

These are technical estimates, not ground truth — always surface them with
hedged language (`probable_blur`, `high_confidence_blur_candidate`), never
as a certain verdict. The calibration constants below (`_CALIBRATION`) are
MVP starting points tuned by eye against a handful of sharp/blurry sample
images, not derived from a large labeled dataset — expect to retune them
once real-world false-positive/negative rates are known.
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
    "tenengrad": (50.0, 4000.0),
    "edge_density": (0.01, 0.15),
    "local_contrast": (3.0, 35.0),
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


def compute_sharpness_from_gray(gray: np.ndarray) -> SharpnessMetrics:
    """Compute sharpness metrics from an already normalized grayscale image."""
    laplacian_variance = _variance_of_laplacian(gray)
    tenengrad = _tenengrad(gray)
    edge_density = _edge_density(gray)
    local_contrast = _local_contrast(gray)

    normalized = [
        _normalize(laplacian_variance, *_CALIBRATION["laplacian_variance"]),
        _normalize(tenengrad, *_CALIBRATION["tenengrad"]),
        _normalize(edge_density, *_CALIBRATION["edge_density"]),
        _normalize(local_contrast, *_CALIBRATION["local_contrast"]),
    ]
    sharpness_score = float(np.mean(normalized))
    blur_confidence = float(np.clip(1.0 - sharpness_score, 0.0, 1.0))

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
