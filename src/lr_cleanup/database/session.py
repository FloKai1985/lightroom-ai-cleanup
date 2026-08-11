"""Engine/session construction.

Milestone 1 uses `Base.metadata.create_all` for simplicity (see
`init_db`); Alembic (`database/migrations/`) is the source of truth for
schema evolution from Milestone 2 onward, once the schema is no longer
brand-new on every checkout.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lr_cleanup.database.models import Base

_MEMORY_URL = "sqlite:///:memory:"
_BUSY_TIMEOUT_MS = 5_000


def make_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args: dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}
    engine_kwargs: dict[str, Any] = {}

    if database_url == _MEMORY_URL:
        # The FastAPI service (Milestone 2) serves each request on a worker
        # thread; a plain :memory: engine hands each thread its own private
        # database, silently losing data across requests. StaticPool shares
        # one connection so tests using an in-memory DB behave like the
        # single shared file-based DB used in production.
        engine_kwargs["poolclass"] = StaticPool
    elif database_url.startswith("sqlite:///"):
        db_path = database_url.removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # The API serves each request on a worker thread and runs
            # background jobs on another (Milestone 2), so two connections
            # can now legitimately want to write at the same time. Without
            # a busy timeout, SQLite raises "database is locked" immediately
            # instead of waiting for the other writer to finish; this makes
            # a second writer retry for up to 5s before giving up.
            cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
