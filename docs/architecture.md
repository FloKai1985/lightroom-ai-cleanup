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
| 1 | Standalone Python analyzer: config, DB, hashing, pHash, sharpness, exposure, grouping, keeper ranking, CLI, tests | **Implemented** (this change) |
| 2 | FastAPI service: health, jobs, background processing, results | Not started |
| 3 | Lightroom Lua plugin: selection → renditions → job → metadata/collections | Not started |
| 4 | MCP server: read tools + action preparation | Not started |
| 5 | Optional MCP → Lightroom command polling | Not started, out of core scope |

Directories `src/lr_cleanup/api/`, `src/lr_cleanup/mcp_server/`, and
`lightroom-plugin/AICleanup.lrplugin/` are intentionally not populated yet.
Creating empty package stubs for code that doesn't exist yet would misstate
progress; they will be added when their milestone starts.

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
