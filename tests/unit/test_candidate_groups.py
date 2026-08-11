from datetime import UTC, datetime, timedelta

from lr_cleanup.analysis.candidate_groups import (
    PhotoForGrouping,
    group_exact_duplicates,
    group_near_duplicates,
)
from lr_cleanup.database.models import GroupType

BASE_HEX = "0" * 16  # 8x8 phash -> 64 bits -> 16 hex chars, all zero


def hex_with_distance(distance: int) -> str:
    """A 64-bit hex hash exactly `distance` bits away from BASE_HEX."""
    value = (1 << distance) - 1 if distance > 0 else 0
    return format(value, "016x")


T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _photo(
    photo_id: int,
    *,
    file_hash: str = "h",
    phash_distance: int = 0,
    seconds_after: float = 0.0,
    is_virtual_copy: bool = False,
    width: int = 4000,
    height: int = 3000,
    capture_time: datetime | None = ...,  # type: ignore[assignment]
) -> PhotoForGrouping:
    ct = T0 + timedelta(seconds=seconds_after) if capture_time is ... else capture_time
    return PhotoForGrouping(
        photo_id=photo_id,
        file_hash=file_hash,
        perceptual_hash=hex_with_distance(phash_distance),
        capture_time=ct,
        is_virtual_copy=is_virtual_copy,
        width=width,
        height=height,
    )


# --- Exact duplicates -------------------------------------------------


def test_exact_duplicates_grouped_by_file_hash() -> None:
    photos = [
        _photo(1, file_hash="same"),
        _photo(2, file_hash="same"),
        _photo(3, file_hash="different"),
    ]
    groups = group_exact_duplicates(photos)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.EXACT_DUPLICATE
    assert groups[0].photo_ids == [1, 2]


def test_single_photo_is_not_a_duplicate_group() -> None:
    photos = [_photo(1, file_hash="unique")]
    assert group_exact_duplicates(photos) == []


def test_virtual_copies_excluded_from_exact_duplicate_groups() -> None:
    photos = [
        _photo(1, file_hash="same"),
        _photo(2, file_hash="same", is_virtual_copy=True),
    ]
    groups = group_exact_duplicates(photos)
    # Only one real photo remains once the virtual copy is excluded — no group.
    assert groups == []


def test_virtual_copy_does_not_block_grouping_of_other_real_duplicates() -> None:
    photos = [
        _photo(1, file_hash="same"),
        _photo(2, file_hash="same"),
        _photo(3, file_hash="same", is_virtual_copy=True),
    ]
    groups = group_exact_duplicates(photos)
    assert len(groups) == 1
    assert groups[0].photo_ids == [1, 2]


# --- Near duplicates / bursts (temporal grouping) ----------------------


def test_close_in_time_and_hash_forms_a_burst() -> None:
    photos = [
        _photo(1, phash_distance=0, seconds_after=0),
        _photo(2, phash_distance=3, seconds_after=2),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.BURST
    assert groups[0].photo_ids == [1, 2]


def test_outside_burst_window_does_not_group() -> None:
    photos = [
        _photo(1, phash_distance=0, seconds_after=0),
        _photo(2, phash_distance=0, seconds_after=3600),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert groups == []


def test_dissimilar_hash_does_not_group_despite_close_time() -> None:
    photos = [
        _photo(1, phash_distance=0, seconds_after=0),
        _photo(2, phash_distance=40, seconds_after=1),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert groups == []


def test_different_aspect_ratio_does_not_group() -> None:
    photos = [
        _photo(1, phash_distance=0, seconds_after=0, width=4000, height=3000),
        _photo(2, phash_distance=0, seconds_after=1, width=3000, height=4000),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert groups == []


def test_missing_capture_time_does_not_group() -> None:
    photos = [
        _photo(1, phash_distance=0, capture_time=None),
        _photo(2, phash_distance=0, seconds_after=0),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert groups == []


def test_transitive_chain_spanning_beyond_window_is_near_duplicate_not_burst() -> None:
    # 1<->2 within window, 2<->3 within window, but 1<->3 spans more than the
    # window — the whole chain is still one connected component, just
    # labeled the looser "near_duplicate" instead of "burst".
    photos = [
        _photo(1, phash_distance=0, seconds_after=0),
        _photo(2, phash_distance=0, seconds_after=9),
        _photo(3, phash_distance=0, seconds_after=18),
    ]
    groups = group_near_duplicates(
        photos, burst_window_seconds=10, phash_max_distance=8, aspect_ratio_tolerance=0.05
    )
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.NEAR_DUPLICATE
    assert groups[0].photo_ids == [1, 2, 3]
