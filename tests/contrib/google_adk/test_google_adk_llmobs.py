from unittest import mock

import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
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
            "session_management": {
                "session_id": "test-session",
                "user_id": "test-user",
                "app_name": "TestADKApp",
            },
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
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
            },
            name="search_docs",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(multiply_tool_span),
            span_kind="tool",
            input_value='{"a": 5, "b": 3}',
            output_value='{"product": 15}',
            metadata={"description": "Simple arithmetic tool."},
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
            },
            name="multiply",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            error=error,
            input_value="Say hello",
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
                "user_id": "test-user",
                "app_name": "TestADKApp",
            },
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
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
            },
            name="search_docs",
        )

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            error=error,
            input_value="Can you search for information about recurring revenue?",
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
                "user_id": "test-user",
                "app_name": "TestADKApp",
            },
            name="test_agent",
            metadata=AGENT_MANIFEST_METADATA,
            output_value=mock.ANY,
            metrics={},
        )

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
            span_kind="code_execute",
            input_value='print("hello world")',
            output_value="hello world\n",
            metadata={},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_adk", "integration": "google_adk"},
            name="Google ADK Code Execute",
        )

    def test_code_execution_inherits_agent_session_id(self, mock_invocation_context, test_spans, google_adk_llmobs):
        """A code-execute span created during an agent run inherits the agent's LLMObs session id.

        The runner wrapper sets the session id on the agent span at creation, so any child span
        (tools, code executors) created before the agent finishes inherits it via context.
        """
        import google.adk as adk
        from google.adk.code_executors.code_execution_utils import CodeExecutionInput
        from google.adk.code_executors.unsafe_local_code_executor import UnsafeLocalCodeExecutor

        integration = adk._datadog_integration

        # Mirror _traced_agent_run_async: open the agent span and propagate the session id at creation.
        with integration.trace("InMemoryRunner.run_async", kind="agent", submit_to_llmobs=True) as agent_span:
            integration.set_session_id(agent_span, "test-session")
            executor = UnsafeLocalCodeExecutor()
            code_input = CodeExecutionInput(code='print("hello world")')
            executor.execute_code(mock_invocation_context, code_input)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        code_execute_spans = [s for s in spans if s.resource.endswith("execute_code")]
        assert len(code_execute_spans) == 1

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(code_execute_spans[0]),
            span_kind="code_execute",
            input_value='print("hello world")',
            output_value="hello world\n",
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "test-session",
            },
            name="Google ADK Code Execute",
        )

    @pytest.mark.asyncio
    async def test_run_live_reads_metadata_from_session_object(self, adk, test_spans, google_adk_llmobs):
        """run_live's deprecated ``session=`` form: session metadata is read off the Session object.

        When called as ``run_live(session=session, ...)`` there is no explicit ``user_id``/``session_id``
        kwarg, so the integration falls back to ``session.id``/``user_id``/``app_name``.
        """
        from ddtrace.contrib.internal.google_adk.patch import _traced_agent_run_async

        class FakeSession:
            id = "live-session"
            user_id = "live-user"
            app_name = "LiveADKApp"

        class FakeAgent:
            name = "live_agent"
            model = None
            tools: list = []

        class FakeRunner:
            # No app_name on the runner forces the fallback to session.app_name.
            app_name = None
            agent = FakeAgent()

        async def fake_run_live(*args, **kwargs):
            for _ in ():  # an async generator that yields nothing
                yield _

        kwargs = {"session": FakeSession(), "live_request_queue": object()}
        async for _ in _traced_agent_run_async(fake_run_live, FakeRunner(), (), kwargs):
            pass

        # The original call kwargs are left untouched by the wrapper.
        assert set(kwargs) == {"session", "live_request_queue"}

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="live_agent",
            tags={
                "ml_app": "<ml-app-name>",
                "session_id": "live-session",
                "user_id": "live-user",
                "app_name": "LiveADKApp",
            },
        )
