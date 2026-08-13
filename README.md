# Lightroom AI Cleanup

A privacy-first local tool for Adobe Lightroom Classic that flags probable
technically blurry images, exact file duplicates, and visually
near-identical images / burst sequences — and recommends the technically
strongest "keeper" within each group.

**The system never deletes photos, never modifies originals, and never
writes directly to Lightroom's catalog.** Everything runs locally; nothing
is sent to the cloud. See [`docs/safety.md`](docs/safety.md) for the full,
mandatory constraint list.

## Status

**Milestones 1–4 are implemented**: the standalone analyzer, the local
FastAPI service, the Lightroom Classic Lua plugin, and the MCP server —
see [`docs/architecture.md`](docs/architecture.md) for the milestone plan.
What exists today: a CLI, a local-only HTTP API, a Lightroom plugin, and
an MCP server that together select photos in Lightroom, render previews,
run the analysis pipeline (sharpness, exposure, hashing) as a background
job, group exact/near-duplicates, produce an explainable keeper ranking,
write the results back into Lightroom as custom metadata and review
collections, and expose all of it to an MCP client (e.g. Claude Desktop)
for querying and staging (never applying) changes — all stored in a local
SQLite database, never in Lightroom's own catalog file.

## Architecture

```text
Lightroom Classic --Lua plugin--\                    /-- Claude / MCP client
                                  >-- FastAPI service <
                     MCP server -/     (+ SQLite)      \-- (none yet: direct API use)
```

Both the Lightroom plugin and the MCP server are separate processes that
talk to the one FastAPI service over `127.0.0.1` HTTP — neither has direct
database access. See [`docs/architecture.md`](docs/architecture.md) for
the full diagram and reasoning.

Details: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/algorithms.md`](docs/algorithms.md) ·
[`docs/lightroom-plugin.md`](docs/lightroom-plugin.md) ·
[`docs/safety.md`](docs/safety.md)

## Requirements

- macOS
- Python **3.12** (the pinned target — see note below if you don't have it)
- No other system dependencies; `opencv-python-headless` and `Pillow` ship
  their own prebuilt binaries.

If you don't have Python 3.12 installed:

```bash
brew install python@3.12
```

## Local setup (macOS)

From the repository root:

```bash
# 1. Create and activate a virtualenv on Python 3.12
/opt/homebrew/bin/python3.12 -m venv .venv   # Apple Silicon Homebrew path;
                                              # use /usr/local/bin/python3.12 on Intel Macs
source .venv/bin/activate

# 2. Install the project with dev dependencies
pip install -U pip
pip install -e ".[dev]"

# 3. Copy the example environment file (defaults are safe/local-only)
cp .env.example .env

# 4. Create the local SQLite database (applies Alembic migrations)
alembic upgrade head
```

This creates `data/lr_cleanup.db` and `data/render_cache/` (both
git-ignored — they're local runtime state, never checked in).

## Running the standalone CLI

For working against a folder of images directly (JPEG/PNG/TIFF), without
Lightroom in the loop at all — `register` points at the files on disk
instead of Lightroom-rendered previews.

```bash
# Register every image under a folder as a "Photo"
lr-cleanup register /path/to/some/photos

# Run the analysis pipeline (hashing, sharpness, exposure) over everything
# registered so far. Safe to re-run — unchanged photos are read from cache.
lr-cleanup analyze

# Recompute exact-duplicate / near-duplicate / burst groups and keeper
# rankings from the latest analysis results. Idempotent.
lr-cleanup group

# List the current groups with per-photo rank, recommendation, and reasons
lr-cleanup groups

# List photos flagged as probable blur (blur_confidence >= threshold)
lr-cleanup blurry --threshold 0.5
```

Example `groups` output:

```text
Group #2 [burst]
  rank=1 KEEPER           score=0.700 conf=0.50 reasons=['highest_sharpness_in_group', ...] path=/…/IMG_001.jpg
  rank=2 REVIEW           score=0.700 conf=0.05 reasons=['highest_sharpness_in_group', ...] path=/…/IMG_002.jpg
  rank=3 LIKELY_REDUNDANT score=0.215 conf=0.48 reasons=['lowest_sharpness_in_group', ...]  path=/…/IMG_003.jpg
