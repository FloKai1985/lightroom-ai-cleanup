--[[----------------------------------------------------------------------------

ReviewResults.lua
Applies analysis results already fetched from the local service: writes AI
plugin metadata and files photos into the "AI Photo Cleanup" review
collections. Never touches a star rating, color label, or pick flag — see
docs/safety.md. This is a library module, not a menu-item entry point (see
AnalyzeSelected.lua, which calls it after polling the backend for results).

catalog:createCollectionSet/createCollection(name, parent, canReturnPrior)
and collection:addPhotos(photos) are confirmed against the official API
Reference (docs/lightroom-plugin.md). Plugin-metadata writes go through
catalog:withPrivateWriteAccessDo; collection creation/membership (built-in,
undo-visible catalog data) goes through catalog:withWriteAccessDo — see
docs/lightroom-plugin.md's "Defining plugin metadata" / "Creating/managing
collections" sections for why these are different gates.

------------------------------------------------------------------------------]]

local LrApplication = import 'LrApplication'

local ReviewResults = {}

-- Must stay in sync with the `id`s declared in Metadata.lua.
local Fields = {
	status = 'aiCleanupStatus',
	sharpness = 'aiSharpnessScore',
	blur = 'aiBlurConfidence',
	duplicateType = 'aiDuplicateType',
	duplicateGroup = 'aiDuplicateGroup',
	similarityGroup = 'aiSimilarityGroup',
	keeperScore = 'aiKeeperScore',
	recommendation = 'aiRecommendation',
	analysisVersion = 'aiAnalysisVersion',
}

local COLLECTION_SET_NAME = 'AI Photo Cleanup'
local COLLECTION_KEEPERS = '01 – Recommended Keepers'
local COLLECTION_BLUR = '02 – High Confidence Blur'
local COLLECTION_EXACT_DUPES = '03 – Exact Duplicates'
local COLLECTION_NEAR_DUPES = '04 – Near Duplicates'
local COLLECTION_REVIEW = '05 – Review Required'
local COLLECTION_PROCESSED = '06 – Processed'

-- Same threshold as src/lr_cleanup/analysis/sharpness.py's
-- is_high_confidence_blur default. Not read from the backend because this
-- only decides *collection membership* (a plugin-local UX convenience);
-- the authoritative blur_confidence number is always the one written to
-- AI Blur Confidence, regardless of this threshold.
local HIGH_CONFIDENCE_BLUR_THRESHOLD = 0.75

-- Near-duplicate/burst ranking is preferred over an exact-duplicate
-- group's ranking for keeper-related fields (recommendation, keeper
-- score, collection placement) when a photo belongs to both, since it
-- reflects the more visually-informed comparison. See docs/algorithms.md.
local GROUP_TYPE_PRIORITY = { burst = 3, near_duplicate = 2, exact_duplicate = 1 }

--------------------------------------------------------------------------------

--- Idempotently ensures the "AI Photo Cleanup" collection set and its six
--- child collections exist, returning { [name] = LrCollection }. Must be
--- called from within an async task; performs its own write-access gate.
function ReviewResults.ensureCollections( catalog )
	local collections = {}
	catalog:withWriteAccessDo( 'AI Cleanup: create review collections', function()
		local set = catalog:createCollectionSet( COLLECTION_SET_NAME, nil, true )
		local names = {
			COLLECTION_KEEPERS, COLLECTION_BLUR, COLLECTION_EXACT_DUPES,
			COLLECTION_NEAR_DUPES, COLLECTION_REVIEW, COLLECTION_PROCESSED,
		}
		for _, name in ipairs( names ) do
			collections[ name ] = catalog:createCollection( name, set, true )
		end
	end )
	return collections
end

--------------------------------------------------------------------------------

