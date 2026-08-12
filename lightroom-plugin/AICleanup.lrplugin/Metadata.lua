--[[----------------------------------------------------------------------------

Metadata.lua
Custom metadata field definitions (the LrMetadataProvider referenced from
Info.lua).

Table shape (`metadataFieldsForPhotos`, `schemaVersion`) confirmed against
Adobe's official custommetadatasample.lrdevplugin — see
docs/lightroom-plugin.md.

Every field uses dataType='string', including the numeric-looking ones
(AI Sharpness Score, AI Blur Confidence, AI Keeper Score) — no source
consulted while building this plugin confirmed a numeric/float dataType
exists, so this is a deliberate conservative choice, not an oversight
(docs/lightroom-plugin.md's "Remaining caveats"). Numbers are written as
formatted strings (e.g. "0.91") by ReviewResults.lua.

These field ids must stay in sync with the `Fields` table in
ReviewResults.lua, which is what actually reads/writes them via
photo:getPropertyForPlugin / setPropertyForPlugin.

------------------------------------------------------------------------------]]

return {

	metadataFieldsForPhotos = {
		{ id = 'aiCleanupStatus', title = 'AI Cleanup Status', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiSharpnessScore', title = 'AI Sharpness Score', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiBlurConfidence', title = 'AI Blur Confidence', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiDuplicateType', title = 'AI Duplicate Type', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiDuplicateGroup', title = 'AI Duplicate Group', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiSimilarityGroup', title = 'AI Similarity Group', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiKeeperScore', title = 'AI Keeper Score', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiRecommendation', title = 'AI Recommendation', dataType = 'string', searchable = true, browsable = true },
		{ id = 'aiAnalysisVersion', title = 'AI Analysis Version', dataType = 'string', searchable = true, browsable = true },
	},

	schemaVersion = 1,

}
