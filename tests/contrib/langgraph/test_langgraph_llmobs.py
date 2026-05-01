import json
import random

import pytest

from ddtrace.contrib.internal.langgraph.patch import LANGGRAPH_VERSION
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_output
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_links
from ddtrace.llmobs._utils import get_llmobs_span_name
from tests.llmobs._utils import _assert_span_link


LLMOBS_GLOBAL_CONFIG = dict(
    _dd_api_key="<not-a-real-api_key>",
    _llmobs_ml_app="unnamed-ml-app",
    _llmobs_enabled=True,
    _llmobs_sample_rate=1.0,
    service="tests.llmobs",
)


def _link_view(span):
    """Adapt an APM span into the dict shape ``_assert_span_link`` expects.

    ``_assert_span_link`` was written against the wire-event shape; in the
    meta_struct world span_links live on ``span._meta_struct["_llmobs"]``
    rather than at the top level of an event.
    """
    return {
        "span_id": str(span.span_id),
        "span_links": get_llmobs_span_links(span) or [],
    }


def _find_span_by_name(spans, name):
    """Find a single span by its LLMObs meta_struct name."""
    for span in spans:
        if get_llmobs_span_name(span) == name:
            return span
    assert False, f"Span '{name}' not found in spans"


def _find_spans_by_name(spans, names):
    """Find multiple spans by name, sorted by start_ns."""
    matched = [span for span in spans if get_llmobs_span_name(span) in names]
    if len(matched) != len(names):
        assert False, f"Spans {names} not found in spans"
    return sorted(matched, key=lambda s: s.start_ns)


