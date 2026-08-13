"""Photo registration and analysis job lifecycle.

`POST /api/v1/jobs` returns as soon as the job row exists (`PENDING`) and
runs the actual analysis in a background task, which opens its own DB
session — see `deps.get_session`'s docstring for why it can't reuse the
request's session.
"""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from lr_cleanup.api.deps import get_repository
from lr_cleanup.config import Settings, get_settings
from lr_cleanup.database.models import AnalysisJob, JobStatus
from lr_cleanup.database.repository import PhotoInput, Repository
from lr_cleanup.database.session import session_scope
from lr_cleanup.service.analyzer import AnalyzerService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["jobs"])


class PhotoRegisterItem(BaseModel):
    original_path: str
    file_size: int
    file_mtime: float
    lightroom_id: str | None = None
    preview_path: str | None = None
    capture_time: datetime | None = None
    is_virtual_copy: bool = False
    width: int | None = None
    height: int | None = None
    existing_rating: int | None = None
    existing_color_label: str | None = None
    existing_pick_status: int | None = None


class PhotoRegisterRequest(BaseModel):
    photos: list[PhotoRegisterItem]


class RegisteredPhoto(BaseModel):
    photo_id: int
    original_path: str


class PhotoRegisterResponse(BaseModel):
    registered: list[RegisteredPhoto]


@router.post("/photos/register", response_model=PhotoRegisterResponse)
def register_photos(
    payload: PhotoRegisterRequest, repo: Repository = Depends(get_repository)
) -> PhotoRegisterResponse:
    if not payload.photos:
        raise HTTPException(status_code=400, detail="photos must not be empty")

    registered: list[RegisteredPhoto] = []
    for item in payload.photos:
        photo = repo.upsert_photo(PhotoInput(**item.model_dump()))
        registered.append(RegisteredPhoto(photo_id=photo.id, original_path=photo.original_path))
    return PhotoRegisterResponse(registered=registered)


class JobCreateRequest(BaseModel):
    photo_ids: list[int] | None = None
    """Photos to analyze. `None` (the default) analyzes every registered photo."""
    regenerate_groups: bool = True
    """Whether to recompute duplicate/near-duplicate groups + keeper ranking
    once analysis finishes, tagged with this job's id."""

    # Per-request threshold/weight overrides — every field here mirrors a
    # Settings field (config.py) and defaults to `None`, meaning "use the
    # server's configured default." This is how the Lightroom plugin's
    # Plug-in Manager settings panel applies the user's chosen thresholds
    # without needing any server-side persistence of its own — the plugin
    # is the source of truth for "what should this run use," stored in
    # its own LrPrefs (see lightroom-plugin/.../PluginInfoProvider.lua).
    burst_window_seconds: float | None = None
    phash_max_distance: int | None = None
    aspect_ratio_tolerance: float | None = None
    weight_sharpness: float | None = None
    weight_exposure: float | None = None
    weight_technical: float | None = None
    weight_existing_preference: float | None = None
    highlight_clip_threshold: float | None = None
    shadow_clip_threshold: float | None = None
    high_confidence_blur_threshold: float | None = None

    def overrides(self) -> dict[str, float | int]:
        fields = (
            "burst_window_seconds",
            "phash_max_distance",
            "aspect_ratio_tolerance",
            "weight_sharpness",
            "weight_exposure",
            "weight_technical",
            "weight_existing_preference",
            "highlight_clip_threshold",
            "shadow_clip_threshold",
            "high_confidence_blur_threshold",
        )
        values = {f: getattr(self, f) for f in fields}
        return {f: v for f, v in values.items() if v is not None}


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    total_photos: int
    processed_photos: int
    failed_photos: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_summary: str | None


def _resolve_settings(overrides: dict[str, float | int]) -> Settings:
    """Overlays `overrides` onto the server's configured defaults and
    re-validates the result — e.g. the four keeper-ranking weights must
    still sum to 1.0 even after a partial override. `Settings.model_copy`
    doesn't re-run validators, so this reconstructs via the constructor
    instead, which does."""
    base = get_settings()
    if not overrides:
        return base
    merged = base.model_dump()
    merged.update(overrides)
    try:
        return Settings(**merged)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _job_response(job: AnalysisJob) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        status=job.status,
        total_photos=job.total_photos,
        processed_photos=job.processed_photos,
        failed_photos=job.failed_photos,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_summary=job.error_summary,
    )


def _run_job_in_background(
    session_factory: sessionmaker[Session],
    job_id: str,
    photo_ids: list[int] | None,
    regenerate_groups: bool,
    settings: Settings,
) -> None:
    with session_scope(session_factory) as session:
        repo = Repository(session)
        job = repo.get_job(job_id)
        if job is None:
            logger.warning("job.not_found_for_background_execution", job_id=job_id)
            return
        try:
            AnalyzerService(repo, settings=settings).execute_job(
                job, photo_ids, regenerate_groups=regenerate_groups
            )
        except Exception as exc:  # noqa: BLE001 - never let the background task die silently
            logger.error("job.background_execution_failed", job_id=job_id, error=str(exc))
            repo.set_job_status(job, JobStatus.FAILED, error_summary=str(exc))


@router.post("/jobs", response_model=JobResponse, status_code=202)
def create_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    repo: Repository = Depends(get_repository),
) -> JobResponse:
    # Resolved (and validated) before any DB writes, so a bad override
    # (e.g. keeper weights that don't sum to 1.0) fails fast with a 400
    # instead of after a job row already exists.
    settings = _resolve_settings(payload.overrides())

    job = AnalyzerService(repo, settings=settings).create_job(payload.photo_ids)
    # Commit explicitly (rather than relying on the request-scoped
    # dependency's teardown timing) so the job row is durable before the
    # background task — running in its own session/connection — looks it up.
    repo.session.commit()

    background_tasks.add_task(
        _run_job_in_background,
        request.app.state.session_factory,
        job.id,
        payload.photo_ids,
        payload.regenerate_groups,
        settings,
    )
    return _job_response(job)


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repo: Repository = Depends(get_repository),
) -> list[JobResponse]:
    jobs = repo.list_jobs(limit=limit, offset=offset)
    return [_job_response(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, repo: Repository = Depends(get_repository)) -> JobResponse:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)
