# Lightroom Classic plugin — verified SDK reference

**Status: implemented (Milestone 3).** Milestone 1 shipped this document as
a memory-based research pass with items marked `UNRESOLVED`. Before writing
any Lua, that pass was redone against real sources — Adobe's own official
sample plugins (mirrored on GitHub, byte-identical Adobe copyright headers)
and several mature, actively-maintained third-party plugins, including at
least one other Lightroom-to-local-HTTP-service bridge. Every API used by
this plugin is listed below as **CONFIRMED**, with the source that
confirmed it. Anything not confirmed to this standard is listed under
**Remaining caveats** and handled defensively in code rather than guessed
at.

Do not treat this document as a substitute for the official
`Lightroom Classic SDK Guide` / `API Reference` — it's a record of what was
specifically checked for this plugin's needs, not a general reference.

## Sources consulted

- **Official Adobe SDK samples** (`helloworld`, `custommetadatasample`,
  `ftp_upload`, `flickr`, `mymetadata`), mirrored at
  [github.com/Jaid/lightroom-sdk-8-examples](https://github.com/Jaid/lightroom-sdk-8-examples)
  — these carry Adobe's original copyright headers and are the closest
  thing to primary-source documentation available outside the SDK zip
  itself.
- **API Reference (Lightroom SDK docs mirror)**:
  [archive.stecman.co.nz/files/docs/lightroom-sdk/API-Reference](https://archive.stecman.co.nz/files/docs/lightroom-sdk/API-Reference/)
  — a long-standing community mirror of Adobe's HTML API reference,
  cross-checked against the official samples above wherever they overlap
  (they agree everywhere checked).
- **gesteves/lightroom-alt-text-plugin** — a real, working plugin that
  exports a resized JPEG rendition and calls an external HTTP AI API
  (Claude) with the result, architecturally close to this project's own
  render→analyze→annotate flow.
- **Automaat/lightroom-mcp** — a real, working Lightroom-to-MCP bridge
  plugin (a close relative of this project's own end goal), useful for
  cross-checking export settings and confirming the "Lua plugins cannot
  host a server, only make outbound calls" architectural constraint.
- **DaveBurns/rc_Exportant** — a mature, long-maintained export utility
  plugin, used to cross-check `LR_jpeg_quality`'s value range.

## Accessing currently selected photos — CONFIRMED

`LrApplication.activeCatalog()` returns the active `LrCatalog`.
`catalog:getTargetPhotos()` returns an array of the currently
selected/target `LrPhoto` objects; `catalog:getTargetPhoto()` returns the
single most-selected one, or `nil`. *(API Reference — LrCatalog)*

## Reading photo metadata — CONFIRMED

`photo:getRawMetadata(key)` for machine-readable fields;
`photo:getFormattedMetadata(key)` for display-formatted strings. Both
"must be called from within a task started using `LrTasks`."
*(API Reference — LrPhoto)*

Exact raw-metadata keys this plugin depends on (all confirmed present in
the official key table):

| Key | Used for |
|---|---|
| `path` | `Photo.original_path` |
| `uuid` | stable cross-session `Photo.lightroom_id` |
| `isVirtualCopy` | `Photo.is_virtual_copy` |
| `rating` | `Photo.existing_rating` |
| `pickStatus` | `Photo.existing_pick_status` (`1`=picked, `0`=unset, `-1`=rejected) |
| `colorNameForLabel` | `Photo.existing_color_label` |
| `width` / `height` | `Photo.width` / `Photo.height` |
| `dateTimeOriginalISO8601` | `Photo.capture_time` (ISO 8601 string, parses directly) |
| `fileFormat` | used to skip formats the analyzer can't handle |
| `isVideo` | used to skip video files (out of scope — see docs/algorithms.md) |

`photo.localIdentifier` (direct property, not via `getRawMetadata`) is a
numeric per-catalog id — confirmed via `Automaat/lightroom-mcp`'s
`PhotoLookup.lua`, which also confirms **there is no
`LrCatalog:findPhotoByLocalIdentifier`** (a negative result worth
recording so it isn't reinvented later). This plugin uses `localIdentifier`
only for cache-subfolder naming, never as `lightroom_id` — `uuid` is the
persistent identifier and is what's sent to the API.

## File size / modification time — CONFIRMED (fills an M1 gap)

`getRawMetadata` has no file-mtime key. `LrFileUtils.fileAttributes(path)`
returns a table with `fileSize`, `fileCreationDate`, and
`fileModificationDate` — this is what `Photo.file_size`/`Photo.file_mtime`
are actually sourced from, applied to `photo:getRawMetadata('path')`.
*(API Reference — LrFileUtils)*

## Rendering temporary JPEG renditions — CONFIRMED

`LrExportSession{ photosToExport = {...}, exportSettings = {...} }`
constructs a session; `exportSession:renditions()` iterates it; each
`rendition:waitForRender()` returns `success, path` for that photo's
exported file. This exact pattern — construction, iteration, and
`waitForRender()` — is used as-is in `gesteves/lightroom-alt-text-plugin`.

Export setting keys (cross-confirmed across three independent real
plugins — `Automaat/lightroom-mcp`, `gesteves/lightroom-alt-text-plugin`,
`DaveBurns/rc_Exportant` — which agree on every key they share):

```lua
{
  LR_export_destinationType = 'specificFolder',
  LR_export_destinationPathPrefix = cacheDir,
  LR_export_useSubfolder = false,
  LR_format = 'JPEG',
  LR_export_colorSpace = 'sRGB',       -- exact string confirmed via Automaat/lightroom-mcp
  LR_jpeg_quality = 0.85,              -- 0..1 float, NOT 0-100 — confirmed via rc_Exportant
                                        -- ("< .7", "< 1") and alt-text-plugin ("0.8")
  LR_size_doConstrain = true,
  LR_size_maxWidth = 1600,
  LR_size_maxHeight = 1600,
  LR_size_resizeType = 'longEdge',
  LR_size_units = 'pixels',
  LR_minimizeEmbeddedMetadata = true,
}
```

The brief's "quality approximately 85" reads as Lightroom's Export dialog's
0–100 UI scale; the SDK field is 0..1, so `0.85` is the equivalent value.

## Defining plugin metadata — CONFIRMED

`Info.lua` sets `LrMetadataProvider = 'Metadata.lua'`; that file returns a
table with `metadataFieldsForPhotos` (array of `{id, title, dataType, ...}`)
and `schemaVersion`. Confirmed field shape directly from Adobe's official
`custommetadatasample.lrdevplugin/CustomMetadataDefinition.lua`. Reads use
`photo:getPropertyForPlugin(plugin, fieldId, optVersion, noThrow)`; writes
use `photo:setPropertyForPlugin(plugin, fieldId, value, optVersion)`.

**Write-access gate — CONFIRMED, resolves the M1 `UNRESOLVED` item**:
`catalog:withPrivateWriteAccessDo(func, timeoutParams)` is the correct
gate for plugin-custom-field writes ("Provides write access to custom
fields defined by your plug-in" — API Reference, LrCatalog), distinct from
`catalog:withWriteAccessDo(actionName, func, timeoutParams)` used for
built-in, undo-visible fields (rating/label/pick/collections). The official
sample's own comment on `updateFromEarlierSchemaVersion` confirms the
pairing: "This function is called from within a
`catalog:withPrivateWriteAccessDo` block."

**Remaining caveat — numeric field type.** No source consulted
enumerates a numeric/float `dataType` for `metadataFieldsForPhotos` (Jeffrey
Friedl's widely-cited plugin-metadata writeup shows only `string` and
`enum`). Rather than gamble on an unconfirmed type, every custom field this
plugin defines — including `AI Sharpness Score`, `AI Blur Confidence`,
`AI Keeper Score` — uses `dataType = 'string'`, storing the formatted
number as text (e.g. `"0.91"`). This is a deliberate, conservative choice,
not an oversight — see `Metadata.lua`.

Fields actually defined (all `dataType = 'string'` per the caveat above,
`id`s are camelCase, `title`s are the human-readable names from the brief):
`aiCleanupStatus`, `aiSharpnessScore`, `aiBlurConfidence`,
`aiDuplicateType`, `aiDuplicateGroup`, `aiSimilarityGroup`,
`aiKeeperScore`, `aiRecommendation`, `aiAnalysisVersion`.

## Creating/managing collections — CONFIRMED

`catalog:createCollectionSet(name, parent, canReturnPrior)` and
`catalog:createCollection(name, parent, canReturnPrior)` — the third
argument, confirmed by name and behavior ("True to return an existing
[collection/set] with this name" — API Reference, LrCatalog), is exactly
the idempotency mechanism the brief requires ("running it twice must not
create duplicate collection trees"). Both require
`catalog:withWriteAccessDo(...)` (collection membership is a built-in,
undo-visible catalog operation — never plugin-private data).

## HTTP calls from Lightroom Lua — CONFIRMED

```lua
LrHttp.get(url, headers, timeout)          -- returns response, headers
LrHttp.post(url, postBody, headers, method, timeout, totalSize) -- returns response, headers
```

`headers` is an array of `{ field = "...", value = "..." }` tables, not a
plain string-keyed map (API Reference — LrHttp, confirmed by real usage in
the flickr sample). Both functions "can only be called within an
asynchronous task" (API Reference — LrHttp), i.e. inside
`LrTasks.startAsyncTask` or a `processRenderedPhotos`-style implicit task.

## Asynchronous Lightroom tasks — CONFIRMED (corrects an M1 assumption)

```lua
LrTasks.startAsyncTask( func, optName )
```

Note the parameter **order**: the function comes first, the optional debug
name second (API Reference — LrTasks). Milestone 1's doc had this
unconfirmed and risked guessing `(name, func)`, the opposite order — worth
recording as a concrete example of why this pass was redone against real
sources rather than trusted from memory.

`LrFunctionContext.callWithContext( name, func )` — confirmed via the
official `flickr.lrdevplugin/FlickrAPI.lua`, used for scoping
cleanup/cancellation around a task.

**`LrTasks.pcall`, not Lua's built-in `pcall`, for anything that yields —
CONFIRMED BY REAL-WORLD FAILURE, not just reading.** Lightroom's Lua
runtime is 5.1 (see the SDK version pin note below), and standard Lua 5.1
`pcall` cannot yield across its own C-call boundary. `LrHttp.get`/`.post`
yield internally (that's how Lightroom keeps the UI responsive during
network I/O — see "HTTP calls from Lightroom Lua" above), so wrapping one
in plain `pcall(HttpClient.getJson, ...)` fails at runtime with
`Yielding is not allowed within a C or metamethod call` — this is not a
theoretical concern, it's the literal error a real Lightroom install threw
when this plugin's `PluginInfoProvider.lua` "Test Connection" button and
`AnalyzeSelected.lua`'s HTTP calls were first exercised end-to-end. The
fix: `LrTasks.pcall(func, ...)` (same call signature as standard `pcall`)
is the SDK's yield-safe equivalent, "permitting yield() calls within it"
per the API Reference — this was already recorded in this document's
`LrTasks` research table before the bug shipped, but not connected to the
fact that every `HttpClient` call site needed it. Every `pcall(` in this
plugin now reads `LrTasks.pcall(` — including the two call sites
(`isSupportedPhoto`, `readPhotoInfo` in `AnalyzeSelected.lua`) that wrap
`photo:getRawMetadata`/`LrFileUtils.fileAttributes` rather than HTTP: those
aren't documented as yielding, but there is no known downside to
`LrTasks.pcall` over plain `pcall` for a non-yielding function, so every
`pcall` in the plugin was standardized rather than reasoning per-call-site
about which ones truly need it. **Lesson for future SDK calls**: knowing
an API exists (as documented here) isn't the same as applying it
everywhere it's needed — this doc is a research record, not a substitute
for exercising the actual code path in real Lightroom.

## Architectural constraint confirmed by cross-reference

Lightroom Lua plugins have no socket/listen API — they can only make
*outbound* `LrHttp` calls. `Automaat/lightroom-mcp` (a real Lightroom↔MCP
bridge) has no HTTP-server module of its own for exactly this reason. This
confirms the direction fixed since `docs/architecture.md` Milestone 1 was
correct and is not just a design preference: **Python must be the HTTP
server; the plugin can only be a client of it.**

## Remaining caveats (deliberately not guessed at)

- **`LR_size_maxWidth` vs `LR_size_maxHeight` for `resizeType='longEdge'`.**
  The one real example found (`Automaat/lightroom-mcp`) sets both to the
  same value defensively when either is requested; no source confirms
  whether Lightroom actually needs both or ignores the shorter-edge one in
  `longEdge` mode. This plugin follows the same defensive pattern (sets
  both to `1600`) rather than assuming one is redundant.
- **Custom metadata numeric `dataType`.** See above — treated as
  unconfirmed; every custom field uses `dataType = 'string'`.
- **SDK version pin.** `LrSdkVersion = 6.0` / `LrSdkMinimumVersion = 6.0` in
  `Info.lua` — chosen because every API this plugin uses is confirmed
  present since long-ago SDK versions (the samples above range from SDK
  3.0 to 8.0 and use the same calls), and a low pin maximizes compatibility
  with older Lightroom Classic installations. Current Lightroom Classic
  ships SDK 14.x, so this is not a ceiling on what's usable, only a floor.
- **`LrHttp`'s failure-path return shape.** The API Reference confirms what
  `LrHttp.get`/`.post` return on *success* (`response, headers` with
  `headers.status`), which `HttpClient.lua::checkResponse` relies on. It
  does not document what they return when the connection itself fails
  (refused, DNS, timeout) — `checkResponse` assumes `body == nil` signals
  that case, a common convention for Lua HTTP wrappers but not one this
  research confirmed against a real failure. This can't cause a crash
  either way: every call site wraps `HttpClient.getJson`/`.postJson` in
  `pcall`, so if `LrHttp` instead throws its own native Lua error on
  connection failure, the same `pcall` catches that too — the only thing
  at risk is the specificity of the message shown to the user (a generic
  SDK error instead of `HttpClient`'s "is it running?" hint), not
  correctness or safety.

## What Milestone 3 deliberately does not build

- **`ApplyActions.lua` still does not exist.** It's the file that would let
  a `CONFIRMED` `PreparedAction` actually get applied to Lightroom, per
  `docs/safety.md`'s two-phase action model. The HTTP side of the action
  queue (`POST /api/v1/actions/prepare`, `GET .../pending`,
  `POST .../confirm`, `POST .../undo`) exists as of Milestone 4 — but
  nothing polls for `CONFIRMED` batches and applies them, on either side.
  See `docs/architecture.md`'s Milestone-4 component map.
- **No persistent background/polling loop** (the `LrInitPlugin` +
  `LrForceInitPlugin` + long-running-task pattern `Automaat/lightroom-mcp`
  uses for its always-on bridge). The brief's Milestone 5 explicitly asks
  for this to be investigated *after* Milestones 1–4 are stable, as a
  separate mechanism, "not part of the core analyzer." Milestone 3's
  `AnalyzeSelected.lua` is a one-shot menu command: select photos, run it,
  wait for it to finish. That satisfies the vertical slice the brief asks
  for without reaching into Milestone 5's scope.
