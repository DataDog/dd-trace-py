import asyncio

from mcp.shared.session import RequestResponder
import pytest


@pytest.mark.snapshot(ignores=["meta.runtime-id"])
def test_mcp_tool_call(mcp_setup, mcp_call_tool):
    """Test MCP tool call produces correct APM spans."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))


@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message", "meta.runtime-id"])
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