```

All thresholds/weights used above come from `.env` /
[`src/lr_cleanup/config.py`](src/lr_cleanup/config.py) — see
[`docs/algorithms.md`](docs/algorithms.md) for what each one means.

## Running the local FastAPI service

```bash
source .venv/bin/activate
scripts/run-server.sh      # or: lr-cleanup-server
```

This binds to `127.0.0.1:8765` by default (see `.env` /
[`src/lr_cleanup/config.py`](src/lr_cleanup/config.py)) and refuses to
start if `LR_CLEANUP_HOST` is set to anything non-loopback — see
[`docs/safety.md`](docs/safety.md). It creates/updates
`data/lr_cleanup.db` on startup the same way the CLI does.

Interactive API docs are served at <http://127.0.0.1:8765/docs> once running.

### Verifying `/health`

```bash
curl -s http://127.0.0.1:8765/health
# {"status":"ok","version":"0.1.0","database":"ok"}
```

### Example: register a photo, run a job, fetch results

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/photos/register \
  -H 'content-type: application/json' \
  -d '{"photos":[{"original_path":"/absolute/path/to/photo.jpg","file_size":123456,"file_mtime":1730000000.0}]}'

curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'content-type: application/json' -d '{"regenerate_groups": true}'
# => {"job_id": "...", "status": "pending", ...} — analysis runs in the background

curl -s http://127.0.0.1:8765/api/v1/jobs/<job_id>
curl -s http://127.0.0.1:8765/api/v1/jobs/<job_id>/results
curl -s http://127.0.0.1:8765/api/v1/groups/<group_id>
curl -s http://127.0.0.1:8765/api/v1/summary
```

Also available: `GET /api/v1/jobs` (list), `GET /api/v1/groups`
(list, filterable by repeated `?group_type=`), `GET /api/v1/photos/blurry`,
and the action queue (`POST /api/v1/actions/prepare`,
`GET /api/v1/actions/pending`, `POST /api/v1/actions/{batch_id}/confirm`,
`POST /api/v1/actions/{batch_id}/undo`) — see
[`docs/safety.md`](docs/safety.md) for what "prepare"/"confirm" do and
don't do (nothing here ever applies a change to Lightroom; that requires
plugin-side work that doesn't exist yet).

## Running the tests

```bash
source .venv/bin/activate
pytest                # unit + integration tests — synthetic fixtures / in-memory DB, no personal data needed
ruff check .           # lint
mypy src/lr_cleanup     # type check (src/ only; tests aren't held to the same strictness)
```

`tests/integration/` exercises the real FastAPI app via `TestClient` against
an isolated in-memory SQLite database per test (see
[`tests/integration/conftest.py`](tests/integration/conftest.py)) — nothing
touches `data/lr_cleanup.db`. `test_mcp_tools.py` goes one step further:
real MCP tool calls (`MCPServer.call_tool`) through a real `BackendClient`
against that same in-process app, so the MCP layer is tested end-to-end
without a live TCP server or a real MCP client process.

## Installing the Lightroom plugin

The backend must be running first (`scripts/run-server.sh`). Then:

```bash
scripts/install-plugin.sh
```

This checks the plugin folder looks valid and reveals it in Finder; there
is no scriptable way to actually register a plugin with Lightroom
Classic — Adobe's only supported mechanism is the UI:

1. In Lightroom Classic: **File > Plug-in Manager…**
2. Click **Add**, then select `lightroom-plugin/AICleanup.lrplugin`
   (the absolute path is printed by the script above).
3. Select one or more photos in the Library, then run
   **Library > Plug-in Extras > AI Cleanup: Analyze Selected Photos**.

The Plug-in Manager's "AI Cleanup" section lets you change the backend URL
(default `http://127.0.0.1:8765`), test the connection, and — under
"Detection & Classification Thresholds" — tune every threshold and
keeper-ranking weight the analysis uses (burst window, similarity
distance, blur confidence threshold, low sharpness threshold, exposure
clipping thresholds, and the four keeper weights). Most of these are
stored locally in the plugin, sent with every analysis job, and take
effect immediately on the next run — no backend restart needed. The low
sharpness threshold is the one exception: it's plugin-local only (it
doesn't affect backend grouping, only which label/collection a photo
lands in) but lives in the same panel for a single place to configure
everything. "Reset to Defaults" restores the shipped values. An invalid
combination (e.g. keeper weights not summing to 1.0) is rejected with a
dialog the next time you run analysis, not in the panel itself. Every SDK
call the plugin makes is cited against an official Adobe sample or a
real, working third-party plugin in
[`docs/lightroom-plugin.md`](docs/lightroom-plugin.md) — read that doc
before modifying anything under `lightroom-plugin/`.

**What it does**: exports a temporary sRGB JPEG (1600px long edge) per
selected photo, registers the photos and previews with the backend, runs
an analysis job, polls for completion, then writes `AI Sharpness Score` /
`AI Blur Confidence` / etc. as custom Lightroom metadata and files photos
into an `AI Photo Cleanup` collection set (`01 – Recommended Keepers`
through `06 – Processed`, plus `02a – Low Sharpness`). Every analyzed
photo gets exactly one `AI Recommendation` value — `OUT_OF_FOCUS`,
`LOW_SHARPNESS` (noticeably soft but not blurry enough to clear the
OUT_OF_FOCUS bar), `KEEPER`, `REVIEW`, `LIKELY_REDUNDANT`, or `UNIQUE`
(analyzed, sharp, nothing to compare it against) — never blank. It never
touches star ratings, color labels, pick flags, or original files — see
[`docs/safety.md`](docs/safety.md).

