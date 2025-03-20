import asyncio

import pytest

from tests.llmobs._utils import _assert_span_link


def _verify_common_trace_properties(llmobs_events, spans):
    """Verify common properties for all spans in a trace"""
    # Check that all spans have the same trace ID
    assert len(spans) > 0
    assert len(llmobs_events) > 0

    # Get the trace ID
    trace_id = llmobs_events[0]["trace_id"]

    # Verify all spans have the same trace ID
    for span in llmobs_events:
        assert (
            span["trace_id"] == trace_id
        ), f"Span {span['span_id']} has different trace ID: {span['trace_id']} != {trace_id}"


def _assert_basic_agent_events(llmobs_events, spans, expected_span_count=3):
    """Helper to assert events for a basic agent run"""
    # Sort by start time
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) >= expected_span_count

    # Check span types
    workflow_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "workflow"]
    agent_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "agent"]
    llm_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "llm"]

    assert len(workflow_spans) >= 1, "No workflow spans found"
    assert len(agent_spans) >= 1, "No agent spans found"
    assert len(llm_spans) >= 1, "No LLM spans found"

    # Essential workflow links
    # Workflow input is linked to the input of the first LLM call
    _assert_span_link(workflow_spans[0], llm_spans[0], "input", "input")

    # Workflow output is linked to the output of the LLM
    _assert_span_link(workflow_spans[0], llm_spans[0], "output", "output")

    # Agent -> LLM
    _assert_span_link(agent_spans[0], llm_spans[0], "input", "input")

    # LLM -> Agent
    _assert_span_link(llm_spans[0], agent_spans[0], "output", "output")

    # Verify all spans have the same trace ID
    _verify_common_trace_properties(llmobs_events, spans)


def _assert_tool_agent_events(llmobs_events, spans, expected_span_count=4):
    """Helper to assert events for an agent run with tool usage"""
    # Sort by start time
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) >= expected_span_count

    # Check span types
    workflow_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "workflow"]
    agent_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "agent"]
    llm_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "llm"]
    tool_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "tool"]

    assert len(workflow_spans) >= 1, "No workflow spans found"
    assert len(agent_spans) >= 1, "No agent spans found"
    assert len(llm_spans) >= 1, "No LLM spans found"
    assert len(tool_spans) >= 1, "No tool spans found"

    # Essential workflow links
    # Workflow input is linked to the input of the first LLM call
    _assert_span_link(workflow_spans[0], llm_spans[0], "input", "input")

    # Workflow output is linked to the output of the LLM
    _assert_span_link(workflow_spans[0], llm_spans[0], "output", "output")

    # Agent -> LLM
    _assert_span_link(agent_spans[0], llm_spans[0], "input", "input")

    # LLM -> Agent
    _assert_span_link(llm_spans[0], agent_spans[0], "output", "output")

    # Essential tool links
    # LLM makes a tool choice which is linked to the tool input
    _assert_span_link(llm_spans[0], tool_spans[0], "output", "input")

    # Tool output is linked to an LLM input
    if len(llm_spans) > 1:
        _assert_span_link(tool_spans[0], llm_spans[1], "output", "input")

    # Verify all spans have the same trace ID
    _verify_common_trace_properties(llmobs_events, spans)


def _assert_handoff_agent_events(llmobs_events, spans, expected_span_count=5):
    """Helper to assert events for an agent run with handoffs"""
    # Sort by start time
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) >= expected_span_count

    # Check span types
    workflow_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "workflow"]
    agent_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "agent"]
    llm_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "llm"]

    assert len(workflow_spans) >= 1, "No workflow spans found"
    assert len(agent_spans) >= 2, "No handoff agent spans found"
    assert len(llm_spans) >= 2, "No LLM spans for handoff found"

    # Essential workflow links
    # Workflow input is linked to the input of the first LLM call
    _assert_span_link(workflow_spans[0], llm_spans[0], "input", "input")

    # Workflow output is linked to the output of the last LLM
    _assert_span_link(workflow_spans[0], llm_spans[-1], "output", "output")

    # First agent -> first LLM
    _assert_span_link(agent_spans[0], llm_spans[0], "input", "input")

    # First LLM -> first agent
    _assert_span_link(llm_spans[0], agent_spans[0], "output", "output")

    # Second agent -> second LLM
    _assert_span_link(agent_spans[1], llm_spans[1], "input", "input")

    # Second LLM -> second agent
    _assert_span_link(llm_spans[1], agent_spans[1], "output", "output")

    # Verify all spans have the same trace ID
    _verify_common_trace_properties(llmobs_events, spans)


