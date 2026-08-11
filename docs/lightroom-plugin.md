# Lightroom Classic plugin — SDK research (pre-Milestone-3)

**Status: research/design only.** No Lua has been written yet — that is
Milestone 3. This document exists because the project brief requires SDK
capabilities to be verified against official Adobe documentation *before*
implementation starts, and requires unclear APIs to be flagged rather than
guessed at. Everything below is either (a) long-standing, stable Lightroom
Classic SDK surface referenced consistently across Adobe's SDK guide and
sample plugins, marked **CONFIRMED (verify exact signature before coding)**,
or (b) a nuance whose precise behavior needs to be checked against the
current SDK PDF/API reference at Adobe's developer site before Milestone 3
lands, marked **UNRESOLVED**.

Do not treat "CONFIRMED" here as "safe to copy into code verbatim" — it
means "this call/module is a real, stable part of the SDK," not "this exact
argument order is correct." Re-check the current API reference
(`Lightroom Classic SDK Guide.pdf` / `API Reference.pdf` from Adobe's
developer downloads) while implementing Milestone 3, since parameter order
and optional-field names are exactly where hand-written docs drift from
memory.

## Accessing currently selected photos

**CONFIRMED**: `LrApplication.activeCatalog()` returns the active
`LrCatalog`. `catalog:getTargetPhotos()` returns the current target/selected
photos as a table of `LrPhoto`; `catalog:getTargetPhoto()` returns the
single most-selected photo. This is the standard way plugins read the
Library/Filmstrip selection.

## Reading photo metadata

**CONFIRMED**: `LrPhoto:getRawMetadata(name)` for machine-readable fields
(path, dimensions, GPS, capture date, virtual-copy flag, UUID, etc.) and
`LrPhoto:getFormattedMetadata(name)` for display-formatted strings.

**UNRESOLVED**: the exact raw-metadata key names we depend on —
`rating`, `pickStatus`, `colorNameForLabel` (or `label`), `isVirtualCopy`,
`fileSize`, `dateTimeOriginal` / `dateTimeOriginalISO8601`,
`path`. These need to be checked one-by-one against the current API
reference's `LrPhoto` metadata key table before `Metadata.lua` /
`AnalyzeSelected.lua` are written, since a wrong key name fails silently
(returns `nil`) rather than erroring.

## Rendering/exporting temporary JPEG renditions

**CONFIRMED**: `LrExportSession` is the supported mechanism for producing
controlled-format renditions (as opposed to
`LrPhoto:requestJpegThumbnail`, which returns Lightroom's cached preview at
whatever size/quality Lightroom chose — not suitable here because we need a
guaranteed sRGB, fixed-long-edge, fixed-quality JPEG for consistent
analysis). An export session is constructed with the photos to export and
an export-settings table, then iterated via `exportSession:renditions()` to
get a filesystem path per photo once rendering completes.

**UNRESOLVED**: exact export-setting keys for our target
(`JPEG, sRGB, 1600px longest edge, quality ≈ 0.85`) —
e.g. `LR_format`, `LR_export_colorSpace`, `LR_size_doConstrain`,
`LR_size_maxWidth`/`LR_size_maxHeight`, `LR_jpeg_quality`,
`LR_export_destinationType`, `LR_export_destinationPathPrefix`. These names
are well-established in Adobe's own sample export plugins but must be
copied from the current SDK sample (`Export Filter` / `FTP Upload` samples
ship with the SDK) rather than typed from memory when Milestone 3 starts.

## Defining plugin metadata

**CONFIRMED**: a plugin declares custom metadata via
`Info.lua`'s `LrMetadataProvider` entry pointing at a metadata-definition
Lua file (planned: `Metadata.lua`), which returns a table including a field
list (`metadataFieldsForPhotos`) with `id`, `title`, `dataType`, etc. Reads
use `LrPhoto:getPropertyForPlugin(_PLUGIN, fieldId)`; writes use
`LrPhoto:setPropertyForPlugin(_PLUGIN, fieldId, value)`.

