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
    members: list[GroupMemberResponse]


class JobResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    total_photos: int
    processed_photos: int
    failed_photos: int
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
    return GroupResponse(group_id=group.id, group_type=group.group_type, members=members)


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

    groups = repo.list_groups_for_job(job_id, limit=limit, offset=offset)
    return JobResultsResponse(
        job_id=job.id,
        status=job.status,
        total_photos=job.total_photos,
        processed_photos=job.processed_photos,
        failed_photos=job.failed_photos,
        groups=[_group_response(g, repo) for g in groups],
    )


@router.get("/groups/{group_id}", response_model=GroupResponse)
def get_group(group_id: int, repo: Repository = Depends(get_repository)) -> GroupResponse:
    group = repo.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    return _group_response(group, repo)
