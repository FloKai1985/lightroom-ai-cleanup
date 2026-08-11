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

These four raw measurements are combined (min-max normalized against the
population being compared, e.g. within a batch or a group) into:

- `sharpness_score` (`0..1`, higher = sharper)
- `blur_confidence` (`0..1`, higher = more likely blurry)

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
