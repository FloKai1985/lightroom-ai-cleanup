"""Exact-duplicate detection input: SHA-256 of the original file's bytes.

See docs/algorithms.md §1. This hashes the original file, never a rendered
preview — two renditions of the same original could differ in bytes even
though the source is byte-identical.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """Return the lowercase hex SHA-256 digest of the file at `path`."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
