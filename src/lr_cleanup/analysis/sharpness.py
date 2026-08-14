"""Technical sharpness / probable-blur estimation.

See docs/algorithms.md §3. Four complementary focus measures are computed
on a dimension-normalized grayscale image and combined into two headline
numbers:

- `sharpness_score` (0..1, higher = sharper)
- `blur_confidence` (0..1, higher = more likely blurry)

These are technical estimates, not ground truth — always surface them with
hedged language (`probable_blur`, `high_confidence_blur_candidate`), never
as a certain verdict.

History (see git log for the full detail of each step -- this is the
short version): originally an unweighted mean of four whole-frame
metrics, MVP-tuned by eye against synthetic checkerboard images. A
real-world false negative (a severely out-of-focus outdoor photo scored
blur_confidence=0.055) traced two of the four metrics
(`tenengrad`/`edge_density`) to calibration ceilings that saturated on
*every* real photo regardless of focus -- fixed by raising those
ceilings and switching to a weighted combination favoring
`laplacian_variance`, the metric that best separated real sharp/blurry
photos at the time.

That whole-frame `laplacian_variance` metric was then replaced entirely
with `regional_sharpness` (below), on user request for a way to
correctly classify photos with a genuinely sharp subject against a
deliberately soft background (shallow depth of field) -- whole-frame
averaging was diluting those to a false "somewhat blurry" reading.
First attempt (max-of-tiles / percentile-of-tiles using plain per-tile
Laplacian variance) turned out not to work: real out-of-focus photos
with locally high-contrast content (fence rails, animal markings,
dappled light) could score a single tile *higher* than a genuinely
sharp portrait's best tile, because raw gradient magnitude conflates
contrast with focus at the tile level exactly as it does whole-frame.

What actually discriminates the two: `_crete_blanchard_blur` (Crete et
al., 2007) deliberately re-blurs the image and measures how much local
pixel-to-pixel variation is *destroyed* by that extra blur. A genuinely
sharp edge loses most of its variation when blurred further; an
already-soft, high-contrast region (the blurred fence) doesn't have
much left to lose, however strong its raw gradient magnitude. Applied
per-tile (`regional_sharpness` -- i.e. "is there a real, meaningfully-
sized sharp region," not just one lucky tile). Center-weighting the
tiles (assuming the subject is centered) was tried and measured *worse*
-- real photo composition doesn't reliably put the subject in the exact
center, so it was dropped.

First working version had a real bug, caught only by testing against a
synthetic all-flat image: on an almost perfectly flat tile (a heavily
blurred region, or a real sky/blank-wall patch), the destroyed/total
variation ratio is a small-number-over-small-number division and can
read as confidently *sharp* from aliasing/rounding noise alone, even
though there's essentially nothing there. This wasn't just a synthetic-
test curiosity -- it was quietly responsible for an earlier, over-
optimistic result on the real shallow-DOF portrait case below (a
handful of near-flat sky tiles were scoring spuriously "sharp" and
happened to be what drove that photo's good number, not its actual
in-focus subject). Fixed with a contrast floor (`low_variance_std` in
`_crete_blanchard_blur`): below it, the result blends toward "blurry"
in proportion to how little genuine contrast the region has, rather
than trusting an unreliable ratio.

With that fixed honestly, `regional_sharpness` (3x3 grid -- a coarser
grid than first tried, because smaller tiles give the ratio less data
and made it noisier; 90th percentile) was validated against seven real
photos (recomputed through the actual plugin export pipeline): a
confirmed-blurry photo, five ordinary confirmed-sharp photos, and one
genuinely hard case -- a close-up portrait with a sharp subject against
a deliberately soft background, which whole-frame averaging previously
misclassified as borderline-blurry (blur_confidence 0.47). The honest
result: `regional_sharpness` alone now cleanly separates the hard case
from the confirmed-blurry photo (a real, reproducible +0.109 margin
across parameter choices) and does not regress the five ordinary sharp
photos. It carries most of the combination weight (0.80) since it's the
metric that actually improved this case; `tenengrad`/`edge_density`/
`local_contrast` remain as whole-frame minor contributors, unchanged in
how they're computed. The hard case itself lands close to (not
comfortably clear of) the `high_confidence_blur_threshold` boundary
even after this fix -- a real, honest limitation, not glossed over: a
close-up portrait inherently has less high-frequency detail than a busy
scene even in sharp focus, and this class of hand-crafted metric can
only partially compensate for that. The real photos behind this
calibration can't be committed (personal photos, not redistributable
test assets) -- see `tests/unit/test_sharpness.py`'s regression tests,
which assert against the real recorded metric values instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from lr_cleanup.analysis._imaging import load_bgr, resize_long_edge, to_gray


@dataclass(frozen=True)
class SharpnessMetrics:
    regional_sharpness: float
    tenengrad: float
    edge_density: float
    local_contrast: float
    sharpness_score: float
    blur_confidence: float


# Rough (low, high) ranges used to map each raw metric onto 0..1 before
# combining. Values below `low` clamp to 0.0, above `high` clamp to 1.0.
# Calibrated for grayscale images normalized to ~768px long edge.
_CALIBRATION = {
    "regional_sharpness": (0.5, 0.75),
    "tenengrad": (50.0, 20000.0),
    "edge_density": (0.01, 0.20),
    "local_contrast": (3.0, 35.0),
}

# How much each normalized metric contributes to `sharpness_score`. Must
# sum to 1.0 (enforced by test_sharpness.py). regional_sharpness
# dominates -- it's the metric that actually resolved the shallow-DOF
# false-negative case (see module docstring); the other three are
# whole-frame and known to conflate contrast/texture with real focus, so
# they're kept only as minor tie-breakers.
_WEIGHTS = {
    "regional_sharpness": 0.80,
    "tenengrad": 0.09,
    "edge_density": 0.07,
    "local_contrast": 0.04,
}


def _normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _crete_blanchard_blur(
    gray: np.ndarray, kernel_size: int = 9, low_variance_std: float = 10.0
) -> float:
    """No-reference blur estimate, 0 (sharp) .. 1 (blurry), after Crete et
    al. (2007), "The Blur Effect: Perception and Estimation with a New
    No-Reference Perceptual Blur Metric." Deliberately re-blurs the image
    in each direction independently (a `kernel_size`-tap mean filter,
    applied only horizontally or only vertically) and measures how much
    local pixel-to-pixel variation that additional blur destroys. A real
    edge loses most of its variation when blurred further; a region
    that's already soft (regardless of how much raw contrast/gradient
    magnitude it has) doesn't have much left to lose -- this is what
    lets it discriminate genuine focus from high-contrast-but-blurred
    texture where whole-frame or per-tile gradient magnitude alone
    can't (see module docstring).

    `low_variance_std` guards a real failure mode found via testing: on
    an almost perfectly flat region (a heavily-blurred synthetic image,
    or a real sky/blank-wall tile), the residual pixel-to-pixel variation
    is tiny and can be an aliasing/rounding artifact rather than real
    structure -- the destroyed/total ratio then becomes numerically
    unstable and can read as confidently *sharp* even though there's
    essentially nothing there. Below this contrast floor, the result is
    blended toward "blurry" in proportion to how far below it the
    region's own pixel std is, rather than trusting the ratio outright."""
    gray_f = gray.astype(np.float64)
    kernel = np.ones(kernel_size) / kernel_size
    identity = np.array([1.0])
    blurred_vertically = cv2.sepFilter2D(gray_f, -1, identity, kernel)
    blurred_horizontally = cv2.sepFilter2D(gray_f, -1, kernel, identity)

    def _variation_destroyed(original: np.ndarray, reblurred: np.ndarray, axis: int) -> float:
        original_variation = np.abs(np.diff(original, axis=axis))
        reblurred_variation = np.abs(np.diff(reblurred, axis=axis))
        total_original = original_variation.sum()
        if total_original < 1e-6:
            return 1.0  # no variation to begin with -- treat as blurry, not sharp
        destroyed = np.maximum(0.0, original_variation - reblurred_variation).sum()
        return float(1.0 - destroyed / total_original)

    blur_vertical = _variation_destroyed(gray_f, blurred_vertically, axis=0)
    blur_horizontal = _variation_destroyed(gray_f, blurred_horizontally, axis=1)
    blur = max(blur_vertical, blur_horizontal)

    confidence = min(1.0, float(gray.std()) / low_variance_std)
    blur = blur + (1.0 - blur) * (1.0 - confidence)
    return float(np.clip(blur, 0.0, 1.0))


