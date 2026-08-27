from contextlib import contextmanager
from types import SimpleNamespace
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

    @pytest.mark.asyncio
    async def test_run_live_reads_metadata_from_session_object(self, test_runner, test_spans, google_adk_llmobs):
        """run_live's deprecated ``session=`` form: session metadata is read off the Session object.

        Driven end-to-end through the patched ``Runner.run_live``. The agent's live turn is stubbed so
        the test doesn't open a real model connection; the agent span is still tagged with the session
        id and user id resolved from the Session object, since neither is passed as a keyword.
        """
        from google.adk.agents.live_request_queue import LiveRequestQueue

        session = await test_runner.session_service.create_session(
            app_name=test_runner.app_name,
            user_id="live-user",
            session_id="live-session",
        )

        async def _stub_live_turn(*args, **kwargs):
            # Stand in for the agent's live model turn so no real connection is opened.
            for _ in ():
                yield _

        with mock.patch.object(type(test_runner.agent), "run_live", _stub_live_turn):
            async for _ in test_runner.run_live(session=session, live_request_queue=LiveRequestQueue()):
                pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        run_live_spans = [s for s in spans if s.resource.endswith("run_live")]
        assert len(run_live_spans) == 1

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(run_live_spans[0]),
            span_kind="agent",
            name="test_agent",
            tags={
                "ml_app": "<ml-app-name>",
                "service": "tests.contrib.google_adk",
                "integration": "google_adk",
                "session_id": "live-session",
                "user_id": "live-user",
                "app_name": "TestADKApp",
            },
        )

    @pytest.mark.asyncio
    async def test_run_live_does_not_buffer_when_llmobs_disabled(self, adk, test_spans):
        """APMSP-3136: the agent wrapper must not retain anything when LLMObs is disabled.

        Events were previously buffered unconditionally for the generator's lifetime, so a
        long-lived run_live stream could exhaust memory even though the buffer is only ever
        consumed by llmobs_set_tags (a no-op when LLMObs is disabled).
        """
        from ddtrace.contrib.internal.google_adk.patch import _traced_agent_run_async

        integration = adk._datadog_integration
        assert not integration.llmobs_enabled

        async def run_live(*args, **kwargs):
            for i in range(50):
                yield _fake_event("event-%d" % i)

        run_live.__name__ = "run_live"

        with _capture_tagged_response(integration) as captured:
            gen = _traced_agent_run_async(run_live, _fake_agent_instance(), (), {"session_id": "s", "user_id": "u"})
            consumed = [event async for event in gen]

        # All 50 events still flow through to the caller...
        assert len(consumed) == 50
        # ...but nothing is retained when LLMObs is disabled.
        assert captured["response"] == []

    @pytest.mark.asyncio
    async def test_run_live_bounds_retained_messages_by_count(self, adk, test_spans, google_adk_llmobs, monkeypatch):
        """APMSP-3136: retention is bounded by message count, and compact messages (not raw
        events) are retained.
        """
        from ddtrace.contrib.internal.google_adk import patch as adk_patch_mod
        from ddtrace.contrib.internal.google_adk.patch import _traced_agent_run_async

        monkeypatch.setattr(adk_patch_mod, "_MAX_BUFFERED_AGENT_MESSAGES", 5)

        integration = adk._datadog_integration
        assert integration.llmobs_enabled

        async def run_live(*args, **kwargs):
            for i in range(20):
                yield _fake_event("event-%d" % i)

        run_live.__name__ = "run_live"

        with _capture_tagged_response(integration) as captured:
            gen = _traced_agent_run_async(run_live, _fake_agent_instance(), (), {"session_id": "s", "user_id": "u"})
            consumed = [event async for event in gen]

        # All events still reach the caller, but only the cap is retained for tagging...
        assert len(consumed) == 20
        assert len(captured["response"]) == 5
        # ...as the extracted compact representation, not the raw event objects.
        assert all(isinstance(m, dict) for m in captured["response"])

    @pytest.mark.asyncio
    async def test_run_live_bounds_retained_messages_by_size(self, adk, test_spans, google_adk_llmobs, monkeypatch):
        """APMSP-3136: a few very large events cannot grow the buffer without bound; retention is
        also capped by a total character budget.
        """
        from ddtrace.contrib.internal.google_adk import patch as adk_patch_mod
        from ddtrace.contrib.internal.google_adk.patch import _traced_agent_run_async

        monkeypatch.setattr(adk_patch_mod, "_MAX_BUFFERED_AGENT_MESSAGES", 10000)
        monkeypatch.setattr(adk_patch_mod, "_MAX_BUFFERED_AGENT_CHARS", 500)

        integration = adk._datadog_integration

        async def run_live(*args, **kwargs):
            for _ in range(10):
                yield _fake_event("x" * 1000)

        run_live.__name__ = "run_live"

        with _capture_tagged_response(integration) as captured:
            gen = _traced_agent_run_async(run_live, _fake_agent_instance(), (), {"session_id": "s", "user_id": "u"})
            consumed = [event async for event in gen]

        # Well under the message-count cap, but the size budget stops retention early.
        assert len(consumed) == 10
        assert 0 < len(captured["response"]) < 10

    @pytest.mark.asyncio
    async def test_run_live_releases_buffer_when_llmobs_disabled_midstream(self, adk, test_spans, google_adk_llmobs):
        """APMSP-3136: disabling LLMObs mid-stream releases the retained buffer instead of holding
        it until the (potentially unbounded) live session ends.
        """
        from ddtrace.contrib.internal.google_adk.patch import _traced_agent_run_async
        from ddtrace.llmobs import LLMObs

        integration = adk._datadog_integration
        assert integration.llmobs_enabled

        async def run_live(*args, **kwargs):
            for i in range(10):
                yield _fake_event("event-%d" % i)

        run_live.__name__ = "run_live"

        with _capture_tagged_response(integration) as captured:
            gen = _traced_agent_run_async(run_live, _fake_agent_instance(), (), {"session_id": "s", "user_id": "u"})
            consumed = 0
            async for _ in gen:
                consumed += 1
                if consumed == 3:
                    LLMObs.disable()

        assert consumed == 10
        # Buffer was released once LLMObs turned off.
        assert captured["response"] == []


@contextmanager
def _capture_tagged_response(integration):
    """Replace ``llmobs_set_tags`` to capture the ``response`` handed to it by the wrapper."""
    captured = {}

    def _spy(span, args, kwargs, response=None, operation=""):
        captured["response"] = response

    with mock.patch.object(integration, "llmobs_set_tags", _spy):
        yield captured


def _fake_event(text):
    """A minimal stand-in for an ADK ``Event`` carrying a single text part."""
    return SimpleNamespace(content=create_test_message(text))


def _fake_agent_instance():
    """Minimal stand-in for an ADK ``Runner`` for exercising the agent-run wrapper directly."""

    class _Model:
        pass

    class _Agent:
        name = "test_agent"
        model = _Model()

    class _Runner:
        agent = _Agent()
        app_name = "TestADKApp"

    return _Runner()
