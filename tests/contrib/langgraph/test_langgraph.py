from collections import Counter


def assert_has_spans(spans, expected):
    resources = [span.resource for span in spans]
    assert len(resources) == len(expected)
    assert Counter(resources) == Counter(expected)


def assert_simple_graph_spans(spans):
    assert_has_spans(
        spans,
        expected=[
            "langgraph.graph.state.CompiledStateGraph.LangGraph",
            "langgraph.utils.runnable.RunnableSeq.a",
            "langgraph.utils.runnable.RunnableSeq.b",
        ],
    )


def assert_conditional_graph_spans(spans, which):
    assert_has_spans(
        spans,
        expected=[
            "langgraph.graph.state.CompiledStateGraph.LangGraph",
            "langgraph.utils.runnable.RunnableSeq.a",
            f"langgraph.utils.runnable.RunnableSeq.{which}",
        ],
    )


def assert_subgraph_spans(spans):
    assert_has_spans(
        spans,
        expected=[
            "langgraph.graph.state.CompiledStateGraph.LangGraph",
            "langgraph.utils.runnable.RunnableSeq.a",
            "langgraph.graph.state.CompiledStateGraph.LangGraph",
            "langgraph.utils.runnable.RunnableSeq.b1",
            "langgraph.utils.runnable.RunnableSeq.b2",
            "langgraph.utils.runnable.RunnableSeq.b3",
        ],
    )


def assert_fanning_graph_spans(spans):
    assert_has_spans(
        spans,
        expected=[
            "langgraph.graph.state.CompiledStateGraph.LangGraph",
            "langgraph.utils.runnable.RunnableSeq.a",
            "langgraph.utils.runnable.RunnableSeq.b",
            "langgraph.utils.runnable.RunnableSeq.c",
            "langgraph.utils.runnable.RunnableSeq.d",
        ],
    )


def test_simple_graph(simple_graph, mock_tracer):
    simple_graph.invoke({"a_list": [], "which": "a"})
    spans = mock_tracer.pop_traces()[0]
    assert_simple_graph_spans(spans)


async def test_simple_graph_async(simple_graph, mock_tracer):
    await simple_graph.ainvoke({"a_list": [], "which": "a"})
    spans = mock_tracer.pop_traces()[0]
    assert_simple_graph_spans(spans)


def test_simple_graph_stream(simple_graph, mock_tracer):
    for _ in simple_graph.stream({"a_list": [], "which": "a"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_simple_graph_spans(spans)


async def test_simple_graph_stream_async(simple_graph, mock_tracer):
    async for _ in simple_graph.astream({"a_list": [], "which": "a"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_simple_graph_spans(spans)


def test_conditional_graph(conditional_graph, mock_tracer):
    conditional_graph.invoke({"a_list": [], "which": "c"})
    spans = mock_tracer.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="c")


async def test_conditional_graph_async(conditional_graph, mock_tracer):
    await conditional_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="b")


def test_conditional_graph_stream(conditional_graph, mock_tracer):
    for _ in conditional_graph.stream({"a_list": [], "which": "c"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="c")


async def test_conditional_graph_stream_async(conditional_graph, mock_tracer):
    async for _ in conditional_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="b")


def test_subgraph(complex_graph, mock_tracer):
    complex_graph.invoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert_subgraph_spans(spans)


async def test_subgraph_async(complex_graph, mock_tracer):
    await complex_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert_subgraph_spans(spans)


def test_subgraph_stream(complex_graph, mock_tracer):
    for _ in complex_graph.stream({"a_list": [], "which": "b"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_subgraph_spans(spans)


async def test_subgraph_stream_async(complex_graph, mock_tracer):
    async for _ in complex_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_subgraph_spans(spans)


def test_fanning_graph(fanning_graph, mock_tracer):
    fanning_graph.invoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_fanning_graph_async(fanning_graph, mock_tracer):
    await fanning_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert_fanning_graph_spans(spans)


def test_fanning_graph_stream(fanning_graph, mock_tracer):
    for _ in fanning_graph.stream({"a_list": [], "which": "b"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_fanning_graph_stream_async(fanning_graph, mock_tracer):
    async for _ in fanning_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_astream_events(simple_graph, mock_tracer):
    async for _ in simple_graph.astream_events({"a_list": [], "which": "a"}, version="v2"):
        pass
    spans = mock_tracer.pop_traces()[0]
    assert_simple_graph_spans(spans)
