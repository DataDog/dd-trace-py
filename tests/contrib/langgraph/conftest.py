import operator
from typing import Annotated
from typing import TypedDict

from langgraph.constants import Send
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
import pytest

from ddtrace.contrib.internal.langgraph.patch import patch
from ddtrace.contrib.internal.langgraph.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_tracer():
    yield DummyTracer()


@pytest.fixture
def langgraph(monkeypatch, mock_tracer):
    patch()
    import langgraph

    pin = Pin.get_from(langgraph)
    pin._override(langgraph, tracer=mock_tracer)
    yield langgraph
    unpatch()


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "unnamed-ml-app", "service": "tests.llmobs"}


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def llmobs(tracer, llmobs_span_writer):
    with override_global_config(default_global_config()):
        llmobs_service.enable(_tracer=tracer)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


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


@pytest.fixture
def graph_with_send(langgraph):
    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_conditional_edges("a", lambda state: [Send("b", state) for _ in range(3)])
    graph_builder.add_edge("b", END)
    graph = graph_builder.compile()

    yield graph


@pytest.fixture
def graph_with_send_complex(langgraph):
    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_node("c", _do_op("c"))
    graph_builder.add_node("d", _do_op("d"))
    graph_builder.add_node("e", _do_op("e"))
    graph_builder.add_node("f", _do_op("f"))
    graph_builder.add_node("g", _do_op("g"))
    graph_builder.add_node("h", _do_op("h"))
    graph_builder.add_node("i", _do_op("i"))
    graph_builder.add_node("j", _do_op("j"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", "b")
    graph_builder.add_edge("a", "c")

    graph_builder.add_edge("b", "d")
    graph_builder.add_edge("b", "e")

    graph_builder.add_conditional_edges("c", lambda state: [Send("e", state)])
    graph_builder.add_edge("c", "e")
    graph_builder.add_edge("c", "f")
    graph_builder.add_edge("c", "g")

    graph_builder.add_edge("d", "h")
    graph_builder.add_edge("e", "h")

    graph_builder.add_edge("f", "i")
    graph_builder.add_edge("g", "i")

    graph_builder.add_edge("h", "j")
    graph_builder.add_edge("i", "j")

    graph_builder.add_edge("j", END)
    graph = graph_builder.compile()

    yield graph


@pytest.fixture
def graph_with_uneven_sides(langgraph):
    graph_builder = StateGraph(State)
    graph_builder.add_node("a", _do_op("a"))
    graph_builder.add_node("b", _do_op("b"))
    graph_builder.add_node("c", _do_op("c"))
    graph_builder.add_node("d", _do_op("d"))
    graph_builder.add_node("e", _do_op("e"))
    graph_builder.add_edge(START, "a")
    graph_builder.add_edge("a", "b")
    graph_builder.add_edge("a", "c")
    graph_builder.add_edge("b", "e")
    graph_builder.add_edge("c", "d")
    graph_builder.add_edge("d", "e")
    graph_builder.add_edge("e", END)
    graph = graph_builder.compile()

    yield graph
