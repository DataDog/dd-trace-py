from unittest import mock

import pytest

from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_parent_id
from tests.contrib.google_adk.conftest import create_test_message
from tests.llmobs._utils import assert_llmobs_span_data


AGENT_MANIFEST_METADATA = {
    "_dd": {
        "agent_manifest": {
            "description": "Test agent for ADK integration testing",
            "framework": "Google ADK",
            "instructions": "You are a helpful test agent. You can: "
            "(1) call tools using the provided "
            "functions, (2) execute Python code "
            "blocks when they are provided to you. "
            "When you see ```python code blocks, "
            "execute them using your code execution "
            "capability. Always be helpful and use "
            "your available capabilities.",
            "model": "gemini-2.5-pro",
            "model_configuration": '{"arbitrary_types_allowed": true, "extra": "forbid"}',
            "name": "test_agent",
            "session_management": {"session_id": "test-session", "user_id": "test-user"},
            "tools": [
                {"description": "A tiny search tool stub.", "name": "search_docs"},
                {"description": "Simple arithmetic tool.", "name": "multiply"},
            ],
        }
    }
}


class TestLLMObsGoogleADK:
    @pytest.mark.asyncio
    async def test_agent_run1(self, test_runner, request_vcr, test_spans, google_adk_llmobs):
        """Test that a simple agent run creates a valid LLMObs span event."""
        error = None
        with request_vcr.use_cassette("agent_run_async.yaml"):
            message = create_test_message("Say hello")
            try:
                async for _ in test_runner.run_async(
                    user_id="test-user",
                    session_id="test-session",
                    new_message=message,
                ):
                    pass
            except (TypeError, ValueError) as e:
                # Handle known ADK library issues with VCR cassettes
                if any(phrase in str(e) for phrase in ["exec_python", "Function", "JSON serializable", "bytes"]):
                    error = {
                        "type": "builtins.TypeError" if isinstance(e, TypeError) else "builtins.ValueError",
                        "message": mock.ANY,
                        "stack": mock.ANY,
                    }
                else:
                    raise

        spans = [s for trace in test_spans.pop_traces() for s in trace]

        # We expect 3 spans: 1 agent run and 2 tool calls
        assert len(spans) == 3

        agent_span = spans[0]
        search_tool_span = spans[1]
        multiply_tool_span = spans[2]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(search_tool_span),
            span_kind="tool",
            input_value='{"query": "test"}',
            output_value='{"results": ["Found reference for: test"]}',
            metadata={"description": "A tiny search tool stub."},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="search_docs",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(multiply_tool_span),
            span_kind="tool",
            input_value='{"a": 5, "b": 3}',
            output_value='{"product": 15}',
            metadata={"description": "Simple arithmetic tool."},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="multiply",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            error=error,
            input_value="Say hello",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="test_agent",
            metadata=AGENT_MANIFEST_METADATA,
            output_value=mock.ANY,
            metrics={},
        )

    @pytest.mark.asyncio
    async def test_agent_run_with_tools(self, test_runner, request_vcr, test_spans, google_adk_llmobs):
        """Test that an agent run with tool usage creates a valid LLMObs span event."""
        error = None
        with request_vcr.use_cassette("agent_tool_usage.yaml"):
            message = create_test_message("Can you search for information about recurring revenue?")
            try:
                async for _ in test_runner.run_async(
                    user_id="test-user",
                    session_id="test-session",
                    new_message=message,
                ):
                    pass
            except (TypeError, ValueError) as e:
                # Handle known ADK library issues with VCR cassettes
                if any(phrase in str(e) for phrase in ["exec_python", "Function", "JSON serializable", "bytes"]):
                    error = {
                        "type": "builtins.TypeError" if isinstance(e, TypeError) else "builtins.ValueError",
                        "message": mock.ANY,
                        "stack": mock.ANY,
                    }
                else:
                    raise

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 2

        agent_span = spans[0]
        tool_span = spans[1]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value='{"query": "recurring revenue"}',
            output_value='{"results": ["Found reference for: recurring revenue"]}',
            metadata={"description": "A tiny search tool stub."},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="search_docs",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            error=error,
            input_value="Can you search for information about recurring revenue?",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="test_agent",
            metadata=AGENT_MANIFEST_METADATA,
            output_value=mock.ANY,
            metrics={},
        )

    def test_agent_span_kept_when_extraction_raises(self, adk, test_spans, google_adk_llmobs):
        """Regression test for issue #18698.

        If the operation-specific extractor raises on malformed response data, the agent span must
        still be annotated with its kind so it survives event preparation. A dropped agent span
        orphans its child spans, so we also assert a child span stays parented to the agent.
        """
        integration = adk._datadog_integration
        agent_span = integration.trace(
            "Runner.run_async",
            provider="google",
            model="gemini-2.5-pro",
            kind="agent",
            submit_to_llmobs=True,
        )

        # A child LLM span started while the agent span is active should be parented to it.
        child_span = integration.trace("models.generate_content", kind="llm", submit_to_llmobs=True)
        _annotate_llmobs_span_data(child_span, kind="llm")

        # The public entry point swallows-and-logs extractor exceptions, mirroring production.
        with mock.patch.object(integration, "_llmobs_set_tags_agent", side_effect=ValueError("malformed Gemini Part")):
            integration.llmobs_set_tags(agent_span, args=[], kwargs={}, response=None, operation="agent")

        child_span.finish()
        agent_span.finish()

        # The agent span keeps its kind and is not dropped during event preparation: a generated
        # event is cached on the span, which is exactly what keeps child spans from being orphaned.
        agent_data = _get_llmobs_data_metastruct(agent_span)
        assert agent_data["meta"]["span"]["kind"] == "agent"
        assert agent_span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) is not None

        # The child resolves its parent to the (surviving) agent span rather than being orphaned.
        assert get_llmobs_parent_id(child_span) == str(agent_span.span_id)

    def test_code_execution(self, mock_invocation_context, test_spans, google_adk_llmobs):
        """Test that code execution creates a valid LLMObs span event."""
        from google.adk.code_executors.code_execution_utils import CodeExecutionInput
        from google.adk.code_executors.unsafe_local_code_executor import UnsafeLocalCodeExecutor

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code='print("hello world")')
        executor.execute_code(mock_invocation_context, code_input)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="tool",
            input_value='print("hello world")',
            output_value="hello world\n",
            metadata={},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="Google ADK Code Execute",
        )
