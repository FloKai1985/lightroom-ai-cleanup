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

**Milestones 1–3 are implemented**: the standalone analyzer, the local
FastAPI service, and the Lightroom Classic Lua plugin. There is no MCP
server yet — see [`docs/architecture.md`](docs/architecture.md) for the
milestone plan. What exists today: a CLI, a local-only HTTP API, and a
Lightroom plugin that together select photos in Lightroom, render
previews, run the analysis pipeline (sharpness, exposure, hashing) as a
background job, group exact/near-duplicates, produce an explainable
keeper ranking, and write the results back into Lightroom as custom
metadata and review collections — all stored in a local SQLite database,
never in Lightroom's own catalog file.

## Architecture

```text
Lightroom Classic --Lua plugin--> Python service (FastAPI) --> SQLite
                                         |                        ^
                                         v                        |
                                   Analysis Engine          MCP Server <- MCP client
```

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

## Running the Milestone-1 CLI

There is no Lightroom plugin yet, so `register` points directly at a folder
of images on disk (JPEG/PNG/TIFF) instead of Lightroom-rendered previews.

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
```

`api/actions.py` (prepare/confirm/undo) doesn't exist yet — see
[`docs/architecture.md`](docs/architecture.md)'s Milestone-2 component map
for why it's deferred rather than stubbed out.

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
touches `data/lr_cleanup.db`.

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
(default `http://127.0.0.1:8765`) and test the connection. Every SDK call
the plugin makes is cited against an official Adobe sample or a real,
working third-party plugin in
[`docs/lightroom-plugin.md`](docs/lightroom-plugin.md) — read that doc
before modifying anything under `lightroom-plugin/`.

**What it does**: exports a temporary sRGB JPEG (1600px long edge) per
selected photo, registers the photos and previews with the backend, runs
an analysis job, polls for completion, then writes `AI Sharpness Score` /
`AI Blur Confidence` / etc. as custom Lightroom metadata and files photos
into an `AI Photo Cleanup` collection set (`01 – Recommended Keepers`
through `06 – Processed`). It never touches star ratings, color labels,
pick flags, or original files — see [`docs/safety.md`](docs/safety.md).

## Connecting an MCP client

Not applicable yet — the MCP server (Milestone 4) hasn't been implemented.

## Repository layout

```text
lightroom-ai-cleanup/
├── docs/                        architecture, algorithms, safety, plugin SDK research
├── src/lr_cleanup/
│   ├── analysis/                pure functions: hashing, phash, sharpness, exposure, grouping, keeper ranking
│   ├── database/                SQLAlchemy models, repository, Alembic migrations
│   ├── service/                 analyzer.py — orchestrates the pipeline (create_job/execute_job)
│   ├── config.py                all thresholds/weights, env-driven
│   ├── cli.py                   Milestone-1 CLI entry point (`lr-cleanup`)
│   ├── api/                     FastAPI app (`lr-cleanup-server`): app.py, deps.py, jobs.py, results.py
│   └── mcp_server/               MCP tools — Milestone 4, not yet implemented
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

`mcp_server/` is intentionally not populated yet — see
[`docs/architecture.md`](docs/architecture.md) for the milestone-by-milestone
plan and why an empty stub wasn't created ahead of the code that needs it.
`api/actions.py` and `lightroom-plugin/AICleanup.lrplugin/ApplyActions.lua`
deliberately don't exist yet either, for the same reason (see that doc's
Milestone-2 and Milestone-3 component maps).

## Safety

Read [`docs/safety.md`](docs/safety.md) before changing anything related to
Lightroom writes, network binding, or action application. The short version:
no deletion, no catalog writes, no cloud calls, and no AI-driven change to
Lightroom ever happens without an explicit, separate user confirmation.
