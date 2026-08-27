import gc
from typing import Any

from google.adk.code_executors.code_execution_utils import CodeExecutionInput
from google.adk.code_executors.unsafe_local_code_executor import UnsafeLocalCodeExecutor
from google.adk.tools.function_tool import FunctionTool
import pytest

from ddtrace.contrib.internal.google_adk.patch import _traced_functions_call_tool_live
from tests.contrib.google_adk.conftest import call_tool_async
from tests.contrib.google_adk.conftest import create_test_message
from tests.contrib.google_adk.conftest import stream_then_raise
from tests.contrib.google_adk.conftest import stream_values
from tests.contrib.google_adk.conftest import streaming_via_call_tool_async


@pytest.mark.asyncio
async def test_agent_run_async(test_runner, test_spans, request_vcr):
    """Test agent run async creates proper spans with basic APM tags."""
    message = create_test_message("Say hello")

    with request_vcr.use_cassette("agent_run_async.yaml"):
        output = ""
        try:
            async for event in test_runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=message,
            ):
                # google-adk >= 2.7.0 emits framework events with no content
                if event.content is None:
                    continue
                for part in event.content.parts:
                    if hasattr(part, "function_response") and part.function_response is not None:
                        response = part.function_response.response
                        if "results" in response:
                            output += response["results"][0]
                        else:
                            output += str(response)
                        output += "\n"
        except (TypeError, ValueError):
            # we're getting a TypeError for telemetry issues from the google adk library that
            # is most likely due to the vcr cassette. we can ignore it.
            # we are also getting a ValueError for the code executor when it tries to execute the code, saying
            # "Function exec_python is not found in the tools_dict", this must be a bug in the google adk library.
            # only happens on latest version of google adk library.
            pass

    assert output == "Found reference for: test\n{'product': 15}\n"

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    runner_spans = [s for s in spans if "InMemoryRunner.run_async" in s.resource]
    assert len(runner_spans) >= 1, f"Expected InMemoryRunner.run_async span, got spans: {[s.resource for s in spans]}"

    span = runner_spans[0]
    assert span.name == "google_adk.request"
    assert span.get_tag("component") == "google_adk"
    assert span.get_tag("google_adk.request.provider") == "google"
    assert span.get_tag("google_adk.request.model") == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_agent_with_tool_usage(test_runner, test_spans, request_vcr):
    """Test E2E agent run that triggers tool usage."""
    message = create_test_message("Can you search for information about recurring revenue?")

    with request_vcr.use_cassette("agent_tool_usage.yaml"):
        try:
            output = ""
            async for event in test_runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=message,
            ):
                for part in event.content.parts:
                    if hasattr(part, "function_response") and part.function_response is not None:
                        response = part.function_response.response
                        if "results" in response:
                            output += response["results"][0]
                        else:
                            output += str(response)
                        output += "\n"
        except TypeError:
            # we're getting a TypeError for telemetry issues from the google adk library that
            # is most likely due to the vcr cassette. we can ignore it.
            pass

    assert output == "Found reference for: recurring revenue\n"

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    runner_spans = [s for s in spans if "Runner.run_async" in s.resource]
    assert len(runner_spans) >= 1, f"Expected Runner.run_async spans, got spans: {[s.resource for s in spans]}"

    tool_spans = [s for s in spans if "FunctionTool.__call_tool_async" in s.resource]
    assert len(tool_spans) >= 1, (
        f"Expected FunctionTool.__call_tool_async spans, got spans: {[s.resource for s in spans]}"
    )

    runner_span = runner_spans[0]
    assert runner_span.name == "google_adk.request"
    assert runner_span.get_tag("component") == "google_adk"
    assert runner_span.get_tag("google_adk.request.provider") == "google"
    assert runner_span.get_tag("google_adk.request.model") == "gemini-2.5-pro"

    tool_span = tool_spans[0]
    assert tool_span.name == "google_adk.request"
    assert tool_span.get_tag("component") == "google_adk"


