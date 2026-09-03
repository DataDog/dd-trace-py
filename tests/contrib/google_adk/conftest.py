import os
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import google.adk
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.session import Session
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
import pytest

from ddtrace.contrib.internal.google_adk.patch import patch as adk_patch
from ddtrace.contrib.internal.google_adk.patch import unpatch as adk_unpatch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from tests.contrib.google_adk.utils import get_request_vcr
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def google_adk_llmobs(tracer, monkeypatch):
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
            "service": "tests.contrib.google_adk",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


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


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
async def test_runner(adk, test_spans):
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
        multiply,
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


ADK_VERSION = parse_version(google.adk.__version__)

# google-adk >= 2.7.0 removed `__call_tool_live` and routes streaming tools through
# `__call_tool_async`, which then returns an async generator.
streaming_via_call_tool_async = pytest.mark.skipif(
    ADK_VERSION < (2, 7, 0),
    reason="streaming tools are dispatched through __call_tool_live before google-adk 2.7.0",
)


def call_tool_async(module):
    """Return the wrapped tool dispatch function.

    Looked up with getattr so the name is not mangled when referenced from a class body.
    """
    return getattr(module.flows.llm_flows.functions, "__call_tool_async")


async def stream_values(count: int):
    """A streaming tool, which google-adk 2.7.0+ dispatches through `__call_tool_async`."""
    for i in range(count):
        yield {"value": i}


async def stream_then_raise(count: int):
    """A streaming tool that fails partway through the stream."""
    for i in range(count):
        yield {"value": i}
    raise RuntimeError("stream blew up")


@pytest.fixture
def streaming_tool_context(adk):
    """A ToolContext backed by a real Session, whose `state` ToolContext reads on init.

    The agent is built here rather than reused from `test_runner` so this does not depend on
    `test_spans`, which requires a DummyWriter and so is unusable once LLMObs is enabled.
    """
    invocation_context = InvocationContext(
        invocation_id="test_invocation",
        agent=LlmAgent(name="test_agent", model=Gemini(model="gemini-2.5-pro")),
        session=Session(id="test-session", app_name="TestADKApp", user_id="test-user", state={}, events=[]),
        session_service=MagicMock(spec=BaseSessionService),
    )
    return ToolContext(invocation_context=invocation_context)
