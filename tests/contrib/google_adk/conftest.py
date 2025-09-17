import os
from unittest.mock import MagicMock

from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.session import Session
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.google_adk.patch import patch as adk_patch
from ddtrace.contrib.internal.google_adk.patch import unpatch as adk_unpatch
from tests.contrib.google_adk.app import setup_test_agent
from tests.contrib.google_adk.utils import get_request_vcr
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def adk_ddtrace_global_config():
    return {}


@pytest.fixture
def adk(adk_ddtrace_global_config):
    # Set dummy API key for VCR mode if no real API key is present
    if not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = "dummy-api-key-for-vcr"

    # Location/project may be required for client init.
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"))

    with override_global_config(adk_ddtrace_global_config):
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


class DummyTool:
    def __init__(self, name: str = "dummy_tool", description: str = "desc", mandatory_args=None):
        self.name = name
        self.description = description
        self._mandatory_args = list(mandatory_args or [])

    def _get_mandatory_args(self):
        return self._mandatory_args


class DummyAgent:
    def __init__(self):
        self.name = "test-agent"
        self.instruction = "do things"
        self.model_config = {"model": "gemini-2.0"}
        self.tools = [DummyTool(name="get_current_weather", description="weather", mandatory_args=["location"])]


class DummyRunner:
    def __init__(self):
        self.agent = DummyAgent()
        self.app_name = "TestApp"
        self.session_service = type(
            "SessSvc",
            (),
            {
                "get_session": staticmethod(
                    lambda app_name, user_id, session_id: _async_return(
                        type(
                            "Sess",
                            (),
                            {
                                "app_name": app_name,
                                "user_id": user_id,
                                "id": session_id,
                                "events": [],
                            },
                        )()
                    )
                )
            },
        )()


async def _async_return(value):
    return value


class DummyCodeInput:
    def __init__(self, code: str):
        self.code = code


class DummyCodeResult:
    def __init__(self, stdout: str = "", stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def dummy_runner():
    return DummyRunner()


@pytest.fixture
def adk_runner(adk):
    class FakeAgent:
        name = "fake-agent"
        instruction = "instr"
        model_config = {"model": "test"}
        tools = []

    return adk.runners.InMemoryRunner(agent=FakeAgent(), app_name="TestApp")


def mk_session(app_name: str = "app", user_id: str = "user", session_id: str = "sid"):
    return type("Sess", (), {"app_name": app_name, "user_id": user_id, "id": session_id, "events": []})()


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
def dummy_tool():
    return DummyTool()


@pytest.fixture
def dummy_code_input():
    return DummyCodeInput("print('hello')")


@pytest.fixture
def dummy_code_result():
    return DummyCodeResult(stdout="ok", stderr="")


@pytest.fixture
async def test_runner(adk, mock_tracer):
    """Set up a test runner with agent."""
    runner = await setup_test_agent()
    # Ensure the mock tracer is attached to the runner
    Pin()._override(runner, tracer=mock_tracer)
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
