--[[----------------------------------------------------------------------------

PluginInfoProvider.lua
Plug-in Manager panel: backend URL + connection test, and per-request
analysis threshold/weight overrides. `sectionsForTopOfDialog(f,
propertyTable)` returning an array of `{title=..., f:row{...}}` sections is
confirmed against Adobe's official
custommetadatasample.lrdevplugin/PluginInfoProvider.lua
(docs/lightroom-plugin.md).

THRESHOLD_DEFINITIONS below must stay in sync with the identical table in
AnalyzeSelected.lua (which builds the POST /api/v1/jobs override payload
from it) and with src/lr_cleanup/config.py's Settings field defaults.
Originally factored into a shared Thresholds.lua required by both files;
inlined into each instead after a real-world "Could not load toolkit
script: Thresholds" failure that couldn't be reproduced or diagnosed in
this environment (no way to run actual Lightroom LrView/require code
here — see docs/architecture.md's "first real-world Lightroom test"
note). Duplication here is a deliberate trade against that undiagnosable
risk, not an oversight.

Threshold fields are plain string edit fields, not numeric-typed ones —
values are stored in LrPrefs as strings and parsed with tonumber() at
send time (AnalyzeSelected.lua), never bound as numeric LrView fields.
This sidesteps relying on any LrView numeric-edit-field-specific binding
behavior this project hasn't verified against a real source
(docs/lightroom-plugin.md's "don't guess the SDK" discipline) —
f:edit_field bound to a string pref is the one pattern already confirmed
working here (the backend-URL field). Invalid combinations (e.g. keeper
weights not summing to 1.0) are validated server-side when a job is
created and surface as the same "could not start analysis job" dialog
AnalyzeSelected.lua already shows for any other backend error.

------------------------------------------------------------------------------]]

local LrView = import 'LrView'
local LrPrefs = import 'LrPrefs'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'

local HttpClient = require 'HttpClient'

local prefs = LrPrefs.prefsForPlugin()

-- Keep in sync with AnalyzeSelected.lua's identical table.
local THRESHOLD_DEFINITIONS = {
	{
		prefsKey = 'burstWindowSeconds',
		label = 'Burst window (seconds)',
		default = '10',
		help = 'Photos captured within this many seconds of each other are candidates for the same burst.',
	},
	{
		prefsKey = 'phashMaxDistance',
		label = 'Similarity distance (0-64)',
		default = '8',
		help = 'Perceptual-hash distance for "visually similar." Lower = stricter near-duplicate matching.',
	},
	{
		prefsKey = 'aspectRatioTolerance',
		label = 'Aspect ratio tolerance',
		default = '0.05',
		help = 'Max relative aspect-ratio difference allowed within a group (0.05 = 5%).',
	},
	{
		prefsKey = 'highConfidenceBlurThreshold',
		label = 'Blur confidence threshold (0-1)',
		default = '0.75',
		help = 'At/above this, a photo is marked OUT_OF_FOCUS and skips duplicate comparison entirely.',
	},
	{
		prefsKey = 'highlightClipThreshold',
		label = 'Highlight clipping threshold (0-1)',
		default = '0.98',
		help = 'Normalized pixel brightness above which a pixel counts as blown out.',
	},
	{
		prefsKey = 'shadowClipThreshold',
		label = 'Shadow clipping threshold (0-1)',
		default = '0.02',
		help = 'Normalized pixel brightness below which a pixel counts as crushed.',
	},
	{
		prefsKey = 'weightSharpness',
		label = 'Keeper weight: sharpness',
		default = '0.55',
		help = nil,
	},
	{
		prefsKey = 'weightExposure',
		label = 'Keeper weight: exposure',
		default = '0.25',
		help = nil,
	},
	{
		prefsKey = 'weightTechnical',
		label = 'Keeper weight: resolution',
		default = '0.10',
		help = nil,
	},
	{
		prefsKey = 'weightExistingPreference',
		label = 'Keeper weight: existing rating',
		default = '0.10',
		help = 'The four keeper weights above must sum to 1.0 -- the backend rejects the job otherwise.',
	},
}

local function ensureThresholdDefaults()
	for _, def in ipairs( THRESHOLD_DEFINITIONS ) do
		if prefs[ def.prefsKey ] == nil then
			prefs[ def.prefsKey ] = def.default
		end
	end
end

local function resetThresholdDefaults()
	for _, def in ipairs( THRESHOLD_DEFINITIONS ) do
		prefs[ def.prefsKey ] = def.default
	end
end

local function connectionSection( f )
	local bind = LrView.bind

	return {
		title = 'AI Cleanup',

		f:row {
			spacing = f:control_spacing(),
			f:static_text {
				title = 'Local service URL:',
				width = 120,
			},
			f:edit_field {
				value = bind {
					key = 'backendBaseUrl',
					bind_to_object = prefs,
				},
				fill_horizontal = 1,
				width_in_chars = 30,
			},
		},

		f:row {
			spacing = f:control_spacing(),
			f:static_text {
				title = 'Defaults to http://127.0.0.1:8765 if left blank. Never point this ' ..
					'at a non-loopback address -- see docs/safety.md.',
				fill_horizontal = 1,
				width_in_chars = 55,
				height_in_lines = 2,
				size = 'small',
			},
		},

		f:row {
			spacing = f:control_spacing(),
			f:push_button {
				title = 'Test Connection',
				action = function()
					LrTasks.startAsyncTask( function()
						LrFunctionContext.callWithContext( 'AICleanup.TestConnection', function()
							local ok, result = LrTasks.pcall( HttpClient.getJson, '/health' )
							if ok then
								LrDialogs.message(
									'AI Cleanup',
									'Connected. Backend reports: ' .. tostring( result.status ) ..
										' (database: ' .. tostring( result.database ) .. ')',
									'info'
								)
							else
								LrDialogs.message(
									'AI Cleanup: connection failed',
									tostring( result ) .. '\n\nStart the backend with scripts/run-server.sh.',
									'warning'
								)
							end
						end )
					end, 'AI Cleanup: Test Connection' )
				end,
			},
		},
	}
end

local function thresholdsSection( f, propertyTable )
	local bind = LrView.bind
	ensureThresholdDefaults()

	local section = {
		title = 'AI Cleanup: Detection & Classification Thresholds',
	}

	for _, def in ipairs( THRESHOLD_DEFINITIONS ) do
		section[ #section + 1 ] = f:row {
			spacing = f:control_spacing(),
			f:static_text {
				title = def.label .. ':',
				width = 220,
			},
			f:edit_field {
				value = bind { key = def.prefsKey, bind_to_object = prefs },
				width_in_chars = 8,
			},
			f:static_text {
				title = def.help or '',
				fill_horizontal = 1,
				width_in_chars = 40,
				height_in_lines = 2,
				size = 'small',
			},
		}
	end

	section[ #section + 1 ] = f:row {
		spacing = f:control_spacing(),
		f:static_text {
			title = 'Applied to every analysis job you run -- see docs/algorithms.md for what each ' ..
				'one means. Invalid combinations (e.g. keeper weights not summing to 1.0) are ' ..
				'rejected with an error dialog when you next run analysis, not here.',
			fill_horizontal = 1,
			width_in_chars = 70,
			height_in_lines = 2,
			size = 'small',
		},
	}

	section[ #section + 1 ] = f:row {
		spacing = f:control_spacing(),
		f:push_button {
			title = 'Reset to Defaults',
			action = function()
				resetThresholdDefaults()
			end,
		},
	}

	return section
end

--- Wraps a section builder in pcall so a bug in one section can't blank
--- out the whole Plug-in Manager panel -- the other section still
--- renders, and the failing one shows the actual Lua error message
--- instead of silently vanishing. This can't be verified against a real
--- Lightroom install in this environment, so failing loudly and visibly
--- here is safer than assuming the happy path.
local function safeSection( f, title, builder, ... )
	local ok, result = pcall( builder, ... )
	if ok then
		return result
	end
	return {
		title = title .. ' -- failed to load',
		f:static_text {
			title = tostring( result ),
			fill_horizontal = 1,
			width_in_chars = 70,
			height_in_lines = 4,
		},
	}
end

local function sectionsForTopOfDialog( f, propertyTable )
	return {
		safeSection( f, 'AI Cleanup', connectionSection, f ),
		safeSection(
			f, 'AI Cleanup: Detection & Classification Thresholds', thresholdsSection, f, propertyTable
		),
	}
end

return {
	sectionsForTopOfDialog = sectionsForTopOfDialog,
}
