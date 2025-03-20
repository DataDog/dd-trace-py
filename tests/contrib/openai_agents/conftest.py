import os

from agents import Agent
from agents import Runner
from agents import Tool
import mock
from agents import function_tool
import pytest
import vcr

from ddtrace.contrib.internal.openai_agents.patch import patch
from ddtrace.contrib.internal.openai_agents.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import AGENTLESS_BASE_URL
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def request_vcr():
    yield vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@function_tool
def calculate_average(numbers: list[int]) -> float:
    """Calculate the average of a list of numbers"""
    return sum(numbers) / len(numbers)


@function_tool
def concatenate_strings(strings: list[str]) -> str:
    """Concatenate a list of strings with spaces in between"""
    return " ".join(strings)


@function_tool
def search_web(query: str) -> str:
    """Simulated web search function"""
    return f"Simulated web search results for: {query}"


@pytest.fixture
def simple_agent():
    """A simple agent with no tools or handoffs"""
    yield Agent(
        name="Assistant",
        instructions="You are a helpful assistant who answers questions concisely and accurately.",
    )


@pytest.fixture
def calculator_agent():
    """An agent with calculation tools"""
    yield Agent(
        name="Calculator",
        instructions="You are a helpful assistant specialized in mathematical calculations.",
        tools=[calculate_average],
    )


@pytest.fixture
def research_assistant_agent():
    """An agent specialized in research with tools"""
    yield Agent(
        name="Researcher",
        instructions="You are a research assistant who can search the web and summarize information.",
        tools=[search_web],
    )


@pytest.fixture
def summarizer_agent():
    """An agent specialized in summarizing information"""
    yield Agent(
        name="Summarizer",
        instructions="You are an assistant that specializes in summarizing complex information into concise summaries.",
    )


@pytest.fixture
def handoffs_agent(summarizer_agent):
    """An agent that can hand off to other agents"""
    yield Agent(
        name="Query Router",
        instructions="You route queries to the appropriate specialized agent. If a query requires summarization, hand off to the Summarizer agent.",
        handoffs=[summarizer_agent],
    )


@pytest.fixture
def agents_integration():
    """The OpenAI Agents integration with patching and cleanup"""
    patch()
    import agents

    yield agents
    unpatch()


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def enqueue(self, event):
        self.events.append(event)


@pytest.fixture
def mock_tracer(agents_integration):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(agents_integration)
    pin._override(agents_integration, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def mock_llmobs_span_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


@pytest.fixture
def agents_llmobs(mock_tracer, mock_llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.agents"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = mock_llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(agents_llmobs, mock_llmobs_span_writer):
    return mock_llmobs_span_writer.events
