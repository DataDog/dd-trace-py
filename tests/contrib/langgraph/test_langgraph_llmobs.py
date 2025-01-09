import json

import pytest


def _assert_span_link(from_span_event, to_span_event, from_io, to_io):
    """
    Assert that a span link exists between two span events, specifically the correct span ID and from/to specification.
    """
    found = False
    expected_to_span_id = "undefined" if not to_span_event else to_span_event["span_id"]
    for span_link in from_span_event["span_links"]:
        if span_link["span_id"] == expected_to_span_id:
            assert span_link["attributes"] == {"from": from_io, "to": to_io}
            found = True
            break
    assert found


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [{"_llmobs_enabled": True, "_api_key": "<not-a-real-key>", "_llmobs_ml_app": "unnamed-ml-app"}],
)
class TestLangGraphLLMObs:
    def test_simple_graph(self, ddtrace_global_config, langgraph, simple_graph, mock_llmobs_writer):
        simple_graph.invoke({"a_list": [], "which": "a"}, stream_mode=["values"])
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[2][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[1][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, b_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")

        # Check that the graph span has the appropriate output
        # stream_mode=["values"] should result in the last yield being a tuple
        assert graph_span["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    async def test_simple_graph_async(self, ddtrace_global_config, langgraph, simple_graph, mock_llmobs_writer):
        await simple_graph.ainvoke({"a_list": [], "which": "a"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[2][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[1][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, b_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")

        # Check that the graph span has the appropriate output
        # default stream_mode of "values" should result in the last yield being an object
        assert graph_span["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    def test_conditional_graph(self, ddtrace_global_config, langgraph, conditional_graph, mock_llmobs_writer):
        conditional_graph.invoke({"a_list": [], "which": "c"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[2][0][0]
        a_span = span_events[0][0][0]
        c_span = span_events[1][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, c_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert c_span["name"] == "c"
        _assert_span_link(c_span, a_span, "output", "input")

    async def test_conditional_graph_async(
        self, ddtrace_global_config, langgraph, conditional_graph, mock_llmobs_writer
    ):
        await conditional_graph.ainvoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[2][0][0]
        a_span = span_events[0][0][0]
        c_span = span_events[1][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, c_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert c_span["name"] == "b"
        _assert_span_link(c_span, a_span, "output", "input")

    def test_subgraph(self, ddtrace_global_config, langgraph, complex_graph, mock_llmobs_writer):
        complex_graph.invoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[5][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[4][0][0]
        b1_span = span_events[1][0][0]
        b2_span = span_events[2][0][0]
        b3_span = span_events[3][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, b_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")
        _assert_span_link(b_span, b3_span, "output", "output")
        assert b1_span["name"] == "b1"
        _assert_span_link(b1_span, b_span, "input", "input")
        assert b2_span["name"] == "b2"
        _assert_span_link(b2_span, b1_span, "output", "input")
        assert b3_span["name"] == "b3"
        _assert_span_link(b3_span, b2_span, "output", "input")

    async def test_subgraph_async(self, ddtrace_global_config, langgraph, complex_graph, mock_llmobs_writer):
        await complex_graph.ainvoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[5][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[4][0][0]
        b1_span = span_events[1][0][0]
        b2_span = span_events[2][0][0]
        b3_span = span_events[3][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, b_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")
        _assert_span_link(b_span, b3_span, "output", "output")
        assert b1_span["name"] == "b1"
        _assert_span_link(b1_span, b_span, "input", "input")
        assert b2_span["name"] == "b2"
        _assert_span_link(b2_span, b1_span, "output", "input")
        assert b3_span["name"] == "b3"
        _assert_span_link(b3_span, b2_span, "output", "input")

    def test_fanning_graph(self, ddtrace_global_config, langgraph, fanning_graph, mock_llmobs_writer):
        fanning_graph.invoke({"a_list": [], "which": ""})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[4][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[1][0][0]
        c_span = span_events[2][0][0]
        d_span = span_events[3][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, d_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")
        assert c_span["name"] == "c"
        _assert_span_link(c_span, a_span, "output", "input")
        assert d_span["name"] == "d"
        _assert_span_link(d_span, b_span, "output", "input")
        _assert_span_link(d_span, c_span, "output", "input")

    async def test_fanning_graph_async(self, ddtrace_global_config, langgraph, fanning_graph, mock_llmobs_writer):
        await fanning_graph.ainvoke({"a_list": [], "which": ""})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        graph_span = span_events[4][0][0]
        a_span = span_events[0][0][0]
        b_span = span_events[1][0][0]
        c_span = span_events[2][0][0]
        d_span = span_events[3][0][0]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, d_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert b_span["name"] == "b"
        _assert_span_link(b_span, a_span, "output", "input")
        assert c_span["name"] == "c"
        _assert_span_link(c_span, a_span, "output", "input")
        assert d_span["name"] == "d"
        _assert_span_link(d_span, b_span, "output", "input")
        _assert_span_link(d_span, c_span, "output", "input")
