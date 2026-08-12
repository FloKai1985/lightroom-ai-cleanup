"""Explainable keeper ranking within a duplicate/near-duplicate/burst group.

See docs/algorithms.md §5. Pure function of the group's members — never
reads or writes Lightroom rating/pick/label fields, only reads them as a
tie-breaking *input* to the score (docs/safety.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lr_cleanup.config import get_settings
from lr_cleanup.database.models import Recommendation


@dataclass(frozen=True)
class KeeperWeights:
    sharpness: float
    exposure: float
    technical: float
    existing_preference: float

    @classmethod
    def from_settings(cls) -> KeeperWeights:
        s = get_settings()
        return cls(
            sharpness=s.weight_sharpness,
            exposure=s.weight_exposure,
            technical=s.weight_technical,
            existing_preference=s.weight_existing_preference,
        )


@dataclass(frozen=True)
class KeeperCandidate:
    photo_id: int
    sharpness_score: float
    exposure_score: float
    highlight_clipping: float
    shadow_clipping: float
    megapixels: float
    existing_rating: int | None = None
    existing_pick_status: int | None = None


@dataclass(frozen=True)
class KeeperResult:
    photo_id: int
    keeper_score: float
    rank: int
    recommendation: Recommendation
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _relative(value: float, values: list[float]) -> float:
    lo, hi = min(values), max(values)
    if hi <= lo:
        return 1.0
    return (value - lo) / (hi - lo)


def _clamp_confidence(value: float) -> float:
    """Confidence is never reported as fully certain (0.99 ceiling) or
    fully arbitrary (0.05 floor) — both ends read as overclaiming for a
    heuristic technical estimate (docs/algorithms.md)."""
    return max(0.05, min(0.99, value))


def _preference_score(candidate: KeeperCandidate) -> float:
    rating = candidate.existing_rating
    rating_component = rating / 5 if rating is not None else 0.5
    pick_status = candidate.existing_pick_status
    pick_component = 0.5
    if pick_status is not None:
        pick_component = {1: 1.0, 0: 0.5, -1: 0.0}.get(pick_status, 0.5)
    return 0.5 * rating_component + 0.5 * pick_component


def rank_group(
    candidates: list[KeeperCandidate],
    weights: KeeperWeights | None = None,
    review_margin: float = 0.05,
) -> list[KeeperResult]:
    """Rank `candidates` (all members of one duplicate/near-duplicate group).

    The top-scoring member is `KEEPER`. Members within `review_margin` of
    the top score are `REVIEW` (too close to call automatically); the rest
    are `LIKELY_REDUNDANT`.
    """
    if not candidates:
        return []
    if weights is None:
        weights = KeeperWeights.from_settings()

    sharpness_values = [c.sharpness_score for c in candidates]
    resolution_values = [c.megapixels for c in candidates]

    scored: list[tuple[KeeperCandidate, float, dict[str, float]]] = []
    for c in candidates:
        relative_sharpness = _relative(c.sharpness_score, sharpness_values)
        relative_resolution = _relative(c.megapixels, resolution_values)
        preference = _preference_score(c)

        keeper_score = (
            weights.sharpness * relative_sharpness
            + weights.exposure * c.exposure_score
            + weights.technical * relative_resolution
            + weights.existing_preference * preference
        )
        scored.append(
            (
                c,
                keeper_score,
                {
                    "relative_sharpness": relative_sharpness,
                    "relative_resolution": relative_resolution,
                    "preference": preference,
                },
            )
        )

    scored.sort(key=lambda item: item[1], reverse=True)
    top_score = scored[0][1]

    results: list[KeeperResult] = []
    for rank, (candidate, score, parts) in enumerate(scored, start=1):
        gap_from_top = top_score - score
        if rank == 1:
            recommendation = Recommendation.KEEPER
        elif gap_from_top <= review_margin:
            recommendation = Recommendation.REVIEW
        else:
            recommendation = Recommendation.LIKELY_REDUNDANT

        if rank == 1:
            # Confidence *in the KEEPER pick*: a 0.5 baseline (a lone
            # candidate is never reported as fully certain) plus how far
            # ahead of the runner-up it is — a clear win raises confidence,
            # a near-tie keeps it close to the baseline.
            runner_up_score = scored[1][1] if len(scored) > 1 else top_score
            confidence = _clamp_confidence(0.5 + (top_score - runner_up_score))
        else:
            # Confidence *that this candidate is NOT the keeper*: how far
            # behind the top score it is. A small gap is exactly the
            # REVIEW case (ambiguous -> low confidence); a large gap is
            # clearly LIKELY_REDUNDANT (-> high confidence).
            confidence = _clamp_confidence(gap_from_top)

        reasons: list[str] = []
        if parts["relative_sharpness"] >= 0.999:
            reasons.append("highest_sharpness_in_group")
        elif parts["relative_sharpness"] <= 0.001:
            reasons.append("lowest_sharpness_in_group")
        if candidate.highlight_clipping < 0.01:
            reasons.append("low_highlight_clipping")
        elif candidate.highlight_clipping > 0.05:
            reasons.append("visible_highlight_clipping")
        if candidate.shadow_clipping > 0.05:
            reasons.append("visible_shadow_clipping")
        if parts["relative_resolution"] >= 0.999 and len(candidates) > 1:
            reasons.append("highest_resolution_in_group")
        if candidate.existing_pick_status == 1:
            reasons.append("existing_pick_flag")
        if not reasons:
            reasons.append("balanced_technical_profile")

        results.append(
            KeeperResult(
                photo_id=candidate.photo_id,
                keeper_score=round(score, 4),
                rank=rank,
                recommendation=recommendation,
                confidence=round(confidence, 4),
                reasons=reasons,
            )
        )

    return results
