from lr_cleanup.analysis.sharpness import compute_sharpness_from_gray, is_probable_blur
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
