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
import os
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice

import structlog

from lr_cleanup.analysis._imaging import load_bgr, resize_long_edge, to_gray
from lr_cleanup.analysis.candidate_groups import (
    PhotoForGrouping,
    group_exact_duplicates,
    group_near_duplicates,
)
from lr_cleanup.analysis.exposure import compute_exposure_from_gray
from lr_cleanup.analysis.file_hash import sha256_file
from lr_cleanup.analysis.keeper import KeeperCandidate, rank_group
from lr_cleanup.analysis.perceptual_hash import compute_phash_from_bgr
from lr_cleanup.analysis.sharpness import compute_sharpness_from_gray
from lr_cleanup.config import Settings, get_settings
from lr_cleanup.database.models import AnalysisJob, DuplicateGroup, JobStatus, Photo
from lr_cleanup.database.repository import AnalysisResult, GroupMemberInput, Repository

logger = structlog.get_logger(__name__)

# Below this many photos needing (re-)analysis, process-pool startup
# overhead isn't worth it -- run sequentially in-process instead.
#
# Measured, not guessed: benchmarked sequential vs. parallel wall-clock
# time analyzing realistically-sized photos (25MB original + 1600px
# textured JPEG preview, matching the plugin's real export pipeline) on
# an 8-core machine. Below ~20 photos, spawning worker processes and
# re-importing cv2/PIL/imagehash in each one (~0.25-0.4s fixed cost) cost
# more than the sequential work itself -- parallel was *slower*
# (0.6-0.9x) for 10-16 photos, only broke even around 16-20, and only
# became a clear win at 20+. Common Lightroom usage (selecting a handful
# of photos to check) stays on the sequential path, which is also what
# every existing single/two-photo unit test exercises via the plain
# `self.analyze_one` call path.
_MIN_PHOTOS_FOR_PARALLEL_ANALYSIS = 20

# Worker count is capped so each worker gets at least this many photos --
# also measured, not guessed: at a fixed 20-30 photos, using fewer,
# better-utilized workers beat maxing out at cpu_count (e.g. 30 photos:
# 4 workers hit 1.51x, 8 workers only 1.39x -- more workers than there's
# work to amortize their startup cost just adds overhead for no benefit).
_MIN_PHOTOS_PER_WORKER = 8


class PhotoAnalysisError(Exception):
    """A single photo failed to analyze. Caught per-photo so one bad file
    never aborts a batch job — see docs brief's Error handling section."""


@dataclass(frozen=True)
class _PhotoAnalysisTask:
    """Plain, picklable per-photo work item for the process pool -- a
    `Photo` ORM row can't cross a process boundary (it's tied to the
    SQLAlchemy session), so only the handful of fields `_analyze_photo_task`
    actually needs are carried across."""

    photo_id: int
    original_path: str
    image_path: str
    fingerprint: str


@dataclass(frozen=True)
class _PhotoAnalysisOutcome:
    photo_id: int
    result: AnalysisResult | None
    error: str | None


def _analyze_photo_task(task: _PhotoAnalysisTask, settings: Settings) -> _PhotoAnalysisOutcome:
    """The actual per-photo analysis work (image decode + hashing + metric
    computation) — CPU-bound and independent of every other photo, which is
    what makes it safe to run in a worker process. Module-level (not a
    method) so it can be pickled to worker processes; never raises — any
    failure comes back as `_PhotoAnalysisOutcome.error` so one bad photo
    can't take down a worker or the pool.

    Decodes `task.image_path` exactly once and shares the array across
    phash/sharpness/exposure, instead of each independently re-reading
    and re-decoding the same file (previously ~3 decodes per photo).
    Verified this doesn't change any computed value: sharpness and
    exposure already both decoded via OpenCV (`load_bgr`) and are
    unchanged here in every other respect (same resize/grayscale order,
    same `_from_gray` functions); phash previously decoded independently
    via PIL, and PIL vs. OpenCV's decoders were confirmed to produce
    bit-identical pixel data for this pipeline's real-world formats
    (JPEG/PNG/TIFF) — see `perceptual_hash.compute_phash_from_bgr`."""
    try:
        file_hash = sha256_file(task.original_path)
        bgr = load_bgr(task.image_path)

        phash = compute_phash_from_bgr(bgr)

        sharpness_gray = to_gray(resize_long_edge(bgr, settings.sharpness_working_size))
        sharpness = compute_sharpness_from_gray(sharpness_gray)

        exposure_gray = to_gray(bgr)
        exposure = compute_exposure_from_gray(
            exposure_gray, settings.highlight_clip_threshold, settings.shadow_clip_threshold
        )
    except (OSError, ValueError) as exc:
        return _PhotoAnalysisOutcome(
            task.photo_id, None, f"failed to analyze photo {task.photo_id}: {exc}"
        )

    result = AnalysisResult(
        file_hash=file_hash,
        perceptual_hash=phash,
        sharpness_score=sharpness.sharpness_score,
        blur_confidence=sharpness.blur_confidence,
        exposure_score=exposure.exposure_score,
        highlight_clipping=exposure.highlight_clipping,
        shadow_clipping=exposure.shadow_clipping,
        analysis_version=settings.analysis_version,
        fingerprint=task.fingerprint,
    )
    return _PhotoAnalysisOutcome(task.photo_id, result, None)


