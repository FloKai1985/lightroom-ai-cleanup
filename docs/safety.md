# Safety constraints

These are mandatory product requirements, not defaults that can be relaxed
by configuration. Any code change that risks violating one of these needs to
be caught in review before it merges.

## Absolute rules

The software must **never**:

1. Delete image files.
2. Move original image files.
3. Modify original image files (no in-place edits, no re-encodes, no
   metadata writes to the original on disk).
4. Write directly into Lightroom's `.lrcat` SQLite database. All Lightroom
   state changes go through the Lua plugin using supported Lightroom
   Classic SDK calls, which is the only Adobe-sanctioned way to mutate a
   catalog safely (direct SQLite writes to an open catalog will corrupt it).
5. Overwrite an existing star rating.
6. Overwrite an existing color label.
7. Overwrite an existing pick/reject flag.
8. Send images, renditions, or derived image data to a cloud service.
9. Call an external/cloud AI API.

## Why the architecture enforces this

- **No catalog DB access from Python.** `lr_cleanup` never opens or knows
  the path of a `.lrcat` file. It only exchanges JSON with the Lightroom
  plugin over `127.0.0.1`. This makes rule 4 structurally true rather than
  merely policy.
- **Local-only network binding.** The FastAPI service (Milestone 2) binds to
  `127.0.0.1` by default and this must not be made configurable to `0.0.0.0`
  without a deliberate, separately-reviewed change. This is what makes rules
  8–9 hold by default — there is nowhere else for image data to go.
- **AI output is staged, not applied.** All recommendations
  (`AnalysisResult`, `DuplicateGroup`, `GroupMember`) are written to:
  - custom Lightroom **plugin metadata** fields (namespaced, never reusing a
    built-in field),
  - dedicated Lightroom **collections** under an `AI Photo Cleanup`
    collection set,
  - the local **SQLite** database.

  None of these is a Lightroom star rating, color label, or pick flag.
  Rules 5–7 are satisfied because the AI never writes to those fields at
  all in the default flow.
- **Two-phase action model.** Any future feature that *would* touch a
  standard Lightroom field (e.g. "apply AI recommendation as a reject flag")
  must go through `PreparedAction` → user confirmation → plugin apply. MCP
  tools and background jobs may only create `PreparedAction` rows; nothing
  in this codebase may call the (not-yet-implemented) "apply" path other
  than the plugin, and only after a human confirms a specific batch. See
  `POST /api/v1/actions/{batch_id}/confirm` in the API design.
- **No destructive MCP tool.** Every MCP tool is either read-only or ends in
  "prepare an action for review." There is intentionally no
  `delete_photos`, `apply_ratings`, or similar tool.

## What "AI recommendation" means in this system

A recommendation is a *probabilistic technical signal*, not a verdict.
User-facing and machine-readable text uses hedged terminology:
`probable_blur`, `high_confidence_blur_candidate`, `KEEPER` (recommended,
not decided). The system explains every recommendation with structured,
machine-readable reasons (see `docs/algorithms.md`) so a human can verify
the reasoning before acting on it.

## Milestone-1 scope note

Milestone 1 is a standalone analyzer with no network service and no
Lightroom plugin. It reads image files the caller points it at (via the
`register` CLI command) and writes only to its own local SQLite database
under `data/`. Rules 8–9 are trivially satisfied (no network code exists
yet); rules 1–3 are satisfied because the analyzer never writes to a source
image path, only reads it (`analysis/*.py` open files read-only); rules 4–7
don't yet apply because there is no Lightroom integration yet — they become
load-bearing starting Milestone 3.

## Milestone-2 scope note

