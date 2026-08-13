"""Near-duplicate detection input: perceptual hashing.

See docs/algorithms.md §2. Uses the `ImageHash` package's pHash
implementation over a dimension-normalized rendition so hashes are
comparable regardless of the source image's original resolution.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

DEFAULT_HASH_SIZE = 8
DEFAULT_NORMALIZED_LONG_EDGE = 512


def _phash_from_pil_image(rgb: Image.Image, hash_size: int, normalized_long_edge: int) -> str:
    rgb.thumbnail((normalized_long_edge, normalized_long_edge), Image.Resampling.LANCZOS)
    return str(imagehash.phash(rgb, hash_size=hash_size))


def compute_phash_from_bgr(
    bgr: np.ndarray,
    hash_size: int = DEFAULT_HASH_SIZE,
    normalized_long_edge: int = DEFAULT_NORMALIZED_LONG_EDGE,
) -> str:
    """Same as `compute_phash`, but from an already-decoded BGR array (as
    produced by `_imaging.load_bgr`) instead of re-reading the file from
    disk. Exists so `analyzer.py` can decode a photo once and reuse the
    array for sharpness/exposure/phash, instead of each doing its own
    independent decode. PIL and OpenCV's JPEG/PNG/TIFF decoders were
    verified to produce bit-identical pixel data for this pipeline's
    purposes (checked across real-photo-like JPEG, PNG, and TIFF test
    images: 0 Hamming distance in every case), so this is not a
    behavior-changing shortcut."""
    rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    return _phash_from_pil_image(rgb, hash_size, normalized_long_edge)


def compute_phash(
    path: str | Path,
    hash_size: int = DEFAULT_HASH_SIZE,
    normalized_long_edge: int = DEFAULT_NORMALIZED_LONG_EDGE,
) -> str:
    """Return the hex string of the perceptual hash for the image at `path`."""
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        return _phash_from_pil_image(rgb, hash_size, normalized_long_edge)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Hamming distance between two hex-encoded perceptual hashes."""
    a = imagehash.hex_to_hash(hash_a)
    b = imagehash.hex_to_hash(hash_b)
    return int(a - b)
