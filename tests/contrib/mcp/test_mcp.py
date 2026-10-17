import asyncio

import pytest

from tests.contrib.mcp.utils import MCP_V2


# mcp 2.x nests the server initialize span under the client initialize span of the same trace.
SNAPSHOT_VARIANTS = {"": not MCP_V2, "mcp_v2": MCP_V2}


@pytest.mark.snapshot(ignores=["meta.runtime-id"], variants=SNAPSHOT_VARIANTS)
def test_mcp_tool_call(mcp_setup, mcp_call_tool):
    """Test MCP tool call produces correct APM spans."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))


@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message", "meta.runtime-id"], variants=SNAPSHOT_VARIANTS)
def test_mcp_tool_error(mcp_setup, mcp_call_tool):
    """Test MCP tool error handling produces correct APM spans."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "test"}))
