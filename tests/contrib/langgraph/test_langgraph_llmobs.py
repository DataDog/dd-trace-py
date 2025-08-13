import json
import random

import pytest

from ddtrace.contrib.internal.langgraph.patch import LANGGRAPH_VERSION
from tests.llmobs._utils import _assert_span_link


def _find_span_by_name(llmobs_events, name):
    """Find a single span by name."""
    for event in llmobs_events:
        if event["name"] == name:
            return event
    assert False, f"Span '{name}' not found in llmobs span events"


def _find_spans_by_name(llmobs_events, names):
    """Find multiple spans by name, sorting by start_ns."""
    spans = []
    for event in llmobs_events:
        if event["name"] in names:
            spans.append(event)
    if len(spans) != len(names):
        assert False, f"Spans {names} not found in llmobs span events"
    return sorted(spans, key=lambda x: x["start_ns"])


class TestLangGraphLLMObs:
    def test_simple_graph(self, llmobs_events, simple_graph):
        simple_graph.invoke({"a_list": [], "which": "a"}, stream_mode=["values"])
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")

        # Check that the graph span has the appropriate output
        # stream_mode=["values"] should result in the last yield being a tuple
        assert graph_span["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    async def test_simple_graph_async(self, llmobs_events, simple_graph):
        await simple_graph.ainvoke({"a_list": [], "which": "a"})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")

        # Check that the graph span has the appropriate output
        # default stream_mode of "values" should result in the last yield being an object
        assert graph_span["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    def test_conditional_graph(self, llmobs_events, conditional_graph):
        conditional_graph.invoke({"a_list": [], "which": "c"})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        c_span = _find_span_by_name(llmobs_events, "c")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(c_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, c_span, "output", "input")

    async def test_conditional_graph_async(self, llmobs_events, conditional_graph):
        await conditional_graph.ainvoke({"a_list": [], "which": "b"})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")

    def test_subgraph(self, llmobs_events, complex_graph):
        complex_graph.invoke({"a_list": [], "which": "b"})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        b1_span = _find_span_by_name(llmobs_events, "b1")
        b2_span = _find_span_by_name(llmobs_events, "b2")
        b3_span = _find_span_by_name(llmobs_events, "b3")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b3_span, b_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(b3_span, b_span, "output", "output")
        _assert_span_link(b_span, b1_span, "input", "input")
        _assert_span_link(b1_span, b2_span, "output", "input")
        _assert_span_link(b2_span, b3_span, "output", "input")

    async def test_subgraph_async(self, llmobs_events, complex_graph):
        await complex_graph.ainvoke({"a_list": [], "which": "b"})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        b1_span = _find_span_by_name(llmobs_events, "b1")
        b2_span = _find_span_by_name(llmobs_events, "b2")
        b3_span = _find_span_by_name(llmobs_events, "b3")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b3_span, b_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(b3_span, b_span, "output", "output")
        _assert_span_link(b_span, b1_span, "input", "input")
        _assert_span_link(b1_span, b2_span, "output", "input")
        _assert_span_link(b2_span, b3_span, "output", "input")

    def test_fanning_graph(self, llmobs_events, fanning_graph):
        fanning_graph.invoke({"a_list": [], "which": ""})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(d_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(a_span, c_span, "output", "input")
        _assert_span_link(b_span, d_span, "output", "input")
        _assert_span_link(c_span, d_span, "output", "input")

    async def test_fanning_graph_async(self, llmobs_events, fanning_graph):
        await fanning_graph.ainvoke({"a_list": [], "which": ""})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(d_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(a_span, c_span, "output", "input")
        _assert_span_link(b_span, d_span, "output", "input")
        _assert_span_link(c_span, d_span, "output", "input")

    def test_graph_with_send(self, llmobs_events, graph_with_send):
        graph_with_send.invoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span_1, b_span_2, b_span_3 = _find_spans_by_name(llmobs_events, ["b", "b", "b"])

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span_1, graph_span, "output", "output")
        _assert_span_link(b_span_2, graph_span, "output", "output")
        _assert_span_link(b_span_3, graph_span, "output", "output")
        _assert_span_link(a_span, b_span_1, "output", "input")
        _assert_span_link(a_span, b_span_2, "output", "input")
        _assert_span_link(a_span, b_span_3, "output", "input")

    async def test_graph_with_send_async(self, llmobs_events, graph_with_send):
        await graph_with_send.ainvoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span_1, b_span_2, b_span_3 = _find_spans_by_name(llmobs_events, ["b", "b", "b"])

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span_1, graph_span, "output", "output")
        _assert_span_link(b_span_2, graph_span, "output", "output")
        _assert_span_link(b_span_3, graph_span, "output", "output")
        _assert_span_link(a_span, b_span_1, "output", "input")
        _assert_span_link(a_span, b_span_2, "output", "input")
        _assert_span_link(a_span, b_span_3, "output", "input")

    def test_graph_with_send_complex(self, llmobs_events, graph_with_send_complex):
        graph_with_send_complex.invoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")
        e_span_from_send, e_span = _find_spans_by_name(llmobs_events, ["e", "e"])
        f_span = _find_span_by_name(llmobs_events, "f")
        g_span = _find_span_by_name(llmobs_events, "g")
        h_span = _find_span_by_name(llmobs_events, "h")
        i_span = _find_span_by_name(llmobs_events, "i")
        j_span = _find_span_by_name(llmobs_events, "j")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(b_span, d_span, "output", "input")
        _assert_span_link(b_span, e_span, "output", "input")
        _assert_span_link(c_span, e_span, "output", "input")
        _assert_span_link(c_span, e_span_from_send, "output", "input")
        _assert_span_link(c_span, f_span, "output", "input")
        _assert_span_link(c_span, g_span, "output", "input")
        _assert_span_link(d_span, h_span, "output", "input")
        _assert_span_link(e_span, h_span, "output", "input")
        _assert_span_link(e_span_from_send, h_span, "output", "input")
        _assert_span_link(f_span, i_span, "output", "input")
        _assert_span_link(g_span, i_span, "output", "input")
        _assert_span_link(h_span, j_span, "output", "input")
        _assert_span_link(i_span, j_span, "output", "input")

    async def test_graph_with_send_complex_async(self, llmobs_events, graph_with_send_complex):
        await graph_with_send_complex.ainvoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")
        e_span_from_send, e_span = _find_spans_by_name(llmobs_events, ["e", "e"])
        f_span = _find_span_by_name(llmobs_events, "f")
        g_span = _find_span_by_name(llmobs_events, "g")
        h_span = _find_span_by_name(llmobs_events, "h")
        i_span = _find_span_by_name(llmobs_events, "i")
        j_span = _find_span_by_name(llmobs_events, "j")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(b_span, d_span, "output", "input")
        _assert_span_link(b_span, e_span, "output", "input")
        _assert_span_link(c_span, e_span, "output", "input")
        _assert_span_link(c_span, e_span_from_send, "output", "input")
        _assert_span_link(c_span, f_span, "output", "input")
        _assert_span_link(c_span, g_span, "output", "input")
        _assert_span_link(d_span, h_span, "output", "input")
        _assert_span_link(e_span, h_span, "output", "input")
        _assert_span_link(e_span_from_send, h_span, "output", "input")
        _assert_span_link(f_span, i_span, "output", "input")
        _assert_span_link(g_span, i_span, "output", "input")
        _assert_span_link(h_span, j_span, "output", "input")
        _assert_span_link(i_span, j_span, "output", "input")

    def test_graph_with_uneven_sides(self, llmobs_events, graph_with_uneven_sides):
        graph_with_uneven_sides.invoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")
        e_first_finish, e_second_finish = _find_spans_by_name(llmobs_events, ["e", "e"])

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(a_span, c_span, "output", "input")
        _assert_span_link(b_span, e_first_finish, "output", "input")
        _assert_span_link(c_span, d_span, "output", "input")
        _assert_span_link(d_span, e_second_finish, "output", "input")
        _assert_span_link(e_first_finish, graph_span, "output", "output")
        _assert_span_link(e_second_finish, graph_span, "output", "output")

    async def test_graph_with_uneven_sides_async(self, llmobs_events, graph_with_uneven_sides):
        await graph_with_uneven_sides.ainvoke({"a_list": []})
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")
        c_span = _find_span_by_name(llmobs_events, "c")
        d_span = _find_span_by_name(llmobs_events, "d")
        e_first_finish, e_second_finish = _find_spans_by_name(llmobs_events, ["e", "e"])

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")
        _assert_span_link(a_span, c_span, "output", "input")
        _assert_span_link(b_span, e_first_finish, "output", "input")
        _assert_span_link(c_span, d_span, "output", "input")
        _assert_span_link(d_span, e_second_finish, "output", "input")
        _assert_span_link(e_first_finish, graph_span, "output", "output")
        _assert_span_link(e_second_finish, graph_span, "output", "output")

    async def test_astream_events(self, simple_graph, llmobs_events):
        async for _ in simple_graph.astream_events({"a_list": [], "which": "a"}, version="v2"):
            pass
        graph_span = _find_span_by_name(llmobs_events, "LangGraph")
        a_span = _find_span_by_name(llmobs_events, "a")
        b_span = _find_span_by_name(llmobs_events, "b")

        _assert_span_link(None, graph_span, "input", "input")
        _assert_span_link(b_span, graph_span, "output", "output")
        _assert_span_link(graph_span, a_span, "input", "input")
        _assert_span_link(a_span, b_span, "output", "input")

        assert a_span["meta"]["output"]["value"] is not None
        assert b_span["meta"]["output"]["value"] is not None

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_simple_graph(self, llmobs_events, agentic_graph_with_conditional_and_definitive_edges):
        agentic_graph_with_conditional_and_definitive_edges.invoke(
            {"a_list": [], "which": random.choice(["agent_b", "agent_c"])}
        )

        agent_a_span = _find_span_by_name(llmobs_events, "agent_a")
        try:
            conditional_agent_span = _find_span_by_name(llmobs_events, "agent_b")
            conditional_agent_name = "agent_b"
        except AssertionError:
            conditional_agent_span = _find_span_by_name(llmobs_events, "agent_c")
            conditional_agent_name = "agent_c"

        agent_d_span = _find_span_by_name(llmobs_events, "agent_d")

        expected_agent_a_manifest = {
            "framework": "LangGraph",
            "max_iterations": 25,
            "dependencies": ["a_list", "which"],
            "name": "agent_a",
            "tools": [],
        }

        expected_conditional_agent_manifest = {
            "framework": "LangGraph",
            "max_iterations": 25,
            "dependencies": ["a_list", "which"],
            "name": conditional_agent_name,
            "tools": [],
        }

        expected_agent_d_manifest = {
            "framework": "LangGraph",
            "max_iterations": 25,
            "dependencies": ["a_list", "which"],
            "name": "agent_d",
            "tools": [],
        }

        assert agent_a_span["meta"]["metadata"]["agent_manifest"] == expected_agent_a_manifest
        assert conditional_agent_span["meta"]["metadata"]["agent_manifest"] == expected_conditional_agent_manifest
        assert agent_d_span["meta"]["metadata"]["agent_manifest"] == expected_agent_d_manifest

    @pytest.mark.skipif(
        LANGGRAPH_VERSION < (0, 3, 21), reason="create_react_agent has full support after LangGraph 0.3.21"
    )
    def test_agent_manifest_from_create_react_agent(self, llmobs_events, agent_from_create_react_agent):
        agent_from_create_react_agent.invoke({"messages": [{"role": "user", "content": "What is 2 + 2?"}]})

        react_agent_span = _find_span_by_name(llmobs_events, "not_your_average_bostonian")

        expected_agent_manifest = {
            "framework": "LangGraph",
            "max_iterations": 25,
            "dependencies": ["messages"],
            "name": "not_your_average_bostonian",
            "tools": [
                {
                    "name": "add",
                    "description": "Adds two numbers together",
                    "parameters": {
                        "a": {
                            "title": "A",
                            "type": "integer",
                        },
                        "b": {
                            "title": "B",
                            "type": "integer",
                        },
                    },
                }
            ],
            "model": "gpt-4o-mini",
            "model_provider": "openai",
            "model_settings": {
                "temperature": 0.5,
            },
            "instructions": "You are a helpful assistant who talks with a Boston accent but is also very nice. You speak in full sentences with at least 15 words.",  # noqa: E501
        }

        assert react_agent_span["meta"]["metadata"]["agent_manifest"] == expected_agent_manifest

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_populates_tools_from_tool_node(self, llmobs_events, custom_agent_with_tool_node):
        custom_agent_with_tool_node.invoke({"a_list": []})

        agent_span = _find_span_by_name(llmobs_events, "custom_agent_with_tool_node")

        expected_agent_manifest = {
            "framework": "LangGraph",
            "max_iterations": 25,
            "dependencies": ["a_list"],
            "name": "custom_agent_with_tool_node",
            "tools": [
                {
                    "name": "add",
                    "description": "Adds two numbers together",
                    "parameters": {
                        "a": {
                            "title": "A",
                            "type": "integer",
                        },
                        "b": {
                            "title": "B",
                            "type": "integer",
                        },
                    },
                }
            ],
        }

        assert agent_span["meta"]["metadata"]["agent_manifest"] == expected_agent_manifest

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_different_recursion_limit(
        self, llmobs_events, agentic_graph_with_conditional_and_definitive_edges
    ):
        agentic_graph_with_conditional_and_definitive_edges.invoke(
            {"a_list": [], "which": random.choice(["agent_b", "agent_c"])}, {"recursion_limit": 100}
        )

        agent_span = _find_span_by_name(llmobs_events, "agent")

        assert agent_span["meta"]["metadata"]["agent_manifest"]["max_iterations"] == 100
