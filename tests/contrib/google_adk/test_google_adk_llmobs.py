from unittest import mock

import pytest

from tests.contrib.google_adk.conftest import create_test_message
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")],
)
class TestLLMObsGoogleADK:
    @pytest.mark.asyncio
    async def test_agent_run1(self, test_runner, llmobs_events, request_vcr, mock_tracer):
        """Test that a simple agent run creates a valid LLMObs span event."""
        error_type = mock.ANY
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
                    error_type = "builtins.TypeError" if isinstance(e, TypeError) else "builtins.ValueError"
                else:
                    raise

        spans = mock_tracer.pop_traces()[0]

        # We expect 3 events: 2 tool calls and 1 agent run
        assert len(llmobs_events) == 3
        assert len(spans) == 3

        agent_span = spans[0]
        search_tool_span = spans[1]
        multiply_tool_span = spans[2]

        expected_llmobs_tool_span_events_agent_run(
            llmobs_events, agent_span, search_tool_span, multiply_tool_span, error_type
        )

    @pytest.mark.asyncio
    async def test_agent_run_with_tools(self, test_runner, llmobs_events, request_vcr, mock_tracer):
        """Test that an agent run with tool usage creates a valid LLMObs span event."""
        error_type = 0
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
                    error_type = "builtins.TypeError" if isinstance(e, TypeError) else "builtins.ValueError"
                else:
                    raise

        spans = mock_tracer.pop_traces()[0]
        assert len(llmobs_events) == 2
        assert len(spans) == 2

        agent_span = spans[0]
        tool_span = spans[1]

        expected_llmobs_agent_span_event_with_tools(llmobs_events, agent_span, tool_span, error_type)

    def test_code_execution(self, mock_invocation_context, mock_tracer, llmobs_events):
        """Test that code execution creates a valid LLMObs span event."""
        from google.adk.code_executors.code_execution_utils import CodeExecutionInput
        from google.adk.code_executors.unsafe_local_code_executor import UnsafeLocalCodeExecutor

        executor = UnsafeLocalCodeExecutor()
        code_input = CodeExecutionInput(code='print("hello world")')
        executor.execute_code(mock_invocation_context, code_input)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        expected_llmobs_code_execution_event(llmobs_events[0], span)


def expected_llmobs_tool_span_events_agent_run(
    llmobs_event, agent_span, search_tool_span, multiply_tool_span, error_type
):
    assert llmobs_event[0] == _expected_llmobs_non_llm_span_event(
        span=search_tool_span,
        span_kind="tool",
        input_value='{"query": "test"}',
        output_value='{"results": ["Found reference for: test"]}',
        metadata={"description": "A tiny search tool stub."},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
    )

    assert llmobs_event[1] == _expected_llmobs_non_llm_span_event(
        span=multiply_tool_span,
        span_kind="tool",
        input_value='{"b": 3, "a": 5}',
        output_value='{"product": 15}',
        metadata={"description": "Simple arithmetic tool."},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
    )

    assert llmobs_event[2] == _expected_llmobs_non_llm_span_event(
        span=agent_span,
        span_kind="agent",
        error_message=mock.ANY,
        error_stack=mock.ANY,
        error=error_type,
        input_value="Say hello",
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
        metadata={
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
        },
        output_value=mock.ANY,
        token_metrics={},
    )


def expected_llmobs_agent_span_event_with_tools(llmobs_event, agent_span, tool_span, error_type):
    assert llmobs_event[0] == _expected_llmobs_non_llm_span_event(
        span=tool_span,
        span_kind="tool",
        input_value='{"query": "recurring revenue"}',
        output_value='{"results": ["Found reference for: recurring revenue"]}',
        metadata={"description": "A tiny search tool stub."},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
    )

    assert llmobs_event[1] == _expected_llmobs_non_llm_span_event(
        span=agent_span,
        span_kind="agent",
        error_message=mock.ANY,
        error_stack=mock.ANY,
        error=error_type,
        input_value="Can you search for information about recurring revenue?",
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
        metadata={
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
        },
        output_value=mock.ANY,
        token_metrics={},
    )


def expected_llmobs_code_execution_event(llmobs_event, span):
    assert llmobs_event == _expected_llmobs_non_llm_span_event(
        span=span,
        span_kind="code_execute",
        input_value='print("hello world")',
        output_value="hello world\n",
        metadata={},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk"},
    )
