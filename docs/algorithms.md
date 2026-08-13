# Algorithms

All thresholds and weights referenced here live in `config.py` /
environment variables — never hardcoded inside the algorithm modules
themselves. Values below are the shipped starting defaults, not universal
truths; expect to retune them against real libraries.

## 1. Exact duplicate detection (`analysis/file_hash.py`)

`SHA-256` over the raw bytes of the **original** file (not the rendered
JPEG preview). Two photos belong to the same `exact_duplicate` group iff
their `file_hash` matches and they are distinct `Photo` rows.

Lightroom **virtual copies** share the same original file by definition, so
a naive file-hash match would put every virtual copy of a photo into an
"exact duplicate" group. That is wrong — a virtual copy is a deliberate
Lightroom construct, not a redundant file. `is_virtual_copy` photos are
therefore excluded from exact-duplicate grouping entirely (see
`analysis/candidate_groups.py::group_exact_duplicates`).

## 2. Near-duplicate / burst detection (`analysis/perceptual_hash.py`,
`analysis/candidate_groups.py`)

MVP signal, deliberately not ML-based:

1. **Capture-time proximity** — photos within `burst_window_seconds` (default
   `10`) of each other are candidates for the same burst.
2. **Perceptual hash (pHash)** — computed via `ImageHash.phash` on the
   normalized rendition (long edge resized consistently before hashing so
   differently-sized inputs are compared fairly). Two photos are visually
   similar if their Hamming distance is `<= phash_max_distance` (default
   `8`).
3. **Dimensions / aspect ratio** — photos with a substantially different
   aspect ratio are not grouped together even if the hash distance is low
   (guards against false positives from hash collisions across unrelated
   content).

Grouping is a single-linkage clustering over the candidate graph: an edge
exists between two photos if all three conditions hold; a `near_duplicate`
or `burst` group is a connected component. A group where every edge is also
within the tightest time window is labeled `burst`; a looser visually-similar
cluster spanning a longer time range is labeled `near_duplicate`. The exact
burst-vs-near-duplicate split is a heuristic — see `clustering.py` docstring
for the precise rule.

Explicitly deferred: CLIP or any other learned embedding model. This is a
scope boundary, not an oversight — revisit only after the MVP heuristics are
validated against real-world false-positive/negative rates.

**Blur pre-filter.** Before candidates are compared, any photo whose
`blur_confidence` is at/above `high_confidence_blur_threshold` (default
`0.55`, `config.py`) is excluded from near-duplicate/burst comparison
entirely — it still participates in exact-duplicate detection (§1), which
is unconditional. This is deliberate, not a scale shortcut: a
high-confidence-blur photo's "reason for cleanup" is that it's out of
focus, full stop. Letting it into a burst group would mean it could come
back labeled `LIKELY_REDUNDANT` ("worse than its sharper sibling") or even
`KEEPER`, both of which bury the actual, more useful signal — see §5's
"Blur takes priority" note for how this plays out in the final
recommendation.

## 3. Sharpness (`analysis/sharpness.py`)

Four basic, complementary technical measurements, computed on a
normalized-size grayscale conversion of the rendition (images are resized
to a common working resolution first so scores are comparable across photos
of different source resolutions):

- **Variance of Laplacian** — classic focus-measure operator; low variance
  correlates with blur.
- **Tenengrad / Sobel gradient magnitude** — mean squared gradient
  magnitude; complements Laplacian variance, less sensitive to noise.
- **Edge density** — fraction of pixels detected as edges (Canny), a coarse
  proxy for how much fine detail survived.
- **Local contrast** — standard deviation of local mean-subtracted
  intensity over small tiles; low local contrast across the frame is
  consistent with softness/blur.

Each raw measurement is min-max normalized against a fixed calibration range
(`_CALIBRATION` in `sharpness.py`, not relative to the current batch/group),
clamped to `0..1`, then combined as a **weighted** average (`_WEIGHTS`) —
not a plain mean — into:

- `sharpness_score` (`0..1`, higher = sharper)
- `blur_confidence` (`0..1`, higher = more likely blurry)

