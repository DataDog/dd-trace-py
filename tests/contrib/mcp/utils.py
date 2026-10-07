from contextlib import asynccontextmanager
from importlib.metadata import version

import anyio

from ddtrace.internal.utils.version import parse_version


MCP_V2 = parse_version(version("mcp")) >= (2, 0, 0)

if MCP_V2:
    from mcp import ClientSession
    from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: F401
else:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
    from mcp.shared.memory import create_connected_server_and_client_session


@asynccontextmanager
async def _connect_v2(server, **kwargs):
    # mcp 2's in-memory transport copies the sender's contextvars into the server's handler task, which
    # parents server spans on client spans even without propagated headers. Plain anyio streams keep both
    # sides isolated like a real transport, as mcp 1's create_connected_server_and_client_session did.
    lowlevel_server = server._lowlevel_server
    server_to_client_send, server_to_client_receive = anyio.create_memory_object_stream(1)
    client_to_server_send, client_to_server_receive = anyio.create_memory_object_stream(1)
    async with server_to_client_send, server_to_client_receive, client_to_server_send, client_to_server_receive:
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lowlevel_server.run,
                client_to_server_receive,
                server_to_client_send,
                lowlevel_server.create_initialization_options(),
            )
            async with ClientSession(server_to_client_receive, client_to_server_send, **kwargs) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


def connect(server, **kwargs):
    """Connect an initialized in-memory ClientSession to a FastMCP (mcp<2) or MCPServer (mcp>=2) server."""
    if MCP_V2:
        return _connect_v2(server, **kwargs)
    return create_connected_server_and_client_session(server._mcp_server, **kwargs)


def tool_input_schema(tool):
    return tool.input_schema if MCP_V2 else tool.inputSchema
