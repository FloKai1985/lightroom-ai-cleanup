--[[----------------------------------------------------------------------------

HttpClient.lua
Thin JSON-over-LrHttp client for the local lr_cleanup service.

LrHttp.get/post signatures, the {field=,value=} header table shape, and the
"must run inside an async task" requirement are all confirmed against the
official API Reference — see docs/lightroom-plugin.md. Every function here
must therefore only be called from inside LrTasks.startAsyncTask (enforced
by callers, not here — this module has no task-launching logic of its own).

The service binds to 127.0.0.1 only, by design (docs/safety.md) — the
default below matches src/lr_cleanup/config.py's default port. It is
user-editable via the Plug-in Manager (PluginInfoProvider.lua) for the rare
case someone reconfigured LR_CLEANUP_PORT.

------------------------------------------------------------------------------]]

local LrHttp = import 'LrHttp'
local LrPrefs = import 'LrPrefs'

local Json = require 'Json'

local prefs = LrPrefs.prefsForPlugin()

local HttpClient = {}

local DEFAULT_BASE_URL = 'http://127.0.0.1:8765'
local TIMEOUT_SECONDS = 30

function HttpClient.baseUrl()
	-- Lua treats "" as truthy, so an empty (but set) pref must be checked
	-- explicitly rather than relying on `prefs.backendBaseUrl or DEFAULT_BASE_URL`.
	if prefs.backendBaseUrl == nil or prefs.backendBaseUrl == '' then
		return DEFAULT_BASE_URL
	end
	return prefs.backendBaseUrl
end

local jsonHeaders = {
	{ field = 'Content-Type', value = 'application/json' },
	{ field = 'Accept', value = 'application/json' },
}

--- Raises a Lua error (catch with LrTasks/pcall) if the request fails to
--- reach the service or the service returns a non-2xx status.
local function checkResponse( body, headers, url )
	if body == nil then
		error( 'AI Cleanup: could not reach the local service at ' .. url ..
			' — is it running? (scripts/run-server.sh)' )
	end
	local status = headers and headers.status
	if status and ( status < 200 or status >= 300 ) then
		error( 'AI Cleanup: ' .. url .. ' returned HTTP ' .. tostring( status ) .. ': ' .. tostring( body ) )
	end
end

--- GET `path` (e.g. '/health', '/api/v1/jobs/xyz') and decode the JSON body.
function HttpClient.getJson( path )
	local url = HttpClient.baseUrl() .. path
	local body, headers = LrHttp.get( url, jsonHeaders, TIMEOUT_SECONDS )
	checkResponse( body, headers, url )
	return Json.decode( body )
end

--- POST `bodyTable` as JSON to `path` and decode the JSON response body.
function HttpClient.postJson( path, bodyTable )
	local url = HttpClient.baseUrl() .. path
	local payload = Json.encode( bodyTable or {} )
	local body, headers = LrHttp.post( url, payload, jsonHeaders, 'POST', TIMEOUT_SECONDS )
	checkResponse( body, headers, url )
	return Json.decode( body )
end

return HttpClient
