from lr_cleanup.analysis.sharpness import (
    _CALIBRATION,
    _WEIGHTS,
    _combine_metrics,
    compute_sharpness_from_gray,
    is_probable_blur,
)
from tests.fixtures.images import blur_gray, checkerboard_gray


def test_sharp_image_scores_higher_than_blurred_version() -> None:
    sharp_gray = checkerboard_gray()
    blurred_gray = blur_gray(sharp_gray)

    sharp = compute_sharpness_from_gray(sharp_gray)
    blurred = compute_sharpness_from_gray(blurred_gray)

    assert sharp.sharpness_score > blurred.sharpness_score
    assert sharp.blur_confidence < blurred.blur_confidence


def test_heavily_blurred_image_is_flagged_probable_blur() -> None:
    heavily_blurred = blur_gray(checkerboard_gray(), ksize=41, sigma=20.0)
    metrics = compute_sharpness_from_gray(heavily_blurred)
    assert is_probable_blur(metrics)


def test_sharp_checkerboard_is_not_flagged_probable_blur() -> None:
    metrics = compute_sharpness_from_gray(checkerboard_gray())
    assert not is_probable_blur(metrics)


def test_scores_are_bounded_0_to_1() -> None:
    for gray in (checkerboard_gray(), blur_gray(checkerboard_gray())):
        metrics = compute_sharpness_from_gray(gray)
        assert 0.0 <= metrics.sharpness_score <= 1.0
        assert 0.0 <= metrics.blur_confidence <= 1.0


def test_weights_sum_to_one() -> None:
    assert sum(_WEIGHTS.values()) == 1.0
    assert set(_WEIGHTS) == set(_CALIBRATION)


def test_real_world_false_negative_now_reads_as_probably_blurry() -> None:
    """Regression test for a real false negative: a visibly, severely
    out-of-focus outdoor photo (fence/bench/grass scene, full frame soft,
    no crisp edges anywhere) scored blur_confidence=0.055 under the
    original unweighted-mean combination -- read as confidently sharp.

    The raw metric values below are the actual numbers recomputed for
    that photo by re-running it through the real plugin export pipeline
    (1600px long edge, JPEG q85, then this module's 768px working
    resize) -- see sharpness.py's module docstring for the full
    investigation. The photo itself can't be committed as a test asset
    (personal photo, not synthetic/redistributable per
    tests/fixtures/images.py's module docstring), so this test locks in
    the fix against the real derived scalars instead.
    """
    sharpness_score, blur_confidence = _combine_metrics(
        laplacian_variance=175.8, tenengrad=7242.9, edge_density=0.0839, local_contrast=28.08
    )
    assert blur_confidence >= 0.5  # crosses is_probable_blur's bar
    assert blur_confidence > 0.5  # and specifically: more likely blurry than not


def test_real_world_sharp_photos_stay_unflagged_after_recalibration() -> None:
    """Companion to the false-negative regression above: six real photos
    the plugin correctly did NOT flag as blurry (verified via the live
    database, blur_confidence 0.0-0.02 under the original calibration)
    must still score comfortably low after the recalibration -- otherwise
    the fix for the false negative just traded it for false positives.
    Raw metric values recomputed the same way as the regression test
    above (real plugin export pipeline replicated locally)."""
    known_sharp_raw_metrics = [
        dict(
            laplacian_variance=1272.7, tenengrad=17621.2, edge_density=0.1959, local_contrast=36.75
        ),
        dict(
            laplacian_variance=1018.1, tenengrad=23114.1, edge_density=0.1534, local_contrast=36.76
        ),
        dict(
            laplacian_variance=392.9, tenengrad=11170.1, edge_density=0.1144, local_contrast=35.19
        ),
        dict(
            laplacian_variance=656.4, tenengrad=14061.7, edge_density=0.1457, local_contrast=32.37
        ),
        dict(
            laplacian_variance=1015.9, tenengrad=23346.2, edge_density=0.1559, local_contrast=37.56
        ),
        dict(
            laplacian_variance=1468.4, tenengrad=18691.5, edge_density=0.1998, local_contrast=37.22
        ),
    ]
    for raw in known_sharp_raw_metrics:
        _, blur_confidence = _combine_metrics(**raw)
        assert blur_confidence < 0.3