## Running the MCP server

The backend must be running first (`scripts/run-server.sh`). The MCP
server is a separate process that talks to it over the same `127.0.0.1`
HTTP API the Lightroom plugin uses — it has no direct database access.

```bash
source .venv/bin/activate
lr-cleanup-mcp
```

This starts an MCP server on stdio — it's meant to be launched by an MCP
client, not run interactively. To test it manually with the official
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
mcp dev src/lr_cleanup/mcp_server/server.py:server
```

(`mcp[cli]` is a dev dependency — installed via `pip install -e ".[dev]"`.
`mcp dev` needs `npx`/Node.js to launch the Inspector's web UI.)

### Connecting Claude Desktop

Add to Claude Desktop's MCP config (**Settings > Developer > Edit Config**):

```json
{
  "mcpServers": {
    "lightroom-ai-cleanup": {
      "command": "/absolute/path/to/lightroom-ai-cleanup/.venv/bin/lr-cleanup-mcp"
    }
  }
}
```

### What it exposes

10 tools — `lightroom_cleanup_status`, `list_analysis_jobs`,
`get_analysis_summary`, `find_blurry_photos`, `find_exact_duplicates`,
`find_near_duplicates`, `get_duplicate_group`, `prepare_review_collections`,
`prepare_markings`, `undo_action_batch`. Every list-returning tool is
limit/offset-paginated with an enforced max page size. **No tool can
confirm or apply anything to Lightroom** — `prepare_*` tools only ever
stage a `PENDING` action batch in SQLite; `undo_action_batch` only ever
cancels a not-yet-applied one. See
[`docs/safety.md`](docs/safety.md)'s Milestone-4 scope note and
[`docs/architecture.md`](docs/architecture.md)'s Milestone-4 component map
for the full reasoning, including how the current MCP Python SDK API was
verified (not assumed) before use.

## Repository layout

```text
lightroom-ai-cleanup/
├── docs/                        architecture, algorithms, safety, plugin SDK research
├── src/lr_cleanup/
│   ├── analysis/                pure functions: hashing, phash, sharpness, exposure, grouping, keeper ranking
│   ├── database/                SQLAlchemy models, repository, Alembic migrations
│   ├── service/                 analyzer.py — orchestrates the pipeline (create_job/execute_job)
│   ├── config.py                all thresholds/weights, env-driven
│   ├── cli.py                   standalone CLI entry point (`lr-cleanup`)
│   ├── api/                     FastAPI app (`lr-cleanup-server`): app.py, deps.py, jobs.py, results.py, actions.py
│   └── mcp_server/               MCP server (`lr-cleanup-mcp`): server.py, client.py, tools.py
├── lightroom-plugin/AICleanup.lrplugin/
│   ├── Info.lua                 plugin manifest: menu item, metadata provider, manager panel
│   ├── AnalyzeSelected.lua       the vertical slice: select -> export -> register -> job -> apply
│   ├── ReviewResults.lua         writes AI metadata + files photos into review collections
│   ├── Metadata.lua              custom metadata field definitions
│   ├── HttpClient.lua            LrHttp + JSON wrapper for the backend
│   ├── Json.lua                  vendored pure-Lua JSON codec (SDK has none natively)
│   └── PluginInfoProvider.lua    Plug-in Manager panel: backend URL + connection test
├── tests/
│   ├── unit/                    pytest, synthetic fixtures only
│   ├── integration/              FastAPI TestClient against an isolated in-memory DB
│   └── fixtures/                in-process synthetic image generators (no binary test assets)
└── scripts/                     run-server.sh, install-plugin.sh
```

`api/actions.py` has no **apply** endpoint, and
`lightroom-plugin/AICleanup.lrplugin/` has no `ApplyActions.lua` — actually
applying a confirmed action to Lightroom is the one piece of the full
`MCP -> PreparedAction -> SQLite -> Lightroom plugin -> user confirmation
-> apply` pipeline still unbuilt. See
[`docs/architecture.md`](docs/architecture.md)'s Milestone-4 component map
for why, and why that's a deliberate stopping point, not an oversight.

## Safety

Read [`docs/safety.md`](docs/safety.md) before changing anything related to
Lightroom writes, network binding, or action application. The short version:
no deletion, no catalog writes, no cloud calls, and no AI-driven change to
Lightroom ever happens without an explicit, separate user confirmation.
