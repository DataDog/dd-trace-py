import asyncio
import json
from typing import Optional
from unittest import mock

import openai
import pytest


try:
    from openai.types.responses.tool import Mcp
except ImportError:  # openai versions without hosted MCP tools
    Mcp = None
try:
    from openai.types.responses.function_shell_tool import FunctionShellTool
    from openai.types.responses.tool import CodeInterpreter
except ImportError:  # openai versions without container network policies
    CodeInterpreter = FunctionShellTool = None

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import safe_mcp_tool_config
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_span_name
from tests.llmobs._utils import _assert_span_link
from tests.llmobs._utils import assert_llmobs_span_data


COMMON_TAGS = {"service": "tests.contrib.agents", "ml_app": "<ml-app-name>", "integration": "openai_agents"}


COMMON_RESPONSE_LLM_METADATA = {
    "temperature": mock.ANY,
    "top_p": mock.ANY,
    "tool_choice": "auto",
    "tools": mock.ANY,
    "truncation": "disabled",
    "text": {"format": {"type": "text"}},
}

AGENT_TO_EXPECTED_AGENT_MANIFEST = {
    "Simple Agent": {
        "framework": "OpenAI",
        "name": "Simple Agent",
        "instructions": "You are a helpful assistant who answers questions concisely and accurately.",
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,  # different versions of the library have different model settings
    },
    "Simple Agent with Guardrails": {
        "framework": "OpenAI",
        "name": "Simple Agent",
        "instructions": "You are a helpful assistant specialized in addition calculations.",
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,  # different versions of the library have different model settings
        "tools": [
            {
                "name": "add",
                "description": "Add two numbers together",
                "strict_json_schema": True,
                "parameters": {
                    "a": {"type": "integer", "title": "A", "required": True},
                    "b": {"type": "integer", "title": "B", "required": True},
                },
            }
        ],
        "guardrails": mock.ANY,
    },
    "Addition Agent": {
        "framework": "OpenAI",
        "name": "Addition Agent",
        "instructions": mock.ANY,
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,
        "tools": [
            {
                "name": "add",
                "description": "Add two numbers together",
                "strict_json_schema": True,
                "parameters": {
                    "a": {"type": "integer", "title": "A", "required": True},
                    "b": {"type": "integer", "title": "B", "required": True},
                },
            }
        ],
    },
    "Researcher": {
        "framework": "OpenAI",
        "name": "Researcher",
        "instructions": "You are a helpful assistant that can research a topic using your research tool. "
        "Always research the topic before summarizing.",
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,
        "tools": [
            {
                "name": "research",
                "description": "Research the internet on a topic.",
                "strict_json_schema": True,
                "parameters": {"query": {"type": "string", "title": "Query", "required": True}},
            }
        ],
        "handoffs": [
            {"handoff_description": None, "agent_name": "Summarizer"},
        ],
    },
    "Summarizer": {
        "framework": "OpenAI",
        "name": "Summarizer",
        "instructions": "You are a helpful assistant that can summarize a research results.",
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,
    },
    "Weather Agent": {
        "framework": "OpenAI",
        "name": "Weather Agent",
        "instructions": "You are a helpful assistant specialized in searching the web for weather information.",
        "handoff_description": None,
        "model": "gpt-4o",
        "model_settings": mock.ANY,
        "tools": [
            {
                "name": "web_search_preview",
                "user_location": {"type": "approximate", "city": "New York"},
                "search_context_size": "medium",
            }
        ],
    },
}


def _expected_agent_metadata(agent_name: str) -> dict:
    return {"_dd": {"agent_manifest": AGENT_TO_EXPECTED_AGENT_MANIFEST[agent_name]}}


