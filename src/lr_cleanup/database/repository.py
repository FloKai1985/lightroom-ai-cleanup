"""Repository: the only place that issues SQLAlchemy queries.

Kept deliberately thin and free of analysis logic — callers (service layer,
API layer, CLI) decide *what* to compute; this module only persists and
retrieves it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from lr_cleanup.database.models import (
    Analysis,
    AnalysisJob,
    DuplicateGroup,
    GroupMember,
    GroupType,
    JobStatus,
    Photo,
    Recommendation,
)


@dataclass
class PhotoInput:
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


@dataclass
class AnalysisResult:
    file_hash: str
    perceptual_hash: str
    sharpness_score: float
    blur_confidence: float
    exposure_score: float
    highlight_clipping: float
    shadow_clipping: float
    analysis_version: int
    fingerprint: str


@dataclass
class GroupMemberInput:
    photo_id: int
    keeper_score: float
    rank: int
    recommendation: Recommendation
    confidence: float
    reasons: list[str] = field(default_factory=list)


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Photos -----------------------------------------------------

    def upsert_photo(self, data: PhotoInput) -> Photo:
        photo = self.session.execute(
            select(Photo).where(Photo.original_path == data.original_path)
        ).scalar_one_or_none()
        if photo is None:
            photo = Photo(original_path=data.original_path)
            self.session.add(photo)

        photo.lightroom_id = data.lightroom_id
        photo.preview_path = data.preview_path
        photo.capture_time = data.capture_time
        photo.file_size = data.file_size
        photo.file_mtime = data.file_mtime
        photo.is_virtual_copy = data.is_virtual_copy
        photo.width = data.width
        photo.height = data.height
        photo.existing_rating = data.existing_rating
        photo.existing_color_label = data.existing_color_label
        photo.existing_pick_status = data.existing_pick_status

        self.session.flush()
        return photo

    def get_photo(self, photo_id: int) -> Photo | None:
        return self.session.get(Photo, photo_id)

    def list_photos(self, limit: int = 200, offset: int = 0) -> list[Photo]:
        stmt = select(Photo).order_by(Photo.id).limit(limit).offset(offset)
        return list(self.session.execute(stmt).scalars().all())

    def count_photos(self) -> int:
        return len(self.session.execute(select(Photo.id)).all())

    def iter_all_photos(self, batch_size: int = 200) -> Iterator[Photo]:
        """Stream every photo in fixed-size batches — never materializes the
        whole library in memory. See docs/architecture.md "Incremental analysis"."""
        offset = 0
        while True:
            batch = self.list_photos(limit=batch_size, offset=offset)
            if not batch:
                return
            yield from batch
            offset += batch_size

    # --- Analysis / caching ------------------------------------------

    def get_analysis(self, photo_id: int) -> Analysis | None:
        return self.session.execute(
            select(Analysis).where(Analysis.photo_id == photo_id)
        ).scalar_one_or_none()

    def upsert_analysis(self, photo_id: int, result: AnalysisResult) -> Analysis:
        analysis = self.get_analysis(photo_id)
        if analysis is None:
            analysis = Analysis(photo_id=photo_id)
            self.session.add(analysis)

        analysis.file_hash = result.file_hash
        analysis.perceptual_hash = result.perceptual_hash
        analysis.sharpness_score = result.sharpness_score
        analysis.blur_confidence = result.blur_confidence
        analysis.exposure_score = result.exposure_score
        analysis.highlight_clipping = result.highlight_clipping
        analysis.shadow_clipping = result.shadow_clipping
        analysis.analysis_version = result.analysis_version
        analysis.fingerprint = result.fingerprint

        self.session.flush()
        return analysis

    def list_analyses_for_photos(self, photo_ids: list[int]) -> dict[int, Analysis]:
        if not photo_ids:
            return {}
        stmt = select(Analysis).where(Analysis.photo_id.in_(photo_ids))
        return {a.photo_id: a for a in self.session.execute(stmt).scalars().all()}

    # --- Jobs ----------------------------------------------------------

    def create_job(self, total_photos: int) -> AnalysisJob:
        job = AnalysisJob(total_photos=total_photos, status=JobStatus.PENDING)
        self.session.add(job)
        self.session.flush()
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        return self.session.get(AnalysisJob, job_id)

    def update_job_progress(
        self, job: AnalysisJob, *, processed_delta: int = 0, failed_delta: int = 0
    ) -> None:
        job.processed_photos += processed_delta
        job.failed_photos += failed_delta
        self.session.flush()

    def set_job_status(
        self,
        job: AnalysisJob,
        status: JobStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_summary: str | None = None,
    ) -> None:
        job.status = status
        if started_at is not None:
            job.started_at = started_at
        if completed_at is not None:
            job.completed_at = completed_at
        if error_summary is not None:
            job.error_summary = error_summary
        self.session.flush()

    # --- Groups ----------------------------------------------------------

    def create_group(
        self,
        group_type: GroupType,
        members: list[GroupMemberInput],
        analysis_job_id: str | None = None,
    ) -> DuplicateGroup:
        group = DuplicateGroup(group_type=group_type, analysis_job_id=analysis_job_id)
        self.session.add(group)
        self.session.flush()

        for m in members:
            self.session.add(
                GroupMember(
                    group_id=group.id,
                    photo_id=m.photo_id,
                    keeper_score=m.keeper_score,
                    rank=m.rank,
                    recommendation=m.recommendation,
                    reasons=m.reasons,
                    confidence=m.confidence,
                )
            )
        self.session.flush()
        return group

    def clear_all_groups(self) -> None:
        for group in self.session.execute(select(DuplicateGroup)).scalars().all():
            self.session.delete(group)
        self.session.flush()

    def clear_groups_for_job(self, job_id: str) -> None:
        stmt = select(DuplicateGroup).where(DuplicateGroup.analysis_job_id == job_id)
        for group in self.session.execute(stmt).scalars().all():
            self.session.delete(group)
        self.session.flush()

    def get_group(self, group_id: int) -> DuplicateGroup | None:
        return self.session.get(DuplicateGroup, group_id)

    def list_groups(
        self, group_type: GroupType | None = None, limit: int = 100, offset: int = 0
    ) -> list[DuplicateGroup]:
        stmt = select(DuplicateGroup).order_by(DuplicateGroup.id).limit(limit).offset(offset)
        if group_type is not None:
            stmt = stmt.where(DuplicateGroup.group_type == group_type)
        return list(self.session.execute(stmt).scalars().all())

    def list_blurry_photos(
        self, blur_confidence_min: float = 0.6, limit: int = 100, offset: int = 0
    ) -> list[Analysis]:
        stmt = (
            select(Analysis)
            .where(Analysis.blur_confidence >= blur_confidence_min)
            .order_by(Analysis.blur_confidence.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.execute(stmt).scalars().all())
