"""Synthetic, redistributable image generators for tests.

Nothing here reads from a personal photo library or Lightroom catalog —
every fixture is generated in-process from a fixed random seed so tests are
fully reproducible without shipping binary test assets.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def checkerboard_gray(width: int = 400, height: int = 300, cell: int = 16) -> np.ndarray:
    """A crisp high-frequency pattern: strong edges everywhere -> "sharp"."""
    yy, xx = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    pattern = ((xx // cell) + (yy // cell)) % 2
    return (pattern * 255).astype(np.uint8)


def noisy_gray(
    width: int = 400, height: int = 300, low: int = 0, high: int = 256, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(low, high, size=(height, width), dtype=np.uint16).astype(np.uint8)


def blur_gray(gray: np.ndarray, ksize: int = 25, sigma: float = 12.0) -> np.ndarray:
    return cv2.GaussianBlur(gray, (ksize, ksize), sigma)


def save_gray_as_jpeg(gray: np.ndarray, path: str | Path, quality: int = 90) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray, mode="L").convert("RGB").save(path, format="JPEG", quality=quality)
    return path


def make_sharp_jpeg(path: str | Path, width: int = 400, height: int = 300) -> Path:
    return save_gray_as_jpeg(checkerboard_gray(width, height), path)


def make_blurred_jpeg(path: str | Path, width: int = 400, height: int = 300) -> Path:
    sharp = checkerboard_gray(width, height)
    return save_gray_as_jpeg(blur_gray(sharp), path)


def make_overexposed_jpeg(path: str | Path, width: int = 400, height: int = 300) -> Path:
    rng = np.random.default_rng(1)
    gray = rng.integers(250, 256, size=(height, width), dtype=np.uint16).astype(np.uint8)
    return save_gray_as_jpeg(gray, path)


def make_underexposed_jpeg(path: str | Path, width: int = 400, height: int = 300) -> Path:
    rng = np.random.default_rng(2)
    gray = rng.integers(0, 5, size=(height, width), dtype=np.uint16).astype(np.uint8)
    return save_gray_as_jpeg(gray, path)


def make_balanced_exposure_jpeg(path: str | Path, width: int = 400, height: int = 300) -> Path:
    rng = np.random.default_rng(3)
    gray = rng.integers(60, 196, size=(height, width), dtype=np.uint16).astype(np.uint8)
    return save_gray_as_jpeg(gray, path)
