# Architecture

## System overview

```text
Lightroom Classic                              Claude / MCP Client
      │                                                │
      │ Lua Plugin                                     │ stdio (lr-cleanup-mcp)
      │ (lightroom-plugin/AICleanup.lrplugin)          │
      │                                          MCP Server (mcp_server/)
      │                                                │
      │              localhost HTTP, JSON              │ localhost HTTP, JSON
      └───────────────────────┬─────────────────────────┘
                               ▼
                    Python Local Service (src/lr_cleanup/api)
                               │
                    ├── Analysis Engine   (analysis/)   — hashing, sharpness, exposure, grouping, ranking
                    └── SQLite            (database/)   — durable state, never the Lightroom catalog
```

The Lightroom plugin owns everything that requires the Lightroom SDK:
selection, metadata reads, rendition export, plugin-metadata writes,
collection management. The Python FastAPI service owns everything
computational and stateful: image analysis, persistence, ranking, and the
HTTP contract. The MCP server owns translating that HTTP contract into MCP
tools for an MCP client — it is a peer of the Lightroom plugin, not a
component living inside the FastAPI process (see "Milestone-4 component
map" below for why this differs from earlier drafts of this diagram).
Python never talks to Lightroom directly; the plugin never does image
analysis; the MCP server never touches SQLite directly.

This split exists because (a) only the Lightroom SDK can safely touch the
catalog, and (b) Lua is a poor fit for numeric image analysis, while Python
has mature, well-tested libraries for it (OpenCV, NumPy, ImageHash).

## Milestones and current status

| Milestone | Scope | Status |
|---|---|---|
| 1 | Standalone Python analyzer: config, DB, hashing, pHash, sharpness, exposure, grouping, keeper ranking, CLI, tests | **Implemented** |
| 2 | FastAPI service: health, jobs, background processing, results | **Implemented** |
| 3 | Lightroom Lua plugin: selection → renditions → job → metadata/collections | **Implemented** |
| 4 | MCP server: read tools + action preparation | **Implemented** (this change) |
| 5 | Optional MCP → Lightroom command polling | Not started, out of core scope |

`api/actions.py` now exists (prepare/pending/confirm/undo — see the
Milestone-4 component map below) but still has no **apply** endpoint, and
`lightroom-plugin/AICleanup.lrplugin/` still has no `ApplyActions.lua` —
actually applying a confirmed action to Lightroom remains future work.

## Milestone-1 component map

```text
cli.py
  │
  ▼
service/analyzer.py  (orchestration: batch photos through the pipeline)
  │
  ├─ analysis/file_hash.py         SHA-256 of original file
  ├─ analysis/perceptual_hash.py   pHash of a normalized rendition
  ├─ analysis/sharpness.py         Laplacian variance, Tenengrad, edge density, local contrast
  ├─ analysis/exposure.py          histogram, highlight/shadow clipping
  ├─ analysis/candidate_groups.py  exact-duplicate + near-duplicate/burst grouping
  ├─ analysis/keeper.py            explainable keeper ranking within a group
  │
  ▼
database/repository.py  →  database/models.py (SQLAlchemy)  →  SQLite
```

In Milestone 1 there is no Lightroom plugin producing JPEG renditions yet,
so the CLI's `register` command points directly at image files on disk
(e.g. a folder of test JPEGs). `Photo.lightroom_id` and `Photo.preview_path`
are nullable for this reason — they become required-in-practice once the
plugin (Milestone 3) is the thing calling `POST /api/v1/photos/register`
with a real Lightroom-rendered preview path.

## Milestone-2 component map

```text
api/app.py            FastAPI app factory, /health, lifespan (engine + session_factory)
  │
  ├─ api/deps.py       per-request Session/Repository dependency wiring
  ├─ api/jobs.py        POST /api/v1/photos/register, POST /api/v1/jobs, GET /api/v1/jobs/{id}
  └─ api/results.py     GET /api/v1/jobs/{id}/results, GET /api/v1/groups/{id}
       │
       ▼
service/analyzer.py   (unchanged pipeline, now also callable in two phases:
                        create_job() returns immediately, execute_job() runs
                        the actual work)
```

**Background processing**: `POST /api/v1/jobs` persists a `PENDING`
`AnalysisJob` row, commits it explicitly, and schedules a
`BackgroundTasks` callback to run `execute_job`. The background callback
opens its **own** database session via `app.state.session_factory` — it
must not reuse the request's session, since that session's dependency
teardown isn't guaranteed to still be open by the time the background task
runs (standard FastAPI/Starlette guidance). This required splitting
`AnalyzerService.run_job` into `create_job` (fast, synchronous, returns a
job id) and `execute_job` (the actual analysis loop) — the CLI's `run_job`
is now a thin convenience wrapper calling both in sequence.

**Group-to-job association**: `regenerate_groups()` accepts an
`analysis_job_id` and tags every group it creates with it — useful
provenance (`GroupResponse.generated_by_job_id`), but since it's still a
full, idempotent recompute (clearing all previous groups first — see
docs/algorithms.md), there is only ever **one** current group set, not a
private snapshot per job. Filtering `GET /api/v1/jobs/{id}/results` by
`DuplicateGroup.analysis_job_id == id` would make an older job's results go
empty the moment a newer job regenerates groups again — indistinguishable
from a data-loss bug, even though the older job's findings are still just
as valid. Instead, `AnalysisJob.groups_regenerated` (set the moment a job
actually runs regeneration) gates the response: if `True`, the endpoint
returns the *current* full group set (`Repository.list_groups`), which
reflects this job's run unless a later job has regenerated since; if
`False`, the job never touched grouping and the list is always empty. See
`tests/integration/test_api.py::test_older_job_results_stay_populated_after_a_later_regeneration`
for the regression this guards against. Incremental (non-full-recompute)
grouping remains a scale improvement to revisit alongside the
"100,000+ photos" work below — that's a separate concern from this fix.

**`api/actions.py` did not exist as of Milestone 2.** The brief's full HTTP
API list includes `POST /api/v1/actions/prepare`, `GET
/api/v1/actions/pending`, and the `confirm`/`undo` endpoints, but Milestone
2's stated deliverables were health/jobs/background-processing/results
only. Action preparation is the mechanism that lets MCP (Milestone 4)
stage a change for human confirmation (docs/safety.md) — building it ahead
of an actual caller risked guessing at a shape that didn't fit, so it
waited until Milestone 4 actually needed it. See "Milestone-4 component
map" below for what was built and why `confirm`/`apply` still aren't
reachable from MCP.

**SQLite pooling and locking**: Milestone 1's CLI was single-threaded and
sequential, so SQLite's default connection pooling and locking behavior
were never an issue. The API serves each request on a worker thread and
runs background tasks on another, so `database/session.py::make_engine`
now: pins `sqlite:///:memory:` engines to `StaticPool` (one shared
connection across threads — a plain per-thread pool would silently give
each thread its own empty in-memory database); enables
`PRAGMA foreign_keys=ON` / `PRAGMA journal_mode=WAL` on every SQLite
connection for better concurrent read/write behavior; and sets
`PRAGMA busy_timeout=5000` so a second writer retries for up to 5s instead
of immediately raising `database is locked` when it collides with another
in-flight write (e.g. a background job committing progress while a request
handler registers a photo). This meaningfully reduces lock contention at
the concurrency levels this service actually sees (a handful of local
requests/background jobs, not a multi-user server) — it is not a claim
that SQLite is safe under heavy concurrent write load in general; a
higher-throughput deployment would need a different database engine
entirely, which is out of scope for a local single-user tool.

## Milestone-3 component map

```text
Info.lua                 plugin manifest: menu item, metadata provider, plugin-manager panel
  │
  ├─ Metadata.lua          LrMetadataProvider: field definitions (AI Sharpness Score, ...)
  ├─ PluginInfoProvider.lua Plug-in Manager panel: backend URL + "Test Connection"
  │
  └─ AnalyzeSelected.lua   Library > Plug-in Extras menu command — the vertical slice:
       │                     select photos -> export renditions -> register -> create job
       │                     -> poll -> read results -> apply
       │
       ├─ HttpClient.lua    LrHttp + Json wrapper for talking to the FastAPI service
       ├─ Json.lua           vendored pure-Lua JSON codec (SDK has no native JSON)
       └─ ReviewResults.lua  writes AI plugin metadata + files photos into review collections
```

Every non-trivial SDK call in these files is confirmed against real
sources (official Adobe samples, or real working third-party plugins) —
see `docs/lightroom-plugin.md` for the full research trail and citations.
Two corrections that research caught before they became bugs: menu items
under Library > Plug-in Extras go through `LrLibraryMenuItems`, not
`LrExportMenuItems` (which is for the File menu); and
`LrTasks.startAsyncTask` takes `(func, name)`, not `(name, func)`.

**`GET /api/v1/photos/{photo_id}/analysis` was added during this
milestone**, not planned in Milestone 2. The job-results endpoint only
returns photos that ended up in a duplicate/near-duplicate group, but the
plugin needs to write `AI Sharpness Score`/`AI Blur Confidence` metadata
for *every* analyzed photo, including ones that never grouped with
anything. Rather than teach the backend to persist which photo ids
belonged to which job (a schema change with the same "which snapshot is
this" complexity as the groups-staleness issue fixed earlier), the plugin
already knows its own photo ids from the register step and just asks for
each one directly.

**Cache directory**: the plugin exports renditions into
`<system temp>/AICleanupCache/run-<timestamp>/`, one subfolder per
invocation, and deletes it after a successful run — the brief's "the
architecture must allow cache cleanup" requirement, satisfied at the
point that's actually able to know the files are no longer needed (the
Python side never renders anything itself, so it has no cache of its own
to clean up here; `render_cache`/`cache_dir` in `config.py` remains
reserved for a future Python-side cache, unused today).

**Numeric plugin metadata fields are stored as strings.** No source
consulted while building this confirmed a numeric/float `dataType` for
`metadataFieldsForPhotos` — see `docs/lightroom-plugin.md`'s "Remaining
caveats". `AI Sharpness Score` etc. are `dataType = 'string'`, formatted
to 4 decimal places by `ReviewResults.lua`.

**`ApplyActions.lua` still does not exist.** `api/actions.py` now exists
(Milestone 4), so a `PreparedAction` batch can reach `CONFIRMED` — but
nothing polls for confirmed batches and applies them to Lightroom yet.
That's the one piece of the full pipeline
(`MCP -> PreparedAction -> SQLite -> Lightroom plugin -> user confirmation
-> apply`) still unbuilt; see the Milestone-4 component map below.

## Milestone-4 component map

```text
mcp_server/server.py     builds the MCPServer, registers tools, `run()` for stdio
  │
  ├─ mcp_server/client.py  BackendClient — httpx wrapper, the ONLY thing
  │                        tools talk to (no direct DB/repository access)
  └─ mcp_server/tools.py    10 tool functions + their Pydantic response models
       │
       ▼  HTTP, same 127.0.0.1 contract the Lightroom plugin uses
api/app.py  (unchanged)
  ├─ api/actions.py (new)   POST .../prepare, GET .../pending,
  │                          POST .../{batch_id}/confirm, POST .../{batch_id}/undo
  ├─ api/jobs.py (+GET /api/v1/jobs list)
  └─ api/results.py (+GET /api/v1/summary, /api/v1/photos/blurry, /api/v1/groups list)
       │
       ▼
service/action_queue.py (new)   prepare / list_pending / confirm / undo
       │
       ▼
database/repository.py (+action-queue CRUD, +count_*/list_jobs/list_groups(group_types=...))
```

**The MCP server is its own process, not a component inside the FastAPI
process** — despite how the original architecture sketch drew it. Every
tool calls the FastAPI service over HTTP through `BackendClient`, exactly
how the Lightroom plugin calls it. This was a deliberate choice made
while implementing, not a downgrade from some richer original plan: giving
MCP direct repository/SQLite access would mean two different processes
(uvicorn's workers and the MCP stdio process) opening the same SQLite file
independently, on top of the request-thread/background-task pooling
already handled in Milestone 2 — reusing the one HTTP contract that
already exists, that the plugin already proves out, and that already has
`StaticPool`/`WAL`/`busy_timeout` handling is simpler and has no new
concurrency surface to reason about.

**Verifying the MCP SDK, not guessing.** `mcp==2.0.0` (installed per
Milestone 1's dependency list) does not have `mcp.server.fastmcp.FastMCP`
— that class, and the whole `mcp.server.fastmcp` module, is absent from
this version; tutorials referencing it are written against an older SDK.
The current equivalent, confirmed by inspecting the installed package
directly (`from mcp.server.mcpserver import MCPServer`), is
`MCPServer`, registered via the same `@server.tool(...)` decorator
pattern. This was checked with a real Python REPL against the actually
installed package — not assumed from training data — per the project
brief's explicit instruction to verify the current MCP SDK before use.

**Every tool is read-only or "prepare"; none is "confirm" or "apply".**
`prepare_review_collections` and `prepare_markings` create `PENDING`
`PreparedAction` rows; `undo_action_batch` cancels a not-yet-applied batch.
There is no MCP tool that calls `POST /api/v1/actions/{batch_id}/confirm`
— confirmation is deliberately left to a human acting outside MCP (a
future review UI, or a direct API call), consistent with "no destructive
MCP tool may exist" (docs/safety.md), even though `confirm` itself doesn't
touch Lightroom (it only flips `PENDING` -> `CONFIRMED` in SQLite).

**Two incompatible httpx major versions exist in this dependency tree.**
`httpx` (this project's own declared dependency, used by `BackendClient`
for real network calls to `127.0.0.1`) and `httpx2` (Starlette's `TestClient`
is built on it in the installed version) are separate PyPI packages with
separate class hierarchies — a `TestClient` is not an `httpx.Client`.
`BackendClient.__init__`'s `client` parameter accepts either, since it only
ever calls `.request(method, url, **kwargs)` on whatever it's given; this
is what lets `tests/integration/test_mcp_tools.py` exercise real MCP tools
against the real FastAPI app in-process, with no live TCP server, by
passing a `TestClient` in as `client=`.

**A bug the tests caught**: `POST /api/v1/actions/prepare` with an unknown
`photo_id` originally surfaced as a raw SQLite `FOREIGN KEY constraint
failed` `IntegrityError` — a 500 with a database traceback, not a usable
error. `ActionQueueService.prepare` now checks every `photo_id` exists
before writing anything, raising a clean `ActionQueueError` (-> HTTP 400)
instead.

## Post-Milestone-4 refactor pass

A deliberate risk/clarity review across the whole codebase once Milestones
1–4 were feature-complete, rather than only reviewing each milestone's own
diff in isolation. Findings, in order of severity:

- **O(n²) near-duplicate grouping — the most serious finding.**
  `analysis/candidate_groups.py::group_near_duplicates` compared every
  candidate photo against every other one. `_similar()` always rejects a
  pair outside `burst_window_seconds` first, so almost every comparison in
  a large library was wasted work — at the brief's own "100,000+ photos"
  target this is ~5 billion comparisons, not a slow-but-working path but
  an effectively infinite one. Fixed by sorting candidates by capture time
  and comparing each one against a sliding window that stops the moment
  the time gap exceeds the burst window (sorted order guarantees every
  later candidate is even further away). Behavior is identical — same
  edge set, same groups — just without the wasted comparisons; the
  existing test suite passed unchanged, and new tests cover shuffled input
  order and a lone photo that must not bridge two separate clusters.
  Measured: 5,000 photos spread across a year with a few small bursts
  went from a naive-worst-case tens-of-seconds estimate to 0.033s.
- **N+1 queries in every group/photo list endpoint.**
  `GroupResponse` rendering, `GET /api/v1/photos/blurry`, the CLI's
  `groups`/`blurry` commands, and `ActionQueueService.prepare`'s
  photo-existence check all called `repository.get_photo(id)` once per
  row in a loop. Added `Repository.get_photos_by_ids` (one `WHERE id
  IN (...)` query) and switched every one of those call sites to it —
  a page of 500 groups with a few members each went from potentially
  thousands of individual queries to two (one for groups, one for all
  their members' photos).
- **`AnalyzerService._resolve_photos` silently defeated its own batching.**
  `execute_job` is documented (this file's Incremental analysis section)
  as streaming photos from the database in batches rather than
  materializing the whole library — but `_resolve_photos` wrapped
  `iter_all_photos()` in `list(...)`, pulling every `Photo` row into memory
  before the analysis loop even started. Fixed by returning the generator
  directly when analyzing the whole library (a specific `photo_ids` list —
  always a bounded MCP/plugin-submitted batch, never "the whole library" —
  is still resolved eagerly, which is fine at that size).
- **`keeper.py`'s confidence formula was needlessly hard to follow.**
  `second_score`/`raw_confidence` were computed identically on every loop
  iteration but only meaningfully used for the top-ranked candidate,
  collapsing two different semantic questions ("how confident are we this
  is the keeper" vs. "how confident are we this ISN'T the keeper") into
  one unexplained expression. Split into two explicit, commented branches
  with a shared `_clamp_confidence` helper; the math is unchanged (all
  existing tests pass byte-for-byte on their assertions).
- **`HttpClient.lua`'s failure-path assumption, documented rather than
  left implicit.** See docs/lightroom-plugin.md's "Remaining caveats" —
  `checkResponse` assumes `LrHttp` returns a nil body on connection
  failure, which wasn't explicitly confirmed (only the success-path return
  shape was). Every call site's error-protection wrapper makes this safe
  either way; the gap is now written down instead of silently assumed
  correct.

## First real-world Lightroom test: a bug this environment couldn't catch

No Lightroom Classic install was available while Milestones 3–4 and the
refactor pass above were built, so the Lua plugin's verification ceiling
was: correct syntax, correct SDK call shapes (cross-checked against real
sample plugins), and pure-logic unit tests run outside Lightroom
(`Json.lua`, `ReviewResults.lua`'s grouping-reduction logic). None of that
can exercise Lightroom's actual Lua 5.1 runtime semantics.

The first real run inside Lightroom Classic failed immediately with
`Yielding is not allowed within a C or metamethod call` — every `pcall(...)`
wrapping an `HttpClient` call (and, defensively, the two wrapping plain
metadata reads) used Lua's built-in `pcall`, which cannot yield across its
own C-call boundary in Lua 5.1. `LrHttp.get`/`.post` yield internally by
design (that's what keeps Lightroom's UI responsive during network I/O),
so every HTTP call the plugin made was guaranteed to hit this. Fixed by
switching every `pcall` in the plugin to `LrTasks.pcall` — the SDK's
yield-safe equivalent, same call signature, already recorded (but not
connected to this requirement) in docs/lightroom-plugin.md's `LrTasks`
research. Full details and the reasoning for standardizing every call
site rather than reasoning about each one individually are in that doc's
"Asynchronous Lightroom tasks" section.

This is worth naming plainly: the SDK research process in this project
(cross-referencing real sample plugins, verifying signatures) caught
*wrong* assumptions before they shipped, as documented throughout this
file and docs/lightroom-plugin.md — but it cannot substitute for running
the code in the actual target environment, and didn't catch this. Treat
everything under `lightroom-plugin/` as verified-by-reading until it's
been exercised in a real Lightroom Classic install; this was the first
time that happened.

## Differentiating "out of focus" from "likely redundant"

Prompted by real usage: after the first successful end-to-end run, a
photo that was blurry *and* part of a burst could come back with a
`LIKELY_REDUNDANT` recommendation — technically correct (it did lose the
in-group ranking) but a confusing thing to tell a user, since the real
issue (it's out of focus) had nothing to do with the group it happened to
be near in time. Two changes, both covered in docs/algorithms.md §2/§5:

- **`AnalyzerService.regenerate_groups()`** now excludes any photo at/above
  `config.py`'s new `high_confidence_blur_threshold` (default `0.75`) from
  near-duplicate/burst comparison before grouping runs at all — not a
  post-hoc filter on the output, an input filter, so `keeper.py`'s ranking
  never sees these photos and has no opinion about them. Exact-duplicate
  detection is unaffected (it's an unconditional hash-bucket lookup with
  no comparison cost to skip).
- **`ReviewResults.lua::effectiveRecommendation`** composes the
  user-facing label from both signals where they're both available: blur
  wins outright (`OUT_OF_FOCUS`) over whatever a group ranking would have
  said, including for the edge case of a blurry photo that's still part of
  an exact-duplicate group (still possible, since that grouping is
  unconditional). `Recommendation` itself
  (`KEEPER`/`REVIEW`/`LIKELY_REDUNDANT`) was deliberately **not** extended
  with an `OUT_OF_FOCUS` value at the database/API level — it stays scoped
  to "ranking outcome within a group" (no migration needed), and the
  presentation-level composition happens in the one place that already
  has both the blur number and the group result in hand.

## Incremental analysis / scale

Target: libraries with 100,000+ photos, without loading the library into
memory.

- `AnalysisJob` tracks a batch by id; the service processes photos in
  configurable-size chunks (`AnalyzerConfig.batch_size`), streaming from the
  database rather than materializing the whole photo set.
- Each `Analysis` row is keyed by a **fingerprint** derived from
  `(lightroom_id, file_size, file_mtime, analysis_version)`. If an incoming
  photo's fingerprint matches a stored one, the analyzer reuses the cached
  result instead of recomputing it. Bumping `analysis_version` (e.g. after
  changing the sharpness algorithm) invalidates all cached results.
- Grouping and ranking are computed per candidate neighborhood (same
  file-hash bucket, or same burst-window/phash bucket), not as an all-pairs
  comparison across the whole library.

## MCP (forward-looking note, not implemented in Milestone 1)

The current stable MCP Python SDK (`mcp` on PyPI, the reference
`modelcontextprotocol/python-sdk`) exposes a high-level server via
`mcp.server.fastmcp.FastMCP`, where tools are plain Python functions
decorated with `@mcp.tool()` and the server runs over stdio or streamable
HTTP transport. `src/lr_cleanup/mcp_server/` will be built against whatever
is current stable at Milestone 4 — pin and verify the exact version then
rather than trusting this note as gospel.
