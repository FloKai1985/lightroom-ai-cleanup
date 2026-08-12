"""End-to-end tests for the MCP server: each test calls a real MCP tool
via `MCPServer.call_tool`, which runs the tool function, which calls the
real FastAPI app in-process through `BackendClient` (see conftest.py's
`mcp_server` fixture). This exercises the full path — MCP tool -> HTTP ->
ASGI app -> repository -> SQLite — without a live TCP server or a real
MCP client process.

`call_tool` raises `mcp.server.mcpserver.exceptions.ToolError` when the
tool function raises or its arguments fail validation — confirmed by
inspecting the installed SDK directly, not assumed (docs/lightroom-plugin.md-
style verification; see server.py's docstring for the parallel MCP note).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from lr_cleanup.mcp_server.client import BackendClient
from lr_cleanup.mcp_server.server import create_server
from tests.fixtures.images import make_sharp_jpeg

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _register_one_photo(client: TestClient, tmp_path: Path) -> int:
    image = make_sharp_jpeg(tmp_path / "solo.jpg")
    stat = image.stat()
    response = client.post(
        "/api/v1/photos/register",
        json={
            "photos": [
                {
                    "original_path": str(image),
                    "file_size": stat.st_size,
                    "file_mtime": stat.st_mtime,
                    "capture_time": T0.isoformat(),
                }
            ]
        },
    )
    return response.json()["registered"][0]["photo_id"]


async def test_all_tools_are_registered(mcp_server: MCPServer) -> None:
    tools = await mcp_server.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "lightroom_cleanup_status",
        "list_analysis_jobs",
        "get_analysis_summary",
        "find_blurry_photos",
        "find_exact_duplicates",
        "find_near_duplicates",
        "get_duplicate_group",
        "prepare_review_collections",
        "prepare_markings",
        "undo_action_batch",
    }


async def test_status_reports_reachable_backend(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool("lightroom_cleanup_status", {})
    assert result.structured_content["reachable"] is True
    assert result.structured_content["status"] == "ok"


async def test_status_reports_unreachable_without_raising(client: TestClient) -> None:
    """The status tool's entire job is answering "is it up" — it must
    never itself raise, even when the backend is unreachable."""
    unreachable = BackendClient(base_url="http://127.0.0.1:1")  # nothing listens here
    server = create_server(client=unreachable)

    result = await server.call_tool("lightroom_cleanup_status", {})
    assert result.structured_content["reachable"] is False
    assert result.structured_content["detail"] is not None


async def test_get_analysis_summary_reflects_registered_photos(
    mcp_server: MCPServer, client: TestClient, tmp_path: Path
) -> None:
    _register_one_photo(client, tmp_path)
    result = await mcp_server.call_tool("get_analysis_summary", {})
    assert result.structured_content["total_photos"] == 1


async def test_list_analysis_jobs_enforces_max_limit(mcp_server: MCPServer) -> None:
    with pytest.raises(ToolError):
        await mcp_server.call_tool("list_analysis_jobs", {"limit": 9999})


async def test_find_blurry_photos_returns_registered_photo(
    mcp_server: MCPServer, client: TestClient, tmp_path: Path
) -> None:
    photo_id = _register_one_photo(client, tmp_path)
    client.post("/api/v1/jobs", json={"regenerate_groups": False})

    result = await mcp_server.call_tool("find_blurry_photos", {"min_confidence": 0.0})
    photos = result.structured_content["result"]
    assert any(p["photo_id"] == photo_id for p in photos)


async def test_get_duplicate_group_not_found_raises(mcp_server: MCPServer) -> None:
    with pytest.raises(ToolError):
        await mcp_server.call_tool("get_duplicate_group", {"group_id": 999999})


async def test_prepare_review_collections_rejects_unknown_collection_name(
    mcp_server: MCPServer, client: TestClient, tmp_path: Path
) -> None:
    photo_id = _register_one_photo(client, tmp_path)
    with pytest.raises(ToolError):
        await mcp_server.call_tool(
            "prepare_review_collections",
            {"photo_ids": [photo_id], "collection_name": "Not A Real Collection"},
        )


async def test_prepare_review_collections_rejects_empty_photo_ids(mcp_server: MCPServer) -> None:
    with pytest.raises(ToolError):
        await mcp_server.call_tool(
            "prepare_review_collections",
            {"photo_ids": [], "collection_name": "06 – Processed"},
        )


async def test_prepare_markings_rejects_unknown_marking(
    mcp_server: MCPServer, client: TestClient, tmp_path: Path
) -> None:
    photo_id = _register_one_photo(client, tmp_path)
    with pytest.raises(ToolError):
        await mcp_server.call_tool(
            "prepare_markings", {"photo_ids": [photo_id], "marking": "five_stars"}
        )


async def test_prepare_and_undo_full_cycle(
    mcp_server: MCPServer, client: TestClient, tmp_path: Path
) -> None:
    photo_id = _register_one_photo(client, tmp_path)

    prepared = await mcp_server.call_tool(
        "prepare_markings", {"photo_ids": [photo_id], "marking": "flagged_for_review"}
    )
    batch_id = prepared.structured_content["batch_id"]
    assert prepared.structured_content["actions"][0]["status"] == "pending"

    undone = await mcp_server.call_tool("undo_action_batch", {"batch_id": batch_id})
    assert undone.structured_content["actions"][0]["status"] == "undone"

    # Never applied to Lightroom — this whole cycle only ever touched
    # SQLite (docs/safety.md's two-phase action model).
    pending = client.get("/api/v1/actions/pending", params={"batch_id": batch_id}).json()
    assert pending == []


async def test_no_confirm_or_apply_tool_exists(mcp_server: MCPServer) -> None:
    """No destructive MCP tool may exist (docs/safety.md) — confirming or
    applying a change is deliberately not reachable from MCP at all."""
    tools = await mcp_server.list_tools()
    names = {t.name for t in tools}
    assert "confirm_action_batch" not in names
    assert not any("apply" in name for name in names)
