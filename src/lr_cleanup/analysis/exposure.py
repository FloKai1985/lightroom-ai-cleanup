"""Technical exposure signals: highlight/shadow clipping and histogram shape.

See docs/algorithms.md §4. Deliberately limited to technical measurements —
no aesthetic judgment of "good" exposure beyond clipping/spread.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from lr_cleanup.analysis._imaging import load_bgr, to_gray


@dataclass(frozen=True)
class ExposureMetrics:
    exposure_score: float
    highlight_clipping: float
    shadow_clipping: float


def compute_exposure_from_gray(
    gray: np.ndarray,
    highlight_threshold: float = 0.98,
    shadow_threshold: float = 0.02,
) -> ExposureMetrics:
    """Compute exposure metrics from a grayscale image, values normalized 0..1."""
    normalized = gray.astype(np.float64) / 255.0

    highlight_clipping = float(np.mean(normalized >= highlight_threshold))
    shadow_clipping = float(np.mean(normalized <= shadow_threshold))

    # Histogram spread: a healthy exposure uses most of the tonal range.
    # We penalize the score by how much of the frame is clipped at either end.
    clipping_penalty = highlight_clipping + shadow_clipping
    std_spread = float(np.std(normalized))  # low spread ~ flat/underexposed frame
    spread_score = float(np.clip(std_spread / 0.25, 0.0, 1.0))

    exposure_score = float(np.clip(spread_score * (1.0 - clipping_penalty), 0.0, 1.0))

    return ExposureMetrics(
        exposure_score=exposure_score,
        highlight_clipping=highlight_clipping,
        shadow_clipping=shadow_clipping,
    )


def compute_exposure(
    path: str,
    highlight_threshold: float = 0.98,
    shadow_threshold: float = 0.02,
) -> ExposureMetrics:
    """Load an image from disk and compute its exposure metrics."""
    image = load_bgr(path)
    gray = to_gray(image)
    return compute_exposure_from_gray(gray, highlight_threshold, shadow_threshold)
