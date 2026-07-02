"""Tests for LLMObs agent attribution: meta.agent_attribution stamped at span finish.

The SDK resolves the nearest agent ancestor at span activation (O(1) one-level lookup,
inheriting the parent's already-resolved attribution) and surfaces it as
``meta.agent_attribution`` only on spans that have an agent ancestor. Spans with no agent
ancestor omit the block entirely.
"""


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
        "parent_agent_name": "my_agent",
        "parent_agent_span_id": str(agent_span.span_id),
    }


def test_indirect_nesting_attributes_to_agent(llmobs, llmobs_events):
    """agent -> workflow -> tool: the workflow and the tool both attribute to the agent."""
    with llmobs.agent(name="my_agent") as agent_span:
        with llmobs.workflow(name="my_workflow"):
            with llmobs.tool(name="my_tool"):
                pass
    expected = {"parent_agent_name": "my_agent", "parent_agent_span_id": str(agent_span.span_id)}
    assert _event_by_name(llmobs_events, "my_workflow")["meta"]["agent_attribution"] == expected
    assert _event_by_name(llmobs_events, "my_tool")["meta"]["agent_attribution"] == expected


def test_sub_agent_attributes_to_enclosing_agent(llmobs, llmobs_events):
    """An agent nested under an agent attributes to the enclosing agent, never itself."""
    with llmobs.agent(name="outer_agent") as outer:
        with llmobs.agent(name="inner_agent") as inner:
            with llmobs.tool(name="inner_tool"):
                pass
    assert _event_by_name(llmobs_events, "inner_agent")["meta"]["agent_attribution"] == {
        "parent_agent_name": "outer_agent",
        "parent_agent_span_id": str(outer.span_id),
    }
    # The tool's nearest agent ancestor is the inner agent.
    assert _event_by_name(llmobs_events, "inner_tool")["meta"]["agent_attribution"] == {
        "parent_agent_name": "inner_agent",
        "parent_agent_span_id": str(inner.span_id),
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