The weighting (laplacian_variance 0.45, tenengrad 0.25, edge_density 0.20,
local_contrast 0.10) and the calibration ranges themselves were
recalibrated after a real-world false negative: a severely out-of-focus
outdoor photo scored `blur_confidence=0.055` under the original
unweighted mean. Root cause: `tenengrad` and `edge_density`'s calibration
ceilings were tuned against low-detail synthetic test images and
saturated to `1.0` for *every* real photo tested (sharp or blurry) —
dappled sunlight/foliage generates strong gradient energy independent of
actual focus. With those two metrics stuck voting "sharp" regardless of
input, they diluted `laplacian_variance` — the metric that actually
separated the real blurry/sharp photos tested — 3-to-1 in an unweighted
mean. See `sharpness.py`'s module docstring for the full investigation
and `tests/unit/test_sharpness.py`'s regression tests (which assert
against the real recorded metric values, since the photo itself isn't a
redistributable test asset).

`blur_confidence` is presented with hedged terminology
(`probable_blur`, `high_confidence_blur_candidate`) — it is a technical
estimate, never asserted as fact. **Relative** sharpness within a
near-duplicate group is weighted more heavily by the keeper ranker than the
absolute score, because "sharpest of this burst" is a much more reliable
signal than "sharp in isolation."

## 4. Exposure (`analysis/exposure.py`)

Purely technical histogram signals — no aesthetic judgment:

- **Highlight clipping** — fraction of pixels at or near the sensor's
  maximum value (per-channel, on the rendered JPEG).
- **Shadow clipping** — fraction of pixels at or near zero.
- **Histogram distribution** — basic spread/skew statistics used to derive
  `exposure_score` (`0..1`, higher = more technically balanced exposure).

## 5. Keeper ranking (`analysis/keeper.py`)

Computed **within** a duplicate/near-duplicate/burst group only — it is a
relative ranking, not a standalone quality score. Default weights (all
configurable, must sum to `1.0`):

```text
55%  sharpness      (relative sharpness_score within the group)
25%  exposure       (exposure_score, penalized by clipping)
10%  technical integrity / resolution  (rendition/original resolution, corruption-free decode)
10%  existing Lightroom preference     (tie-breaker only: existing rating / pick status)
```

Every `GroupMember` gets a `keeper_score` (`0..1`), a `rank` within the
group, a `recommendation` (`KEEPER`, `REVIEW`, `LIKELY_REDUNDANT`), a
`confidence`, and a machine-readable `reasons` list, e.g.:

```json
{
  "recommendation": "KEEPER",
  "keeper_score": 0.91,
  "reasons": ["highest_sharpness_in_group", "low_highlight_clipping"]
}
```

The "existing Lightroom preference" term is a **tie-breaker input to the
score**, not a bypass of the safety rules — it only ever *reads*
`existing_rating` / `existing_pick_status`; the ranker never writes to
them.

**Blur takes priority over group-based ranking.** `Recommendation`
(`KEEPER`/`REVIEW`/`LIKELY_REDUNDANT`) is computed purely from ranking
within a group — `keeper.py` has no notion of blur at all, and doesn't
need one, because §2's blur pre-filter means a high-confidence-blur photo
is never in a near-duplicate/burst group to begin with (it can still be
the "best of" an exact-duplicate group, since that grouping stays
unconditional). The final, user-facing label a photo gets — including the
`OUT_OF_FOCUS` marking distinct from `LIKELY_REDUNDANT` — is composed one
layer up, where both signals are available together:
`ReviewResults.lua::effectiveRecommendation` in the Lightroom plugin. Blur
wins there too: a blurry photo that happens to also be an exact duplicate
still reads `OUT_OF_FOCUS`, never `KEEPER`, even though its
exact-duplicate group ranking might otherwise have called it one. This
keeps "why does this photo need attention" unambiguous — a photo is never
labeled both "worse than its duplicate" and "out of focus" at once.

**Every analyzed photo gets a label, never a blank field.**
`effectiveRecommendation` returns one of exactly five values:
`OUT_OF_FOCUS`, `KEEPER`, `REVIEW`, `LIKELY_REDUNDANT`, or — for a photo
that's sharp and isn't part of any duplicate/near-duplicate group, which
previously fell through to a blank `AI Recommendation` — `UNIQUE`.
`UNIQUE` is deliberately a distinct value from `KEEPER`, not a synonym:
`KEEPER` means "won a comparison against at least one other photo";
`UNIQUE` means "had nothing to compare against." Collapsing the two would
overstate what the analysis actually found for a photo that was simply
never in contention. This is a plugin-layer guarantee, not a backend
one — the API's `GroupResponse`/`PhotoAnalysisResponse` are unaffected;
`GET /api/v1/photos/{id}/analysis` still has no `recommendation` field at
all for an ungrouped photo, since the backend has genuinely computed
nothing to report there. `UNIQUE` is what the plugin says on the backend's
behalf once it also knows the photo wasn't flagged as blurry.
