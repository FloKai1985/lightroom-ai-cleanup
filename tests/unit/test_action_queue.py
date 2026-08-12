from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from lr_cleanup.database.models import ActionStatus, ActionType
from lr_cleanup.database.repository import PhotoInput, Repository
from lr_cleanup.service.action_queue import (
    ActionItem,
    ActionQueueError,
    ActionQueueService,
    BatchNotFoundError,
)


@pytest.fixture
def photo_id(repository: Repository, db_session: Session) -> int:
    photo = repository.upsert_photo(
        PhotoInput(original_path="/tmp/a.jpg", file_size=1, file_mtime=1.0)
    )
    db_session.commit()
    return photo.id


def test_prepare_creates_pending_actions_with_shared_batch_id(
    repository: Repository, photo_id: int
) -> None:
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.SET_PLUGIN_METADATA,
        [ActionItem(photo_id=photo_id, payload={"field": "aiCleanupStatus", "value": "x"})],
    )
    assert len(actions) == 1
    assert actions[0].status == ActionStatus.PENDING
    assert actions[0].batch_id


def test_prepare_rejects_empty_items(repository: Repository) -> None:
    service = ActionQueueService(repository)
    with pytest.raises(ActionQueueError):
        service.prepare(ActionType.SET_PLUGIN_METADATA, [])


def test_confirm_transitions_pending_to_confirmed(repository: Repository, photo_id: int) -> None:
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.ADD_TO_REVIEW_COLLECTION,
        [ActionItem(photo_id=photo_id, payload={"collection_name": "06 – Processed"})],
    )
    batch_id = actions[0].batch_id

    confirmed = service.confirm(batch_id)
    assert all(a.status == ActionStatus.CONFIRMED for a in confirmed)
    assert all(a.confirmed_at is not None for a in confirmed)


def test_confirm_unknown_batch_raises_not_found(repository: Repository) -> None:
    service = ActionQueueService(repository)
    with pytest.raises(BatchNotFoundError):
        service.confirm("does-not-exist")


def test_confirm_already_confirmed_batch_raises(repository: Repository, photo_id: int) -> None:
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.SET_PLUGIN_METADATA,
        [ActionItem(photo_id=photo_id, payload={})],
    )
    batch_id = actions[0].batch_id
    service.confirm(batch_id)

    with pytest.raises(ActionQueueError):
        service.confirm(batch_id)


def test_undo_cancels_pending_batch(repository: Repository, photo_id: int) -> None:
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.SET_PLUGIN_METADATA,
        [ActionItem(photo_id=photo_id, payload={})],
    )
    batch_id = actions[0].batch_id

    undone = service.undo(batch_id)
    assert all(a.status == ActionStatus.UNDONE for a in undone)
    assert all(a.undone_at is not None for a in undone)


def test_undo_cancels_confirmed_batch(repository: Repository, photo_id: int) -> None:
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.SET_PLUGIN_METADATA,
        [ActionItem(photo_id=photo_id, payload={})],
    )
    batch_id = actions[0].batch_id
    service.confirm(batch_id)

    undone = service.undo(batch_id)
    assert all(a.status == ActionStatus.UNDONE for a in undone)


def test_undo_unknown_batch_raises_not_found(repository: Repository) -> None:
    service = ActionQueueService(repository)
    with pytest.raises(BatchNotFoundError):
        service.undo("does-not-exist")


def test_undo_applied_action_is_rejected(repository: Repository, photo_id: int) -> None:
    """Undoing an already-applied action requires plugin-side support that
    doesn't exist yet (docs/architecture.md) — the service must refuse
    rather than silently pretend to revert a live Lightroom change."""
    service = ActionQueueService(repository)
    actions = service.prepare(
        ActionType.SET_PLUGIN_METADATA,
        [ActionItem(photo_id=photo_id, payload={})],
    )
    action = actions[0]
    repository.set_action_status(action, ActionStatus.APPLIED, applied_at=datetime.now(UTC))

    with pytest.raises(ActionQueueError):
        service.undo(action.batch_id)


def test_list_pending_filters_by_batch(repository: Repository, photo_id: int) -> None:
    service = ActionQueueService(repository)
    batch1 = service.prepare(
        ActionType.SET_PLUGIN_METADATA, [ActionItem(photo_id=photo_id, payload={})]
    )
    batch2 = service.prepare(
        ActionType.SET_PLUGIN_METADATA, [ActionItem(photo_id=photo_id, payload={})]
    )

    assert len(service.list_pending()) == 2
    assert len(service.list_pending(batch_id=batch1[0].batch_id)) == 1
    assert len(service.list_pending(batch_id=batch2[0].batch_id)) == 1
