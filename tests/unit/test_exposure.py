from lr_cleanup.analysis.exposure import compute_exposure_from_gray
from tests.fixtures.images import noisy_gray


def test_overexposed_image_has_high_highlight_clipping() -> None:
    gray = noisy_gray(low=250, high=256, seed=1)
    metrics = compute_exposure_from_gray(gray)
    assert metrics.highlight_clipping > 0.8
    assert metrics.shadow_clipping < 0.05


def test_underexposed_image_has_high_shadow_clipping() -> None:
    gray = noisy_gray(low=0, high=5, seed=2)
    metrics = compute_exposure_from_gray(gray)
    assert metrics.shadow_clipping > 0.8
    assert metrics.highlight_clipping < 0.05


def test_balanced_exposure_has_low_clipping_and_higher_score() -> None:
    balanced = noisy_gray(low=60, high=196, seed=3)
    overexposed = noisy_gray(low=250, high=256, seed=1)

    balanced_metrics = compute_exposure_from_gray(balanced)
    overexposed_metrics = compute_exposure_from_gray(overexposed)

    assert balanced_metrics.highlight_clipping < 0.05
    assert balanced_metrics.shadow_clipping < 0.05
    assert balanced_metrics.exposure_score > overexposed_metrics.exposure_score


def test_exposure_scores_are_bounded_0_to_1() -> None:
    for gray in (noisy_gray(low=250, high=256), noisy_gray(low=0, high=5), noisy_gray()):
        metrics = compute_exposure_from_gray(gray)
        assert 0.0 <= metrics.exposure_score <= 1.0
        assert 0.0 <= metrics.highlight_clipping <= 1.0
        assert 0.0 <= metrics.shadow_clipping <= 1.0