def _chunked(items: Iterable[Photo], size: int) -> Iterator[list[Photo]]:
    """Splits a (possibly lazily-streamed) iterable into fixed-size lists
    without ever materializing more than one chunk at a time — preserves
    the memory-bounded streaming `_resolve_photos`/`iter_all_photos`
    already provide for 100k+-photo libraries (docs/architecture.md's
    "Incremental analysis / scale")."""
    it = iter(items)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


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
        """Analyze a single photo in-process (synchronous). Used directly
        by the CLI's one-shot path and by `execute_job`'s small-batch/
        sequential fallback; delegates to the same `_analyze_photo_task`
        the process-pool path uses so there's exactly one implementation
        of the actual analysis work."""
        fingerprint = compute_fingerprint(
            photo.lightroom_id or photo.original_path,
            photo.file_size,
            photo.file_mtime,
            self.settings.analysis_version,
        )
        task = _PhotoAnalysisTask(
            photo_id=photo.id,
            original_path=photo.original_path,
            image_path=_image_path_for_analysis(photo),
            fingerprint=fingerprint,
        )
        outcome = _analyze_photo_task(task, self.settings)
        if outcome.error is not None:
            raise PhotoAnalysisError(outcome.error)
        assert outcome.result is not None  # error is None iff result is set
        return outcome.result

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

        # Chunked (not "collect every photo up front") so a whole-library
        # job still never holds more than `batch_size` photos in memory at
        # once — see _chunked's docstring.
        for chunk in _chunked(photos, self.settings.batch_size):
            pending: list[tuple[Photo, str]] = []
            for photo in chunk:
                fingerprint = compute_fingerprint(
                    photo.lightroom_id or photo.original_path,
                    photo.file_size,
                    photo.file_mtime,
                    self.settings.analysis_version,
                )
                cached = self.repository.get_analysis(photo.id)
                if cached is not None and cached.fingerprint == fingerprint:
                    log.info("analysis.cache_hit", photo_id=photo.id, stage="analyze", duration=0.0)
                    self.repository.update_job_progress(job, processed_delta=1)
                else:
                    pending.append((photo, fingerprint))

            self._analyze_pending(pending, job, log)

        status = JobStatus.COMPLETED if job.failed_photos < job.total_photos else JobStatus.FAILED
        self.repository.set_job_status(job, status, completed_at=datetime.now(UTC))

        if regenerate_groups:
            self.regenerate_groups(analysis_job_id=job.id)
            self.repository.mark_job_groups_regenerated(job)

        return job

    def _resolve_worker_count(self, pending_count: int) -> int:
        """Configured (or auto-detected) worker count, capped so each
        worker gets at least `_MIN_PHOTOS_PER_WORKER` photos. Applies even
        to an explicit `analysis_worker_processes` override -- there's no
        benefit to spinning up more workers than there's work to amortize
        their startup cost, regardless of what's configured."""
        configured = self.settings.analysis_worker_processes
        desired = configured if configured >= 1 else max(1, os.cpu_count() or 1)
        return min(desired, max(1, pending_count // _MIN_PHOTOS_PER_WORKER))

    def _analyze_pending(
        self,
        pending: list[tuple[Photo, str]],
        job: AnalysisJob,
        log: structlog.typing.FilteringBoundLogger,
    ) -> None:
        """Analyzes every (photo, fingerprint) pair that missed the cache,
        recording each result (or failure) against `job`. Dispatches to a
        process pool when there's enough work to be worth it; otherwise
        falls back to the plain sequential path — see
        _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS."""
        if not pending:
            return

        if len(pending) < _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS:
            self._analyze_sequential(pending, job, log)
            return

        worker_count = self._resolve_worker_count(len(pending))
        if worker_count == 1:
            self._analyze_sequential(pending, job, log)
        else:
            self._analyze_parallel(pending, job, log, worker_count)

    def _analyze_sequential(
        self,
        pending: list[tuple[Photo, str]],
        job: AnalysisJob,
        log: structlog.typing.FilteringBoundLogger,
    ) -> None:
        """One photo at a time, in this process, via `self.analyze_one` —
        the same call site tests monkeypatch to assert cache behavior, and
        the only reasonable choice for small batches where pool startup
        overhead would dominate the actual work."""
        for photo, _fingerprint in pending:
            started = time.monotonic()
            try:
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

    def _analyze_parallel(
        self,
        pending: list[tuple[Photo, str]],
        job: AnalysisJob,
        log: structlog.typing.FilteringBoundLogger,
        worker_count: int,
    ) -> None:
        """Same outcome as `_analyze_sequential`, but the actual image
        decode/hashing/metric work (`_analyze_photo_task`, CPU-bound and
        independent per photo) runs across `worker_count` processes.
        Every `self.repository.*` call still happens here, on the main
        process — worker processes never touch the DB/session."""
        tasks = {
            photo.id: _PhotoAnalysisTask(
                photo_id=photo.id,
                original_path=photo.original_path,
                image_path=_image_path_for_analysis(photo),
                fingerprint=fingerprint,
            )
            for photo, fingerprint in pending
        }
        started_by_id = {photo.id: time.monotonic() for photo, _ in pending}

        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(_analyze_photo_task, task, self.settings): photo_id
                for photo_id, task in tasks.items()
            }
            for future in as_completed(futures):
                photo_id = futures[future]
                duration = time.monotonic() - started_by_id[photo_id]
                try:
                    outcome = future.result()
                except Exception as exc:  # noqa: BLE001 - one failed photo must not crash the job
                    log.warning(
                        "analysis.failed",
                        photo_id=photo_id,
                        stage="analyze",
                        duration=duration,
                        error=str(exc),
                    )
                    self.repository.update_job_progress(job, failed_delta=1)
                    continue

                if outcome.error is not None:
                    log.warning(
                        "analysis.failed",
                        photo_id=photo_id,
                        stage="analyze",
                        duration=duration,
                        error=outcome.error,
                    )
                    self.repository.update_job_progress(job, failed_delta=1)
                else:
                    assert outcome.result is not None
                    self.repository.upsert_analysis(photo_id, outcome.result)
                    log.info(
                        "analysis.completed", photo_id=photo_id, stage="analyze", duration=duration
                    )
                    self.repository.update_job_progress(job, processed_delta=1)

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

        # A high-confidence-blur photo's "reason for cleanup" is out of
        # focus, full stop — it never enters near-duplicate/burst
        # comparison, so it can't also come back labeled LIKELY_REDUNDANT
        # (confusing: which is it?) or KEEPER (misleading: keeper of what,
        # a blurry shot?). Exact-duplicate detection stays unconditional —
        # it's a free hash-bucket lookup, and "this exact file exists
        # elsewhere" is still useful even if the file itself is blurry.
        # See docs/algorithms.md §2.
        blur_threshold = self.settings.high_confidence_blur_threshold
        near_dup_candidates = [
            g for g in grouping_inputs if analyses[g.photo_id].blur_confidence < blur_threshold
        ]

        results = group_exact_duplicates(grouping_inputs) + group_near_duplicates(
            near_dup_candidates,
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
