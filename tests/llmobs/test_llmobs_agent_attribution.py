"""Tests for LLMObs agent attribution: meta.agent_attribution.

The SDK resolves the nearest agent ancestor at span activation (O(1) one-level lookup,
inheriting the parent's already-resolved attribution) and surfaces it as
``meta.agent_attribution`` at span finish, only on spans that have an agent ancestor. Spans
with no agent ancestor omit the block entirely. Resolution reads the parent's span kind, which
is why integrations must have that kind set by the time a child activates (see the
auto-instrumented regression test below).
"""

from types import SimpleNamespace

import pytest

from ddtrace import config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.llmobs._integrations.bedrock import BedrockIntegration
from ddtrace.llmobs._integrations.claude_agent_sdk import ClaudeAgentSdkIntegration
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration
from ddtrace.llmobs._integrations.google_adk import GoogleAdkIntegration
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration


# Auto-instrumented integrations that create agent spans via integration.trace(), keyed by the
# start-time kwarg each one uses to signal "this is an agent span". openai_agents also emits agent
# spans but only via an OaiSpanAdapter, so it is exercised by its own contrib suite, not here.
_AUTO_AGENT_CASES = [
    pytest.param(CrewAIIntegration, "crewai", {"operation": "agent"}, id="crewai"),
    pytest.param(GoogleAdkIntegration, "google_adk", {"kind": "agent"}, id="google_adk"),
    pytest.param(BedrockIntegration, "botocore", {"interface_type": "agent"}, id="bedrock"),
    pytest.param(LangGraphIntegration, "langgraph", {"kind": "agent"}, id="langgraph"),
    pytest.param(ClaudeAgentSdkIntegration, "claude_agent_sdk", {"kind": "agent"}, id="claude_agent_sdk"),
]


def _event_by_name(llmobs_events, name):
    matches = [e for e in llmobs_events if e["name"] == name]
    assert len(matches) == 1, f"expected exactly one event named {name!r}, got {len(matches)}"
    return matches[0]


def test_tool_under_agent_attributes_to_agent(llmobs, llmobs_events):
    with llmobs.agent(name="my_agent") as agent_span:
        with llmobs.tool(name="my_tool"):
            pass
    tool_event = _event_by_name(llmobs_events, "my_tool")
    assert tool_event["meta"]["agent_attribution"] == {
        "pagent_name": "my_agent",
        "pagent_span_id": str(agent_span.span_id),
    }


def test_indirect_nesting_attributes_to_agent(llmobs, llmobs_events):
    """agent -> workflow -> tool: the workflow and the tool both attribute to the agent."""
    with llmobs.agent(name="my_agent") as agent_span:
        with llmobs.workflow(name="my_workflow"):
            with llmobs.tool(name="my_tool"):
                pass
    expected = {"pagent_name": "my_agent", "pagent_span_id": str(agent_span.span_id)}
    assert _event_by_name(llmobs_events, "my_workflow")["meta"]["agent_attribution"] == expected
    assert _event_by_name(llmobs_events, "my_tool")["meta"]["agent_attribution"] == expected


def test_sub_agent_attributes_to_enclosing_agent(llmobs, llmobs_events):
    """An agent nested under an agent attributes to the enclosing agent, never itself."""
    with llmobs.agent(name="outer_agent") as outer:
        with llmobs.agent(name="inner_agent") as inner:
            with llmobs.tool(name="inner_tool"):
                pass
    assert _event_by_name(llmobs_events, "inner_agent")["meta"]["agent_attribution"] == {
        "pagent_name": "outer_agent",
        "pagent_span_id": str(outer.span_id),
    }
    # The tool's nearest agent ancestor is the inner agent.
    assert _event_by_name(llmobs_events, "inner_tool")["meta"]["agent_attribution"] == {
        "pagent_name": "inner_agent",
        "pagent_span_id": str(inner.span_id),
    }


def test_top_level_agent_omits_block(llmobs, llmobs_events):
    with llmobs.agent(name="root_agent"):
        pass
    assert "agent_attribution" not in _event_by_name(llmobs_events, "root_agent")["meta"]


def test_top_level_llm_omits_block(llmobs, llmobs_events):
    with llmobs.llm(name="root_llm", model_name="test-model"):
        pass
    assert "agent_attribution" not in _event_by_name(llmobs_events, "root_llm")["meta"]


