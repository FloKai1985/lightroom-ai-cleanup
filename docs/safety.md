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
`api/actions.py` — the only part of the HTTP contract that could eventually
apply a user-confirmed change — does not exist yet (see
`docs/architecture.md`'s Milestone-2 component map), so rules 4–7 remain
satisfied by the same "the capability doesn't exist yet" argument as
Milestone 1.
