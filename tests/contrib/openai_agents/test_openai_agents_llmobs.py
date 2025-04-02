from typing import Dict
from typing import List
from typing import Tuple

import mock
import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


COMMON_RESPONSE_LLM_METADATA = {
    "temperature": mock.ANY,
    "top_p": mock.ANY,
    "reasoning_tokens": mock.ANY,
    "tool_choice": "auto",
    "tools": mock.ANY,
    "truncation": "disabled",
    "text": {"format": {"type": "text"}},
}


def _assert_expected_agent_run(
    spans,
    llmobs_events,
    handoffs: List[str] = None,
    tools: List[str] = None,
    llm_calls: List[Tuple[List[Dict], List[Dict]]] = None,
    tool_calls: List[dict] = None,
) -> None:
    """Assert expected LLMObs events matches actual events for an agent run

    Args:
        spans: List of spans from the mock tracer
        llmobs_events: List of LLMObs events
        agent_name: Name of the agent
        handoffs: List of handoff names
        tools: List of tool names
        llm_calls: List of (input_messages, output_messages) for each LLM call
        tool_calls: List of information about tool calls
    """
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="agent",
        metadata={"handoffs": handoffs, "tools": tools},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    for i, event in enumerate(llmobs_events[1:]):
        if i % 2 == 0:
            assert event == _expected_llmobs_llm_span_event(
                spans[i + 1],
                span_kind="llm",
                input_messages=llm_calls[i // 2][0],
                output_messages=llm_calls[i // 2][1],
                token_metrics={
                    "input_tokens": mock.ANY,
                    "output_tokens": mock.ANY,
                    "total_tokens": mock.ANY,
                },
                metadata=COMMON_RESPONSE_LLM_METADATA,
                model_name="gpt-4o-2024-08-06",
                model_provider="openai",
                tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
            )
        else:
            tool_call = tool_calls[i // 2]
            error_args = (
                {
                    "error": f'{{"tool_name": "{tool_call["tool_name"]}", "error": "This is a test error"}}',
                    "error_message": "Error running tool (non-fatal)",
                }
                if tool_call["error"]
                else {}
            )
            io_args = (
                {"input_value": mock.ANY, "output_value": mock.ANY} if tool_call["type"] == "function_call" else {}
            )
            assert event == _expected_llmobs_non_llm_span_event(
                spans[i + 1],
                span_kind="tool",
                tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
                **io_args,
                **error_args,
            )


@pytest.mark.asyncio
async def test_llmobs_single_agent(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        result = await agents.Runner.run(simple_agent, "What is the capital of France?")

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 3

    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:],
        llmobs_events[1:],
        handoffs=[],
        tools=[],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the capital of France?"}],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_streamed_single_agent(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
    from openai.types.responses import ResponseTextDeltaEvent

    final_output = ""
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent_streamed.yaml"):
        result = agents.Runner.run_streamed(simple_agent, "What is the capital of France?")
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                final_output += event.data.delta

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 3

    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:],
        llmobs_events[1:],
        handoffs=[],
        tools=[],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the capital of France?"}],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


def test_llmobs_single_agent_sync(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        result = agents.Runner.run_sync(simple_agent, "What is the capital of France?")

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 3

    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:],
        llmobs_events[1:],
        handoffs=[],
        tools=[],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the capital of France?"}],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_manual_tracing_llmobs(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
    from agents.tracing import custom_span
    from agents.tracing import trace

    with request_vcr.use_cassette("test_simple_agent.yaml"):
        with trace("Simple Workflow", metadata={"foo": "bar"}):
            cspan = custom_span("custom", data={"foo": "bar"})
            cspan.start()
            cspan.finish()
            result = await agents.Runner.run(simple_agent, "What is the capital of France?")

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 4
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={"foo": "bar"},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="task",
        metadata={"foo": "bar"},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[2:],
        llmobs_events[2:],
        handoffs=[],
        tools=[],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the capital of France?"}],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_single_agent_with_tool_calls_llmobs(
    agents, mock_tracer, request_vcr, llmobs_events, addition_agent
):
    with request_vcr.use_cassette("test_single_agent_with_tool_calls.yaml"):
        result = await agents.Runner.run(addition_agent, "What is the sum of 1 and 2?")

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 5

    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the sum of 1 and 2?",
        output_value=result.final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:],
        llmobs_events[1:],
        handoffs=[],
        tools=["add"],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the sum of 1 and 2?"}],
                [
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ]
                    }
                ],
            ),
            (
                [
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ]
                    },
                    {"tool_calls": [{"tool_id": mock.ANY, "type": "function_call_output"}]},
                ],
                [{"role": "assistant", "content": result.final_output}],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": False}],
    )