def _collect_spans(test_spans):
    return [s for trace in test_spans.pop_traces() for s in trace]


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
class TestLangGraphLLMObs:
    def test_simple_graph(self, test_spans, simple_graph):
        simple_graph.invoke({"a_list": [], "which": "a"}, stream_mode=["values"])
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")

        # Check that the graph span has the appropriate output
        # stream_mode=["values"] should result in the last yield being a tuple
        graph_output = get_llmobs_output(graph_span) or {}
        assert graph_output.get("value") == json.dumps({"a_list": ["a", "b"], "which": "a"})

    async def test_simple_graph_async(self, test_spans, simple_graph):
        await simple_graph.ainvoke({"a_list": [], "which": "a"})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")

        # Check that the graph span has the appropriate output
        # default stream_mode of "values" should result in the last yield being an object
        graph_output = get_llmobs_output(graph_span) or {}
        assert graph_output.get("value") == json.dumps({"a_list": ["a", "b"], "which": "a"})

    def test_conditional_graph(self, test_spans, conditional_graph):
        conditional_graph.invoke({"a_list": [], "which": "c"})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        c_span = _find_span_by_name(spans, "c")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(c_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(c_span), "output", "input")

    async def test_conditional_graph_async(self, test_spans, conditional_graph):
        await conditional_graph.ainvoke({"a_list": [], "which": "b"})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")

    def test_subgraph(self, test_spans, complex_graph):
        complex_graph.invoke({"a_list": [], "which": "b"})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        b1_span = _find_span_by_name(spans, "b1")
        b2_span = _find_span_by_name(spans, "b2")
        b3_span = _find_span_by_name(spans, "b3")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b3_span), _link_view(b_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(b3_span), _link_view(b_span), "output", "output")
        _assert_span_link(_link_view(b_span), _link_view(b1_span), "input", "input")
        _assert_span_link(_link_view(b1_span), _link_view(b2_span), "output", "input")
        _assert_span_link(_link_view(b2_span), _link_view(b3_span), "output", "input")

    async def test_subgraph_async(self, test_spans, complex_graph):
        await complex_graph.ainvoke({"a_list": [], "which": "b"})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        b1_span = _find_span_by_name(spans, "b1")
        b2_span = _find_span_by_name(spans, "b2")
        b3_span = _find_span_by_name(spans, "b3")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b3_span), _link_view(b_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(b3_span), _link_view(b_span), "output", "output")
        _assert_span_link(_link_view(b_span), _link_view(b1_span), "input", "input")
        _assert_span_link(_link_view(b1_span), _link_view(b2_span), "output", "input")
        _assert_span_link(_link_view(b2_span), _link_view(b3_span), "output", "input")

    def test_fanning_graph(self, test_spans, fanning_graph):
        fanning_graph.invoke({"a_list": [], "which": ""})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(d_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(c_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(d_span), "output", "input")

    async def test_fanning_graph_async(self, test_spans, fanning_graph):
        await fanning_graph.ainvoke({"a_list": [], "which": ""})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")

        assert get_llmobs_span_name(graph_span) == "LangGraph"
        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(d_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(c_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(d_span), "output", "input")

    def test_graph_with_send(self, test_spans, graph_with_send):
        graph_with_send.invoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span_1, b_span_2, b_span_3 = _find_spans_by_name(spans, ["b", "b", "b"])

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span_1), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(b_span_2), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(b_span_3), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(a_span), _link_view(b_span_1), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span_2), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span_3), "output", "input")

    async def test_graph_with_send_async(self, test_spans, graph_with_send):
        await graph_with_send.ainvoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span_1, b_span_2, b_span_3 = _find_spans_by_name(spans, ["b", "b", "b"])

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span_1), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(b_span_2), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(b_span_3), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(a_span), _link_view(b_span_1), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span_2), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span_3), "output", "input")

    def test_graph_with_send_complex(self, test_spans, graph_with_send_complex):
        graph_with_send_complex.invoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")
        e_span_from_send, e_span = _find_spans_by_name(spans, ["e", "e"])
        f_span = _find_span_by_name(spans, "f")
        g_span = _find_span_by_name(spans, "g")
        h_span = _find_span_by_name(spans, "h")
        i_span = _find_span_by_name(spans, "i")
        j_span = _find_span_by_name(spans, "j")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(e_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(e_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(e_span_from_send), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(f_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(g_span), "output", "input")
        _assert_span_link(_link_view(d_span), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(e_span), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(e_span_from_send), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(f_span), _link_view(i_span), "output", "input")
        _assert_span_link(_link_view(g_span), _link_view(i_span), "output", "input")
        _assert_span_link(_link_view(h_span), _link_view(j_span), "output", "input")
        _assert_span_link(_link_view(i_span), _link_view(j_span), "output", "input")

    async def test_graph_with_send_complex_async(self, test_spans, graph_with_send_complex):
        await graph_with_send_complex.ainvoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")
        e_span_from_send, e_span = _find_spans_by_name(spans, ["e", "e"])
        f_span = _find_span_by_name(spans, "f")
        g_span = _find_span_by_name(spans, "g")
        h_span = _find_span_by_name(spans, "h")
        i_span = _find_span_by_name(spans, "i")
        j_span = _find_span_by_name(spans, "j")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(e_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(e_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(e_span_from_send), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(f_span), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(g_span), "output", "input")
        _assert_span_link(_link_view(d_span), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(e_span), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(e_span_from_send), _link_view(h_span), "output", "input")
        _assert_span_link(_link_view(f_span), _link_view(i_span), "output", "input")
        _assert_span_link(_link_view(g_span), _link_view(i_span), "output", "input")
        _assert_span_link(_link_view(h_span), _link_view(j_span), "output", "input")
        _assert_span_link(_link_view(i_span), _link_view(j_span), "output", "input")

    def test_graph_with_uneven_sides(self, test_spans, graph_with_uneven_sides):
        graph_with_uneven_sides.invoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")
        e_first_finish, e_second_finish = _find_spans_by_name(spans, ["e", "e"])

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(c_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(e_first_finish), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(d_span), _link_view(e_second_finish), "output", "input")
        _assert_span_link(_link_view(e_first_finish), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(e_second_finish), _link_view(graph_span), "output", "output")

    async def test_graph_with_uneven_sides_async(self, test_spans, graph_with_uneven_sides):
        await graph_with_uneven_sides.ainvoke({"a_list": []})
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")
        c_span = _find_span_by_name(spans, "c")
        d_span = _find_span_by_name(spans, "d")
        e_first_finish, e_second_finish = _find_spans_by_name(spans, ["e", "e"])

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")
        _assert_span_link(_link_view(a_span), _link_view(c_span), "output", "input")
        _assert_span_link(_link_view(b_span), _link_view(e_first_finish), "output", "input")
        _assert_span_link(_link_view(c_span), _link_view(d_span), "output", "input")
        _assert_span_link(_link_view(d_span), _link_view(e_second_finish), "output", "input")
        _assert_span_link(_link_view(e_first_finish), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(e_second_finish), _link_view(graph_span), "output", "output")

    async def test_astream_events(self, test_spans, simple_graph):
        async for _ in simple_graph.astream_events({"a_list": [], "which": "a"}, version="v2"):
            pass
        spans = _collect_spans(test_spans)
        graph_span = _find_span_by_name(spans, "LangGraph")
        a_span = _find_span_by_name(spans, "a")
        b_span = _find_span_by_name(spans, "b")

        _assert_span_link(None, _link_view(graph_span), "input", "input")
        _assert_span_link(_link_view(b_span), _link_view(graph_span), "output", "output")
        _assert_span_link(_link_view(graph_span), _link_view(a_span), "input", "input")
        _assert_span_link(_link_view(a_span), _link_view(b_span), "output", "input")

        a_output = get_llmobs_output(a_span) or {}
        b_output = get_llmobs_output(b_span) or {}
        assert a_output.get("value") is not None
        assert b_output.get("value") is not None

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_simple_graph(self, test_spans, agentic_graph_with_conditional_and_definitive_edges):
        agentic_graph_with_conditional_and_definitive_edges.invoke(
            {"a_list": [], "which": random.choice(["agent_b", "agent_c"])}
        )
        spans = _collect_spans(test_spans)

        agent_a_span = _find_span_by_name(spans, "agent_a")
        try:
            conditional_agent_span = _find_span_by_name(spans, "agent_b")
            conditional_agent_name = "agent_b"
        except AssertionError:
            conditional_agent_span = _find_span_by_name(spans, "agent_c")
            conditional_agent_name = "agent_c"

        agent_d_span = _find_span_by_name(spans, "agent_d")

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

        agent_a_metadata = get_llmobs_metadata(agent_a_span) or {}
        conditional_metadata = get_llmobs_metadata(conditional_agent_span) or {}
        agent_d_metadata = get_llmobs_metadata(agent_d_span) or {}

        assert agent_a_metadata.get("_dd", {}).get("agent_manifest") == expected_agent_a_manifest
        assert conditional_metadata.get("_dd", {}).get("agent_manifest") == expected_conditional_agent_manifest
        assert agent_d_metadata.get("_dd", {}).get("agent_manifest") == expected_agent_d_manifest

    @pytest.mark.skipif(
        LANGGRAPH_VERSION < (0, 3, 21), reason="create_react_agent has full support after LangGraph 0.3.21"
    )
    def test_agent_manifest_from_create_react_agent(self, test_spans, agent_from_create_react_agent):
        agent_from_create_react_agent.invoke({"messages": [{"role": "user", "content": "What is 2 + 2?"}]})
        spans = _collect_spans(test_spans)

        react_agent_span = _find_span_by_name(spans, "not_your_average_bostonian")

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

        react_agent_metadata = get_llmobs_metadata(react_agent_span) or {}
        assert react_agent_metadata.get("_dd", {}).get("agent_manifest") == expected_agent_manifest

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_populates_tools_from_tool_node(self, test_spans, custom_agent_with_tool_node):
        custom_agent_with_tool_node.invoke({"a_list": []})
        spans = _collect_spans(test_spans)

        agent_span = _find_span_by_name(spans, "custom_agent_with_tool_node")

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

        agent_metadata = get_llmobs_metadata(agent_span) or {}
        assert agent_metadata.get("_dd", {}).get("agent_manifest") == expected_agent_manifest

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_manifest_different_recursion_limit(
        self, test_spans, agentic_graph_with_conditional_and_definitive_edges
    ):
        agentic_graph_with_conditional_and_definitive_edges.invoke(
            {"a_list": [], "which": random.choice(["agent_b", "agent_c"])}, {"recursion_limit": 100}
        )
        spans = _collect_spans(test_spans)

        agent_span = _find_span_by_name(spans, "agent")

        agent_metadata = get_llmobs_metadata(agent_span) or {}
        assert agent_metadata.get("_dd", {}).get("agent_manifest", {}).get("max_iterations") == 100

    @pytest.mark.skipif(LANGGRAPH_VERSION < (0, 3, 22), reason="Agent names are only supported in LangGraph 0.3.22+")
    def test_agent_with_tool_calls_integrations_enabled(
        self, test_spans, agent_from_create_react_agent_integrations_enabled
    ):
        """
        Test that invoking an agent with tool calls while other integrations are enabled results in
        llm -> tool -> llm span links.
        """
        agent_from_create_react_agent_integrations_enabled.invoke(
            {"messages": [{"role": "user", "content": "What is 2 + 2?"}]}
        )
        spans = _collect_spans(test_spans)

        # Filter to LLMObs-bearing spans (some APM-only spans may be present from langchain/openai patching)
        llmobs_spans = [s for s in spans if _get_llmobs_data_metastruct(s)]
        llmobs_spans.sort(key=lambda s: s.start_ns)

        assert len(llmobs_spans) == 11

        first_llm_span = llmobs_spans[0]
        tool_span = llmobs_spans[4]
        second_llm_span = llmobs_spans[6]

        assert get_llmobs_span_kind(first_llm_span) == "llm"
        assert get_llmobs_span_kind(tool_span) == "tool"
        assert get_llmobs_span_kind(second_llm_span) == "llm"

        tool_links = get_llmobs_span_links(tool_span) or []
        second_llm_links = get_llmobs_span_links(second_llm_span) or []

        # assert llm -> tool span link
        assert tool_links[1]["span_id"] == str(first_llm_span.span_id)
        assert tool_links[1]["attributes"]["from"] == "output"
        assert tool_links[1]["attributes"]["to"] == "input"

        # assert tool -> llm span link
        assert second_llm_links[0]["span_id"] == str(tool_span.span_id)
        assert second_llm_links[0]["attributes"]["from"] == "output"
        assert second_llm_links[0]["attributes"]["to"] == "input"