@pytest.mark.asyncio
async def test_agent_with_tool_calculation(test_runner, test_spans, request_vcr):
    """Test E2E agent run that triggers tool usage for calculations."""
    message = create_test_message("Please use the multiply tool to calculate 37 times 29.")

    with request_vcr.use_cassette("agent_math_and_code.yaml"):
        try:
            output = ""
            async for event in test_runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=message,
            ):
                if event.content is None:
                    # Skip events with no content (e.g., malformed function calls)
                    continue

                for part in event.content.parts:
                    # Capture all text output
                    if hasattr(part, "text") and part.text:
                        output += part.text + "\n"

                    # Capture function responses
                    if hasattr(part, "function_response") and part.function_response is not None:
                        response = part.function_response.response
                        if "results" in response:
                            output += response["results"][0] + "\n"
                        elif "product" in response:
                            output += str(response["product"]) + "\n"
                        else:
                            output += str(response) + "\n"

        except Exception:
            # we're getting a TypeError for telemetry issues from the google adk library that
            # is most likely due to the vcr cassette. we can ignore it.
            pass

    # Check for tool calculation result
    assert output.strip() != "", f"Expected some output but got: '{output}'"
    assert "1073" in output or "product" in output.lower(), f"Expected multiply tool result (1073) but got: '{output}'"

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    runner_spans = [s for s in spans if "Runner.run_async" in s.resource]
    assert len(runner_spans) >= 1, f"Expected Runner.run_async spans, got spans: {[s.resource for s in spans]}"

    tool_spans = [s for s in spans if "FunctionTool.__call_tool_async" in s.resource]
    assert len(tool_spans) >= 1, (
        f"Expected FunctionTool.__call_tool_async spans, got spans: {[s.resource for s in spans]}"
    )

    runner_span = runner_spans[0]
    assert runner_span.name == "google_adk.request"
    assert runner_span.get_tag("component") == "google_adk"
    assert runner_span.get_tag("google_adk.request.provider") == "google"
    assert runner_span.get_tag("google_adk.request.model") == "gemini-2.5-pro"

    tool_span = tool_spans[0]
    assert tool_span.name == "google_adk.request"
    assert tool_span.get_tag("component") == "google_adk"


def test_execute_code_creates_span(mock_invocation_context, test_spans):
    """Test that a span is created when code is executed."""
    executor = UnsafeLocalCodeExecutor()
    code_input = CodeExecutionInput(code='print("hello world")')
    executor.execute_code(mock_invocation_context, code_input)

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "google_adk.request"
    assert span.resource == "UnsafeLocalCodeExecutor.execute_code"
    assert span.get_tag("component") == "google_adk"
    assert span.get_tag("google_adk.request.provider") == "google"
    assert span.get_tag("google_adk.request.model") == "gemini-2.5-pro"
    assert span.error == 0


def test_execute_code_with_error_creates_span(mock_invocation_context, test_spans):
    """Test that a span is created with error tags when code execution fails."""
    executor = UnsafeLocalCodeExecutor()
    code_input = CodeExecutionInput(code='raise ValueError("Test error")')
    executor.execute_code(mock_invocation_context, code_input)

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "google_adk.request"
    assert span.resource == "UnsafeLocalCodeExecutor.execute_code"
    assert span.get_tag("component") == "google_adk"
    assert span.get_tag("google_adk.request.provider") == "google"
    assert span.get_tag("google_adk.request.model") == "gemini-2.5-pro"
    # we don't set error tags for code execution failures
    assert span.error == 0