Milestone 2 adds the local HTTP service (`src/lr_cleanup/api/`), so rule 8
(no cloud calls) is no longer trivially true by omission — it's now
enforced by `api/app.py::run()` refusing to start unless
`settings.host` is a loopback address (`127.0.0.1`/`localhost`/`::1`),
raising instead of silently binding wider. Every endpoint added
(`/health`, photo registration, job creation/status/results, group detail)
is either read-only or appends new `Photo`/`Analysis`/`AnalysisJob`/
`DuplicateGroup`/`GroupMember` rows — none of them touch
`existing_rating`/`existing_color_label`/`existing_pick_status` on write,
only read them (as keeper-ranking tie-breaker inputs, per rules 5–7).
`api/actions.py` — the part of the HTTP contract that could eventually
apply a user-confirmed change — did not exist yet as of this milestone
(see `docs/architecture.md`'s Milestone-2 component map), so rules 4–7
remained satisfied by the same "the capability doesn't exist yet" argument
as Milestone 1. It exists starting Milestone 4 — see that scope note below
for why rules 4–7 are still satisfied even with it built.

## Milestone-3 scope note

Milestone 3 adds the Lightroom Lua plugin, which is the first code in this
project that can actually touch a live Lightroom catalog — this is where
rules 4–7 stop being true "by omission" and start being enforced by what
the plugin does and doesn't call:

- **Rule 4 (no direct catalog DB access)**: the plugin never opens the
  `.lrcat` file; every read goes through `photo:getRawMetadata` /
  `getFormattedMetadata`, every write through the SDK's `LrCatalog` gates
  (`withPrivateWriteAccessDo` for plugin metadata,
  `withWriteAccessDo` for collections) — see `docs/lightroom-plugin.md`.
- **Rules 5–7 (never overwrite rating/label/pick)**: `AnalyzeSelected.lua`
  and `ReviewResults.lua` only ever *read* `rating`, `pickStatus`, and
  `colorNameForLabel` (sent to the backend as keeper-ranking tie-breaker
  input, per `docs/algorithms.md`) and only ever *write* the plugin's own
  custom fields (`aiCleanupStatus`, `aiSharpnessScore`, etc., defined in
  `Metadata.lua`) and collection membership. There is no code path in this
  plugin that calls `photo:setRawMetadata` or anything else that could
  touch a built-in rating/label/pick field. `ApplyActions.lua` — the file
  that would eventually let a user-confirmed action touch one of those
  fields — deliberately does not exist yet (see
  `docs/architecture.md`'s Milestone-3 component map).
- **Rules 1–3 (never delete/move/modify originals)**: the plugin only ever
  reads `photo:getRawMetadata('path')` to locate the original; every write
  operation (`LrExportSession`) targets a dedicated temp cache directory,
  never the original's path, and the only `LrFileUtils.delete` call in the
  plugin targets that same cache directory after a successful run — never
  an original.
- **Rules 8–9 (no cloud/no external AI)**: the plugin's only outbound HTTP
  calls (`HttpClient.lua`) go to `HttpClient.baseUrl()`, which is
  user-editable via the Plug-in Manager but documented there as never
  belonging on anything but `127.0.0.1` — there is no default or
  fallback URL that points anywhere else.

## Milestone-4 scope note

Milestone 4 adds the MCP server and, with it, `api/actions.py` — the part
of the HTTP contract capable of staging a change. Two things keep rules
4–9 satisfied even though action preparation is now real (not just
schema):

- **No MCP tool can reach "confirm" or "apply".** `prepare_review_collections`
  and `prepare_markings` create `PENDING` rows; `undo_action_batch` only
  cancels a not-yet-applied batch. `POST /api/v1/actions/{batch_id}/confirm`
  exists on the HTTP API (completing the contract Milestone 2 deferred) but
  is not wired to any MCP tool — reaching `CONFIRMED` requires a direct API
  call, deliberately outside MCP's reach. There is still no "apply" endpoint
  or tool anywhere in the codebase; a `CONFIRMED` batch cannot currently
  cause anything to happen in Lightroom, because nothing polls for it (see
  `docs/architecture.md`'s Milestone-4 component map). This is what "No
  destructive MCP tool may exist" means concretely here — not that every
  action-queue state transition is literally destructive, but that MCP's
  reach stops one full human decision short of anything that could be.
- **`prepare_markings` cannot stage a rating/label/pick change even if
  asked.** Its `marking` parameter is validated against a fixed allow-list
  (`flagged_for_review`, `confirmed_keeper`, `confirmed_redundant` —
  `mcp_server/tools.py`) and always writes to the plugin's own
  `aiCleanupStatus` custom field, never a built-in Lightroom field. There
  is no parameter on any tool that accepts an arbitrary Lightroom field
  name or value.
- **The MCP server has no database or Lightroom access of its own** — see
  `docs/architecture.md`'s Milestone-4 component map. Every tool is an HTTP
  client of the same `127.0.0.1`-only service the plugin uses; there is no
  additional attack surface or bypass path introduced by adding MCP.
