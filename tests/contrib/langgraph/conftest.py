import operator
from typing import Annotated
from typing import TypedDict
from unittest import mock

from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
import pytest

from ddtrace import Pin
from ddtrace.contrib.internal.langgraph.patch import patch
from ddtrace.contrib.internal.langgraph.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def mock_tracer(ddtrace_global_config, mock_llmobs_writer):
    yield DummyTracer()


@pytest.fixture
def langgraph(mock_tracer, ddtrace_global_config):
    with override_global_config(ddtrace_global_config):
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
        patch()
        import langgraph

        pin = Pin.get_from(langgraph)
        pin.override(langgraph, tracer=mock_tracer)
        yield langgraph
        unpatch()


@pytest.fixture
def mock_llmobs_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


class State(TypedDict):
    a_list: Annotated[list, operator.add]
    which: str


def _do_op(name):
    def op(state: State):
        return {"a_list": [name]}

    return op


@pytest.fixture
def simple_graph(langgraph):
    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", "b")
    graph_builder.add_edge("b", END)
    graph = graph_builder.compile()

    yield graph


@pytest.fixture
def conditional_graph(langgraph):
    def which(state):
        if state["which"] not in ("b", "c"):
            return "b"
        return state["which"]

    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_node("c", _do_op("c"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_conditional_edges("a", which)
    graph_builder.add_edge("b", END)
    graph_builder.add_edge("c", END)
    graph = graph_builder.compile()

    yield graph


@pytest.fixture
def complex_graph(langgraph):
    def which(state):
        if state["which"] not in ("b", "c"):
            return "b"
        return state["which"]

    subgraph_builder_b = StateGraph(State)
    subgraph_builder_b.add_node("b1", _do_op("b1"))
    subgraph_builder_b.add_node("b2", _do_op("b2"))
    subgraph_builder_b.add_node("b3", _do_op("b3"))
    subgraph_builder_b.add_edge(START, "b1")
    subgraph_builder_b.add_edge("b1", "b2")
    subgraph_builder_b.add_edge("b2", "b3")
    subgraph_builder_b.add_edge("b3", END)
    subgraph_b = subgraph_builder_b.compile()

    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", subgraph_b)
    graph_builder.add_node("c", _do_op("c"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_conditional_edges("a", which)
    graph_builder.add_edge("b", END)
    graph_builder.add_edge("c", END)
    graph = graph_builder.compile()

    yield graph


@pytest.fixture
def fanning_graph(langgraph):
    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_node("c", _do_op("c"))
    graph_builder.add_node("d", _do_op("d"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", "b")
    graph_builder.add_edge("a", "c")
    graph_builder.add_edge("b", "d")
    graph_builder.add_edge("c", "d")
    graph_builder.add_edge("d", END)
    graph = graph_builder.compile()

    yield graph
