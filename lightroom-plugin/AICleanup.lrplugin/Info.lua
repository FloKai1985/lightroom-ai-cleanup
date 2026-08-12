--[[----------------------------------------------------------------------------

Info.lua
Plugin manifest for Lightroom AI Cleanup.

Table shape and every key used here is confirmed against Adobe's own
official sample plugins (ftp_upload, helloworld, custommetadatasample) —
see docs/lightroom-plugin.md for sources. LrSdkVersion is pinned low
(6.0) deliberately: every SDK call this plugin makes has been confirmed
present since old SDK versions, and a low pin maximizes compatibility
with older Lightroom Classic installations rather than requiring the
newest one.

------------------------------------------------------------------------------]]

return {

	LrSdkVersion = 6.0,
	LrSdkMinimumVersion = 6.0,

	LrToolkitIdentifier = 'com.lightroomaicleanup.plugin',
	LrPluginName = 'AI Cleanup',
	LrPluginInfoUrl = 'https://github.com/',

	-- Custom metadata field definitions (AI Sharpness Score, AI Blur
	-- Confidence, etc.) — see Metadata.lua and docs/lightroom-plugin.md's
	-- "Defining plugin metadata" section for the write-access gate this
	-- requires (catalog:withPrivateWriteAccessDo, not withWriteAccessDo).
	LrMetadataProvider = 'Metadata.lua',

	-- Plug-in Manager panel: shows backend status and lets the user set
	-- the local service URL (default http://127.0.0.1:8765).
	LrPluginInfoProvider = 'PluginInfoProvider.lua',

	-- Library menu items (Library > Plug-in Extras). Confirmed as the
	-- correct table for this — NOT LrExportMenuItems, which adds to the
	-- File menu and is meant for export-related commands. See
	-- docs/lightroom-plugin.md.
	LrLibraryMenuItems = {
		{
			title = 'AI Cleanup: Analyze Selected Photos',
			file = 'AnalyzeSelected.lua',
			enabledWhen = 'photosAvailable',
		},
	},

	VERSION = { major = 0, minor = 1, revision = 0, build = 0 },

}
