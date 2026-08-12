"""Action queue: the only mechanism by which anything in this codebase can
eventually cause a change in Lightroom, and only after explicit human
confirmation. See docs/safety.md's two-phase action model:

    MCP -> PreparedAction -> SQLite Action Queue -> Lightroom plugin
        -> user confirmation -> apply

This module implements "prepare", "list pending", "confirm", and "undo".
It deliberately does *not* implement "apply" — that requires the
Lightroom-plugin side of the pipeline (ApplyActions.lua), which does not
exist yet (see docs/architecture.md's Milestone-3 component map). A
PreparedAction created here can reach at most CONFIRMED in this
milestone; APPLIED is reserved for that future work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from lr_cleanup.database.models import ActionEvent, ActionStatus, ActionType, PreparedAction
from lr_cleanup.database.repository import Repository


class ActionQueueError(ValueError):
    """Raised when a batch exists but isn't in a valid state for the
    requested operation (e.g. confirming a batch that's already applied)."""


class BatchNotFoundError(ActionQueueError):
    """Raised when `batch_id` matches no PreparedAction rows at all."""


@dataclass(frozen=True)
class ActionItem:
    photo_id: int
    payload: dict


class ActionQueueService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def prepare(self, action_type: ActionType, items: list[ActionItem]) -> list[PreparedAction]:
        """Creates one new batch containing one PreparedAction per item, all
        PENDING. Never touches Lightroom — this only ever writes to SQLite.

        Validates every `photo_id` exists first, rather than letting an
        unknown id surface as a raw FOREIGN KEY IntegrityError from SQLite
        (a 500 with a database traceback) partway through the batch.
        """
        if not items:
            raise ActionQueueError("cannot prepare an empty batch")

        missing = [
            item.photo_id
            for item in items
            if self.repository.get_photo(item.photo_id) is None
        ]
        if missing:
            raise ActionQueueError(f"unknown photo_id(s): {sorted(set(missing))}")

        batch_id = uuid.uuid4().hex
        actions = []
        for item in items:
            action = self.repository.create_prepared_action(
                batch_id, item.photo_id, action_type, item.payload
            )
            self.repository.create_action_log(action.id, ActionEvent.CREATED)
            actions.append(action)
        return actions

    def list_pending(
        self, batch_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[PreparedAction]:
        return self.repository.list_pending_actions(batch_id, limit, offset)

    def confirm(self, batch_id: str) -> list[PreparedAction]:
        """Marks every action in `batch_id` CONFIRMED. All actions in the
        batch must currently be PENDING — confirming a partially-applied
        or already-decided batch is rejected rather than guessed at."""
        actions = self.repository.list_actions_for_batch(batch_id)
        if not actions:
            raise BatchNotFoundError(f"no actions found for batch {batch_id!r}")
        non_pending = [a for a in actions if a.status != ActionStatus.PENDING]
        if non_pending:
            raise ActionQueueError(
                f"batch {batch_id!r} has {len(non_pending)} action(s) not in PENDING "
                f"state (status: {sorted({a.status.value for a in non_pending})})"
            )

        now = datetime.now(UTC)
        for action in actions:
            self.repository.set_action_status(action, ActionStatus.CONFIRMED, confirmed_at=now)
            self.repository.create_action_log(action.id, ActionEvent.CONFIRMED)
        return actions

    def undo(self, batch_id: str) -> list[PreparedAction]:
        """Cancels every action in `batch_id` that hasn't been applied yet
        (PENDING or CONFIRMED -> UNDONE). Rejects the batch outright if any
        action in it has already reached APPLIED — reverting an
        already-applied Lightroom change requires the plugin-side apply/undo
        mechanism, which doesn't exist yet (see the module docstring)."""
        actions = self.repository.list_actions_for_batch(batch_id)
        if not actions:
            raise BatchNotFoundError(f"no actions found for batch {batch_id!r}")
        applied = [a for a in actions if a.status == ActionStatus.APPLIED]
        if applied:
            raise ActionQueueError(
                f"batch {batch_id!r} has {len(applied)} already-applied action(s); "
                "undoing an applied change requires plugin-side support that doesn't exist yet"
            )

        now = datetime.now(UTC)
        undoable_statuses = (ActionStatus.PENDING, ActionStatus.CONFIRMED)
        undoable = [a for a in actions if a.status in undoable_statuses]
        for action in undoable:
            self.repository.set_action_status(action, ActionStatus.UNDONE, undone_at=now)
            self.repository.create_action_log(action.id, ActionEvent.UNDONE)
        return actions
