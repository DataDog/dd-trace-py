import os

from agents import Agent
from agents import GuardrailFunctionOutput
from agents import function_tool
from agents import input_guardrail
import pytest
import vcr

from ddtrace.contrib.internal.openai_agents.patch import patch
from ddtrace.contrib.internal.openai_agents.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
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
def research(query: str) -> str:
    """Research the internet on a topic.

    Args:
        query: The query to search the internet for

    Returns:
        A comprehensive research report responding to the query
    """
    return (
        "united beat liverpool 2-1 yesterday. "
        "also a lot of other stuff happened. "
        "like super important stuff. "
        "blah blah blah."
    )


@function_tool
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b


@pytest.fixture
def research_workflow():
    summarizer = Agent(
        name="Summarizer",
        instructions="""You are a helpful assistant that can summarize a research results.""",
    )
    yield Agent(
        name="Researcher",
        instructions=(
            "You are a helpful assistant that can research a topic using your research tool. "
            "Always research the topic before summarizing."
        ),
        tools=[research],
        handoffs=[summarizer],
    )


@pytest.fixture
def addition_agent():
    """An agent with addition tools"""
    yield Agent(
        name="Addition Agent",
        instructions="You are a helpful assistant specialized in addition calculations.",
        tools=[add],
    )


@function_tool(name_override="add")
def add_with_error(a: int, b: int) -> int:
    """Add two numbers together"""
    raise ValueError("This is a test error")


@pytest.fixture
def addition_agent_with_tool_errors():
    """An agent with addition tools that will error"""
    yield Agent(
        name="Addition Agent",
        instructions=(
            "You are a helpful assistant specialized in addition calculations. "
            "Do not retry the tool call if it errors and instead return immediately"
        ),
        tools=[add_with_error],
    )


@input_guardrail
async def simple_guardrail(
    context,
    agent,
    inp,
):
    return GuardrailFunctionOutput(
        output_info="dummy",
        tripwire_triggered="safe" not in inp,
    )


@pytest.fixture
def simple_agent_with_guardrail():
    """An agent with addition tools and a guardrail"""
    yield Agent(
        name="Simple Agent",
        instructions="You are a helpful assistant specialized in addition calculations.",
        input_guardrails=[simple_guardrail],
    )


@pytest.fixture
def simple_agent():
    """A simple agent with no tools or handoffs"""
    yield Agent(
        name="Simple Agent",
        instructions="You are a helpful assistant who answers questions concisely and accurately.",
    )


@pytest.fixture
def agents(monkeypatch):
    """The OpenAI Agents integration with patching and cleanup"""
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    import agents

    """
    Remove the default trace processor to avoid errors sending to OpenAI backend
    `patch` will add the LLMObs trace processor.
    """
    from agents.tracing import set_trace_processors

    set_trace_processors([])
    patch()
    yield agents
    unpatch()


@pytest.fixture
def openai(agents):
    """Fixture for openai client when chat completions is used as
    The default API for agents SDK and LLM spans are produced by the openai integration.
    """
    import openai

    from ddtrace.contrib.internal.openai.patch import patch as patch_openai
    from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai

    patch_openai()
    from agents import set_default_openai_api

    set_default_openai_api("chat_completions")
    yield openai
    unpatch_openai()


@pytest.fixture
def mock_tracer(agents):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(agents)
    pin._override(agents, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def mock_tracer_chat_completions(agents, openai, mock_tracer):
    pin = Pin.get_from(agents)
    pin._override(openai, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, "datad0g.com", "<not-a-real-key>", is_agentless=True)


@pytest.fixture
def agents_llmobs(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.agents"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(agents_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events
