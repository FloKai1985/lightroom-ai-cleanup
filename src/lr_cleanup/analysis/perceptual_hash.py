"""Near-duplicate detection input: perceptual hashing.

See docs/algorithms.md §2. Uses the `ImageHash` package's pHash
implementation over a dimension-normalized rendition so hashes are
comparable regardless of the source image's original resolution.
"""

from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image

DEFAULT_HASH_SIZE = 8
DEFAULT_NORMALIZED_LONG_EDGE = 512


def compute_phash(
    path: str | Path,
    hash_size: int = DEFAULT_HASH_SIZE,
    normalized_long_edge: int = DEFAULT_NORMALIZED_LONG_EDGE,
) -> str:
    """Return the hex string of the perceptual hash for the image at `path`."""
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        rgb.thumbnail((normalized_long_edge, normalized_long_edge), Image.Resampling.LANCZOS)
        phash = imagehash.phash(rgb, hash_size=hash_size)
    return str(phash)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Hamming distance between two hex-encoded perceptual hashes."""
    a = imagehash.hex_to_hash(hash_a)
    b = imagehash.hex_to_hash(hash_b)
    return int(a - b)