def _regional_sharpness(gray: np.ndarray, grid: int = 3, percentile: float = 90) -> float:
    """Splits `gray` into a `grid` x `grid` set of tiles, computes
    `1 - _crete_blanchard_blur` (i.e. sharpness) for each, and returns
    the `percentile`-th value across all tiles. A percentile rather than
    the plain max: requires a meaningfully-sized sharp region (several
    tiles), not just one lucky tile, to register as sharp -- see module
    docstring for why center-weighting tiles was tried and dropped.
    `grid=3` (not a finer grid) is also a measured choice: smaller tiles
    give the re-blur ratio less data to work with, making it noisier
    (see `_crete_blanchard_blur`'s `low_variance_std` guard) -- coarser
    tiles were more robust in testing against real photos."""
    height, width = gray.shape
    tile_height, tile_width = height // grid, width // grid
    tile_sharpness_values = []
    for row in range(grid):
        for col in range(grid):
            tile = gray[
                row * tile_height : (row + 1) * tile_height,
                col * tile_width : (col + 1) * tile_width,
            ]
            tile_sharpness_values.append(1.0 - _crete_blanchard_blur(tile))
    return float(np.percentile(tile_sharpness_values, percentile))


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx**2 + gy**2))


def _edge_density(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges > 0))


def _local_contrast(gray: np.ndarray, tile_size: int = 32) -> float:
    h, w = gray.shape
    tile_stds: list[float] = []
    for y in range(0, h - tile_size + 1, tile_size):
        for x in range(0, w - tile_size + 1, tile_size):
            tile = gray[y : y + tile_size, x : x + tile_size]
            tile_stds.append(float(tile.std()))
    if not tile_stds:
        return float(gray.std())
    return float(np.mean(tile_stds))


