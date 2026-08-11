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
Lightroom Classic --Lua plugin--> Python service (FastAPI) --> SQLite
                                         |                        ^
                                         v                        |
                                   Analysis Engine          MCP Server <- MCP client
```

The Lua plugin owns all Lightroom interaction (selection, renditions,
metadata, collections). Python owns analysis, persistence, ranking, HTTP API,
and MCP. Python never talks to Lightroom directly — only through the HTTP
contract the plugin calls into.

## Repository layout

- `src/lr_cleanup/analysis/` — pure, testable image analysis functions
  (hashing, sharpness, exposure, grouping, keeper ranking). No I/O beyond
  reading the files/paths given to them.
- `src/lr_cleanup/database/` — SQLAlchemy models, repository, Alembic
  migrations. This is the single source of truth on disk; it is never the
  Lightroom catalog.
- `src/lr_cleanup/service/` — orchestration: batches photos through the
  analysis pipeline and writes results via the repository.
- `src/lr_cleanup/cli.py` — Milestone-1 CLI entry point for running analysis
  without the HTTP/plugin layers.
- `src/lr_cleanup/api/` — FastAPI app (Milestone 2+, not yet implemented).
- `src/lr_cleanup/mcp_server/` — MCP tools (Milestone 4+, not yet
  implemented).
- `lightroom-plugin/AICleanup.lrplugin/` — Lua plugin (Milestone 3+, not yet
  implemented). Any SDK call used there must be verified against the official
  Lightroom Classic SDK docs — see `docs/lightroom-plugin.md` for what is
  confirmed vs. still marked `UNRESOLVED SDK CALL`.

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
