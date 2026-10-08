import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.shared.session import RequestResponder
from mcp.types import CallToolResult
from mcp.types import TextContent
import pytest

from ddtrace.contrib.internal.mcp.patch import traced_request_responder_respond
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


@pytest.mark.parametrize("keyword_response", [False, True])
def test_mcp_tool_error_without_llmobs(mcp_setup, mcp_call_tool, test_spans, monkeypatch, keyword_response):
    if keyword_response:
        respond = RequestResponder.respond

        async def respond_with_keyword(self, response, *args, **kwargs):
            return await respond(self, *args, response=response, **kwargs)

        monkeypatch.setattr(RequestResponder, "respond", respond_with_keyword)

    assert not mcp_setup._datadog_integration.llmobs_enabled
    result = asyncio.run(mcp_call_tool("failing_tool", {"param": "test"}))
    assert result.isError
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    server_span = next(span for span in spans if span.resource == "server_tool_call")
    assert server_span.error == 1
    assert server_span.get_tag("error.type") == "ToolError"
    assert server_span.get_tag("error.message") == "Error executing tool failing_tool: Tool execution failed"


@pytest.mark.parametrize("keyword_response", [False, True])
def test_mcp_direct_tool_error(mcp_setup, tracer, keyword_response):
    response = CallToolResult(content=[TextContent(type="text", text="Tool execution failed")], isError=True)
    respond = AsyncMock()
    args, kwargs = ((), {"response": response}) if keyword_response else ((response,), {})

    assert not mcp_setup._datadog_integration.llmobs_enabled
    with tracer.trace("mcp.server") as span:
        asyncio.run(traced_request_responder_respond(respond, SimpleNamespace(_dd_span=span), args, kwargs))

    respond.assert_awaited_once_with(*args, **kwargs)
    assert span.error == 1
    assert span.get_tag("error.type") == "ToolError"
    assert span.get_tag("error.message") == "Tool execution failed"
