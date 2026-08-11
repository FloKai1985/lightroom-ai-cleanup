"""Shared, read-only image loading helpers used across the analysis modules.

Every analysis function normalizes input dimensions before comparison (see
docs/algorithms.md) so scores are comparable across photos of different
source resolutions.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class UnsupportedImageError(ValueError):
    """Raised when a file cannot be decoded as an image."""


def load_bgr(path: str | Path) -> np.ndarray:
    """Read an image as an 8-bit BGR array. Read-only — never writes back."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise UnsupportedImageError(f"Could not decode image: {path}")
    return image


def resize_long_edge(image: np.ndarray, target_long_edge: int) -> np.ndarray:
    """Resize so the longer edge equals `target_long_edge`, preserving aspect ratio."""
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if long_edge == target_long_edge:
        return image
    scale = target_long_edge / long_edge
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, new_size, interpolation=interpolation)


def to_gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