@pytest.mark.asyncio
async def test_single_agent(agents_integration, mock_tracer, request_vcr, llmobs_events, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        result = await agents_integration.Runner.run(simple_agent, "What is the capital of France?")

    # Verify spans were created
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) > 0

    # Check span structure
    _assert_basic_agent_events(llmobs_events, spans)


async def test_single_agent_with_chat_completions(
    agents_integration, simple_agent, request_vcr, mock_tracer, llmobs_events
):
    pass


@pytest.mark.asyncio
async def test_agent_with_tool(agents_integration, calculator_agent, request_vcr, mock_tracer, llmobs_events):
    """Test tracing with an agent that uses tools"""
    with request_vcr.use_cassette("test_agent_with_tool.yaml"):
        result = await agents_integration.Runner.run(calculator_agent, "What is the average of 10, 20, and 30?")

    # Verify spans were created
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) > 0

    # Check span structure with tool usage
    _assert_tool_agent_events(llmobs_events, spans)


@pytest.mark.asyncio
async def test_agent_handoffs(agents_integration, handoffs_agent, request_vcr, mock_tracer, llmobs_events):
    """Test tracing with an agent that hands off to another agent"""
    with request_vcr.use_cassette("test_agent_handoffs.yaml"):
        result = await agents_integration.Runner.run(
            handoffs_agent, "Summarize the key points about climate change in 3 bullet points"
        )

    # Verify spans were created
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) > 0

    # Check span structure with handoffs
    _assert_handoff_agent_events(llmobs_events, spans)


@pytest.mark.asyncio
async def test_single_agent_with_streaming(agents_integration, simple_agent, request_vcr, mock_tracer, llmobs_events):
    """Test tracing with streaming enabled"""
    with request_vcr.use_cassette("test_streaming.yaml"):
        response = await agents_integration.Runner.run_stream(simple_agent, "Tell me a short story about a robot")

        # consume the stream
        async for chunk in response:
            pass

    # Verify spans were created
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) > 0

    # Basic span structure should be the same
    _assert_basic_agent_events(llmobs_events, spans)

    # Additional check for stream metadata
    workflow_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "workflow"]
    assert len(workflow_spans) >= 1


@pytest.mark.asyncio
async def test_gaurdrails(agents_integration, simple_agent, request_vcr, mock_tracer, llmobs_events):
    """Test tracing with guardrails for language detection/translation"""
    with request_vcr.use_cassette("test_guardrails_language.yaml"):
        # Use non-English input to trigger language detection/translation guardrails
        result = await agents_integration.Runner.run(
            simple_agent, "¿Cuál es la capital de Francia?"  # "What is the capital of France?" in Spanish
        )

    # Verify spans were created
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) > 0

    # Check for guardrail spans (may be labeled as 'task' type)
    workflow_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "workflow"]
    agent_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "agent"]
    llm_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "llm"]
    task_spans = [span for span in llmobs_events if span.get("meta", {}).get("span.kind") == "task"]

    assert len(workflow_spans) >= 1, "No workflow spans found"
    assert len(agent_spans) >= 1, "No agent spans found"
    assert len(llm_spans) >= 1, "No LLM spans found"

    # If guardrails spans are present as task spans
    if len(task_spans) > 0:
        # Language detection guardrail should be linked to the agent
        _assert_span_link(agent_spans[0], task_spans[0], "input", "input")
        # And the result should be linked back
        _assert_span_link(task_spans[0], agent_spans[0], "output", "output")

    # Verify all spans have the same trace ID
    _verify_common_trace_properties(llmobs_events, spans)
