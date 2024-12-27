def test_simple_graph(langgraph, simple_graph, mock_tracer):
    simple_graph.invoke({"a_list": [], "which": "a"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 3
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.b"


async def test_simple_graph_async(langgraph, simple_graph, mock_tracer):
    await simple_graph.ainvoke({"a_list": [], "which": "a"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 3
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.b"


def test_conditional_graph(langgraph, conditional_graph, mock_tracer):
    conditional_graph.invoke({"a_list": [], "which": "c"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 3
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.c"


async def test_conditional_graph_async(langgraph, conditional_graph, mock_tracer):
    await conditional_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 3
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.b"


def test_subgraph(langgraph, complex_graph, mock_tracer):
    complex_graph.invoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 6
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[3].resource == "langgraph.utils.runnable.RunnableSeq.b1"
    assert spans[4].resource == "langgraph.utils.runnable.RunnableSeq.b2"
    assert spans[5].resource == "langgraph.utils.runnable.RunnableSeq.b3"


async def test_subgraph_async(langgraph, complex_graph, mock_tracer):
    await complex_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 6
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[3].resource == "langgraph.utils.runnable.RunnableSeq.b1"
    assert spans[4].resource == "langgraph.utils.runnable.RunnableSeq.b2"
    assert spans[5].resource == "langgraph.utils.runnable.RunnableSeq.b3"


def test_fanning_graph(langgraph, fanning_graph, mock_tracer):
    fanning_graph.invoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 5
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.b"
    assert spans[3].resource == "langgraph.utils.runnable.RunnableSeq.c"
    assert spans[4].resource == "langgraph.utils.runnable.RunnableSeq.d"


async def test_fanning_graph_async(langgraph, fanning_graph, mock_tracer):
    await fanning_graph.ainvoke({"a_list": [], "which": "b"})
    spans = mock_tracer.pop_traces()[0]
    assert len(spans) == 5
    assert spans[0].resource == "langgraph.graph.state.CompiledStateGraph.LangGraph"
    assert spans[1].resource == "langgraph.utils.runnable.RunnableSeq.a"
    assert spans[2].resource == "langgraph.utils.runnable.RunnableSeq.b"
    assert spans[3].resource == "langgraph.utils.runnable.RunnableSeq.c"
    assert spans[4].resource == "langgraph.utils.runnable.RunnableSeq.d"
