"""Exact-duplicate and near-duplicate/burst grouping.

See docs/algorithms.md §1-2. Operates on plain dataclasses rather than ORM
objects so it is trivially unit-testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lr_cleanup.analysis.clustering import connected_components
from lr_cleanup.analysis.perceptual_hash import hamming_distance
from lr_cleanup.database.models import GroupType


@dataclass(frozen=True)
class PhotoForGrouping:
    photo_id: int
    file_hash: str
    perceptual_hash: str
    capture_time: datetime | None
    is_virtual_copy: bool
    width: int
    height: int


@dataclass(frozen=True)
class DuplicateGroupResult:
    group_type: GroupType
    photo_ids: list[int]


def group_exact_duplicates(photos: list[PhotoForGrouping]) -> list[DuplicateGroupResult]:
    """Bucket photos by file hash. Virtual copies are never grouped as exact
    duplicates — they intentionally share an original file (docs/algorithms.md §1).
    """
    buckets: dict[str, list[int]] = {}
    for photo in photos:
        if photo.is_virtual_copy:
            continue
        buckets.setdefault(photo.file_hash, []).append(photo.photo_id)

    return [
        DuplicateGroupResult(group_type=GroupType.EXACT_DUPLICATE, photo_ids=sorted(ids))
        for ids in buckets.values()
        if len(ids) > 1
    ]


def _aspect_ratio(photo: PhotoForGrouping) -> float:
    if photo.height == 0:
        return 0.0
    return photo.width / photo.height


def _similar(
    a: PhotoForGrouping,
    b: PhotoForGrouping,
    burst_window_seconds: float,
    phash_max_distance: int,
    aspect_ratio_tolerance: float,
) -> bool:
    if a.capture_time is None or b.capture_time is None:
        return False
    time_delta = abs((a.capture_time - b.capture_time).total_seconds())
    if time_delta > burst_window_seconds:
        return False

    if hamming_distance(a.perceptual_hash, b.perceptual_hash) > phash_max_distance:
        return False

    ar_a, ar_b = _aspect_ratio(a), _aspect_ratio(b)
    if ar_b == 0:
        return False
    return abs(ar_a / ar_b - 1.0) <= aspect_ratio_tolerance


def group_near_duplicates(
    photos: list[PhotoForGrouping],
    burst_window_seconds: float,
    phash_max_distance: int,
    aspect_ratio_tolerance: float,
) -> list[DuplicateGroupResult]:
    """Cluster visually-similar, temporally-close photos.

    A group is labeled `BURST` if every pair within it falls inside
    `burst_window_seconds` of each other; a looser, transitively-connected
    cluster spanning more time is labeled `NEAR_DUPLICATE` (see
    docs/algorithms.md §2).
    """
    candidates = [p for p in photos if not p.is_virtual_copy]
    node_ids = [p.photo_id for p in candidates]
    by_id = {p.photo_id: p for p in candidates}

    edges: list[tuple[int, int]] = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if _similar(a, b, burst_window_seconds, phash_max_distance, aspect_ratio_tolerance):
                edges.append((a.photo_id, b.photo_id))

    results: list[DuplicateGroupResult] = []
    for component in connected_components(node_ids, edges):
        if len(component) < 2:
            continue

        capture_times = [t for pid in component if (t := by_id[pid].capture_time) is not None]
        assert len(capture_times) == len(component)  # guaranteed by _similar's edge rule
        span = max(capture_times) - min(capture_times)
        group_type = (
            GroupType.BURST
            if span.total_seconds() <= burst_window_seconds
            else GroupType.NEAR_DUPLICATE
        )
        results.append(DuplicateGroupResult(group_type=group_type, photo_ids=sorted(component)))

    return results
