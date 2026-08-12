"""Orchestrates the analysis pipeline: fingerprint caching, per-photo
analysis, and duplicate/near-duplicate grouping + keeper ranking.

Used directly (synchronously) by the Milestone-1 CLI, and invoked by the
FastAPI background job worker (Milestone 2, see api/jobs.py) via the
`create_job`/`execute_job` split below. Contains no HTTP, no Lightroom SDK
calls, and no MCP — only orchestration over the analysis functions and the
repository.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from datetime import UTC, datetime

import structlog

from lr_cleanup.analysis.candidate_groups import (
    PhotoForGrouping,
    group_exact_duplicates,
    group_near_duplicates,
)
from lr_cleanup.analysis.exposure import compute_exposure
from lr_cleanup.analysis.file_hash import sha256_file
from lr_cleanup.analysis.keeper import KeeperCandidate, rank_group
from lr_cleanup.analysis.perceptual_hash import compute_phash
from lr_cleanup.analysis.sharpness import compute_sharpness
from lr_cleanup.config import Settings, get_settings
from lr_cleanup.database.models import AnalysisJob, DuplicateGroup, JobStatus, Photo
from lr_cleanup.database.repository import AnalysisResult, GroupMemberInput, Repository

logger = structlog.get_logger(__name__)


class PhotoAnalysisError(Exception):
    """A single photo failed to analyze. Caught per-photo so one bad file
    never aborts a batch job — see docs brief's Error handling section."""


def compute_fingerprint(
    identity: str, file_size: int, file_mtime: float, analysis_version: int
) -> str:
    """Cache key for a photo's analysis result.

    Changes when the underlying file changes (size/mtime) or when the
    analysis pipeline itself changes (`analysis_version`), so a bumped
    pipeline version transparently invalidates all cached results.
    """
    raw = f"{identity}:{file_size}:{file_mtime}:{analysis_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _image_path_for_analysis(photo: Photo) -> str:
    # Milestone 1 has no Lightroom plugin producing a rendered preview yet,
    # so this falls back to the original file. That only works for formats
    # OpenCV/Pillow can decode directly (JPEG/PNG/TIFF) — RAW libraries
    # require the plugin-rendered preview from Milestone 3 onward.
    return photo.preview_path or photo.original_path


