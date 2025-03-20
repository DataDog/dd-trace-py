import asyncio

import mock
import pytest

from tests.llmobs._utils import _assert_span_link
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


@pytest.mark.asyncio
async def test_single_simple_agent(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
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
        span_links=True,
    )
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="agent",
        metadata={"handoffs": [], "tools": []},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[2] == _expected_llmobs_llm_span_event(
        spans[2],
        span_kind="llm",
        input_messages=[{"role": "user", "content": "What is the capital of France?"}],
        output_messages=[{"role": "assistant", "content": result.final_output}],
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )


@pytest.mark.asyncio
async def test_manual_tracing(agents, mock_tracer, request_vcr, llmobs_events, simple_agent):
    from agents.tracing import trace, custom_span

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
        span_links=True,
    )
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="task",
        metadata={"foo": "bar"},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
        spans[2],
        span_kind="agent",
        metadata={"handoffs": [], "tools": []},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[3] == _expected_llmobs_llm_span_event(
        spans[3],
        span_kind="llm",
        input_messages=[{"role": "user", "content": "What is the capital of France?"}],
        output_messages=[{"role": "assistant", "content": result.final_output}],
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )

@pytest.mark.asyncio
async def test_single_agent_with_tool_calls(
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
        span_links=True,
    )
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="agent",
        metadata={"handoffs": [], "tools": ["add"]},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[2] == _expected_llmobs_llm_span_event(
        spans[2],
        span_kind="llm",
        input_messages=[{"role": "user", "content": "What is the sum of 1 and 2?"}],
        output_messages=[
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
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )
    assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
        spans[3],
        span_kind="tool",
        input_value=mock.ANY,
        output_value=mock.ANY,
        metadata=mock.ANY,
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )
    assert llmobs_events[4] == _expected_llmobs_llm_span_event(
        spans[4],
        span_kind="llm",
        input_messages=[
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
        output_messages=[{"role": "assistant", "content": result.final_output}],
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )


async def test_multiple_agent_handoffs(agents, mock_tracer, request_vcr, llmobs_events, research_workflow):
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
        span_links=True,
    )

    # first research agent invoked
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="agent",
        metadata={"handoffs": ["Summarizer"], "tools": ["research"]},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )

    # first llm call of the research agent
    assert llmobs_events[2] == _expected_llmobs_llm_span_event(
        spans[2],
        span_kind="llm",
        input_messages=[
            {"role": "user", "content": "What is a brief summary of what happened yesterday in the soccer world??"}
        ],
        output_messages=[
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
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )

    # tool call to do research
    assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
        spans[3],
        span_kind="tool",
        input_value=mock.ANY,
        output_value=mock.ANY,
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )

    # second llm call of the research agent
    assert llmobs_events[4] == _expected_llmobs_llm_span_event(
        spans[4],
        span_kind="llm",
        input_messages=[
            {"content": "What is a brief summary of what happened yesterday in the soccer world??", "role": "user"},
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
        output_messages=[
            {"role": "assistant", "content": mock.ANY},
            {
                "tool_calls": [
                    {"tool_id": mock.ANY, "arguments": {}, "name": "transfer_to_summarizer", "type": "function_call"}
                ]
            },
        ],
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )

    # handoff tool call
    assert llmobs_events[5] == _expected_llmobs_non_llm_span_event(
        spans[5],
        span_kind="tool",
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )

    # summarizer agent invoked
    assert llmobs_events[6] == _expected_llmobs_non_llm_span_event(
        spans[6],
        span_kind="agent",
        metadata={"handoffs": [], "tools": []},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )

    # final llm call of the summarizer agent
    assert llmobs_events[7] == _expected_llmobs_llm_span_event(
        spans[7],
        span_kind="llm",
        input_messages=mock.ANY,
        output_messages=[{"role": "assistant", "content": result.final_output}],
        token_metrics={
            "input_tokens": mock.ANY,
            "output_tokens": mock.ANY,
            "total_tokens": mock.ANY,
        },
        metadata=COMMON_RESPONSE_LLM_METADATA,
        model_name="gpt-4o-2024-08-06",
        model_provider="openai",
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
        span_links=True,
    )


@pytest.mark.asyncio
async def test_single_agent_with_gaurdrail_errors(
    agents, mock_tracer, request_vcr, llmobs_events, simple_agent_with_gaurdrail
):
    from agents.exceptions import InputGuardrailTripwireTriggered
    with request_vcr.use_cassette("test_single_agent_with_gaurdrail_errors.yaml"):
        with pytest.raises(InputGuardrailTripwireTriggered):
            result = await agents.Runner.run(simple_agent_with_gaurdrail, "What is the sum of 1 and 2?")
    print(llmobs_events)
    spans = mock_tracer.pop_traces()[0]
    spans.sort(key=lambda span: span.start_ns)
    llmobs_events.sort(key=lambda event: event["start_ns"])

    assert len(spans) == len(llmobs_events) == 3

    # First span - workflow
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        spans[0],
        span_kind="workflow",
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )

    # Second span - agent with error
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        spans[1],
        span_kind="agent",
        metadata={"handoffs": [], "tools": []},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>", "error": "1"},
        error="Guardrail tripwire triggered\n{'guardrail': 'simple_gaurdrail'}",
        error_message="Guardrail tripwire triggered\n{'guardrail': 'simple_gaurdrail'}",
    )

    # Third span - guardrail execution
    assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
        spans[2],
        span_kind="task",
        metadata={},
        tags={"service": "tests.contrib.agents", "ml_app": "<ml-app-name>"},
    )


async def test_single_agent_with_chat_completions(
    agents, simple_agent, request_vcr, mock_tracer, llmobs_events
):
    pass
