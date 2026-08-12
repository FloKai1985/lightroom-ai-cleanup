"""Read-only endpoints: job results and group detail.

No endpoint here mutates anything — see docs/safety.md. Action preparation
(the only way this system will eventually *do* something in Lightroom) is
deliberately not part of Milestone 2; see docs/architecture.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lr_cleanup.api.deps import get_repository
from lr_cleanup.database.models import DuplicateGroup, GroupType, JobStatus, Recommendation
from lr_cleanup.database.repository import Repository

router = APIRouter(prefix="/api/v1", tags=["results"])


class PhotoAnalysisResponse(BaseModel):
    photo_id: int
    original_path: str
    file_hash: str
    perceptual_hash: str
    sharpness_score: float
    blur_confidence: float
    exposure_score: float
    highlight_clipping: float
    shadow_clipping: float
    analysis_version: int


class GroupMemberResponse(BaseModel):
    photo_id: int
    original_path: str
    rank: int
    recommendation: Recommendation
    keeper_score: float
    confidence: float
    reasons: list[str]


class GroupResponse(BaseModel):
    group_id: int
    group_type: GroupType
    generated_by_job_id: str | None
    """The job that most recently (re)computed this group. Group
    regeneration is a full, idempotent recompute (docs/algorithms.md), so
    there is only ever one current group set — this is provenance, not a
    per-job private snapshot."""
    members: list[GroupMemberResponse]


class JobResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    total_photos: int
    processed_photos: int
    failed_photos: int
    groups_regenerated: bool
    """Whether this job requested group regeneration. If `False`, `groups`
    is always empty — this job never touched grouping. If `True`, `groups`
    is the *current* group set, which reflects this job's run unless a
    later job has regenerated groups again since (see
    docs/architecture.md's Milestone-2 component map)."""
    groups: list[GroupResponse]


def _group_response(group: DuplicateGroup, repo: Repository) -> GroupResponse:
    members = []
    for member in sorted(group.members, key=lambda m: m.rank):
        photo = repo.get_photo(member.photo_id)
        members.append(
            GroupMemberResponse(
                photo_id=member.photo_id,
                original_path=photo.original_path if photo is not None else "",
                rank=member.rank,
                recommendation=member.recommendation,
                keeper_score=member.keeper_score,
                confidence=member.confidence,
                reasons=member.reasons,
            )
        )
    return GroupResponse(
        group_id=group.id,
        group_type=group.group_type,
        generated_by_job_id=group.analysis_job_id,
        members=members,
    )


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(
    job_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    repo: Repository = Depends(get_repository),
) -> JobResultsResponse:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    groups: list[DuplicateGroup] = []
    if job.groups_regenerated:
        # There is only one current group set (full-recompute model) — see
        # GroupResponse.generated_by_job_id's docstring.
        groups = repo.list_groups(limit=limit, offset=offset)

    return JobResultsResponse(
        job_id=job.id,
        status=job.status,
        total_photos=job.total_photos,
        processed_photos=job.processed_photos,
        failed_photos=job.failed_photos,
        groups_regenerated=job.groups_regenerated,
        groups=[_group_response(g, repo) for g in groups],
    )


@router.get("/groups/{group_id}", response_model=GroupResponse)
def get_group(group_id: int, repo: Repository = Depends(get_repository)) -> GroupResponse:
    group = repo.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    return _group_response(group, repo)


@router.get("/photos/{photo_id}/analysis", response_model=PhotoAnalysisResponse)
def get_photo_analysis(
    photo_id: int, repo: Repository = Depends(get_repository)
) -> PhotoAnalysisResponse:
    """The latest analysis for a single photo — sharpness/exposure/hash data
    that doesn't depend on the photo belonging to any duplicate group.

    Added for Milestone 3: the Lightroom plugin writes `AI Sharpness Score`
    / `AI Blur Confidence` custom metadata for every analyzed photo, not
    just ones that ended up in a group, and the job-results endpoint only
    ever returns grouped photos. The plugin already knows which photo ids
    it registered, so it calls this once per photo after a job completes
    rather than the API needing to persist a job→photo_id list anywhere
    (see docs/architecture.md's Milestone-2 component map for why that
    list isn't tracked).
    """
    photo = repo.get_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="photo not found")
    analysis = repo.get_analysis(photo_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="photo has not been analyzed yet")

    return PhotoAnalysisResponse(
        photo_id=photo.id,
        original_path=photo.original_path,
        file_hash=analysis.file_hash,
        perceptual_hash=analysis.perceptual_hash,
        sharpness_score=analysis.sharpness_score,
        blur_confidence=analysis.blur_confidence,
        exposure_score=analysis.exposure_score,
        highlight_clipping=analysis.highlight_clipping,
        shadow_clipping=analysis.shadow_clipping,
        analysis_version=analysis.analysis_version,
    )
