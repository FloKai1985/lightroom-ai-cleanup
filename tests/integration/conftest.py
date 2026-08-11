from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lr_cleanup.api.app import create_app
from lr_cleanup.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient wired to its own isolated in-memory database per test.

    Uses an explicit `Settings` instance (rather than the process-wide
    `.env`-backed singleton) so tests never touch a real database file and
    can run fully in parallel/isolated from each other.
    """
    settings = Settings(database_url="sqlite:///:memory:", cache_dir=tmp_path / "cache")
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client