def _assert_expected_agent_run(
    expected_span_names: list[str],
    spans,
    llm_calls: Optional[list[tuple[list[dict], list[dict]]]] = None,
    tool_calls: Optional[list[dict]] = None,
    previous_tool_spans: Optional[list] = None,
    is_chat: bool = False,
) -> list:
    """Assert expected LLMObs span data for an agent run.

    Returns the list of tool spans seen so far (for cross-run span-link assertions).

    Args:
        expected_span_names: ordered list of LLMObs span names expected for this run.
        spans: list of APM spans, sorted by start_ns, beginning with the agent span.
        llm_calls: list of ``(input_messages, output_messages)`` pairs for each LLM call.
        tool_calls: list of dicts describing each tool call (``type``, ``error``, optional ``tool_name``).
        previous_tool_spans: list of tool spans from prior agent runs to assert span-links against.
        is_chat: when True, the LLM span is produced by the openai integration (chat completions);
            skip its event-shape assertion (kept compatible with the original test).
    """
    if previous_tool_spans is None:
        previous_tool_spans = []
    # Names match the LLMObs name on each span in order.
    for i, span in enumerate(spans):
        assert get_llmobs_span_name(span) == expected_span_names[i], (
            f"span[{i}] name mismatch: expected={expected_span_names[i]!r}, actual={get_llmobs_span_name(span)!r}"
        )
    # First span: agent span.
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="agent",
        metadata=_expected_agent_metadata(get_llmobs_span_name(spans[0])),
        tags=COMMON_TAGS,
        name=get_llmobs_span_name(spans[0]),
    )

    for i, span in enumerate(spans[1:]):
        if i % 2 == 0:
            # LLM span — skip strict assertion in chat mode (handled by openai integration).
            if not is_chat:
                assert_llmobs_span_data(
                    _get_llmobs_data_metastruct(span),
                    span_kind="llm",
                    input_messages=llm_calls[i // 2][0],
                    output_messages=llm_calls[i // 2][1],
                    metrics={
                        "input_tokens": mock.ANY,
                        "output_tokens": mock.ANY,
                        "total_tokens": mock.ANY,
                        "reasoning_output_tokens": mock.ANY,
                    },
                    metadata=COMMON_RESPONSE_LLM_METADATA,
                    model_name="gpt-4o-2024-08-06",
                    model_provider="openai",
                    tags=COMMON_TAGS,
                    name=expected_span_names[i + 1],
                )
            for tool_span in previous_tool_spans:
                _assert_span_link(tool_span, span, "output", "input")
        else:
            tool_call = tool_calls[i // 2]
            error = None
            if tool_call["error"]:
                error = {
                    "type": "Error running tool (non-fatal)",
                    "message": f'{{"tool_name": "{tool_call["tool_name"]}", "error": "This is a test error"}}',
                    "stack": mock.ANY,
                }
            io_args = (
                {"input_value": mock.ANY, "output_value": mock.ANY}
                if tool_call["type"] in ("function_call", "handoff")
                else {}
            )
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind="tool",
                tags=COMMON_TAGS,
                error=error,
                **io_args,
            )
            # assert tool is linked to the previous LLM call
            _assert_span_link(spans[i], span, "output", "input")
            previous_tool_spans.append(span)
    return previous_tool_spans


@pytest.mark.asyncio
async def test_llmobs_concurrent_agents_in_same_apm_trace(
    agents, openai_agents_llmobs, test_spans, request_vcr, simple_agent, tracer
):
    with request_vcr.use_cassette("test_simple_agent.yaml", allow_playback_repeats=True):
        with tracer.trace("experiment"):
            results = await asyncio.gather(
                agents.Runner.run(simple_agent, "What is the capital of France?"),
                agents.Runner.run(simple_agent, "What is the capital of France?"),
            )

    spans = [span for trace in test_spans.pop_traces() for span in trace]
    workflow_spans = [span for span in spans if span.name == "Agent workflow"]

    assert len(workflow_spans) == 2
    for workflow_span, result in zip(sorted(workflow_spans, key=lambda span: span.start_ns), results):
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(workflow_span),
            span_kind="workflow",
            input_value="What is the capital of France?",
            output_value=result.final_output,
            metadata={},
            tags=COMMON_TAGS,
        )


@pytest.mark.asyncio
async def test_llmobs_single_agent(agents, openai_agents_llmobs, test_spans, request_vcr, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        result = await agents.Runner.run(simple_agent, "What is the capital of France?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 3

    assert get_llmobs_span_name(spans[0]) == "Agent workflow"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Simple Agent", "Simple Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": simple_agent.instructions,
                    },
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_streamed_single_agent(agents, openai_agents_llmobs, test_spans, request_vcr, simple_agent):
    from openai.types.responses import ResponseTextDeltaEvent

    final_output = ""
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent_streamed.yaml"):
        result = agents.Runner.run_streamed(simple_agent, "What is the capital of France?")
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                final_output += event.data.delta

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 3

    assert get_llmobs_span_name(spans[0]) == "Agent workflow"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Simple Agent", "Simple Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": simple_agent.instructions,
                    },
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


def test_llmobs_single_agent_sync(agents, openai_agents_llmobs, test_spans, request_vcr, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        result = agents.Runner.run_sync(simple_agent, "What is the capital of France?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 3

    assert get_llmobs_span_name(spans[0]) == "Agent workflow"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Simple Agent", "Simple Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": simple_agent.instructions,
                    },
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_manual_tracing_llmobs(agents, openai_agents_llmobs, test_spans, request_vcr, simple_agent):
    from agents.tracing import custom_span
    from agents.tracing import trace

    with request_vcr.use_cassette("test_simple_agent.yaml"):
        with trace("Simple Workflow", metadata={"foo": "bar"}):
            cspan = custom_span("custom", data={"foo": "bar"})
            cspan.start()
            cspan.finish()
            result = await agents.Runner.run(simple_agent, "What is the capital of France?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 4

    assert get_llmobs_span_name(spans[0]) == "Simple Workflow"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the capital of France?",
        output_value=result.final_output,
        metadata={"foo": "bar"},
        tags=COMMON_TAGS,
    )
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[1]),
        span_kind="task",
        metadata={"foo": "bar"},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Simple Agent", "Simple Agent (LLM)"],
        spans[2:],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": simple_agent.instructions,
                    },
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
    )


