from typing import Optional

import mock
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_span_links
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


def _link_view(span):
    """Build a dict the shape `_assert_span_link` expects (span_id + span_links)."""
    return {"span_id": str(span.span_id), "span_links": get_llmobs_span_links(span) or []}


def _expected_tool_kwargs(tool_call: dict) -> dict:
    """Build the kwargs for ``assert_llmobs_span_data`` for a tool span."""
    error = None
    if tool_call["error"]:
        error = {
            "type": "Error running tool (non-fatal)",
            "message": f'{{"tool_name": "{tool_call["tool_name"]}", "error": "This is a test error"}}',
            "stack": mock.ANY,
        }
    kwargs = dict(span_kind="tool", tags=COMMON_TAGS, error=error)
    if tool_call["type"] in ("function_call", "handoff"):
        kwargs["input_value"] = mock.ANY
        kwargs["output_value"] = mock.ANY
    return kwargs


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
                _assert_span_link(_link_view(tool_span), _link_view(span), "output", "input")
        else:
            tool_call = tool_calls[i // 2]
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                **_expected_tool_kwargs(tool_call),
            )
            # assert tool is linked to the previous LLM call
            _assert_span_link(_link_view(spans[i]), _link_view(span), "output", "input")
            previous_tool_spans.append(span)
    return previous_tool_spans


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
    _assert_span_link(_link_view(spans[2]), _link_view(spans[3]), "output", "input")
    # assert LLM span links to output guardrail span
    _assert_span_link(_link_view(spans[5]), _link_view(spans[6]), "output", "input")


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
            agent_index=0,
        )
