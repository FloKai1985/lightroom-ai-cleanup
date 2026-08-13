"""Regression test for analyzer.py's shared-decode optimization.

`_analyze_photo_task` decodes each photo once and reuses the array for
phash/sharpness/exposure, instead of each independently re-decoding the
same file (previously ~3 decodes per photo — see docs/architecture.md's
"Shared decode across phash/sharpness/exposure"). This locks in that the
refactor produces byte-identical results to the original independent-call
approach, not just "close enough."
"""

from __future__ import annotations

from pathlib import Path

from lr_cleanup.analysis.exposure import compute_exposure
from lr_cleanup.analysis.file_hash import sha256_file
from lr_cleanup.analysis.perceptual_hash import compute_phash
from lr_cleanup.analysis.sharpness import compute_sharpness
from lr_cleanup.config import Settings
from lr_cleanup.service.analyzer import _analyze_photo_task, _PhotoAnalysisTask
from tests.fixtures.images import make_blurred_jpeg, make_overexposed_jpeg, make_sharp_jpeg


def _assert_matches_independent_calls(preview_path: Path, original_path: Path) -> None:
    settings = Settings(database_url="sqlite:///:memory:")

    expected_phash = compute_phash(str(preview_path))
    expected_sharpness = compute_sharpness(str(preview_path), settings.sharpness_working_size)
    expected_exposure = compute_exposure(
        str(preview_path), settings.highlight_clip_threshold, settings.shadow_clip_threshold
    )
    expected_hash = sha256_file(str(original_path))

    task = _PhotoAnalysisTask(
        photo_id=1,
        original_path=str(original_path),
        image_path=str(preview_path),
        fingerprint="fp",
    )
    outcome = _analyze_photo_task(task, settings)

    assert outcome.error is None
    result = outcome.result
    assert result is not None
    assert result.perceptual_hash == expected_phash
    assert result.file_hash == expected_hash
    assert result.sharpness_score == expected_sharpness.sharpness_score
    assert result.blur_confidence == expected_sharpness.blur_confidence
    assert result.exposure_score == expected_exposure.exposure_score
    assert result.highlight_clipping == expected_exposure.highlight_clipping
    assert result.shadow_clipping == expected_exposure.shadow_clipping


def test_shared_decode_matches_independent_calls_for_sharp_photo(tmp_path: Path) -> None:
    preview = make_sharp_jpeg(tmp_path / "sharp.jpg")
    original = tmp_path / "sharp.raw"
    original.write_bytes(b"pretend-raw-bytes" * 100)
    _assert_matches_independent_calls(preview, original)


def test_shared_decode_matches_independent_calls_for_blurred_photo(tmp_path: Path) -> None:
    preview = make_blurred_jpeg(tmp_path / "blurred.jpg")
    original = tmp_path / "blurred.raw"
    original.write_bytes(b"pretend-raw-bytes" * 100)
    _assert_matches_independent_calls(preview, original)


def test_shared_decode_matches_independent_calls_for_overexposed_photo(tmp_path: Path) -> None:
    preview = make_overexposed_jpeg(tmp_path / "overexposed.jpg")
    original = tmp_path / "overexposed.raw"
    original.write_bytes(b"pretend-raw-bytes" * 100)
    _assert_matches_independent_calls(preview, original)
