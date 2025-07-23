from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def mcp_setup():
    patch()
    import mcp

    yield mcp
    unpatch()


@pytest.fixture
def mock_tracer(mcp_setup):
    pin = Pin.get_from(mcp_setup)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(mcp_setup, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def mcp_llmobs(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "mcptest"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def llmobs_events(mcp_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture
def mcp_server():
    """FastMCP server with common test tools."""
    mcp = FastMCP(name="TestServer")

    @mcp.tool(description="Get weather for a location")
    def get_weather(location: str) -> str:
        """Get weather information for a location."""
        return f"Weather in {location} is 72°F"

    @mcp.tool(description="A simple calculator tool")
    def calculator(operation: str, a: int, b: int) -> dict:
        """Perform arithmetic operations."""
        if operation == "add":
            return {"result": a + b}
        return {"result": 42}

    @mcp.tool(description="A tool that always fails")
    def failing_tool(param: str) -> str:
        """This tool always raises an exception."""
        raise ValueError("Tool execution failed")

    @mcp.tool(description="Tool returning dict")
    def dict_tool() -> dict:
        """Returns a dictionary."""
        return {"result": "result_value"}

    @mcp.tool(description="Tool returning string")
    def string_tool() -> str:
        """Returns a string."""
        return "string_response"

    @mcp.tool(description="Tool returning list")
    def list_tool() -> list:
        """Returns a list."""
        return ["item1", "item2"]

    return mcp


@pytest.fixture
def mcp_call_tool(mcp_server):
    """Fixture for calling MCP tools"""

    def _call_tool(tool_name, arguments):
        async def run_test():
            from mcp.shared.memory import create_connected_server_and_client_session

            async with create_connected_server_and_client_session(mcp_server._mcp_server) as client:
                await client.initialize()
                return await client.call_tool(tool_name, arguments)

        return run_test()

    return _call_tool


@pytest.fixture
async def mcp_client(mcp_server):
    """Connected MCP client-server session."""
    async with create_connected_server_and_client_session(mcp_server._mcp_server) as client:
        await client.initialize()
        yield client
