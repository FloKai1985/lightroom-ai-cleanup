# CLAUDE.md

Guidance for Claude Code (or any AI agent) working in this repository.

## What this project is

A privacy-first local tool for Adobe Lightroom Classic that flags probable
blur, exact duplicates, and near-duplicate/burst sequences, and recommends a
technical "keeper" within each group. See `docs/safety.md` before touching
anything that writes to Lightroom or to originals — the constraints there are
non-negotiable product requirements, not suggestions.

## Hard safety rules (see docs/safety.md for full detail)

- Never delete, move, or modify original image files.
- Never write directly into Lightroom's `.lrcat` SQLite database. All
  Lightroom interaction goes through the Lua plugin using official SDK calls.
- Never overwrite an existing star rating, color label, or pick/reject flag
  without an explicit, separate user-confirmed action.
- Never send images or renditions to a cloud service or external AI API.
- No MCP tool may directly mutate Lightroom state. MCP tools only read data
  and write `PreparedAction` rows; a human confirms before the plugin applies
  anything.
- The HTTP API binds to `127.0.0.1` only. Do not change the default bind
  host.

## Architecture

```
Lightroom Classic --Lua plugin--\                    /-- Claude / MCP client
                                  >-- FastAPI service <
                     MCP server -/     (+ SQLite)      \
```

The Lua plugin owns all Lightroom interaction (selection, renditions,
metadata, collections). Python's FastAPI service owns analysis,
persistence, ranking, and the HTTP API. The MCP server is a separate
process — a peer of the Lua plugin, not code living inside the FastAPI
process — that translates that same HTTP API into MCP tools. Neither the
plugin nor the MCP server ever gets direct database access; both only ever
talk to the one process that owns SQLite, over `127.0.0.1` HTTP.

## Repository layout

- `src/lr_cleanup/analysis/` — pure, testable image analysis functions
  (hashing, sharpness, exposure, grouping, keeper ranking). No I/O beyond
  reading the files/paths given to them.
- `src/lr_cleanup/database/` — SQLAlchemy models, repository, Alembic
  migrations. This is the single source of truth on disk; it is never the
  Lightroom catalog.
- `src/lr_cleanup/service/` — orchestration: `analyzer.py` batches photos
  through the analysis pipeline; `action_queue.py` implements
  prepare/list/confirm/undo for the action queue (never "apply").
- `src/lr_cleanup/cli.py` — standalone CLI entry point for running analysis
  without the HTTP/plugin layers.
- `src/lr_cleanup/api/` — FastAPI app: `jobs.py`, `results.py`,
  `actions.py`. No **apply** endpoint yet — see `docs/architecture.md`'s
  Milestone-4 component map for why.
- `src/lr_cleanup/mcp_server/` — MCP server (`lr-cleanup-mcp`):
  `server.py` builds it, `tools.py` defines the 10 tools, `client.py` is
  the HTTP client tools use to reach the FastAPI service (no direct DB
  access — see `docs/architecture.md`'s Milestone-4 component map for why
  this is a separate process rather than sharing the FastAPI process).
- `lightroom-plugin/AICleanup.lrplugin/` — Lua plugin. Every SDK call it
  makes is cited in `docs/lightroom-plugin.md` against an official Adobe
  sample or a real working third-party plugin — read that doc, and its
  "Remaining caveats" section, before adding a new SDK call rather than
  guessing at one. No `ApplyActions.lua` yet, matching the API's missing
  apply endpoint.

## Working style expected in this repo

- Implement milestones in order (see the project brief / README). Don't
  reach ahead into a later milestone's surface (e.g. don't wire real MCP
  tools while still on Milestone 1).
- Every threshold (phash distance, burst window, keeper-ranking weights)
  must be configurable via `config.py` / environment, never hardcoded inside
  an algorithm.
- Analysis results are probabilistic technical signals, not ground truth.
  Use `probable_blur` / `high_confidence_blur_candidate` style language in
  both code and user-facing strings — never assert certainty.
- Unresolved or unverified SDK/API behavior must be marked with an explicit
  `TODO` and a doc note, never silently guessed at.
- Run `pytest`, `ruff check`, and `mypy` before considering a change done.
