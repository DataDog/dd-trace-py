import pytest

from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._api_client import _agent_route
from ddtrace.aiguard._initialization import load_ai_guard
from tests.aiguard.utils import override_ai_guard_config


_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-application-key",
)


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    # Register the AI Guard listeners (incl. the set_http_meta_for_asm handler that
    # stashes the candidate client IP) before any test runs.
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        load_ai_guard()


@pytest.fixture(autouse=True)
def reset_agent_route():
    """Agent discovery is process-wide state; no test may inherit another's route."""
    _agent_route.reset()
    yield
    _agent_route.reset()


@pytest.fixture
def ai_guard_client(tracer) -> AIGuardClient:
    """Create an AI Guard client for testing."""
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        return new_ai_guard_client()


@pytest.fixture
def agent_transport_client(tracer) -> AIGuardClient:
    """Create a client that reaches AI Guard through the Agent proxy route."""
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        return AIGuardClient("/ai_guard/proxy/api/v2/ai-guard", base_url="http://agent:8126")