@pytest.mark.asyncio
async def test_llmobs_single_agent_with_tool_calls_llmobs(
    agents, openai_agents_llmobs, test_spans, request_vcr, addition_agent
):
    with request_vcr.use_cassette("test_single_agent_with_tool_calls.yaml"):
        result = await agents.Runner.run(addition_agent, "What is the sum of 1 and 2?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 5

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the sum of 1 and 2?",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Addition Agent", "Addition Agent (LLM)", "add", "Addition Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {"role": "system", "content": addition_agent.instructions},
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                ],
                [
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ],
                        "role": "assistant",
                    }
                ],
            ),
            (
                [
                    {"role": "system", "content": "You are a helpful assistant specialized in addition calculations."},
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ],
                        "role": "assistant",
                    },
                    {
                        "role": "user",
                        "tool_results": [
                            {"tool_id": mock.ANY, "name": "", "result": "3", "type": "function_call_output"}
                        ],
                    },
                ],
                [{"role": "assistant", "content": result.final_output}],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": False}],
    )


@pytest.mark.skipif(
    parse_version(openai.__version__) >= (2, 0, 0),
    reason="hosted WebSearchTool serializes as 'web_search' on openai>=2 vs the recorded 'web_search_preview'",
)
@pytest.mark.asyncio
async def test_llmobs_single_agent_with_ootb_tools(
    agents, openai_agents_llmobs, test_spans, request_vcr, weather_agent
):
    with request_vcr.use_cassette("test_single_agent_with_ootb_tools.yaml"):
        result = await agents.Runner.run(weather_agent, "What is the weather like in New York right now?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 3

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the weather like in New York right now?",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Weather Agent", "Weather Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {"role": "system", "content": weather_agent.instructions},
                    {"role": "user", "content": "What is the weather like in New York right now?"},
                ],
                [
                    {
                        "content": "ResponseFunctionWebSearch(id='ws_68814fa4582081989a0bc4a33dc197cc026575ca32f194ce',"
                        " status='completed', type='web_search_call', action={'type': 'search', 'query': 'current "
                        "weather in New York'})",
                        "role": "assistant",
                    },
                    {"role": "assistant", "content": result.final_output},
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_llmobs_multiple_agent_handoffs(agents, openai_agents_llmobs, test_spans, request_vcr, research_workflow):
    with request_vcr.use_cassette("test_multiple_agent_handoffs.yaml"):
        result = await agents.Runner.run(
            research_workflow, "What is a brief summary of what happened yesterday in the soccer world??"
        )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 8

    # top level workflow
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is a brief summary of what happened yesterday in the soccer world??",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    previous_tool_spans = _assert_expected_agent_run(
        ["Researcher", "Researcher (LLM)", "research", "Researcher (LLM)", "transfer_to_summarizer"],
        spans[1:6],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": research_workflow.instructions,
                    },
                    {
                        "role": "user",
                        "content": "What is a brief summary of what happened yesterday in the soccer world??",
                    },
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
                        ],
                        "role": "assistant",
                    }
                ],
            ),
            (
                [
                    {
                        "role": "system",
                        "content": research_workflow.instructions,
                    },
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
                        ],
                        "role": "assistant",
                    },
                    {
                        "role": "user",
                        "tool_results": [
                            {
                                "tool_id": mock.ANY,
                                "name": "",
                                "result": "united beat liverpool 2-1 yesterday. also a lot of other stuff happened."
                                " like super important stuff. blah blah blah.",
                                "type": "function_call_output",
                            }
                        ],
                    },
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
                        ],
                        "role": "assistant",
                    },
                ],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": False}, {"type": "handoff", "error": False}],
    )
    _assert_expected_agent_run(
        ["Summarizer", "Summarizer (LLM)"],
        spans[6:],
        llm_calls=[
            (
                mock.ANY,
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
        previous_tool_spans=previous_tool_spans[-1:],
    )


@pytest.mark.asyncio
async def test_llmobs_single_agent_with_tool_errors(
    agents, openai_agents_llmobs, test_spans, request_vcr, addition_agent_with_tool_errors
):
    with request_vcr.use_cassette("test_agent_with_tool_errors.yaml"):
        result = await agents.Runner.run(addition_agent_with_tool_errors, "What is the sum of 1 and 2?")
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 5

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        input_value="What is the sum of 1 and 2?",
        output_value=result.final_output,
        metadata={},
        tags=COMMON_TAGS,
    )
    _assert_expected_agent_run(
        ["Addition Agent", "Addition Agent (LLM)", "add", "Addition Agent (LLM)"],
        spans[1:],
        llm_calls=[
            (
                [
                    {
                        "role": "system",
                        "content": addition_agent_with_tool_errors.instructions,
                    },
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                ],
                [
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ],
                        "role": "assistant",
                    }
                ],
            ),
            (
                [
                    {
                        "role": "system",
                        "content": addition_agent_with_tool_errors.instructions,
                    },
                    {"role": "user", "content": "What is the sum of 1 and 2?"},
                    {
                        "tool_calls": [
                            {
                                "tool_id": mock.ANY,
                                "arguments": {"a": 1, "b": 2},
                                "name": "add",
                                "type": "function_call",
                            }
                        ],
                        "role": "assistant",
                    },
                    {
                        "role": "user",
                        "tool_results": [
                            {
                                "tool_id": mock.ANY,
                                "name": "",
                                "result": "An error occurred while running the tool."
                                " Please try again. Error: This is a test error",
                                "type": "function_call_output",
                            }
                        ],
                    },
                ],
                [{"role": "assistant", "content": result.final_output}],
            ),
        ],
        tool_calls=[{"type": "function_call", "error": True, "tool_name": "add"}],
    )


