--[[----------------------------------------------------------------------------

Thresholds.lua
Single source of truth for the per-request analysis threshold/weight
overrides exposed in the Plug-in Manager settings panel
(PluginInfoProvider.lua) and sent with every analysis job
(AnalyzeSelected.lua) — see src/lr_cleanup/api/jobs.py's
JobCreateRequest.overrides() for the receiving end.

Values are stored in LrPrefs as strings (not numbers) and parsed with
tonumber() at send time. This sidesteps relying on any LrView
numeric-edit-field-specific binding behavior this project hasn't verified
against a real source (docs/lightroom-plugin.md's "don't guess the SDK"
discipline) — f:edit_field bound to a string pref is the one pattern
already confirmed working here (the backend-URL field).

Defaults below must match src/lr_cleanup/config.py's Settings field
defaults — there is no runtime link between the two, so keep them in sync
by hand if either changes.

------------------------------------------------------------------------------]]

local Thresholds = {}

-- { prefsKey, apiField, label, default, help }
Thresholds.DEFINITIONS = {
	{
		prefsKey = 'burstWindowSeconds',
		apiField = 'burst_window_seconds',
		label = 'Burst window (seconds)',
		default = '10',
		help = 'Photos captured within this many seconds of each other are candidates for the same burst.',
	},
	{
		prefsKey = 'phashMaxDistance',
		apiField = 'phash_max_distance',
		label = 'Similarity distance (0-64)',
		default = '8',
		help = 'Perceptual-hash distance for "visually similar." Lower = stricter near-duplicate matching.',
	},
	{
		prefsKey = 'aspectRatioTolerance',
		apiField = 'aspect_ratio_tolerance',
		label = 'Aspect ratio tolerance',
		default = '0.05',
		help = 'Max relative aspect-ratio difference allowed within a group (0.05 = 5%).',
	},
	{
		prefsKey = 'highConfidenceBlurThreshold',
		apiField = 'high_confidence_blur_threshold',
		label = 'Blur confidence threshold (0-1)',
		default = '0.75',
		help = 'At/above this, a photo is marked OUT_OF_FOCUS and skips duplicate comparison entirely.',
	},
	{
		prefsKey = 'highlightClipThreshold',
		apiField = 'highlight_clip_threshold',
		label = 'Highlight clipping threshold (0-1)',
		default = '0.98',
		help = 'Normalized pixel brightness above which a pixel counts as blown out.',
	},
	{
		prefsKey = 'shadowClipThreshold',
		apiField = 'shadow_clip_threshold',
		label = 'Shadow clipping threshold (0-1)',
		default = '0.02',
		help = 'Normalized pixel brightness below which a pixel counts as crushed.',
	},
	{
		prefsKey = 'weightSharpness',
		apiField = 'weight_sharpness',
		label = 'Keeper weight: sharpness',
		default = '0.55',
		help = nil,
	},
	{
		prefsKey = 'weightExposure',
		apiField = 'weight_exposure',
		label = 'Keeper weight: exposure',
		default = '0.25',
		help = nil,
	},
	{
		prefsKey = 'weightTechnical',
		apiField = 'weight_technical',
		label = 'Keeper weight: resolution',
		default = '0.10',
		help = nil,
	},
	{
		prefsKey = 'weightExistingPreference',
		apiField = 'weight_existing_preference',
		label = 'Keeper weight: existing rating',
		default = '0.10',
		help = 'The four keeper weights above must sum to 1.0 — the backend rejects the job otherwise.',
	},
}

--- Fills in any prefs key that's never been set (first run), without
--- overwriting a value the user already chose.
function Thresholds.ensureDefaults( prefs )
	for _, def in ipairs( Thresholds.DEFINITIONS ) do
		if prefs[ def.prefsKey ] == nil then
			prefs[ def.prefsKey ] = def.default
		end
	end
end

--- Resets every field to its default value.
function Thresholds.resetToDefaults( prefs )
	for _, def in ipairs( Thresholds.DEFINITIONS ) do
		prefs[ def.prefsKey ] = def.default
	end
end

--- Builds the { api_field = number, ... } table to merge into a
--- POST /api/v1/jobs request body. Falls back to the definition's default
--- for anything that doesn't parse as a number — never sends nil or a raw
--- string to the API.
function Thresholds.buildApiOverrides( prefs )
	local overrides = {}
	for _, def in ipairs( Thresholds.DEFINITIONS ) do
		local raw = prefs[ def.prefsKey ]
		overrides[ def.apiField ] = tonumber( raw ) or tonumber( def.default )
	end
	return overrides
end

return Thresholds
