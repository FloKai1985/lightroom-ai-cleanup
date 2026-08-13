--[[----------------------------------------------------------------------------

PluginInfoProvider.lua
Plug-in Manager panel: backend URL + connection test, and per-request
analysis threshold/weight overrides (Thresholds.lua). `sectionsForTopOfDialog
(f, propertyTable)` returning an array of `{title=..., f:row{...}}` sections
is confirmed against Adobe's official
custommetadatasample.lrdevplugin/PluginInfoProvider.lua
(docs/lightroom-plugin.md).

Threshold fields are plain string edit fields, not numeric-typed ones —
see Thresholds.lua's docstring for why (this project doesn't guess at
unverified LrView numeric-binding behavior). They're validated
server-side when a job is created (src/lr_cleanup/api/jobs.py); an invalid
combination (e.g. keeper weights not summing to 1.0) surfaces as the same
"could not start analysis job" dialog AnalyzeSelected.lua already shows
for any other backend error.

------------------------------------------------------------------------------]]

local LrView = import 'LrView'
local LrPrefs = import 'LrPrefs'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'

local HttpClient = require 'HttpClient'
local Thresholds = require 'Thresholds'

local prefs = LrPrefs.prefsForPlugin()
Thresholds.ensureDefaults( prefs )

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
					'at a non-loopback address — see docs/safety.md.',
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
	-- Built by incremental append (section[#section+1] = ...) rather than
	-- `{ title = ..., unpack(rows) }` — `unpack` is a Lua 5.1 global that
	-- was removed in 5.2+ (moved to table.unpack); appending by index
	-- works identically across every Lua version and avoids relying on
	-- either name being correct for the runtime this actually executes in.
	local section = {
		title = 'AI Cleanup: Detection & Classification Thresholds',
	}

	for _, def in ipairs( Thresholds.DEFINITIONS ) do
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
			title = 'Applied to every analysis job you run — see docs/algorithms.md for what each ' ..
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
				Thresholds.resetToDefaults( prefs )
			end,
		},
	}

	return section
end

local function sectionsForTopOfDialog( f, propertyTable )
	return {
		connectionSection( f ),
		thresholdsSection( f, propertyTable ),
	}
end

return {
	sectionsForTopOfDialog = sectionsForTopOfDialog,
}
