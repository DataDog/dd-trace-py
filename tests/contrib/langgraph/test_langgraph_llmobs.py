import json


def _assert_span_link(from_span_event, to_span_event, from_io, to_io):
    """
    Assert that a span link exists between two span events, specifically the correct span ID and from/to specification.
    """
    found = False
    expected_to_span_id = "undefined" if not from_span_event else from_span_event["span_id"]
    for span_link in to_span_event["span_links"]:
        if span_link["span_id"] == expected_to_span_id:
            assert span_link["attributes"] == {"from": from_io, "to": to_io}
            found = True
            break
    assert found


def _find_span_by_name(llmobs_events, name):
    for event in llmobs_events:
        if event["name"] == name:
            return event
    assert False, f"Span '{name}' not found in llmobs span events"


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