@pytest.mark.asyncio
async def test_llmobs_oai_agents_with_chat_completions_span_linking(
    agents, openai, openai_agents_llmobs, test_spans, request_vcr, research_workflow
):
    with request_vcr.use_cassette("test_multiple_agent_handoffs_with_chat_completions.yaml"):
        result = await agents.Runner.run(
            research_workflow, "Research and then summarize what happened yesterday in the soccer world"
        )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 8

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="workflow",
        metadata={},
        tags=COMMON_TAGS,
    )
    previous_tool_spans = _assert_expected_agent_run(
        [
            "Researcher",
            "OpenAI.createChatCompletion",
            "research",
            "OpenAI.createChatCompletion",
            "transfer_to_summarizer",
        ],
        spans[1:6],
        tool_calls=[{"type": "function_call", "error": False}, {"type": "handoff", "error": False}],
        is_chat=True,
    )
    _assert_expected_agent_run(
        ["Summarizer", "OpenAI.createChatCompletion"],
        spans[6:],
        llm_calls=[
            (
                mock.ANY,
                [{"role": "assistant", "content": result.final_output}],
            )
        ],
        tool_calls=[],
        previous_tool_spans=previous_tool_spans[-1:],
        is_chat=True,
    )


async def test_llmobs_oai_agents_with_guardrail_spans(
    agents, openai, openai_agents_llmobs, test_spans, request_vcr, simple_agent_with_guardrail
):
    with request_vcr.use_cassette("test_oai_agents_with_guardrail_spans.yaml"):
        await agents.Runner.run(simple_agent_with_guardrail, "What is the sum of 1 and 2?")

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda span: span.start_ns)

    assert len(spans) == 7

    # assert input guardrail span links to LLM span
    _assert_span_link(spans[2], spans[3], "output", "input")
    # assert LLM span links to output guardrail span
    _assert_span_link(spans[5], spans[6], "output", "input")


