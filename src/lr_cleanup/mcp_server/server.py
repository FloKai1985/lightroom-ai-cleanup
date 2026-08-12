"""MCP server entry point (`lr-cleanup-mcp` console script).

Exposes read tools over analysis data plus action-preparation tools —
never anything that could directly mutate Lightroom (docs/safety.md).

The current stable MCP Python SDK (`mcp==2.0.0` at the time this was
written) does not use the `mcp.server.fastmcp.FastMCP` class from older
tutorials — that module doesn't exist in this version. The equivalent is
`mcp.server.mcpserver.MCPServer`, confirmed by inspecting the installed
package directly (`python -c "from mcp.server.mcpserver import
MCPServer; help(MCPServer)"`) rather than trusting a possibly-outdated
guide, per the project brief's instruction to verify the current SDK
before implementing MCP.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from lr_cleanup import __version__
from lr_cleanup.config import get_settings
from lr_cleanup.mcp_server.client import BackendClient
from lr_cleanup.mcp_server.tools import register_tools

INSTRUCTIONS = (
    "Read-only access to local Lightroom AI Cleanup analysis data (probable-blur "
    "candidates, exact/near-duplicate groups, keeper rankings) plus action "
    "preparation. No tool here can modify Lightroom directly: prepare_* tools only "
    "stage a PENDING PreparedAction batch for a human to confirm outside of MCP; "
    "there is no confirm or apply tool. Requires the local backend to be running "
    "(scripts/run-server.sh) — check with lightroom_cleanup_status first."
)


def create_server(client: BackendClient | None = None) -> MCPServer:
    """Build a fully-registered MCPServer. Pass `client` in tests to point
    at an in-process app instead of a real backend over TCP."""
    settings = get_settings()
    backend_client = client or BackendClient(base_url=f"http://{settings.host}:{settings.port}")

    server = MCPServer(
        name="lightroom-ai-cleanup",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    register_tools(server, backend_client)
    return server


server = create_server()
"""Module-level instance for `mcp run src/lr_cleanup/mcp_server/server.py:server`
(the MCP CLI's "import approach") and for parity with api/app.py's `app = create_app()`."""


def run() -> None:
    """Entry point for the `lr-cleanup-mcp` console script."""
    server.run(transport="stdio")


if __name__ == "__main__":
    run()
