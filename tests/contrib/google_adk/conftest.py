import os
from typing import Any
from unittest.mock import MagicMock

from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.session import Session
from google.adk.tools.function_tool import FunctionTool
from google.genai import types
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.google_adk.patch import patch as adk_patch
from ddtrace.contrib.internal.google_adk.patch import unpatch as adk_unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.google_adk.utils import get_request_vcr
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def adk(ddtrace_global_config):
    # Set dummy API key for VCR mode if no real API key is present
    if not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = "dummy-api-key-for-vcr"

    # Location/project may be required for client init.
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"))

    with override_global_config(ddtrace_global_config):
        adk_patch()
        import google.adk as adk

        yield adk
        adk_unpatch()


@pytest.fixture
def mock_tracer(adk):
    pin = Pin.get_from(adk)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    if pin is not None:
        pin._override(adk, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
async def test_runner(adk, mock_tracer):
    """Set up a test runner with agent."""
    runner = await setup_test_agent()
    return runner


@pytest.fixture
def mock_invocation_context(test_runner) -> InvocationContext:
    """Provides a mock InvocationContext."""
    mock_session = MagicMock(spec=Session)
    mock_session_service = MagicMock(spec=BaseSessionService)
    return InvocationContext(
        invocation_id="test_invocation",
        agent=test_runner.agent,
        session=mock_session,
        session_service=mock_session_service,
    )


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def adk_llmobs(mock_tracer, llmobs_span_writer):
    LLMObs.disable()
    with override_global_config(
        {
            "_dd_api_key": "<not-a-real-api_key>",
            "_llmobs_ml_app": "<ml-app-name>",
            "service": "tests.contrib.google_adk",
        }
    ):
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer = llmobs_span_writer
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def llmobs_events(adk_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


def search_docs(query: str) -> dict[str, Any]:
    """A tiny search tool stub."""
    return {"results": [f"Found reference for: {query}"]}


def multiply(a: int, b: int) -> dict[str, Any]:
    """Simple arithmetic tool."""
    return {"product": a * b}


async def setup_test_agent():
    """Set up a test agent with tools and code executor."""
    model = Gemini(model="gemini-2.5-pro")

    # Wrap Python callables as tools the agent can invoke
    tools = [
        FunctionTool(func=search_docs),
        FunctionTool(func=multiply),
    ]

    # Enable code execution so the model can emit code blocks that get executed
    code_executor = UnsafeLocalCodeExecutor()

    agent = LlmAgent(
        name="test_agent",
        description="Test agent for ADK integration testing",
        model=model,
        tools=tools,  # type: ignore[arg-type]
        code_executor=code_executor,
        instruction=(
            "You are a helpful test agent. You can: (1) call tools using the provided functions, "
            "(2) execute Python code blocks when they are provided to you. "
            "When you see ```python code blocks, execute them using your code execution capability. "
            "Always be helpful and use your available capabilities."
        ),
    )

    runner = InMemoryRunner(agent=agent, app_name="TestADKApp")
    await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id="test-user",
        session_id="test-session",
    )

    return runner


def create_test_message(text: str) -> types.Content:
    """Create a test message content."""
    return types.Content(
        role="user",
        parts=[types.Part(text=text)],
    )
