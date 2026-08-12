"""Action queue endpoints: prepare, list pending, confirm, undo.

No endpoint here can apply a change to Lightroom — see
service/action_queue.py's module docstring and docs/safety.md's two-phase
action model. `confirm` only flips PENDING -> CONFIRMED in SQLite; nothing
in this codebase currently polls for CONFIRMED actions and applies them
(that's future work, the Lightroom-plugin-side ApplyActions.lua).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lr_cleanup.api.deps import get_repository
from lr_cleanup.database.models import ActionStatus, ActionType, PreparedAction
from lr_cleanup.database.repository import Repository
from lr_cleanup.service.action_queue import (
    ActionItem,
    ActionQueueError,
    ActionQueueService,
    BatchNotFoundError,
)

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])


class PrepareActionItem(BaseModel):
    photo_id: int
    payload: dict = {}


class PrepareActionsRequest(BaseModel):
    action_type: ActionType
    items: list[PrepareActionItem]


class PreparedActionResponse(BaseModel):
    id: int
    batch_id: str
    photo_id: int
    action_type: ActionType
    payload: dict
    status: ActionStatus
    created_at: datetime
    confirmed_at: datetime | None
    applied_at: datetime | None
    undone_at: datetime | None


class ActionBatchResponse(BaseModel):
    batch_id: str
    actions: list[PreparedActionResponse]


def _action_response(action: PreparedAction) -> PreparedActionResponse:
    return PreparedActionResponse(
        id=action.id,
        batch_id=action.batch_id,
        photo_id=action.photo_id,
        action_type=action.action_type,
        payload=action.payload,
        status=action.status,
        created_at=action.created_at,
        confirmed_at=action.confirmed_at,
        applied_at=action.applied_at,
        undone_at=action.undone_at,
    )


@router.post("/prepare", response_model=ActionBatchResponse, status_code=201)
def prepare_actions(
    payload: PrepareActionsRequest, repo: Repository = Depends(get_repository)
) -> ActionBatchResponse:
    if not payload.items:
        raise HTTPException(status_code=400, detail="items must not be empty")

    service = ActionQueueService(repo)
    items = [ActionItem(photo_id=i.photo_id, payload=i.payload) for i in payload.items]
    try:
        actions = service.prepare(payload.action_type, items)
    except ActionQueueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ActionBatchResponse(
        batch_id=actions[0].batch_id, actions=[_action_response(a) for a in actions]
    )


@router.get("/pending", response_model=list[PreparedActionResponse])
def list_pending_actions(
    batch_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    repo: Repository = Depends(get_repository),
) -> list[PreparedActionResponse]:
    service = ActionQueueService(repo)
    actions = service.list_pending(batch_id=batch_id, limit=limit, offset=offset)
    return [_action_response(a) for a in actions]


@router.post("/{batch_id}/confirm", response_model=ActionBatchResponse)
def confirm_batch(
    batch_id: str, repo: Repository = Depends(get_repository)
) -> ActionBatchResponse:
    service = ActionQueueService(repo)
    try:
        actions = service.confirm(batch_id)
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionQueueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ActionBatchResponse(batch_id=batch_id, actions=[_action_response(a) for a in actions])


@router.post("/{batch_id}/undo", response_model=ActionBatchResponse)
def undo_batch(
    batch_id: str, repo: Repository = Depends(get_repository)
) -> ActionBatchResponse:
    service = ActionQueueService(repo)
    try:
        actions = service.undo(batch_id)
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionQueueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ActionBatchResponse(batch_id=batch_id, actions=[_action_response(a) for a in actions])
