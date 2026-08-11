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

**Milestone 1 (standalone Python analyzer) is implemented.** There is no
HTTP service, Lightroom plugin, or MCP server yet — see
[`docs/architecture.md`](docs/architecture.md) for the milestone plan. What
exists today is a CLI that registers a folder of images, analyzes them
(sharpness, exposure, hashing), groups exact/near-duplicates, and produces
an explainable keeper ranking, all stored in a local SQLite database.

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

## Running the tests

```bash
source .venv/bin/activate
pytest                # unit tests, synthetic in-process image fixtures — no personal data needed
ruff check .           # lint
mypy src/lr_cleanup     # type check (src/ only; tests aren't held to the same strictness)
```

## Verifying `/health`

Not applicable yet — the FastAPI service (`GET /health`, job endpoints,
etc.) is Milestone 2 and hasn't been implemented. This section will be
filled in when that lands.

## Installing the Lightroom plugin / connecting an MCP client

Not applicable yet — the Lua plugin (Milestone 3) and MCP server
(Milestone 4) haven't been implemented. See
[`docs/lightroom-plugin.md`](docs/lightroom-plugin.md) for the SDK research
already done in preparation for Milestone 3.

## Repository layout

```text
lightroom-ai-cleanup/
├── docs/                        architecture, algorithms, safety, plugin SDK research
├── src/lr_cleanup/
│   ├── analysis/                pure functions: hashing, phash, sharpness, exposure, grouping, keeper ranking
│   ├── database/                SQLAlchemy models, repository, Alembic migrations
│   ├── service/                 analyzer.py — orchestrates the pipeline (Milestone 1)
│   ├── config.py                all thresholds/weights, env-driven
│   ├── cli.py                   Milestone-1 CLI entry point (`lr-cleanup`)
│   ├── api/                     FastAPI app — Milestone 2, not yet implemented
│   └── mcp_server/               MCP tools — Milestone 4, not yet implemented
├── lightroom-plugin/            Lua plugin — Milestone 3, not yet implemented
├── tests/
│   ├── unit/                    pytest, synthetic fixtures only
│   └── fixtures/                in-process synthetic image generators (no binary test assets)
└── scripts/                     run-server.sh / install-plugin.sh — added with Milestones 2–3
```

`api/`, `mcp_server/`, `lightroom-plugin/AICleanup.lrplugin/`, and
`scripts/` are intentionally not populated yet — see
[`docs/architecture.md`](docs/architecture.md) for the milestone-by-milestone
plan and why empty stubs weren't created ahead of the code that needs them.

## Safety

Read [`docs/safety.md`](docs/safety.md) before changing anything related to
Lightroom writes, network binding, or action application. The short version:
no deletion, no catalog writes, no cloud calls, and no AI-driven change to
Lightroom ever happens without an explicit, separate user confirmation.
