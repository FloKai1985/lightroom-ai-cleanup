--[[----------------------------------------------------------------------------

AnalyzeSelected.lua
Menu command (Library > Plug-in Extras > AI Cleanup: Analyze Selected
Photos). The Milestone-3 vertical slice, end to end:

  select photos -> render previews -> create job -> analyze -> read result
  -> create/update AI metadata -> create review collections

Every non-trivial SDK call below is confirmed in docs/lightroom-plugin.md.
Runs as a single one-shot LrTasks.startAsyncTask — no persistent
background loop (that's Milestone 5, explicitly out of scope here).

------------------------------------------------------------------------------]]

local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrFunctionContext = import 'LrFunctionContext'
local LrProgressScope = import 'LrProgressScope'
local LrDialogs = import 'LrDialogs'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrLogger = import 'LrLogger'

local HttpClient = require 'HttpClient'
local ReviewResults = require 'ReviewResults'

local logger = LrLogger( 'AICleanup' )
logger:enable( 'logfile' )

-- LrFileUtils.fileAttributes' dates are seconds since the Cocoa epoch
-- (2001-01-01T00:00:00Z), confirmed via the API Reference. Our backend's
-- Photo.file_mtime is a Unix timestamp (matches Python's os.stat().st_mtime),
-- so every mtime sent to the API is converted through this constant.
local COCOA_TO_UNIX_EPOCH_OFFSET = 978307200

local EXPORT_LONG_EDGE = 1600
local EXPORT_JPEG_QUALITY = 0.85 -- SDK's 0..1 scale; "quality approximately 85" on Lightroom's 0-100 UI scale

--------------------------------------------------------------------------------

local function isSupportedPhoto( photo )
	-- Video isn't in scope for this analyzer (docs/algorithms.md covers
	-- still-image sharpness/exposure/hashing only).
	local ok, isVideo = pcall( function() return photo:getRawMetadata( 'isVideo' ) end )
	return ok and not isVideo
end

--- Reads the raw metadata this plugin needs from a single photo. Returns
--- nil (rather than raising) on failure so one bad photo can't abort the
--- whole selection — mirrors the backend's own per-photo error handling
--- (src/lr_cleanup/service/analyzer.py).
local function readPhotoInfo( photo )
	local ok, result = pcall( function()
		local path = photo:getRawMetadata( 'path' )
		local attrs = LrFileUtils.fileAttributes( path )
		return {
			photo = photo,
			lightroomId = photo:getRawMetadata( 'uuid' ),
			originalPath = path,
			isVirtualCopy = photo:getRawMetadata( 'isVirtualCopy' ) or false,
			rating = photo:getRawMetadata( 'rating' ),
			pickStatus = photo:getRawMetadata( 'pickStatus' ),
			colorLabel = photo:getRawMetadata( 'colorNameForLabel' ),
			width = photo:getRawMetadata( 'width' ),
			height = photo:getRawMetadata( 'height' ),
			captureTimeIso = photo:getRawMetadata( 'dateTimeOriginalISO8601' ),
			fileSize = attrs and attrs.fileSize or 0,
			fileMtime = attrs and attrs.fileModificationDate and ( attrs.fileModificationDate + COCOA_TO_UNIX_EPOCH_OFFSET ) or os.time(),
		}
	end )
	if ok then
		return result
	end
	logger:error( 'readPhotoInfo failed: ' .. tostring( result ) )
	return nil
end

--- Exports JPEG renditions for every photo in `photoInfos` (see
--- docs/lightroom-plugin.md's confirmed export-settings table) into
--- `cacheDir`, filling in `.previewPath` on each entry whose export
--- succeeded. Photos whose export fails keep previewPath = nil and are
--- logged, not aborted.
local function exportRenditions( photoInfos, cacheDir, progressScope )
	local LrExportSession = import 'LrExportSession'

	local photosToExport = {}
	for _, info in ipairs( photoInfos ) do
		photosToExport[ #photosToExport + 1 ] = info.photo
	end

	local exportSession = LrExportSession {
		photosToExport = photosToExport,
		exportSettings = {
			LR_export_destinationType = 'specificFolder',
			LR_export_destinationPathPrefix = cacheDir,
			LR_export_useSubfolder = false,
			LR_format = 'JPEG',
			LR_export_colorSpace = 'sRGB',
			LR_jpeg_quality = EXPORT_JPEG_QUALITY,
			LR_size_doConstrain = true,
			LR_size_maxWidth = EXPORT_LONG_EDGE,
			LR_size_maxHeight = EXPORT_LONG_EDGE,
			LR_size_resizeType = 'longEdge',
			LR_size_units = 'pixels',
			LR_minimizeEmbeddedMetadata = true,
		},
	}

	local infoByPhoto = {}
	for _, info in ipairs( photoInfos ) do
		infoByPhoto[ info.photo ] = info
	end

	for i, rendition in exportSession:renditions() do
		if progressScope then
			progressScope:setPortionComplete( i, #photoInfos )
			progressScope:setCaption( 'Rendering preview ' .. tostring( i ) .. ' of ' .. tostring( #photoInfos ) .. '...' )
		end
		local success, pathOrError = rendition:waitForRender()
		local info = infoByPhoto[ rendition.photo ]
		if info then
			if success then
				info.previewPath = pathOrError
			else
				logger:error( 'export failed for ' .. tostring( info.originalPath ) .. ': ' .. tostring( pathOrError ) )
			end
		end
	end
end

--------------------------------------------------------------------------------

local function run( context )
	local progressScope = LrProgressScope {
		title = 'AI Cleanup: Analyzing Selected Photos',
		functionContext = context,
	}

	-- Error handling: backend unavailable (see project brief's Error
	-- handling section) — fail fast with a clear message rather than
	-- letting every subsequent HTTP call raise a confusing error.
	local healthOk = pcall( function() HttpClient.getJson( '/health' ) end )
	if not healthOk then
		LrDialogs.message(
			'AI Cleanup: backend unavailable',
			'Could not reach the local AI Cleanup service at ' .. HttpClient.baseUrl() ..
				'. Start it with scripts/run-server.sh, then try again.',
			'critical'
		)
		return
	end

	local catalog = LrApplication.activeCatalog()
	local selected = catalog:getTargetPhotos()
	if #selected == 0 then
		LrDialogs.message( 'AI Cleanup', 'Select one or more photos first.', 'info' )
		return
	end

	local photoInfos = {}
	local skippedVideo = 0
	for _, photo in ipairs( selected ) do
		if isSupportedPhoto( photo ) then
			local info = readPhotoInfo( photo )
			if info then
				photoInfos[ #photoInfos + 1 ] = info
			end
		else
			skippedVideo = skippedVideo + 1
		end
	end

	if #photoInfos == 0 then
		LrDialogs.message( 'AI Cleanup', 'No supported (non-video) photos to analyze in the selection.', 'info' )
		return
	end

	-- Dedicated cache directory (docs/lightroom-plugin.md /
	-- project brief: "architecture must allow cache cleanup"). One
	-- subfolder per run so concurrent runs (unlikely, but possible if a
	-- user invokes the command twice quickly) can't collide.
	local cacheRoot = LrPathUtils.child( LrPathUtils.getStandardFilePath( 'temp' ), 'AICleanupCache' )
	local cacheDir = LrPathUtils.child( cacheRoot, 'run-' .. tostring( os.time() ) )
	LrFileUtils.createAllDirectories( cacheDir )

	progressScope:setCaption( 'Rendering previews...' )
	exportRenditions( photoInfos, cacheDir, progressScope )

	local registerPhotos = {}
	for _, info in ipairs( photoInfos ) do
		if info.previewPath then
			registerPhotos[ #registerPhotos + 1 ] = {
				lightroom_id = info.lightroomId,
				original_path = info.originalPath,
				preview_path = info.previewPath,
				file_size = info.fileSize,
				file_mtime = info.fileMtime,
				is_virtual_copy = info.isVirtualCopy,
				width = info.width,
				height = info.height,
				capture_time = info.captureTimeIso,
				existing_rating = info.rating,
				existing_color_label = info.colorLabel,
				existing_pick_status = info.pickStatus,
			}
		end
	end

	if #registerPhotos == 0 then
		LrDialogs.message( 'AI Cleanup', 'No previews could be rendered for the selected photos.', 'warning' )
		return
	end

	progressScope:setCaption( 'Registering photos...' )
	local registerOk, registerResult = pcall( HttpClient.postJson, '/api/v1/photos/register', { photos = registerPhotos } )
	if not registerOk then
		LrDialogs.message( 'AI Cleanup: registration failed', tostring( registerResult ), 'critical' )
		return
	end

	-- Map photo -> backend photo_id via original_path (the register
	-- response echoes it back per src/lr_cleanup/api/jobs.py).
	local photoIdByPath = {}
	for _, entry in ipairs( registerResult.registered ) do
		photoIdByPath[ entry.original_path ] = entry.photo_id
	end
	for _, info in ipairs( photoInfos ) do
		info.photoId = photoIdByPath[ info.originalPath ]
	end

	local photoIds = {}
	for _, info in ipairs( photoInfos ) do
		if info.photoId then
			photoIds[ #photoIds + 1 ] = info.photoId
		end
	end

	progressScope:setCaption( 'Starting analysis job...' )
	local jobOk, job = pcall( HttpClient.postJson, '/api/v1/jobs', { photo_ids = photoIds, regenerate_groups = true } )
	if not jobOk then
		LrDialogs.message( 'AI Cleanup: could not start analysis job', tostring( job ), 'critical' )
		return
	end

	-- Poll for completion. The backend runs the job in a background task
	-- (src/lr_cleanup/api/jobs.py) — there is no push/webhook mechanism,
	-- so polling is the only option available to a Lua client.
	local status = job.status
	local pollCount = 0
	while status ~= 'completed' and status ~= 'failed' and status ~= 'canceled' do
		if progressScope:isCanceled() then
			LrDialogs.message( 'AI Cleanup', 'Canceled. The job may still finish in the background.', 'info' )
			return
		end
		LrTasks.sleep( 1 )
		pollCount = pollCount + 1
		local pollOk, current = pcall( HttpClient.getJson, '/api/v1/jobs/' .. job.job_id )
		if not pollOk then
			LrDialogs.message( 'AI Cleanup: lost contact with backend while waiting', tostring( current ), 'critical' )
			return
		end
		status = current.status
		progressScope:setCaption( string.format( 'Analyzing... (%d/%d processed)', current.processed_photos, current.total_photos ) )
		if pollCount > 300 then -- 5 minutes at 1s/poll
			LrDialogs.message( 'AI Cleanup', 'Timed out waiting for the analysis job to finish.', 'warning' )
			return
		end
	end

	if status ~= 'completed' then
		LrDialogs.message( 'AI Cleanup', 'Analysis job ended with status: ' .. tostring( status ), 'warning' )
		return
	end

	progressScope:setCaption( 'Fetching results...' )
	local resultsOk, jobResults = pcall( HttpClient.getJson, '/api/v1/jobs/' .. job.job_id .. '/results' )
	if not resultsOk then
		LrDialogs.message( 'AI Cleanup: could not fetch job results', tostring( jobResults ), 'critical' )
		return
	end

	-- Per-photo analysis (sharpness/exposure) isn't in the job-results
	-- payload for photos outside a group — see api/results.py's
	-- get_photo_analysis docstring for why this is a separate call.
	local photoRecords = {}
	for _, info in ipairs( photoInfos ) do
		if info.photoId then
			local analysisOk, analysis = pcall( HttpClient.getJson, '/api/v1/photos/' .. info.photoId .. '/analysis' )
			if analysisOk then
				photoRecords[ #photoRecords + 1 ] = { photo = info.photo, photoId = info.photoId, analysis = analysis }
			else
				logger:error( 'could not fetch analysis for photo ' .. tostring( info.photoId ) .. ': ' .. tostring( analysis ) )
			end
		end
	end

	progressScope:setCaption( 'Writing AI metadata and collections...' )
	ReviewResults.applyResults( catalog, photoRecords, jobResults )

	-- Cache cleanup: the backend has already read every rendition by this
	-- point (analysis is synchronous-by-the-time-the-job-completes), so
	-- this run's temp files serve no further purpose.
	LrFileUtils.delete( cacheDir )

	progressScope:done()

	local message = string.format(
		'Analyzed %d photo(s). %d duplicate/near-duplicate group(s) found.',
		#photoRecords, #( jobResults.groups or {} )
	)
	if skippedVideo > 0 then
		message = message .. ' (' .. tostring( skippedVideo ) .. ' video file(s) skipped.)'
	end
	LrDialogs.message( 'AI Cleanup: done', message, 'info' )
end

LrTasks.startAsyncTask( function()
	LrFunctionContext.callWithContext( 'AICleanup.AnalyzeSelected', run )
end, 'AI Cleanup: Analyze Selected Photos' )