@pytest.mark.asyncio
async def test_no_error_when_current_span_is_none(agents, tracer, simple_agent):
    """Regression test: tag_agent_manifest should not raise AttributeError when current_span is None."""
    from ddtrace.contrib.internal.openai_agents.patch import _patched_run_single_turn

    # Create an async mock for the original function that _patched_run_single_turn wraps
    async def mock_func(*args, **kwargs):
        return None

    # Directly test the patched function with current_span returning None
    with mock.patch.object(tracer, "current_span", return_value=None):
        # Should not raise AttributeError: 'NoneType' object has no attribute '_set_ctx_item'
        await _patched_run_single_turn(
            mock_func,
            instance=None,
            args=(simple_agent,),
            kwargs={"input": "What is the capital of France?"},
        )


# Fake values shaped like real credentials. Prefixes are split so secret scanners do not flag them.
_MCP_PASSWORD = "Tr0ub4dor-3xample!"
_MCP_PATH_TOKEN = "NGQ3ZTk1YjItOGM0MS00ZTFhLWI2ZDMtOWYyYTdjMWU1ZDQ4"
_MCP_QUERY_KEY = "sk" + "-proj-" + "Zt8yQm3vLk2pW9xRn5bC7dF1gHj4sA6eUo0iTqYwErT"
_MCP_HEADER_TOKEN = "xo" + "xb-" + "1234567890123-4567890123456-Hq7Lm2Xw9Rt4Yb8Nc3Vd6Js1"
_MCP_OAUTH_TOKEN = "ya" + "29." + "a0AfB_byC7kq2ZxW9mLpT4vNbY8cHdF3sJ6aQe1uXo0iR"
_MCP_CREDENTIALS = (_MCP_PASSWORD, _MCP_PATH_TOKEN, _MCP_QUERY_KEY, _MCP_HEADER_TOKEN, _MCP_OAUTH_TOKEN, "/api/mcp/s/")
_LEAKY_MCP_TOOL_CONFIG = {
    "type": "mcp",
    "server_label": "zapier",
    "server_description": "Zapier actions",
    "server_url": f"https://svc:{_MCP_PASSWORD}@MCP.Zapier.com:443/api/mcp/s/{_MCP_PATH_TOKEN}/mcp?api_key={_MCP_QUERY_KEY}#x",
    "headers": {"Authorization": f"Bearer {_MCP_HEADER_TOKEN}", "Cookie": "sessionid=8f2b"},
    "authorization": _MCP_OAUTH_TOKEN,
    "require_approval": "never",
    "allowed_tools": ["send_email"],
}
_SAFE_MCP_TOOL_CONFIG = {
    "type": "mcp",
    "server_label": "zapier",
    "server_description": "Zapier actions",
    "server_url": "https://mcp.zapier.com",
    "require_approval": "never",
    "allowed_tools": ["send_email"],
}


def _assert_no_mcp_credentials(recorded):
    serialized = json.dumps(recorded, default=str)
    for credential in _MCP_CREDENTIALS + ("sessionid",):
        assert credential not in serialized


def test_agent_manifest_keeps_only_the_safe_view_of_hosted_mcp_tools(agents):
    """A HostedMCPTool config is the application's own, so nothing redacts its credentials upstream."""
    agent = agents.Agent(
        name="mcp agent",
        tools=[agents.HostedMCPTool(tool_config=dict(_LEAKY_MCP_TOOL_CONFIG))],
    )

    (tool,) = OpenAIAgentsIntegration._extract_tools_from_agent(None, agent)

    assert tool["name"] == "hosted_mcp"
    assert tool["tool_config"] == _SAFE_MCP_TOOL_CONFIG
    _assert_no_mcp_credentials(tool)


@pytest.mark.skipif(Mcp is None, reason="openai has no hosted MCP tool type")
def test_response_metadata_keeps_only_the_safe_view_of_hosted_mcp_tools():
    """OpenAI echoes response.tools with header values redacted but server_url verbatim."""
    echoed = dict(_LEAKY_MCP_TOOL_CONFIG, headers={"Authorization": "<redacted>"}, authorization=None)
    function_tool = {"type": "function", "name": "lookup", "parameters": {"type": "object"}, "strict": True}
    response = mock.Mock(spec=["tools"], tools=[Mcp.model_validate(echoed), function_tool])
    span = mock.Mock(span_data=mock.Mock(type="response", response=response))

    metadata = OaiSpanAdapter(span).llmobs_metadata

    assert metadata["tools"] == [_SAFE_MCP_TOOL_CONFIG, function_tool]
    _assert_no_mcp_credentials(metadata)


