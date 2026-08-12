--[[----------------------------------------------------------------------------

PluginInfoProvider.lua
Plug-in Manager panel: lets the user see/edit the local service URL and
test the connection. `sectionsForTopOfDialog(f, propertyTable)` returning
an array of `{title=..., f:row{...}}` sections is confirmed against
Adobe's official custommetadatasample.lrdevplugin/PluginInfoProvider.lua
(docs/lightroom-plugin.md).

------------------------------------------------------------------------------]]

local LrView = import 'LrView'
local LrPrefs = import 'LrPrefs'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'

local HttpClient = require 'HttpClient'

local prefs = LrPrefs.prefsForPlugin()

local function sectionsForTopOfDialog( f, _ )
	local bind = LrView.bind

	return {
		{
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
		},
	}
end

return {
	sectionsForTopOfDialog = sectionsForTopOfDialog,
}
