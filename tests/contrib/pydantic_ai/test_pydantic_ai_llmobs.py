import asyncio
import os
import sys

import mock
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import PYDANTIC_AI_TAGS
from tests.contrib.pydantic_ai.utils import calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_agent_metadata
from tests.contrib.pydantic_ai.utils import expected_calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_external_tool
from tests.contrib.pydantic_ai.utils import expected_foo_tool
from tests.contrib.pydantic_ai.utils import expected_mcp_tool
from tests.contrib.pydantic_ai.utils import foo_tool
from tests.llmobs._utils import assert_llmobs_span_data


PYDANTIC_AI_VERSION = parse_version(pydantic_ai.__version__)

TOOL_DESCRIPTION_METADATA = {"description": "Calculates the square of a number"}

_MCP_SERVER_PATH = os.path.join(os.path.dirname(__file__), "mcp_server.py")

try:
    from pydantic_ai.mcp import MCPServer as _MCPServer

    _HAS_MCP_SERVER_INFO = hasattr(_MCPServer, "server_info")
except Exception:
    _HAS_MCP_SERVER_INFO = False

try:
    from pydantic_ai.capabilities import MCP as _MCPCapability  # noqa: F401

    _HAS_CAPABILITIES = True
except Exception:
    _HAS_CAPABILITIES = False


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")],
)
class TestLLMObsPydanticAI:
    async def test_agent_run(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        model_settings = {"max_tokens": 100, "temperature": 0.5}
        instructions = "dummy instructions"
        system_prompt = "dummy system prompt"
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                instructions=instructions,
                system_prompt=system_prompt,
                tools=[calculate_square_tool],
                model_settings=model_settings,
            )
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                instructions=instructions,
                system_prompt=system_prompt,
                model_settings=model_settings,
                tools=expected_calculate_square_tool(),
            ),
            tags=PYDANTIC_AI_TAGS,
        )

    def test_agent_run_sync(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = agent.run_sync("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_infers_name(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            inferred_stream_agent = pydantic_ai.Agent(model="gpt-4o")
            async with inferred_stream_agent.run_stream("Hello, world!") as result:
                async for _ in result.stream(debounce_by=None):
                    pass
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert _get_llmobs_data_metastruct(spans[0])["name"] == "inferred_stream_agent"

    async def test_agent_run_stream_respects_infer_name_false(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """`infer_name=False` is the caller opting out of name inference; we must not override it."""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            unnamed_agent = pydantic_ai.Agent(model="gpt-4o")
            async with unnamed_agent.run_stream("Hello, world!", infer_name=False) as result:
                async for _ in result.stream(debounce_by=None):
                    pass
        assert unnamed_agent.name is None
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert _get_llmobs_data_metastruct(spans[0])["name"] == "PydanticAI Agent"

    @pytest.mark.parametrize("delta", [False, True])
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, delta):
        """
        delta determines whether each chunk represents the entire output up to the current point or just the
        delta from the previous chunk
        """
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_text(debounce_by=None, delta=delta):
                    output = output + chunk if delta else chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func():
                    output = chunk[0].parts[0].content
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 8, 1), reason="pydantic-ai < 0.8.1 does not support stream_responses")
    async def test_agent_run_stream_responses_early_exit(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that the span is still finished when the stream is exited early"""
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk, last in result.stream_responses():
                    assert not last  # assert this is not the last chunk
                    output = chunk.parts[0].content
                    break
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_get_output(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                output = await result.get_output()
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_with_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for chunk in result.stream():
                    output = chunk
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=output,
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method_with_tool(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

        class Output(TypedDict):
            original_number: int
            square: int

        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_structured_with_tool.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                tools=[calculate_square_tool],
                instructions=instructions,
                output_type=Output,
            )
            async with agent.run_stream("What is the square of 2?") as result:
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func(debounce_by=None):
                    output = chunk
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=safe_json(output[0].parts[0].args, ensure_ascii=False),
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.run_stream("Hello, world!") as result:
                    stream = result.stream(debounce_by=None)
                    async for chunk in stream:
                        output = chunk
                        raise Exception("test error")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
            error={"type": "builtins.Exception", "message": "test error", "stack": mock.ANY},
        )

    async def test_agent_iter(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter("Hello, world!") as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_iter_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        agent_span_data = _get_llmobs_data_metastruct(spans[0])
        assert agent_span_data["meta"]["error"]["message"] == "test error"
        assert spans[0].error == 1

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 4, 4), reason="pydantic-ai < 0.4.4 does not support toolsets")
    async def test_agent_run_with_toolset(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that the agent manifest includes tools from both the function toolset and the user-defined toolsets"""
        from pydantic_ai.toolsets import FunctionToolset

        with request_vcr.use_cassette("agent_run_stream_with_toolset.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                toolsets=[FunctionToolset(tools=[calculate_square_tool])],
                tools=[foo_tool],
            )
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(tools=expected_calculate_square_tool() + expected_foo_tool()),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 0, 0), reason="pydantic-ai < 1.0.0 does not support ExternalToolset")
    async def test_agent_run_with_external_toolset(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that the agent manifest includes external tools exposed via ExternalToolset.tool_defs"""
        from pydantic_ai.tools import ToolDefinition
        from pydantic_ai.toolsets import ExternalToolset

        external_toolset = ExternalToolset(
            [
                ToolDefinition(
                    name="external_tool",
                    description="An external tool",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                )
            ]
        )
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[external_toolset])
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(tools=expected_external_tool()),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize(
        "toolset_kind",
        [
            pytest.param(
                "static",
                marks=pytest.mark.skipif(not _HAS_MCP_SERVER_INFO, reason="needs MCPServer.server_info"),
                id="static_server",
            ),
            pytest.param(
                "dynamic",
                marks=pytest.mark.skipif(
                    not _HAS_MCP_SERVER_INFO or PYDANTIC_AI_VERSION < (1, 97, 0),
                    reason="needs MCPServer.server_info and dynamic toolsets",
                ),
                id="dynamic_toolset",
            ),
            pytest.param(
                "mcptoolset",
                marks=pytest.mark.skipif(
                    not _HAS_MCP_SERVER_INFO or PYDANTIC_AI_VERSION < (1, 97, 0),
                    reason="needs MCPServer.server_info and MCPToolset",
                ),
                id="mcptoolset",
            ),
        ],
    )
    async def test_agent_run_captures_mcp_server_and_tool(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, toolset_kind
    ):
        """A live MCP run records the server identity (name + version) and the called MCP tool on the
        manifest. Covers the deprecated MCPServerStdio on the static toolset list, the same supplied
        dynamically (a callable, captured from the observed tool call), and the newer MCPToolset (whose
        server_info is reset on disconnect, so it too relies on the observed-call capture).
        """
        if toolset_kind == "mcptoolset":
            from fastmcp.client.transports import PythonStdioTransport
            from pydantic_ai.mcp import MCPToolset

            toolset = MCPToolset(PythonStdioTransport(_MCP_SERVER_PATH, env=os.environ.copy()), id="square-mcp")
        else:
            from pydantic_ai.mcp import MCPServerStdio

            server = MCPServerStdio(
                command=sys.executable, args=[_MCP_SERVER_PATH], id="square-mcp", env=os.environ.copy()
            )
            toolset = server if toolset_kind == "static" else (lambda ctx: server)
        with request_vcr.use_cassette("agent_with_tools.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[toolset])
            async with agent:
                result = await agent.run("What is the square of 2?")
        trace = test_spans.pop_traces()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(trace[0]),
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=result.output,
            metadata=expected_agent_metadata(
                tools=expected_mcp_tool("test-mcp"),
                mcp_servers=[{"name": "test-mcp", "version": "1.0.0"}],
            ),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(_get_llmobs_data_metastruct(trace[1]), span_kind="tool", name="calculate_square_tool")

    @pytest.mark.skipif(not _HAS_MCP_SERVER_INFO, reason="pydantic-ai version does not expose MCPServer.server_info")
    async def test_get_mcp_servers_reads_connected_identity(self, pydantic_ai, pydantic_ai_llmobs):
        """A connected server's identity (name + version from server_info) is resolved from the agent's
        constructor toolsets, from per-run toolsets (not stored on the agent), from an active
        `override(toolsets=)`, through a wrapper (`.prefixed()`), and nested in a `CombinedToolset`; a server
        that never connected has no identity and is skipped.
        """
        from pydantic_ai.mcp import MCPServerStdio
        from pydantic_ai.toolsets import CombinedToolset

        identity = [{"name": "test-mcp", "version": "1.0.0"}]
        stdio = dict(command=sys.executable, args=[_MCP_SERVER_PATH], env=os.environ.copy())
        async with MCPServerStdio(id="square-mcp", **stdio) as server:
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[server])
            assert PydanticAIIntegration._get_mcp_servers(agent) == identity
            assert PydanticAIIntegration._get_mcp_servers(pydantic_ai.Agent(model="gpt-4o"), [server]) == identity
            with agent.override(toolsets=[server.prefixed("sq")]):
                assert PydanticAIIntegration._get_mcp_servers(agent) == identity
            nested = pydantic_ai.Agent(model="gpt-4o", toolsets=[CombinedToolset([server])])
            assert PydanticAIIntegration._get_mcp_servers(nested) == identity

        unconnected = pydantic_ai.Agent(model="gpt-4o", toolsets=[MCPServerStdio(id="never", **stdio)])
        assert PydanticAIIntegration._get_mcp_servers(unconnected) == []

    @pytest.mark.skipif(not _HAS_CAPABILITIES, reason="pydantic-ai version does not support capabilities=[MCP(...)]")
    def test_get_mcp_servers_captures_native_capability(self, pydantic_ai, pydantic_ai_llmobs):
        """Native MCP servers attached via `capabilities=[MCP(...)]` are captured by id (provider-side, so no
        handshake and no name/version); URL credentials are scrubbed. A local-only capability (`native=False`)
        rides `agent.toolsets` instead and is not double-counted here.
        """
        from pydantic_ai.capabilities import MCP

        explicit = pydantic_ai.Agent(
            model="gpt-4o",
            capabilities=[MCP(url="https://mcp.example.com/analytics", native=True, id="analytics-mcp")],
        )
        assert PydanticAIIntegration._get_mcp_servers(explicit) == [
            {"name": "analytics-mcp", "url": "https://mcp.example.com/analytics"}
        ]

        derived = pydantic_ai.Agent(
            model="gpt-4o",
            capabilities=[MCP(url="https://user:pass@mcp.example.com/analytics?token=sk", native=True)],
        )
        assert PydanticAIIntegration._get_mcp_servers(derived) == [
            {"name": "mcp.example.com-analytics", "url": "https://mcp.example.com/analytics"}
        ]

        local_only = pydantic_ai.Agent(
            model="gpt-4o", capabilities=[MCP(url="https://mcp.example.com/x", native=False)]
        )
        assert PydanticAIIntegration._get_mcp_servers(local_only) == []

    async def test_agent_run_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history when user_prompt is not provided."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run(message_history=message_history)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_with_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that INPUT_VALUE is set from message_history for run_stream."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream(message_history=message_history) as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_iter_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history for iter."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter(message_history=message_history) as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_with_user_prompt_and_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that user_prompt takes precedence over message_history."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run("Hello, world!", message_history=message_history)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_concurrent_agents_no_tool_cross_contamination(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Two agents run concurrently, each with its own tool. A tool from one agent must not be
        attributed to the other agent's manifest; each tool span resolves its own agent ancestor.
        """
        from pydantic_ai.models.test import TestModel

        a_ready = asyncio.Event()
        b_ready = asyncio.Event()

        async def tool_a() -> str:
            """Tool A"""
            a_ready.set()
            await asyncio.wait_for(b_ready.wait(), timeout=5)
            return "a"

        async def tool_b() -> str:
            """Tool B"""
            b_ready.set()
            await asyncio.wait_for(a_ready.wait(), timeout=5)
            return "b"

        agent_a = pydantic_ai.Agent(model=TestModel(), name="agent_a", tools=[tool_a])
        agent_b = pydantic_ai.Agent(model=TestModel(), name="agent_b", tools=[tool_b])

        await asyncio.gather(agent_a.run("go a"), agent_b.run("go b"))

        manifests = {}
        for trace in test_spans.pop_traces():
            data = _get_llmobs_data_metastruct(trace[0])
            manifest = data["meta"]["metadata"]["_dd"]["agent_manifest"]
            manifests[manifest["name"]] = {tool["name"] for tool in manifest["tools"]}

        assert manifests["agent_a"] == {"tool_a"}, manifests
        assert manifests["agent_b"] == {"tool_b"}, manifests

    async def test_nested_agent_delegation_tool_attribution(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """An outer agent's tool delegates to an inner agent. Each tool span must attribute to its
        nearest agent ancestor, so the inner tool lands on the inner agent and the outer tool on the
        outer agent.
        """
        from pydantic_ai.models.test import TestModel

        async def inner_tool() -> str:
            """Inner tool"""
            return "inner"

        inner_agent = pydantic_ai.Agent(model=TestModel(), name="inner_agent", tools=[inner_tool])

        async def delegate() -> str:
            """Delegate to the inner agent"""
            result = await inner_agent.run("inner go")
            return result.output

        outer_agent = pydantic_ai.Agent(model=TestModel(), name="outer_agent", tools=[delegate])
        await outer_agent.run("outer go")

        manifests = {}
        for trace in test_spans.pop_traces():
            for span in trace:
                manifest = _get_llmobs_data_metastruct(span)["meta"]["metadata"].get("_dd", {}).get("agent_manifest")
                if manifest:
                    manifests[manifest["name"]] = {tool["name"] for tool in manifest["tools"]}

        assert manifests["outer_agent"] == {"delegate"}, manifests
        assert manifests["inner_agent"] == {"inner_tool"}, manifests


class TestLLMObsPydanticAISpanLinks:
    async def test_agent_calls_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, openai_patched, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for _ in result.stream(debounce_by=None):
                    pass

        trace = test_spans.pop_traces()[0]
        # APM trace order: agent → first LLM call → tool → second LLM call.
        first_llm_span = trace[1]
        tool_span = trace[2]
        second_llm_span = trace[3]
        tool_span_data = _get_llmobs_data_metastruct(tool_span)
        second_llm_span_data = _get_llmobs_data_metastruct(second_llm_span)

        # LLM-to-tool span link should be on the tool span, pointing at the first LLM span.
        assert len(tool_span_data["span_links"]) == 1
        assert tool_span_data["span_links"][0]["span_id"] == str(first_llm_span.span_id)
        assert tool_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}
        # tool-to-LLM span link should be on the second LLM span, pointing at the tool span.
        assert len(second_llm_span_data["span_links"]) == 1
        assert second_llm_span_data["span_links"][0]["span_id"] == str(tool_span.span_id)
        assert second_llm_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}
