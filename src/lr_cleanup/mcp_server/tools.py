"""MCP tool implementations.

Every tool is a plain, type-annotated function registered onto an
`MCPServer` via `register_tools()`; all of them call the local FastAPI
service through a `BackendClient` — see client.py's docstring for why the
MCP server has no direct database access of its own.

Safety (docs/safety.md): no tool here can apply anything to Lightroom.
`prepare_review_collections` and `prepare_markings` only ever create
`PENDING` `PreparedAction` rows; `undo_action_batch` only ever cancels a
not-yet-applied batch. There is deliberately no `confirm` tool and no
`apply` tool.

Pagination (project brief: "do not return giant unbounded result sets"):
every list-returning tool has a `limit` bounded by `Field(le=MAX_LIMIT)`,
enforced by the MCP SDK's own argument validation before the tool body
runs (verified directly against the installed SDK, not assumed).
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from lr_cleanup.mcp_server.client import BackendClient

MAX_LIMIT = 100
DEFAULT_LIMIT = 20

Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Max results to return")]
Offset = Annotated[int, Field(ge=0, description="Pagination offset")]

# The six canonical collection names from docs/safety.md / docs/algorithms.md.
# prepare_review_collections rejects anything outside this set rather than
# letting a caller stage an arbitrarily-named collection.
REVIEW_COLLECTION_NAMES = (
    "01 – Recommended Keepers",
    "02 – High Confidence Blur",
    "03 – Exact Duplicates",
    "04 – Near Duplicates",
    "05 – Review Required",
    "06 – Processed",
)

# aiCleanupStatus marker values prepare_markings is allowed to stage —
# never a Lightroom rating/label/pick value (docs/safety.md rules 5-7).
MARKING_VALUES = ("flagged_for_review", "confirmed_keeper", "confirmed_redundant")

MAX_BATCH_SIZE = 500


class StatusResult(BaseModel):
    reachable: bool
    status: str
    database: str | None = None
    version: str | None = None
    backend_url: str
    detail: str | None = None


class JobSummary(BaseModel):
    job_id: str
    status: str
    total_photos: int
    processed_photos: int
    failed_photos: int
    created_at: str


class AnalysisSummary(BaseModel):
    total_photos: int
    analyzed_photos: int
    blurry_photos: int
    blur_confidence_threshold: float
    groups_by_type: dict[str, int]
    latest_job: JobSummary | None


class BlurryPhoto(BaseModel):
    photo_id: int
    original_path: str
    sharpness_score: float
    blur_confidence: float


class GroupMember(BaseModel):
    photo_id: int
    original_path: str
    rank: int
    recommendation: str
    keeper_score: float
    confidence: float
    reasons: list[str]


class DuplicateGroupResult(BaseModel):
    group_id: int
    group_type: str
    members: list[GroupMember]


class PreparedActionSummary(BaseModel):
    id: int
    photo_id: int
    action_type: str
    status: str


class ActionBatchResult(BaseModel):
    batch_id: str
    actions: list[PreparedActionSummary]


def _to_job_summary(raw: dict[str, Any]) -> JobSummary:
    return JobSummary(
        job_id=raw["job_id"],
        status=raw["status"],
        total_photos=raw["total_photos"],
        processed_photos=raw["processed_photos"],
        failed_photos=raw["failed_photos"],
        created_at=raw["created_at"],
    )


def _to_group(raw: dict[str, Any]) -> DuplicateGroupResult:
    return DuplicateGroupResult(
        group_id=raw["group_id"],
        group_type=raw["group_type"],
        members=[GroupMember(**m) for m in raw["members"]],
    )


def _to_action_summary(raw: dict[str, Any]) -> PreparedActionSummary:
    return PreparedActionSummary(
        id=raw["id"],
        photo_id=raw["photo_id"],
        action_type=raw["action_type"],
        status=raw["status"],
    )


def _to_batch_result(raw: dict[str, Any]) -> ActionBatchResult:
    return ActionBatchResult(
        batch_id=raw["batch_id"], actions=[_to_action_summary(a) for a in raw["actions"]]
    )


def register_tools(server: MCPServer, client: BackendClient) -> None:
    @server.tool(
        name="lightroom_cleanup_status",
        description="Check whether the local Lightroom AI Cleanup backend is reachable "
        "and healthy.",
    )
    def lightroom_cleanup_status() -> StatusResult:
        # Deliberately never raises — a status check that itself errors out
        # is a confusing way to report "the thing you're checking is down."
        try:
            health = client.health()
        except Exception as exc:  # noqa: BLE001
            return StatusResult(
                reachable=False, status="unreachable", backend_url=client.base_url, detail=str(exc)
            )
        return StatusResult(
            reachable=True,
            status=health.get("status", "unknown"),
            database=health.get("database"),
            version=health.get("version"),
            backend_url=client.base_url,
        )

    @server.tool(
        name="list_analysis_jobs",
        description="List recent analysis jobs, most recent first.",
    )
    def list_analysis_jobs(limit: Limit = DEFAULT_LIMIT, offset: Offset = 0) -> list[JobSummary]:
        jobs = client.list_jobs(limit=limit, offset=offset)
        return [_to_job_summary(j) for j in jobs]

    @server.tool(
        name="get_analysis_summary",
        description="Aggregate stats: total/analyzed photo counts, blurry photo count, "
        "duplicate/near-duplicate group counts by type, and the most recent job.",
    )
    def get_analysis_summary(
        blur_confidence_min: Annotated[
            float, Field(ge=0.0, le=1.0, description="Threshold for the blurry_photos count")
        ] = 0.5,
    ) -> AnalysisSummary:
        raw = client.summary(blur_confidence_min=blur_confidence_min)
        latest_job = _to_job_summary(raw["latest_job"]) if raw.get("latest_job") else None
        return AnalysisSummary(
            total_photos=raw["total_photos"],
            analyzed_photos=raw["analyzed_photos"],
            blurry_photos=raw["blurry_photos"],
            blur_confidence_threshold=raw["blur_confidence_threshold"],
            groups_by_type=raw["groups_by_type"],
            latest_job=latest_job,
        )

    @server.tool(
        name="find_blurry_photos",
        description="List photos flagged as probable blur, most-blurry first. "
        "blur_confidence is a technical estimate (docs/algorithms.md), not a certainty.",
    )
    def find_blurry_photos(
        min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> list[BlurryPhoto]:
        photos = client.blurry_photos(min_confidence=min_confidence, limit=limit, offset=offset)
        return [BlurryPhoto(**p) for p in photos]

    @server.tool(
        name="find_exact_duplicates",
        description="List exact-duplicate groups (identical file bytes). "
        "Virtual copies are never included — they share an original by design.",
    )
    def find_exact_duplicates(
        limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> list[DuplicateGroupResult]:
        groups = client.list_groups(group_types=["exact_duplicate"], limit=limit, offset=offset)
        return [_to_group(g) for g in groups]

    @server.tool(
        name="find_near_duplicates",
        description="List near-duplicate and burst groups (visually similar, close in "
        "capture time). Each group includes a keeper ranking with machine-readable reasons.",
    )
    def find_near_duplicates(
        limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> list[DuplicateGroupResult]:
        groups = client.list_groups(
            group_types=["near_duplicate", "burst"], limit=limit, offset=offset
        )
        return [_to_group(g) for g in groups]

    @server.tool(
        name="get_duplicate_group",
        description="Get full detail (all members, ranks, recommendations, reasons) for one "
        "duplicate/near-duplicate/burst group by id.",
    )
    def get_duplicate_group(group_id: int) -> DuplicateGroupResult:
        return _to_group(client.get_group(group_id))

    @server.tool(
        name="prepare_review_collections",
        description="Stage (but do not apply) adding photos to a named AI review collection. "
        "Creates a PENDING action batch — nothing changes in Lightroom until a human confirms "
        f"it. collection_name must be one of: {', '.join(REVIEW_COLLECTION_NAMES)}.",
    )
    def prepare_review_collections(photo_ids: list[int], collection_name: str) -> ActionBatchResult:
        if not photo_ids:
            raise ValueError("photo_ids must not be empty")
        if len(photo_ids) > MAX_BATCH_SIZE:
            raise ValueError(f"too many photo_ids (max {MAX_BATCH_SIZE} per batch)")
        if collection_name not in REVIEW_COLLECTION_NAMES:
            raise ValueError(
                f"collection_name must be one of: {', '.join(REVIEW_COLLECTION_NAMES)}"
            )

        items = [
            {"photo_id": pid, "payload": {"collection_name": collection_name}}
            for pid in photo_ids
        ]
        return _to_batch_result(client.prepare_actions("add_to_review_collection", items))

    @server.tool(
        name="prepare_markings",
        description="Stage (but do not apply) an AI status marker on photos — never a "
        "Lightroom star rating, color label, or pick flag. Creates a PENDING action batch. "
        f"marking must be one of: {', '.join(MARKING_VALUES)}.",
    )
    def prepare_markings(photo_ids: list[int], marking: str) -> ActionBatchResult:
        if not photo_ids:
            raise ValueError("photo_ids must not be empty")
        if len(photo_ids) > MAX_BATCH_SIZE:
            raise ValueError(f"too many photo_ids (max {MAX_BATCH_SIZE} per batch)")
        if marking not in MARKING_VALUES:
            raise ValueError(f"marking must be one of: {', '.join(MARKING_VALUES)}")

        items = [
            {"photo_id": pid, "payload": {"field": "aiCleanupStatus", "value": marking}}
            for pid in photo_ids
        ]
        return _to_batch_result(client.prepare_actions("set_plugin_metadata", items))

    @server.tool(
        name="undo_action_batch",
        description="Cancel a not-yet-applied prepared-action batch (PENDING or CONFIRMED -> "
        "UNDONE). Cannot undo a batch that has already been applied to Lightroom — that "
        "requires plugin-side support that doesn't exist yet.",
    )
    def undo_action_batch(batch_id: str) -> ActionBatchResult:
        return _to_batch_result(client.undo_batch(batch_id))
