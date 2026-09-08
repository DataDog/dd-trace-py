import asyncio
from importlib.metadata import version

import pytest

from ddtrace.internal.utils.version import parse_version


MCP_VERSION = parse_version(version("mcp"))
SNAPSHOT_VARIANTS = {"": MCP_VERSION < (2, 0, 0), "mcp2": MCP_VERSION >= (2, 0, 0)}


@pytest.mark.snapshot(ignores=["meta.runtime-id"], variants=SNAPSHOT_VARIANTS)
def test_mcp_tool_call(mcp_setup, mcp_call_tool):
    """Test MCP tool call produces correct APM spans."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))


@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message", "meta.runtime-id"], variants=SNAPSHOT_VARIANTS)
def test_mcp_tool_error(mcp_setup, mcp_call_tool):
    """Test MCP tool error handling produces correct APM spans."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "test"}))
