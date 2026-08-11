from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from lr_cleanup.config import reset_settings_cache
from lr_cleanup.database.repository import Repository
from lr_cleanup.database.session import init_db, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repository(db_session: Session) -> Repository:
    return Repository(db_session)
