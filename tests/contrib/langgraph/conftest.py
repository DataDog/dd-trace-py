import operator
from typing import Annotated
from typing import TypedDict

from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
import pytest

from ddtrace.contrib.internal.langgraph.patch import patch
from ddtrace.contrib.internal.langgraph.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import AGENTLESS_SPAN_BASE_URL
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import override_global_config
from tests.llmobs._utils import TestLLMObsSpanWriter


DATADOG_SITE = "datad0g.com"


@pytest.fixture
def mock_tracer():
    yield DummyTracer()


@pytest.fixture
def langgraph(monkeypatch, mock_tracer):
    monkeypatch.setenv("_DD_TRACE_LANGGRAPH_ENABLED", "true")
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
    agentless_url = "{}.{}".format(AGENTLESS_SPAN_BASE_URL, DATADOG_SITE)
    yield TestLLMObsSpanWriter(is_agentless=True, _agentless_url=agentless_url, interval=1.0, timeout=1.0)


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