@pytest.mark.parametrize(
    "server_url,expected",
    [
        (None, None),
        ("not a url", None),
        ("https://mcp.example.com:notaport/mcp", None),
    ],
)
def test_safe_mcp_tool_config_omits_unusable_urls(server_url, expected):
    safe = safe_mcp_tool_config({"type": "mcp", "server_label": "x", "server_url": server_url})

    assert safe.get("server_url") == expected
    assert safe["server_label"] == "x"


_DOMAIN_SECRET = "fake-domain-secret-" + "Zt8yQm3vLk2pW9xRn5bC"
_NETWORK_POLICY = {
    "type": "allowlist",
    "allowed_domains": ["api.example.com"],
    "domain_secrets": [{"domain": "api.example.com", "name": "API_KEY", "value": _DOMAIN_SECRET}],
}
_SAFE_NETWORK_POLICY = {
    "type": "allowlist",
    "allowed_domains": ["api.example.com"],
    "domain_secrets": [{"domain": "api.example.com", "name": "API_KEY"}],
}


def test_agent_manifest_drops_code_interpreter_domain_secret_values(agents):
    tool_config = {"type": "code_interpreter", "container": {"type": "auto", "network_policy": _NETWORK_POLICY}}
    agent = agents.Agent(name="code agent", tools=[agents.CodeInterpreterTool(tool_config=tool_config)])

    (tool,) = OpenAIAgentsIntegration._extract_tools_from_agent(None, agent)

    assert tool["tool_config"] == {
        "type": "code_interpreter",
        "container": {"type": "auto", "network_policy": _SAFE_NETWORK_POLICY},
    }
    assert _DOMAIN_SECRET not in json.dumps(tool, default=str)


def test_agent_manifest_model_settings_keep_only_inference_params(agents):
    """extra_headers, extra_query, extra_body and extra_args can carry proxy or provider credentials."""
    settings = {
        "temperature": 0.2,
        "max_tokens": 512,
        "extra_headers": {"Authorization": f"Bearer {_MCP_HEADER_TOKEN}", "api-key": _MCP_QUERY_KEY},
        "extra_query": {"api-version": "2024-10-21", "sig": _MCP_PATH_TOKEN},
        "extra_body": {"metadata": {"secret": _MCP_OAUTH_TOKEN}},
    }
    if "extra_args" in agents.ModelSettings.__dataclass_fields__:
        settings["extra_args"] = {"api_key": _MCP_PASSWORD}
    agent = agents.Agent(name="settings agent", model_settings=agents.ModelSettings(**settings))

    recorded = OpenAIAgentsIntegration._extract_model_settings_from_agent(None, agent)

    assert recorded["temperature"] == 0.2
    assert recorded["max_tokens"] == 512
    assert not {"extra_headers", "extra_query", "extra_body", "extra_args"} & set(recorded)
    _assert_no_mcp_credentials(recorded)


@pytest.mark.skipif(CodeInterpreter is None, reason="openai has no container network policies")
def test_response_metadata_drops_domain_secret_values():
    """Applied to every response tool, in case OpenAI echoes container policies like it echoes server_url."""
    code_interpreter = CodeInterpreter.model_validate(
        {"type": "code_interpreter", "container": {"type": "auto", "network_policy": _NETWORK_POLICY}}
    )
    shell = FunctionShellTool.model_validate(
        {"type": "shell", "environment": {"type": "container_auto", "network_policy": _NETWORK_POLICY}}
    )
    response = mock.Mock(spec=["tools"], tools=[code_interpreter, shell])
    span = mock.Mock(span_data=mock.Mock(type="response", response=response))

    metadata = OaiSpanAdapter(span).llmobs_metadata

    code_interpreter_tool, shell_tool = metadata["tools"]
    assert code_interpreter_tool["container"]["network_policy"] == _SAFE_NETWORK_POLICY
    assert shell_tool["environment"]["network_policy"] == _SAFE_NETWORK_POLICY
    assert _DOMAIN_SECRET not in json.dumps(metadata, default=str)
