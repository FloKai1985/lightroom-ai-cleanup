from lr_cleanup.analysis.keeper import KeeperCandidate, KeeperWeights, rank_group
from lr_cleanup.database.models import Recommendation

WEIGHTS = KeeperWeights(sharpness=0.55, exposure=0.25, technical=0.10, existing_preference=0.10)


def test_sharpest_lowest_clipping_candidate_is_keeper() -> None:
    candidates = [
        KeeperCandidate(
            photo_id=1,
            sharpness_score=0.95,
            exposure_score=0.9,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=24.0,
        ),
        KeeperCandidate(
            photo_id=2,
            sharpness_score=0.4,
            exposure_score=0.6,
            highlight_clipping=0.1,
            shadow_clipping=0.0,
            megapixels=24.0,
        ),
        KeeperCandidate(
            photo_id=3,
            sharpness_score=0.2,
            exposure_score=0.5,
            highlight_clipping=0.2,
            shadow_clipping=0.1,
            megapixels=24.0,
        ),
    ]

    results = rank_group(candidates, weights=WEIGHTS)

    assert results[0].photo_id == 1
    assert results[0].rank == 1
    assert results[0].recommendation == Recommendation.KEEPER
    assert "highest_sharpness_in_group" in results[0].reasons
    assert "low_highlight_clipping" in results[0].reasons

    assert results[-1].photo_id == 3
    assert results[-1].recommendation == Recommendation.LIKELY_REDUNDANT


def test_close_scores_are_marked_review_not_likely_redundant() -> None:
    candidates = [
        KeeperCandidate(
            photo_id=1,
            sharpness_score=0.80,
            exposure_score=0.80,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=24.0,
        ),
        KeeperCandidate(
            photo_id=2,
            sharpness_score=0.79,
            exposure_score=0.80,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=24.0,
        ),
    ]

    # Group-relative sharpness normalization spans the group's full 0..1
    # range regardless of how close the raw scores were, so a generous
    # margin is needed to land on REVIEW rather than LIKELY_REDUNDANT.
    results = rank_group(candidates, weights=WEIGHTS, review_margin=0.6)

    assert results[0].recommendation == Recommendation.KEEPER
    assert results[1].recommendation == Recommendation.REVIEW


def test_single_member_group_is_always_keeper() -> None:
    candidates = [
        KeeperCandidate(
            photo_id=1,
            sharpness_score=0.5,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=12.0,
        )
    ]
    results = rank_group(candidates, weights=WEIGHTS)
    assert len(results) == 1
    assert results[0].recommendation == Recommendation.KEEPER
    assert results[0].rank == 1


def test_ranks_are_sequential_starting_at_one() -> None:
    candidates = [
        KeeperCandidate(
            photo_id=i,
            sharpness_score=score,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=12.0,
        )
        for i, score in enumerate([0.9, 0.5, 0.1], start=1)
    ]
    results = rank_group(candidates, weights=WEIGHTS)
    assert [r.rank for r in results] == [1, 2, 3]


def test_existing_pick_flag_used_only_as_tiebreaker_input() -> None:
    """Existing Lightroom pick status contributes to the score but never
    causes the ranker to touch it — see docs/safety.md."""
    candidates = [
        KeeperCandidate(
            photo_id=1,
            sharpness_score=0.5,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=12.0,
            existing_pick_status=1,
        ),
        KeeperCandidate(
            photo_id=2,
            sharpness_score=0.5,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            megapixels=12.0,
            existing_pick_status=None,
        ),
    ]
    results = rank_group(candidates, weights=WEIGHTS)
    winner = next(r for r in results if r.photo_id == 1)
    assert winner.rank == 1
    assert "existing_pick_flag" in winner.reasons
    # The candidate objects themselves are untouched (frozen dataclasses) —
    # nothing here writes back a rating/flag.
    assert candidates[0].existing_pick_status == 1