**UNRESOLVED**: whether `setPropertyForPlugin` requires being called inside
a `catalog:withWriteAccessDo(...)` transaction (the mechanism required for
built-in-field writes like rating/label/pick) or has its own
write-access rules (some SDK versions document a
`catalog:withPrivateWriteAccessDo` variant for plugin-private data). This
directly affects `Metadata.lua`'s implementation and must be confirmed
against the current SDK guide's "Metadata" chapter before writing any code
that calls `setPropertyForPlugin`.

Planned custom fields (names only — data types/write path pending the
above): `AI Cleanup Status`, `AI Sharpness Score`, `AI Blur Confidence`,
`AI Duplicate Type`, `AI Duplicate Group`, `AI Similarity Group`,
`AI Keeper Score`, `AI Recommendation`, `AI Analysis Version`.

## Creating/managing collections

**CONFIRMED**: `catalog:createCollectionSet(name, parent, returnExisting)`
and `catalog:createCollection(name, parent, returnExisting)` exist and
support a "return the existing one instead of erroring/duplicating" mode,
which is exactly what idempotent collection-tree creation (running the
plugin twice must not create duplicate trees) needs. Both must be called
inside `catalog:withWriteAccessDo(...)`.

**UNRESOLVED**: exact parameter order/name for the "return existing"
argument in the current SDK version, and whether collection-set nesting
(`AI Photo Cleanup` → `01 – Recommended Keepers`, etc.) requires the child
collection call to pass the set as `parent` by object reference or by name.
Confirm against the current API reference before `ReviewResults.lua` is
written; until then, `prepare_review_collections` (Milestone 4 MCP tool) is
designed only around the *shape* (idempotent create-if-missing), not a
concrete call.

## HTTP calls from Lightroom Lua

**CONFIRMED**: `LrHttp.get(url, headers, timeoutSecs)` and
`LrHttp.post(url, body, headers, method, timeoutSecs)` are the SDK's
synchronous HTTP primitives, and being synchronous they must run inside an
async task (see below) so they don't block Lightroom's UI thread. This is
the transport `HttpClient.lua` will wrap for calling the local FastAPI
service on `127.0.0.1`.

**UNRESOLVED**: exact parameter order/defaults for `LrHttp.post` across SDK
versions (header table shape, default method). Confirm before
`HttpClient.lua` is written.

## Write-access requirements for catalog changes

**CONFIRMED**: any write to catalog-tracked, undo-visible state (star
rating, color label, pick/reject flag, collection membership) must happen
inside `catalog:withWriteAccessDo("undo step name", function(context) ...
end)`. This module will never be called by anything except an explicit,
user-confirmed apply step — see `docs/safety.md`. Since this project's
default behavior never writes ratings/labels/flags at all, this call path
is not expected to be exercised until (and unless) a future, separately
reviewed feature adds a user-confirmed "apply AI recommendation as a
flag/rating" action.

## Asynchronous Lightroom tasks

**CONFIRMED**: `LrTasks.startAsyncTask(function() ... end)` (or with a name
string) runs code on a Lightroom-managed coroutine off the main UI thread —
required for anything that blocks (HTTP calls, write-access transactions,
export sessions) so Lightroom's UI stays responsive. `LrFunctionContext` is
the companion module for scoping cleanup handlers (e.g.
`LrFunctionContext.callWithContext`) around such tasks.

## Summary for Milestone 3 planning

Nothing above blocks starting Milestone 3, but before writing
`AnalyzeSelected.lua`, `Metadata.lua`, and `ReviewResults.lua`, pull the
current `Lightroom Classic SDK Guide` and `API Reference` from Adobe's
developer site and resolve each `UNRESOLVED` item above against it. Where
the real signature differs from what's assumed here, this document should
be updated alongside the code, not left stale.
