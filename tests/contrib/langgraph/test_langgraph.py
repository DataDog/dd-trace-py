from collections import Counter

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
import pytest

from .conftest import State


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


def test_simple_graph(simple_graph, test_spans):
    simple_graph.invoke({"a_list": [], "which": "a"})
    spans = test_spans.pop_traces()[0]
    assert_simple_graph_spans(spans)


async def test_simple_graph_async(simple_graph, test_spans):
    await simple_graph.ainvoke({"a_list": [], "which": "a"})
    spans = test_spans.pop_traces()[0]
    assert_simple_graph_spans(spans)


def test_simple_graph_stream(simple_graph, test_spans):
    for _ in simple_graph.stream({"a_list": [], "which": "a"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_simple_graph_spans(spans)


async def test_simple_graph_stream_async(simple_graph, test_spans):
    async for _ in simple_graph.astream({"a_list": [], "which": "a"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_simple_graph_spans(spans)


def test_conditional_graph(conditional_graph, test_spans):
    conditional_graph.invoke({"a_list": [], "which": "c"})
    spans = test_spans.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="c")


async def test_conditional_graph_async(conditional_graph, test_spans):
    await conditional_graph.ainvoke({"a_list": [], "which": "b"})
    spans = test_spans.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="b")


def test_conditional_graph_stream(conditional_graph, test_spans):
    for _ in conditional_graph.stream({"a_list": [], "which": "c"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="c")


async def test_conditional_graph_stream_async(conditional_graph, test_spans):
    async for _ in conditional_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_conditional_graph_spans(spans, which="b")


def test_subgraph(complex_graph, test_spans):
    complex_graph.invoke({"a_list": [], "which": "b"})
    spans = test_spans.pop_traces()[0]
    assert_subgraph_spans(spans)


async def test_subgraph_async(complex_graph, test_spans):
    await complex_graph.ainvoke({"a_list": [], "which": "b"})
    spans = test_spans.pop_traces()[0]
    assert_subgraph_spans(spans)


def test_subgraph_stream(complex_graph, test_spans):
    for _ in complex_graph.stream({"a_list": [], "which": "b"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_subgraph_spans(spans)


async def test_subgraph_stream_async(complex_graph, test_spans):
    async for _ in complex_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_subgraph_spans(spans)


def test_fanning_graph(fanning_graph, test_spans):
    fanning_graph.invoke({"a_list": [], "which": "b"})
    spans = test_spans.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_fanning_graph_async(fanning_graph, test_spans):
    await fanning_graph.ainvoke({"a_list": [], "which": "b"})
    spans = test_spans.pop_traces()[0]
    assert_fanning_graph_spans(spans)


def test_fanning_graph_stream(fanning_graph, test_spans):
    for _ in fanning_graph.stream({"a_list": [], "which": "b"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_fanning_graph_stream_async(fanning_graph, test_spans):
    async for _ in fanning_graph.astream({"a_list": [], "which": "b"}):
        pass
    spans = test_spans.pop_traces()[0]
    assert_fanning_graph_spans(spans)


async def test_astream_events(simple_graph, test_spans):
    async for _ in simple_graph.astream_events({"a_list": [], "which": "a"}, version="v2"):
        pass
    spans = test_spans.pop_traces()[0]
    assert_simple_graph_spans(spans)


# Exception handling tests
def test_graphinterrupt_invoke_not_marked_as_error(langgraph, test_spans):
    """GraphInterrupt raised during invoke should not mark span as error.

    Note: GraphInterrupt is caught by LangGraph internally, so it doesn't propagate
    to the user. We test that when it's raised, spans are NOT marked with errors.
    """
    from langgraph.errors import GraphInterrupt

    interrupt_raised = False

    def node_with_interrupt(state):
        nonlocal interrupt_raised
        # Raise GraphInterrupt - framework will catch it
        interrupt_raised = True
        raise GraphInterrupt([{"value": "Need user input"}])

    graph_builder = StateGraph(State)
    graph_builder.add_node("interrupt_node", node_with_interrupt)
    graph_builder.add_edge(START, "interrupt_node")
    graph_builder.add_edge("interrupt_node", END)
    graph = graph_builder.compile(checkpointer=MemorySaver())

    # Graph handles GraphInterrupt internally - doesn't raise to user
    try:
        result = graph.invoke({"a_list": [], "which": "a"}, config={"configurable": {"thread_id": "test"}})
        # If GraphInterrupt was caught by framework, it returns None
        assert result is None or interrupt_raised
    except GraphInterrupt:
        # Some versions might still raise it
        pass

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0
    assert interrupt_raised, "GraphInterrupt should have been raised in node"

    # Verify NO spans have error tags - this is the key test
    for span in spans:
        assert span.get_tag("error.type") is None
        assert span.error == 0


async def test_graphinterrupt_ainvoke_not_marked_as_error(langgraph, test_spans):
    """GraphInterrupt raised during ainvoke should not mark span as error."""
    from langgraph.errors import GraphInterrupt

    interrupt_raised = False

    def node_with_interrupt(state):
        nonlocal interrupt_raised
        interrupt_raised = True
        raise GraphInterrupt([{"value": "Need user input"}])

    graph_builder = StateGraph(State)
    graph_builder.add_node("interrupt_node", node_with_interrupt)
    graph_builder.add_edge(START, "interrupt_node")
    graph_builder.add_edge("interrupt_node", END)
    graph = graph_builder.compile(checkpointer=MemorySaver())

    try:
        result = await graph.ainvoke({"a_list": [], "which": "a"}, config={"configurable": {"thread_id": "test"}})
        assert result is None or interrupt_raised
    except GraphInterrupt:
        pass

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0
    assert interrupt_raised

    # Verify NO spans have error tags
    for span in spans:
        assert span.get_tag("error.type") is None
        assert span.error == 0


def test_graphinterrupt_stream_not_marked_as_error(langgraph, test_spans):
    """GraphInterrupt raised during stream should not mark span as error."""
    from langgraph.errors import GraphInterrupt

    interrupt_raised = False

    def node_with_interrupt(state):
        nonlocal interrupt_raised
        interrupt_raised = True
        raise GraphInterrupt([{"value": "Need user input"}])

    graph_builder = StateGraph(State)
    graph_builder.add_node("interrupt_node", node_with_interrupt)
    graph_builder.add_edge(START, "interrupt_node")
    graph_builder.add_edge("interrupt_node", END)
    graph = graph_builder.compile(checkpointer=MemorySaver())

    try:
        for _ in graph.stream({"a_list": [], "which": "a"}, config={"configurable": {"thread_id": "test"}}):
            pass
    except GraphInterrupt:
        pass

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0
    assert interrupt_raised

    # Verify NO spans have error tags
    for span in spans:
        assert span.get_tag("error.type") is None
        assert span.error == 0


async def test_graphinterrupt_astream_not_marked_as_error(langgraph, test_spans):
    """GraphInterrupt raised during astream should not mark span as error."""
    from langgraph.errors import GraphInterrupt

    interrupt_raised = False

    def node_with_interrupt(state):
        nonlocal interrupt_raised
        interrupt_raised = True
        raise GraphInterrupt([{"value": "Need user input"}])

    graph_builder = StateGraph(State)
    graph_builder.add_node("interrupt_node", node_with_interrupt)
    graph_builder.add_edge(START, "interrupt_node")
    graph_builder.add_edge("interrupt_node", END)
    graph = graph_builder.compile(checkpointer=MemorySaver())

    try:
        async for _ in graph.astream({"a_list": [], "which": "a"}, config={"configurable": {"thread_id": "test"}}):
            pass
    except GraphInterrupt:
        pass

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0
    assert interrupt_raised

    # Verify NO spans have error tags
    for span in spans:
        assert span.get_tag("error.type") is None
        assert span.error == 0


def test_parentcommand_still_not_marked_as_error(langgraph, test_spans):
    """ParentCommand should still not be marked as error (regression test).

    Note: ParentCommand was added in LangGraph 0.3.x, so this test is skipped
    on older versions.
    """
    try:
        from langgraph.errors import ParentCommand
        from langgraph.types import Command
    except ImportError:
        pytest.skip("ParentCommand not available in this LangGraph version")

    def node_with_parent_command(state):
        raise ParentCommand(Command(goto="END"))

    graph_builder = StateGraph(State)
    graph_builder.add_node("parent_command_node", node_with_parent_command)
    graph_builder.add_edge(START, "parent_command_node")
    graph_builder.add_edge("parent_command_node", END)
    graph = graph_builder.compile()

    with pytest.raises(ParentCommand):
        graph.invoke({"a_list": [], "which": "a"})

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0

    # Verify NO spans have error tags
    for span in spans:
        assert span.get_tag("error.type") is None
        assert span.error == 0


def test_base_exception_in_node_still_emits_root_span(langgraph, test_spans):
    """DDBlockException (parent of AIGuardAbortError) is a BaseException, not an Exception.
    The pregel stream generators previously used ``except Exception`` which silently skipped
    ``span.finish()`` on a block, so the entire trace was dropped. This test pins that fix.
    """
    from ddtrace.internal._exceptions import DDBlockException

    def blocking_node(state):
        raise DDBlockException("block")

    graph_builder = StateGraph(State)
    graph_builder.add_node("a", blocking_node)
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", END)
    graph = graph_builder.compile()

    with pytest.raises(DDBlockException):
        graph.invoke({"a_list": [], "which": "a"})

    traces = test_spans.pop_traces()
    assert len(traces) == 1, "trace must be emitted even when DDBlockException propagates"
    resources = [s.resource for s in traces[0]]
    assert any("LangGraph" in r for r in resources), f"root graph span not found in: {resources}"


async def test_base_exception_in_node_still_emits_root_span_async(langgraph, test_spans):
    """Async variant: ainvoke path uses the astream generator which had the same bug."""
    from ddtrace.internal._exceptions import DDBlockException

    async def blocking_node_async(state):
        raise DDBlockException("block")

    graph_builder = StateGraph(State)
    graph_builder.add_node("a", blocking_node_async)
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", END)
    graph = graph_builder.compile()

    with pytest.raises(DDBlockException):
        await graph.ainvoke({"a_list": [], "which": "a"})

    traces = test_spans.pop_traces()
    assert len(traces) == 1, "trace must be emitted even when DDBlockException propagates (async)"
    resources = [s.resource for s in traces[0]]
    assert any("LangGraph" in r for r in resources), f"root graph span not found in: {resources}"


def test_stream_teardown_not_marked_as_error(simple_graph, test_spans):
    """Closing a stream early is normal teardown, not a span error.

    The pregel stream generators wrap ``yield`` in a try/except. Catching bare
    ``BaseException`` would treat the ``GeneratorExit`` raised by ``close()`` (or by
    a ``break`` followed by GC) as a runtime error. The catch is narrowed to
    ``(DDBlockException, Exception)`` so normal teardown is left alone.
    """
    stream = simple_graph.stream({"a_list": [], "which": "a"})
    next(stream)  # suspend the generator at a ``yield``
    stream.close()  # raises GeneratorExit at the suspended ``yield``

    for trace in test_spans.pop_traces():
        for span in trace:
            assert span.error == 0, f"span {span.resource} wrongly marked as error on stream teardown"


async def test_astream_teardown_not_marked_as_error(simple_graph, test_spans):
    """Async variant: ``aclose()`` raises GeneratorExit in the astream generator."""
    stream = simple_graph.astream({"a_list": [], "which": "a"})
    await stream.__anext__()  # suspend the generator at a ``yield``
    await stream.aclose()  # raises GeneratorExit at the suspended ``yield``

    for trace in test_spans.pop_traces():
        for span in trace:
            assert span.error == 0, f"span {span.resource} wrongly marked as error on astream teardown"


def test_regular_exception_still_marked_as_error(langgraph, test_spans):
    """Regular exceptions should still be marked as errors (regression test)."""

    def node_with_error(state):
        raise ValueError("This is a real error")

    graph_builder = StateGraph(State)
    graph_builder.add_node("error_node", node_with_error)
    graph_builder.add_edge(START, "error_node")
    graph_builder.add_edge("error_node", END)
    graph = graph_builder.compile()

    with pytest.raises(ValueError):
        graph.invoke({"a_list": [], "which": "a"})

    spans = test_spans.pop_traces()[0]
    assert len(spans) > 0

    # Verify at least one span has error tags
    error_spans = [span for span in spans if span.get_tag("error.type") is not None]
    assert len(error_spans) > 0

    # The span that had the error should have error.type set
    error_span = error_spans[0]
    assert error_span.get_tag("error.type") == "builtins.ValueError"
    assert error_span.error == 1
