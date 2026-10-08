"""Helpers that let the mcp tests run unchanged against mcp 1.x and mcp 2.x."""

from contextlib import asynccontextmanager
from importlib.metadata import version

from ddtrace.internal.utils.version import parse_version


MCP_VERSION = parse_version(version("mcp"))
MCP_V2 = MCP_VERSION >= (2, 0, 0)

if MCP_V2:
    from mcp.server.mcpserver import MCPServer as FastMCP
else:
    from mcp.server.fastmcp import FastMCP  # noqa: F401


@asynccontextmanager
async def connect_client(server, mode="legacy", **kwargs):
    """Connect an in-memory client session to server and yield the ClientSession.

    On mcp 2.x, mode selects how the client negotiates the protocol. "legacy" runs the
    initialize handshake like mcp 1.x does, so both majors produce the same spans. "auto"
    probes with server/discover and uses the 2026-07-28 protocol. mode is ignored on mcp 1.x.
    """
    if MCP_V2:
        from mcp import Client

        async with Client(server, mode=mode, **kwargs) as client:
            yield client.session
    else:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server._mcp_server, **kwargs) as session:
            yield session
