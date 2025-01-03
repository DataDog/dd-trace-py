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
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")

        # Check that the graph span has the appropriate output
        # stream_mode=["values"] should result in the last yield being a tuple
        assert span_events[0][0][0]["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    async def test_simple_graph_async(self, ddtrace_global_config, langgraph, simple_graph, mock_llmobs_writer):
        await simple_graph.ainvoke({"a_list": [], "which": "a"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")

        # Check that the graph span has the appropriate output
        # default stream_mode of "values" should result in the last yield being an object
        assert span_events[0][0][0]["meta"]["output"]["value"] == json.dumps({"a_list": ["a", "b"], "which": "a"})

    def test_conditional_graph(self, ddtrace_global_config, langgraph, conditional_graph, mock_llmobs_writer):
        conditional_graph.invoke({"a_list": [], "which": "c"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "c"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")

    async def test_conditional_graph_async(
        self, ddtrace_global_config, langgraph, conditional_graph, mock_llmobs_writer
    ):
        await conditional_graph.ainvoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")

    def test_subgraph(self, ddtrace_global_config, langgraph, complex_graph, mock_llmobs_writer):
        complex_graph.invoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")
        _assert_span_link(span_events[2][0][0], span_events[5][0][0], "output", "output")
        assert span_events[3][0][0]["name"] == "b1"
        _assert_span_link(span_events[3][0][0], span_events[2][0][0], "input", "input")
        assert span_events[4][0][0]["name"] == "b2"
        _assert_span_link(span_events[4][0][0], span_events[3][0][0], "output", "input")
        assert span_events[5][0][0]["name"] == "b3"
        _assert_span_link(span_events[5][0][0], span_events[4][0][0], "output", "input")

    async def test_subgraph_async(self, ddtrace_global_config, langgraph, complex_graph, mock_llmobs_writer):
        await complex_graph.ainvoke({"a_list": [], "which": "b"})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[2][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")
        _assert_span_link(span_events[2][0][0], span_events[5][0][0], "output", "output")
        assert span_events[3][0][0]["name"] == "b1"
        _assert_span_link(span_events[3][0][0], span_events[2][0][0], "input", "input")
        assert span_events[4][0][0]["name"] == "b2"
        _assert_span_link(span_events[4][0][0], span_events[3][0][0], "output", "input")
        assert span_events[5][0][0]["name"] == "b3"
        _assert_span_link(span_events[5][0][0], span_events[4][0][0], "output", "input")

    def test_fanning_graph(self, ddtrace_global_config, langgraph, fanning_graph, mock_llmobs_writer):
        fanning_graph.invoke({"a_list": [], "which": ""})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[4][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")
        assert span_events[3][0][0]["name"] == "c"
        _assert_span_link(span_events[3][0][0], span_events[1][0][0], "output", "input")
        assert span_events[4][0][0]["name"] == "d"
        _assert_span_link(span_events[4][0][0], span_events[2][0][0], "output", "input")
        _assert_span_link(span_events[4][0][0], span_events[3][0][0], "output", "input")

    async def test_fanning_graph_async(self, ddtrace_global_config, langgraph, fanning_graph, mock_llmobs_writer):
        await fanning_graph.ainvoke({"a_list": [], "which": ""})
        span_events = mock_llmobs_writer.enqueue.call_args_list
        assert span_events[0][0][0]["name"] == "LangGraph"
        _assert_span_link(span_events[0][0][0], None, "input", "input")
        _assert_span_link(span_events[0][0][0], span_events[4][0][0], "output", "output")
        assert span_events[1][0][0]["name"] == "a"
        _assert_span_link(span_events[1][0][0], span_events[0][0][0], "input", "input")
        assert span_events[2][0][0]["name"] == "b"
        _assert_span_link(span_events[2][0][0], span_events[1][0][0], "output", "input")
        assert span_events[3][0][0]["name"] == "c"
        _assert_span_link(span_events[3][0][0], span_events[1][0][0], "output", "input")
        assert span_events[4][0][0]["name"] == "d"
        _assert_span_link(span_events[4][0][0], span_events[2][0][0], "output", "input")
        _assert_span_link(span_events[4][0][0], span_events[3][0][0], "output", "input")
