from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

from lr_cleanup.api.app import create_app
from lr_cleanup.config import Settings
from lr_cleanup.mcp_server.client import BackendClient
from lr_cleanup.mcp_server.server import create_server


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


@pytest.fixture
def mcp_server(client: TestClient) -> MCPServer:
    """An MCPServer whose tools call the same in-process app as `client`,
    via BackendClient(client=...) — see client.py's docstring for why a
    `TestClient` (not a plain `httpx.Client`) is passed here: this
    environment carries two incompatible httpx major versions, and
    Starlette's TestClient is built on the newer one.
    """
    backend = BackendClient(client=client)
    return create_server(client=backend)
