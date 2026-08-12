# Architecture

## System overview

```text
Lightroom Classic
      │
      │ Lua Plugin (lightroom-plugin/AICleanup.lrplugin)
      │ localhost HTTP, JSON
      ▼
Python Local Service (src/lr_cleanup)
      │
      ├── Analysis Engine   (analysis/)   — hashing, sharpness, exposure, grouping, ranking
      ├── SQLite            (database/)   — durable state, never the Lightroom catalog
      └── MCP Server        (mcp_server/) — read + "prepare action" tools for an MCP client
              │
              ▼
        Claude / MCP Client
```

The Lightroom plugin owns everything that requires the Lightroom SDK:
selection, metadata reads, rendition export, plugin-metadata writes,
collection management. The Python service owns everything computational:
image analysis, persistence, ranking, the HTTP contract, and MCP. Python
never talks to Lightroom directly; the plugin never does image analysis.

This split exists because (a) only the Lightroom SDK can safely touch the
catalog, and (b) Lua is a poor fit for numeric image analysis, while Python
has mature, well-tested libraries for it (OpenCV, NumPy, ImageHash).

## Milestones and current status

| Milestone | Scope | Status |
|---|---|---|
| 1 | Standalone Python analyzer: config, DB, hashing, pHash, sharpness, exposure, grouping, keeper ranking, CLI, tests | **Implemented** |
| 2 | FastAPI service: health, jobs, background processing, results | **Implemented** |
| 3 | Lightroom Lua plugin: selection → renditions → job → metadata/collections | **Implemented** (this change) |
| 4 | MCP server: read tools + action preparation | Not started |
| 5 | Optional MCP → Lightroom command polling | Not started, out of core scope |

Directory `src/lr_cleanup/mcp_server/` is intentionally not populated yet.
Creating empty package stubs for code that doesn't exist yet would misstate
progress; it will be added when Milestone 4 starts. `src/lr_cleanup/api/`
deliberately has no `actions.py`, and
`lightroom-plugin/AICleanup.lrplugin/` deliberately has no
`ApplyActions.lua` — see "Milestone-2 component map" and "Milestone-3
component map" below for why.

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

**`api/actions.py` deliberately does not exist yet.** The brief's full HTTP
API list includes `POST /api/v1/actions/prepare`, `GET
/api/v1/actions/pending`, and the `confirm`/`undo` endpoints, but Milestone
2's stated deliverables are health/jobs/background-processing/results only.
Action preparation is the mechanism that will eventually let MCP (Milestone
4) stage a change for human confirmation (docs/safety.md) — building it
ahead of an actual caller (the MCP server) risks guessing at a shape that
doesn't fit. The `PreparedAction`/`ActionLog` tables already exist
(Milestone 1) and are unchanged.

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

**`ApplyActions.lua` does not exist**, matching `api/actions.py` not
existing — same reasoning as Milestone 2's action-preparation deferral,
now also true on the Lua side.

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
