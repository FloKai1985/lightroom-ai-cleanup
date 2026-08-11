"""SQLAlchemy models — the local SQLite schema.

This is a durable *staging* store for AI-derived data. It is never, and
must never become, a substitute for or a writer of Lightroom's `.lrcat`
catalog. See docs/safety.md.

`PreparedAction` / `ActionLog` are part of the Milestone-2/4 action-queue
design (MCP prepares actions, a human confirms, the plugin applies). The
schema is defined now so it does not have to migrate later, but nothing in
Milestone 1 writes to these two tables yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class GroupType(enum.StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    BURST = "burst"


class Recommendation(enum.StrEnum):
    KEEPER = "KEEPER"
    REVIEW = "REVIEW"
    LIKELY_REDUNDANT = "LIKELY_REDUNDANT"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ActionType(enum.StrEnum):
    """Non-destructive action kinds only — see docs/safety.md.

    Deliberately excludes anything that deletes files or overwrites an
    existing rating/color-label/pick-flag.
    """

    ADD_TO_REVIEW_COLLECTION = "add_to_review_collection"
    SET_PLUGIN_METADATA = "set_plugin_metadata"


class ActionStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    APPLIED = "applied"
    UNDONE = "undone"
    REJECTED = "rejected"


class ActionEvent(enum.StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    APPLIED = "applied"
    UNDONE = "undone"
    FAILED = "failed"


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    lightroom_id: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    original_path: Mapped[str] = mapped_column(String, unique=True, index=True)
    preview_path: Mapped[str | None] = mapped_column(String)

    capture_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_size: Mapped[int] = mapped_column(Integer)
    file_mtime: Mapped[float] = mapped_column(Float)
    is_virtual_copy: Mapped[bool] = mapped_column(default=False)

    # Not in the brief's minimal field list but required by candidate_groups
    # (aspect-ratio guard) and keeper ranking (resolution component).
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    existing_rating: Mapped[int | None] = mapped_column(Integer)
    existing_color_label: Mapped[str | None] = mapped_column(String)
    existing_pick_status: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    analysis: Mapped[Analysis | None] = relationship(
        back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )
    group_memberships: Mapped[list[GroupMember]] = relationship(
        back_populates="photo", cascade="all, delete-orphan"
    )


class Analysis(Base):
    """Latest analysis result for a photo. One row per photo (upserted)."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), unique=True, index=True
    )

    file_hash: Mapped[str] = mapped_column(String, index=True)
    perceptual_hash: Mapped[str] = mapped_column(String, index=True)

    sharpness_score: Mapped[float] = mapped_column(Float)
    blur_confidence: Mapped[float] = mapped_column(Float)

    exposure_score: Mapped[float] = mapped_column(Float)
    highlight_clipping: Mapped[float] = mapped_column(Float)
    shadow_clipping: Mapped[float] = mapped_column(Float)

    analysis_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    """Cache key: derived from (lightroom_id or path, file_size, file_mtime,
    analysis_version). See service/analyzer.py::compute_fingerprint."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    photo: Mapped[Photo] = relationship(back_populates="analysis")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)

    total_photos: Mapped[int] = mapped_column(Integer, default=0)
    processed_photos: Mapped[int] = mapped_column(Integer, default=0)
    failed_photos: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(String)

    groups_regenerated: Mapped[bool] = mapped_column(default=False)
    """Whether this job actually ran group regeneration (vs. analysis-only).
    Since regeneration is a full, idempotent recompute (docs/algorithms.md),
    there is only ever one current group set — this flag tells the API
    whether *this* job is the reason that current set looks the way it
    does, without pretending each job owns a private snapshot."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_type: Mapped[GroupType] = mapped_column(Enum(GroupType), index=True)
    analysis_job_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_jobs.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    members: Mapped[list[GroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="GroupMember.rank"
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "photo_id", name="uq_group_photo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("duplicate_groups.id", ondelete="CASCADE"))
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"))

    keeper_score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    recommendation: Mapped[Recommendation] = mapped_column(Enum(Recommendation))
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)

    group: Mapped[DuplicateGroup] = relationship(back_populates="members")
    photo: Mapped[Photo] = relationship(back_populates="group_memberships")


class PreparedAction(Base):
    """Milestone 2/4: an action awaiting human confirmation before the
    Lightroom plugin applies it. Not yet written to in Milestone 1."""

    __tablename__ = "prepared_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, index=True, default=_uuid)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"))

    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), default=ActionStatus.PENDING)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionLog(Base):
    """Milestone 2/4: audit trail for prepared-action lifecycle events."""

    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prepared_action_id: Mapped[int] = mapped_column(
        ForeignKey("prepared_actions.id", ondelete="CASCADE")
    )
    event: Mapped[ActionEvent] = mapped_column(Enum(ActionEvent))
    detail: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
