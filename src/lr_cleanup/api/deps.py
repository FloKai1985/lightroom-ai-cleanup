"""FastAPI dependency wiring — session/repository construction per request.

Kept separate from `app.py` to avoid a circular import: `app.py` mounts the
`jobs`/`results` routers, and those routers need these dependencies, so
`app.py` can't be the one defining them.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from lr_cleanup.database.repository import Repository
from lr_cleanup.database.session import session_scope


def get_session(request: Request) -> Iterator[Session]:
    """Per-request session, committed on success / rolled back on error.

    Route handlers must not reuse this session inside a `BackgroundTasks`
    callback — background tasks run after the response (and this
    dependency's teardown) and must open their own session via
    `request.app.state.session_factory` instead.
    """
    with session_scope(request.app.state.session_factory) as session:
        yield session


def get_repository(session: Session = Depends(get_session)) -> Repository:
    return Repository(session)