class AnalyzerService:
    def __init__(self, repository: Repository, settings: Settings | None = None) -> None:
        self.repository = repository
        self.settings = settings or get_settings()

    def analyze_one(self, photo: Photo) -> AnalysisResult:
        image_path = _image_path_for_analysis(photo)
        try:
            file_hash = sha256_file(photo.original_path)
            phash = compute_phash(image_path)
            sharpness = compute_sharpness(image_path, self.settings.sharpness_working_size)
            exposure = compute_exposure(
                image_path,
                self.settings.highlight_clip_threshold,
                self.settings.shadow_clip_threshold,
            )
        except (OSError, ValueError) as exc:
            raise PhotoAnalysisError(f"failed to analyze photo {photo.id}: {exc}") from exc

        fingerprint = compute_fingerprint(
            photo.lightroom_id or photo.original_path,
            photo.file_size,
            photo.file_mtime,
            self.settings.analysis_version,
        )
        return AnalysisResult(
            file_hash=file_hash,
            perceptual_hash=phash,
            sharpness_score=sharpness.sharpness_score,
            blur_confidence=sharpness.blur_confidence,
            exposure_score=exposure.exposure_score,
            highlight_clipping=exposure.highlight_clipping,
            shadow_clipping=exposure.shadow_clipping,
            analysis_version=self.settings.analysis_version,
            fingerprint=fingerprint,
        )

    def _resolve_photos(self, photo_ids: list[int] | None) -> Iterable[Photo]:
        """Returns a lazy stream over the whole library (`photo_ids is
        None`) rather than a list — wrapping `iter_all_photos` in `list()`
        here would materialize every `Photo` row before the analysis loop
        even starts, defeating its batching (docs/architecture.md's
        Incremental analysis section). A specific id list is small enough
        (an MCP/plugin-submitted batch, never "the whole library") that
        resolving it eagerly is fine."""
        if photo_ids is None:
            return self.repository.iter_all_photos(batch_size=self.settings.batch_size)
        return [p for pid in photo_ids if (p := self.repository.get_photo(pid)) is not None]

    def create_job(self, photo_ids: list[int] | None = None) -> AnalysisJob:
        """Persist a `PENDING` job row sized for `photo_ids` (or the whole
        library) without doing any analysis work yet.

        Split out from `run_job` so the API layer (Milestone 2) can return a
        job id to the caller immediately and run `execute_job` in the
        background, while the CLI can still call `run_job` for synchronous,
        one-shot use.
        """
        total = len(photo_ids) if photo_ids is not None else self.repository.count_photos()
        return self.repository.create_job(total_photos=total)

    def run_job(
        self, photo_ids: list[int] | None = None, regenerate_groups: bool = False
    ) -> AnalysisJob:
        """Analyze photos (all registered photos, or a specific id list),
        reusing cached results when the fingerprint is unchanged."""
        job = self.create_job(photo_ids)
        return self.execute_job(job, photo_ids, regenerate_groups=regenerate_groups)

    def execute_job(
        self,
        job: AnalysisJob,
        photo_ids: list[int] | None = None,
        regenerate_groups: bool = False,
    ) -> AnalysisJob:
        """Run the analysis loop for an already-created `job` row.

        `photo_ids` must match whatever was used to size the job in
        `create_job` — it is passed separately (rather than re-derived from
        the job row) because the job schema doesn't persist the id list
        (see docs/architecture.md's Incremental analysis note).
        """
        photos = self._resolve_photos(photo_ids)
        self.repository.set_job_status(job, JobStatus.RUNNING, started_at=datetime.now(UTC))
        log = logger.bind(job_id=job.id)

        for photo in photos:
            started = time.monotonic()
            try:
                fingerprint = compute_fingerprint(
                    photo.lightroom_id or photo.original_path,
                    photo.file_size,
                    photo.file_mtime,
                    self.settings.analysis_version,
                )
                cached = self.repository.get_analysis(photo.id)
                if cached is not None and cached.fingerprint == fingerprint:
                    log.info(
                        "analysis.cache_hit",
                        photo_id=photo.id,
                        stage="analyze",
                        duration=time.monotonic() - started,
                    )
                else:
                    result = self.analyze_one(photo)
                    self.repository.upsert_analysis(photo.id, result)
                    log.info(
                        "analysis.completed",
                        photo_id=photo.id,
                        stage="analyze",
                        duration=time.monotonic() - started,
                    )
                self.repository.update_job_progress(job, processed_delta=1)
            except Exception as exc:  # noqa: BLE001 - one failed photo must not crash the job
                log.warning(
                    "analysis.failed",
                    photo_id=photo.id,
                    stage="analyze",
                    duration=time.monotonic() - started,
                    error=str(exc),
                )
                self.repository.update_job_progress(job, failed_delta=1)

        status = JobStatus.COMPLETED if job.failed_photos < job.total_photos else JobStatus.FAILED
        self.repository.set_job_status(job, status, completed_at=datetime.now(UTC))

        if regenerate_groups:
            self.regenerate_groups(analysis_job_id=job.id)
            self.repository.mark_job_groups_regenerated(job)

        return job

    def regenerate_groups(self, analysis_job_id: str | None = None) -> list[DuplicateGroup]:
        """Recompute all duplicate/near-duplicate/burst groups and keeper
        rankings from the currently-cached analyses. Idempotent: replaces
        the previous group set rather than accumulating stale groups."""
        photos = list(self.repository.iter_all_photos(batch_size=self.settings.batch_size))
        analyses = self.repository.list_analyses_for_photos([p.id for p in photos])
        photos_by_id = {p.id: p for p in photos}

        grouping_inputs = [
            PhotoForGrouping(
                photo_id=p.id,
                file_hash=analyses[p.id].file_hash,
                perceptual_hash=analyses[p.id].perceptual_hash,
                capture_time=p.capture_time,
                is_virtual_copy=p.is_virtual_copy,
                width=p.width or 0,
                height=p.height or 0,
            )
            for p in photos
            if p.id in analyses
        ]

        results = group_exact_duplicates(grouping_inputs) + group_near_duplicates(
            grouping_inputs,
            self.settings.burst_window_seconds,
            self.settings.phash_max_distance,
            self.settings.aspect_ratio_tolerance,
        )

        self.repository.clear_all_groups()
        created: list[DuplicateGroup] = []
        for group_result in results:
            candidates = []
            for photo_id in group_result.photo_ids:
                photo = photos_by_id[photo_id]
                analysis = analyses[photo_id]
                megapixels = 0.0
                if photo.width and photo.height:
                    megapixels = photo.width * photo.height / 1_000_000
                candidates.append(
                    KeeperCandidate(
                        photo_id=photo_id,
                        sharpness_score=analysis.sharpness_score,
                        exposure_score=analysis.exposure_score,
                        highlight_clipping=analysis.highlight_clipping,
                        shadow_clipping=analysis.shadow_clipping,
                        megapixels=megapixels,
                        existing_rating=photo.existing_rating,
                        existing_pick_status=photo.existing_pick_status,
                    )
                )

            ranked = rank_group(candidates)
            member_inputs = [
                GroupMemberInput(
                    photo_id=r.photo_id,
                    keeper_score=r.keeper_score,
                    rank=r.rank,
                    recommendation=r.recommendation,
                    confidence=r.confidence,
                    reasons=r.reasons,
                )
                for r in ranked
            ]
            created.append(
                self.repository.create_group(
                    group_result.group_type, member_inputs, analysis_job_id=analysis_job_id
                )
            )

        return created