@pytest.mark.asyncio
async def test_error_handling_e2e(test_runner, test_spans, request_vcr):
    """Test error handling in E2E agent execution."""
    from google.adk.tools.function_tool import FunctionTool

    def failing_tool(query: str) -> dict:
        raise ValueError("Test error message")

    failing_tool_obj = FunctionTool(func=failing_tool)
    test_runner.agent.tools.append(failing_tool_obj)

    message = create_test_message("Can you use the failing_tool to test error handling?")

    with request_vcr.use_cassette("agent_error_handling.yaml"):
        try:
            count = 0
            async for _ in test_runner.run_async(
                user_id="test-user",
                session_id="test-session",
                new_message=message,
            ):
                count += 1
                if count > 5:
                    break
        except Exception:
            pass

    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    runner_spans = [s for s in spans if "Runner.run_async" in s.resource]
    assert len(runner_spans) >= 1, f"Expected Runner.run_async spans, got spans: {[s.resource for s in spans]}"

    tool_spans = [s for s in spans if "FunctionTool.__call_tool_async" in s.resource]
    if tool_spans:
        tool_span = tool_spans[0]
        assert tool_span.name == "google_adk.request"
        assert tool_span.get_tag("component") == "google_adk"

    runner_span = runner_spans[0]
    assert runner_span.name == "google_adk.request"
    assert runner_span.get_tag("component") == "google_adk"
    assert runner_span.get_tag("google_adk.request.provider") == "google"
    assert runner_span.get_tag("google_adk.request.model") == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_call_tool_live_fallback_does_not_double_invoke_wrapped() -> None:
    call_count = 0

    async def fake_wrapped(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        for item in ("a", "b", "c"):
            yield item

    class _ToolContextWithoutInvocation:
        pass

    instance = object()
    args = (object(), object(), _ToolContextWithoutInvocation())
    kwargs: dict[str, Any] = {}

    yielded: list[str] = []
    async for item in _traced_functions_call_tool_live(fake_wrapped, instance, args, kwargs):
        yielded.append(item)

    assert yielded == ["a", "b", "c"]
    assert call_count == 1, f"wrapped should be invoked exactly once, was invoked {call_count} times"


@streaming_via_call_tool_async
@pytest.mark.asyncio
async def test_streaming_tool_called_with_keyword_arguments(adk, test_spans, streaming_tool_context):
    """The streaming dispatch path passes every argument by keyword, including `tool`."""
    tool = FunctionTool(func=stream_values)

    result = await call_tool_async(adk)(tool=tool, args={"count": 3}, tool_context=streaming_tool_context)

    assert [item async for item in result] == [{"value": 0}, {"value": 1}, {"value": 2}]

    spans = [s for t in test_spans.pop_traces() for s in t]
    tool_spans = [s for s in spans if "__call_tool_async" in s.resource]
    assert len(tool_spans) == 1
    assert tool_spans[0].duration is not None, "the tool span should stay open until the stream is exhausted"


@streaming_via_call_tool_async
@pytest.mark.asyncio
async def test_streaming_tool_span_finished_when_stream_raises(adk, test_spans, streaming_tool_context):
    """A tool that fails partway through the stream still finishes its span, with the error set."""
    tool = FunctionTool(func=stream_then_raise)

    result = await call_tool_async(adk)(tool=tool, args={"count": 2}, tool_context=streaming_tool_context)

    with pytest.raises(RuntimeError, match="stream blew up"):
        async for _ in result:
            pass

    tool_spans = [s for t in test_spans.pop_traces() for s in t if "__call_tool_async" in s.resource]
    assert len(tool_spans) == 1
    assert tool_spans[0].duration is not None
    assert tool_spans[0].error == 1


@streaming_via_call_tool_async
@pytest.mark.asyncio
async def test_streaming_tool_span_finished_when_consumer_stops_early(adk, test_spans, streaming_tool_context):
    """google-adk closes the stream with `Aclosing`, so an early close must finish the span."""
    tool = FunctionTool(func=stream_values)

    result = await call_tool_async(adk)(tool=tool, args={"count": 10}, tool_context=streaming_tool_context)

    async for _ in result:
        break
    await result.aclose()

    tool_spans = [s for t in test_spans.pop_traces() for s in t if "__call_tool_async" in s.resource]
    assert len(tool_spans) == 1
    assert tool_spans[0].duration is not None, "the span must be finished when the consumer stops early"


@streaming_via_call_tool_async
@pytest.mark.asyncio
async def test_streaming_tool_span_finished_when_stream_never_started(adk, test_spans, streaming_tool_context):
    """The non-live dispatch does not iterate an async generator result, it just stores it.

    A generator that never starts never runs its finally, so the span cannot be finished from
    inside the stream wrapper. Dropping the result must still finish it.
    """
    tool = FunctionTool(func=stream_values)

    # `tool` positionally, matching the non-live dispatch that does not check for a generator.
    result = await call_tool_async(adk)(tool, args={"count": 3}, tool_context=streaming_tool_context)

    del result
    gc.collect()

    tool_spans = [s for t in test_spans.pop_traces() for s in t if "__call_tool_async" in s.resource]
    assert len(tool_spans) == 1
    assert tool_spans[0].duration is not None, "an abandoned stream must not leave its span unfinished"
    assert tool_spans[0].duration >= 0, "an abandoned stream must not report a negative duration"
