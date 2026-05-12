import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from tests.appsec.ai_guard.utils import override_ai_guard_config


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
        init_ai_guard()


@pytest.fixture
def ai_guard_client(tracer) -> AIGuardClient:
    """Create an AI Guard client for testing."""
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        return new_ai_guard_client()