def _combine_metrics(
    regional_sharpness: float, tenengrad: float, edge_density: float, local_contrast: float
) -> tuple[float, float]:
    """Maps the four raw metrics onto `(sharpness_score, blur_confidence)`
    via `_CALIBRATION` + `_WEIGHTS`. Split out from `compute_sharpness_from_gray`
    so it can be unit-tested directly against real recorded metric values
    (tests/unit/test_sharpness.py) without needing an actual image on disk."""
    normalized = {
        "regional_sharpness": _normalize(regional_sharpness, *_CALIBRATION["regional_sharpness"]),
        "tenengrad": _normalize(tenengrad, *_CALIBRATION["tenengrad"]),
        "edge_density": _normalize(edge_density, *_CALIBRATION["edge_density"]),
        "local_contrast": _normalize(local_contrast, *_CALIBRATION["local_contrast"]),
    }
    sharpness_score = float(sum(_WEIGHTS[key] * value for key, value in normalized.items()))
    blur_confidence = float(np.clip(1.0 - sharpness_score, 0.0, 1.0))
    return sharpness_score, blur_confidence


def compute_sharpness_from_gray(gray: np.ndarray) -> SharpnessMetrics:
    """Compute sharpness metrics from an already normalized grayscale image."""
    regional_sharpness = _regional_sharpness(gray)
    tenengrad = _tenengrad(gray)
    edge_density = _edge_density(gray)
    local_contrast = _local_contrast(gray)

    sharpness_score, blur_confidence = _combine_metrics(
        regional_sharpness, tenengrad, edge_density, local_contrast
    )

    return SharpnessMetrics(
        regional_sharpness=regional_sharpness,
        tenengrad=tenengrad,
        edge_density=edge_density,
        local_contrast=local_contrast,
        sharpness_score=sharpness_score,
        blur_confidence=blur_confidence,
    )


def compute_sharpness(path: str, working_long_edge: int = 768) -> SharpnessMetrics:
    """Load an image from disk and compute its sharpness metrics."""
    image = load_bgr(path)
    image = resize_long_edge(image, working_long_edge)
    gray = to_gray(image)
    return compute_sharpness_from_gray(gray)


def is_high_confidence_blur(metrics: SharpnessMetrics, threshold: float = 0.75) -> bool:
    """Whether `metrics` clears the bar for `high_confidence_blur_candidate`."""
    return metrics.blur_confidence >= threshold


def is_probable_blur(metrics: SharpnessMetrics, threshold: float = 0.5) -> bool:
    """Whether `metrics` clears the lower bar for `probable_blur`."""
    return metrics.blur_confidence >= threshold
