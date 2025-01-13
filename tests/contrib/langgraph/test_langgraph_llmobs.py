import json


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


class TestLangGraphLLMObs:
    def test_simple_graph(self, llmobs_events, simple_graph):
        simple_graph.invoke({"a_list": [], "which": "a"}, stream_mode=["values"])
        graph_span = llmobs_events[2]
        a_span = llmobs_events[0]
        b_span = llmobs_events[1]

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

    async def test_simple_graph_async(self, llmobs_events, simple_graph):
        await simple_graph.ainvoke({"a_list": [], "which": "a"})
        graph_span = llmobs_events[2]
        a_span = llmobs_events[0]
        b_span = llmobs_events[1]

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

    def test_conditional_graph(self, llmobs_events, conditional_graph):
        conditional_graph.invoke({"a_list": [], "which": "c"})
        graph_span = llmobs_events[2]
        a_span = llmobs_events[0]
        c_span = llmobs_events[1]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, c_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert c_span["name"] == "c"
        _assert_span_link(c_span, a_span, "output", "input")

    async def test_conditional_graph_async(self, llmobs_events, conditional_graph):
        await conditional_graph.ainvoke({"a_list": [], "which": "b"})
        graph_span = llmobs_events[2]
        a_span = llmobs_events[0]
        c_span = llmobs_events[1]

        assert graph_span["name"] == "LangGraph"
        _assert_span_link(graph_span, None, "input", "input")
        _assert_span_link(graph_span, c_span, "output", "output")
        assert a_span["name"] == "a"
        _assert_span_link(a_span, graph_span, "input", "input")
        assert c_span["name"] == "b"
        _assert_span_link(c_span, a_span, "output", "input")

    def test_subgraph(self, llmobs_events, complex_graph):
        complex_graph.invoke({"a_list": [], "which": "b"})
        graph_span = llmobs_events[5]
        a_span = llmobs_events[0]
        b_span = llmobs_events[4]
        b1_span = llmobs_events[1]
        b2_span = llmobs_events[2]
        b3_span = llmobs_events[3]

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

    async def test_subgraph_async(self, llmobs_events, complex_graph):
        await complex_graph.ainvoke({"a_list": [], "which": "b"})
        graph_span = llmobs_events[5]
        a_span = llmobs_events[0]
        b_span = llmobs_events[4]
        b1_span = llmobs_events[1]
        b2_span = llmobs_events[2]
        b3_span = llmobs_events[3]

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

    def test_fanning_graph(self, llmobs_events, fanning_graph):
        fanning_graph.invoke({"a_list": [], "which": ""})
        graph_span = llmobs_events[4]
        a_span = llmobs_events[0]
        b_span = llmobs_events[1]
        c_span = llmobs_events[2]
        d_span = llmobs_events[3]

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

    async def test_fanning_graph_async(self, llmobs_events, fanning_graph):
        await fanning_graph.ainvoke({"a_list": [], "which": ""})
        graph_span = llmobs_events[4]
        a_span = llmobs_events[0]
        b_span = llmobs_events[1]
        c_span = llmobs_events[2]
        d_span = llmobs_events[3]

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