def test_tool_outside_agent_omits_block(llmobs, llmobs_events):
    """A workflow with a tool but no agent anywhere in the chain: neither gets the block."""
    with llmobs.workflow(name="lonely_workflow"):
        with llmobs.tool(name="lonely_tool"):
            pass
    assert "agent_attribution" not in _event_by_name(llmobs_events, "lonely_workflow")["meta"]
    assert "agent_attribution" not in _event_by_name(llmobs_events, "lonely_tool")["meta"]


@pytest.mark.parametrize("integration_cls, config_name, agent_trace_kwargs", _AUTO_AGENT_CASES)
def test_auto_instrumented_agent_parent_attributes_child(
    llmobs, llmobs_events, integration_cls, config_name, agent_trace_kwargs
):
    """A child under an auto-instrumented (trace()-based) agent span must attribute to that agent.

    Regression guard for the LIFO start-vs-finish bug: the agent kind must be known at span start,
    not finish. Also exercises each integration's start-time signal (operation / kind /
    interface_type) feeding ``_llmobs_span_kind``.
    """
    integration = integration_cls(IntegrationConfig(config, config_name))
    agent_span = integration.trace("agent_run", span_name="my_agent", submit_to_llmobs=True, **agent_trace_kwargs)
    try:
        with llmobs.tool(name="my_tool"):
            pass
    finally:
        agent_span.finish()

    tool_event = _event_by_name(llmobs_events, "my_tool")
    assert tool_event["meta"]["agent_attribution"] == {
        "pagent_name": "my_agent",
        "pagent_span_id": str(agent_span.span_id),
    }


def test_event_based_agent_parent_attributes_child(llmobs, llmobs_events):
    """Same guard for the event-based path: llama_index creates its agent span via the event
    subscriber (kind stamped in ``on_started``), not ``trace()``.
    """
    from ddtrace.contrib._events.llm import LlmRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.span_bus import span_from_context
    from ddtrace.llmobs._integrations.llama_index import LlamaIndexIntegration

    integration = LlamaIndexIntegration(IntegrationConfig(config, "llama_index"))
    event = LlmRequestEvent(
        component="llama_index",
        integration_config=integration.integration_config,
        resource="my_agent",
        provider="llama_index",
        llmobs_integration=integration,
        submit_to_llmobs=True,
        operation="agent",
    )
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        agent_span = span_from_context(ctx)
        with llmobs.tool(name="my_tool"):
            pass
        ctx.dispatch_ended_event()

    tool_event = _event_by_name(llmobs_events, "my_tool")
    assert tool_event["meta"]["agent_attribution"]["pagent_span_id"] == str(agent_span.span_id)


def test_google_adk_agent_name_stamped_at_start(llmobs, llmobs_events):
    """Google ADK never passes span_name to trace(); without name-at-start stamping, children
    attribute to 'google_adk.request' (the APM span name) instead of the actual ADK agent name.
    Regression guard: _dd_agent kwarg must flow through trace() to _llmobs_agent_name_at_start.
    """
    integration = GoogleAdkIntegration(IntegrationConfig(config, "google_adk"))
    mock_agent = SimpleNamespace(name="my_adk_agent")
    # Mirrors _traced_agent_run_async: no span_name, passes _dd_agent with the agent object.
    agent_span = integration.trace("Runner.run_async", kind="agent", submit_to_llmobs=True, _dd_agent=mock_agent)
    try:
        with llmobs.tool(name="my_tool"):
            pass
    finally:
        agent_span.finish()
    tool_event = _event_by_name(llmobs_events, "my_tool")
    assert tool_event["meta"]["agent_attribution"] == {
        "pagent_name": "my_adk_agent",
        "pagent_span_id": str(agent_span.span_id),
    }


def test_langgraph_agent_name_stamped_at_start(llmobs, llmobs_events):
    """LangGraph's LLMObs agent name is instance.name (short), but the APM span name is
    '{module}.CompiledGraph.{name}'. Without name-at-start stamping, children attribute to the
    long composed APM name. Regression guard: LangGraphIntegration.trace() must stamp name.
    """
    integration = LangGraphIntegration(IntegrationConfig(config, "langgraph"))
    mock_graph = SimpleNamespace(name="my_graph")
    # Mirrors traced_pregel_stream: long operation_id, instance carries the short name.
    agent_span = integration.trace(
        "module.CompiledGraph.my_graph", kind="agent", submit_to_llmobs=True, instance=mock_graph
    )
    try:
        with llmobs.tool(name="my_tool"):
            pass
    finally:
        agent_span.finish()
    tool_event = _event_by_name(llmobs_events, "my_tool")
    assert tool_event["meta"]["agent_attribution"] == {
        "pagent_name": "my_graph",
        "pagent_span_id": str(agent_span.span_id),
    }
