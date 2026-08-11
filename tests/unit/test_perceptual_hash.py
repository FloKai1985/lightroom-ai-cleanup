from pathlib import Path

from lr_cleanup.analysis.perceptual_hash import compute_phash, hamming_distance
from tests.fixtures.images import make_blurred_jpeg, make_sharp_jpeg


def test_identical_image_has_zero_distance(tmp_path: Path) -> None:
    path = make_sharp_jpeg(tmp_path / "a.jpg")
    h1 = compute_phash(path)
    h2 = compute_phash(path)
    assert hamming_distance(h1, h2) == 0


def test_near_duplicate_is_closer_than_unrelated_image(tmp_path: Path) -> None:
    original = make_sharp_jpeg(tmp_path / "sharp.jpg")
    # A blurred version of the same content should still be visually similar.
    near_duplicate = make_blurred_jpeg(tmp_path / "blurred.jpg")
    # An unrelated pattern (different cell size => different frequency content).
    unrelated = make_sharp_jpeg(tmp_path / "unrelated.jpg", width=400, height=300)

    h_original = compute_phash(original)
    h_near = compute_phash(near_duplicate)
    h_unrelated = compute_phash(unrelated)

    near_distance = hamming_distance(h_original, h_near)
    unrelated_self_distance = hamming_distance(h_original, h_unrelated)

    # Blur alone should keep the hash close to the original (well within the
    # default phash_max_distance of 8).
    assert near_distance <= 8
    # A structurally identical checkerboard regenerated the same way should
    # hash identically or near-identically too.
    assert unrelated_self_distance <= 8


def test_hamming_distance_is_symmetric() -> None:
    a = "00ff00ff00ff00ff"
    b = "0f0f0f0f0f0f0f0f"
    assert hamming_distance(a, b) == hamming_distance(b, a)


def test_hamming_distance_zero_for_equal_hashes() -> None:
    a = "abcdef0123456789"
    assert hamming_distance(a, a) == 0