--- Reduces a job-results `groups` array (see api/results.py::GroupResponse)
--- into per-photo info: { [photoId] = { duplicateType, duplicateGroupId,
--- similarityGroupId, recommendation, keeperScore, reasons, groupTypePriority } }
local function buildPhotoGroupInfo( groups )
	local info = {}
	for _, group in ipairs( groups or {} ) do
		for _, member in ipairs( group.members or {} ) do
			local entry = info[ member.photo_id ]
			if not entry then
				entry = {}
				info[ member.photo_id ] = entry
			end

			if group.group_type == 'exact_duplicate' then
				entry.duplicateType = entry.duplicateType or 'exact_duplicate'
				entry.duplicateGroupId = group.group_id
			else -- 'burst' or 'near_duplicate'
				entry.similarityGroupId = group.group_id
			end

			local priority = GROUP_TYPE_PRIORITY[ group.group_type ] or 0
			if not entry.groupTypePriority or priority > entry.groupTypePriority then
				entry.groupTypePriority = priority
				entry.recommendation = member.recommendation
				entry.keeperScore = member.keeper_score
				entry.reasons = member.reasons
				-- Only near-dup/burst groups drive the "type" for
				-- collection routing when a photo is in both kinds —
				-- exact_duplicate alone still sets duplicateType above.
				if group.group_type ~= 'exact_duplicate' then
					entry.duplicateType = group.group_type
				end
			end
		end
	end
	return info
end

--------------------------------------------------------------------------------

local function formatNumber( n )
	if n == nil then
		return ''
	end
	return string.format( '%.4f', n )
end

--- Writes AI plugin metadata and files each photo into the appropriate
--- review collection(s).
---
--- `photoRecords`: array of { photo = <LrPhoto>, photoId = <int>, analysis = <table> }
---   `analysis` is the decoded body of GET /api/v1/photos/{id}/analysis.
--- `jobResults`: decoded body of GET /api/v1/jobs/{id}/results.
function ReviewResults.applyResults( catalog, photoRecords, jobResults )
	local groupInfo = buildPhotoGroupInfo( jobResults.groups )
	local collections = ReviewResults.ensureCollections( catalog )

	catalog:withPrivateWriteAccessDo( function()
		for _, record in ipairs( photoRecords ) do
			local photo = record.photo
			local info = groupInfo[ record.photoId ] or {}

			photo:setPropertyForPlugin( _PLUGIN, Fields.status, 'analyzed' )
			photo:setPropertyForPlugin( _PLUGIN, Fields.sharpness, formatNumber( record.analysis.sharpness_score ) )
			photo:setPropertyForPlugin( _PLUGIN, Fields.blur, formatNumber( record.analysis.blur_confidence ) )
			photo:setPropertyForPlugin( _PLUGIN, Fields.analysisVersion, tostring( record.analysis.analysis_version ) )
			photo:setPropertyForPlugin( _PLUGIN, Fields.duplicateType, info.duplicateType or '' )
			photo:setPropertyForPlugin( _PLUGIN, Fields.duplicateGroup, info.duplicateGroupId and tostring( info.duplicateGroupId ) or '' )
			photo:setPropertyForPlugin( _PLUGIN, Fields.similarityGroup, info.similarityGroupId and tostring( info.similarityGroupId ) or '' )
			photo:setPropertyForPlugin( _PLUGIN, Fields.keeperScore, formatNumber( info.keeperScore ) )
			photo:setPropertyForPlugin( _PLUGIN, Fields.recommendation, info.recommendation or '' )
		end
	end )

	catalog:withWriteAccessDo( 'AI Cleanup: file photos into review collections', function()
		for _, record in ipairs( photoRecords ) do
			local photo = record.photo
			local info = groupInfo[ record.photoId ] or {}

			collections[ COLLECTION_PROCESSED ]:addPhotos( { photo } )

			if record.analysis.blur_confidence >= HIGH_CONFIDENCE_BLUR_THRESHOLD then
				collections[ COLLECTION_BLUR ]:addPhotos( { photo } )
			end
			if info.duplicateGroupId then
				collections[ COLLECTION_EXACT_DUPES ]:addPhotos( { photo } )
			end
			if info.similarityGroupId then
				collections[ COLLECTION_NEAR_DUPES ]:addPhotos( { photo } )
			end
			if info.recommendation == 'KEEPER' then
				collections[ COLLECTION_KEEPERS ]:addPhotos( { photo } )
			elseif info.recommendation == 'REVIEW' then
				collections[ COLLECTION_REVIEW ]:addPhotos( { photo } )
			end
		end
	end )
end

return ReviewResults