@pytest.mark.asyncio
async def test_llmobs_multiple_agent_handoffs(agents, mock_tracer, request_vcr, llmobs_events, research_workflow):
    with request_vcr.use_cassette("test_multiple_agent_handoffs.yaml"):
        result = await agents.Runner.run(
            research_workflow, "What is a brief summary of what happened yesterday in the soccer world??"
        )

    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 8

    # top level workflow
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is a brief summary of what happened yesterday in the soccer world??",
        output_value=result.final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:6],
        llmobs_events[1:6],
        handoffs=["Summarizer"],
        tools=["research"],
        llm_calls=[
            (
                [
                    {
                        "role": "user",
                        "content": "What is a brief summary of what happened yesterday in the soccer world??",
                    }
                ],
                [
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"query": "soccer news October 4 2023"},
                                "name": "research",
                                "type": "function_call",
                            }
                        ]
                    }
                ],
            ),
            (
                [
                    {
                        "content": "What is a brief summary of what happened yesterday in the soccer world??",
                        "role": "user",
                    },
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"query": "soccer news October 4 2023"},
                                "name": "research",
                                "type": "function_call",
                            }
                        ]
                    },
                    {"tool_calls": [{"tool_id": mock.ANY, "type": "function_call_output"}]},
                ],
                [
                    {"role": "assistant", "content": mock.ANY},
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {},
                                "name": "transfer_to_summarizer",
                                "type": "function_call",
                            }
                        ]
                    },
                ],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": False}, {"type": "handoff", "error": False}],
    )
    _assert_expected_agent_run(
        spans[6:],
        llmobs_events[6:],
        handoffs=[],
        tools=[],
        llm_calls=[
            (
                mock.ANY,
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_single_agent_with_tool_errors(
    agents, mock_tracer, request_vcr, llmobs_events, addition_agent_with_tool_errors
):
    with request_vcr.use_cassette("test_agent_with_tool_errors.yaml"):
        result = await agents.Runner.run(addition_agent_with_tool_errors, "What is the sum of 1 and 2?")
    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 5

    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        input_value="What is the sum of 1 and 2?",
        output_value=result.final_output,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    _assert_expected_agent_run(
        spans[1:],
        llmobs_events[1:],
        handoffs=[],
        tools=["add"],
        llm_calls=[
            (
                [{"role": "user", "content": "What is the sum of 1 and 2?"}],
                [
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ]
                    }
                ],
            ),
            (
                [
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ]
                    },
                    {"tool_calls": [{"tool_id": mock.ANY, "type": "function_call_output"}]},
                ],
                [{"role": "assistant", "content": result.final_output}],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": True, "tool_name": "add"}],
    )


@pytest.mark.asyncio
async def test_llmobs_single_agent_with_guardrail_errors(agents, llmobs_events, simple_agent_with_guardrail):
    from agents.exceptions import InputGuardrailTripwireTriggered

    with pytest.raises(InputGuardrailTripwireTriggered):
        await agents.Runner.run(simple_agent_with_guardrail, "What is the sum of 1 and 2?")

    # NOTE: there is an issue where APM spans are not enqeued to the mock tracer
    # NOTE: guardrails are executed in parallel so sorting by time is unreliable
    llmobs_events.sort(key=lambda event: event["meta"]["span.kind"])

    assert len(llmobs_events) == 3

    assert llmobs_events[0]["meta"]["span.kind"] == "agent"
    assert llmobs_events[1]["meta"]["span.kind"] == "task"
    assert llmobs_events[2]["meta"]["span.kind"] == "workflow"

    assert llmobs_events[0]["status"] == "error"
    assert llmobs_events[0]["meta"]["error.message"] == "Guardrail tripwire triggered"
    assert llmobs_events[0]["meta"]["error.type"] == '{"guardrail": "simple_guardrail"}'
