import pytest

from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from tests.appsec.ai_guard.utils import override_ai_guard_config


@pytest.fixture
def ai_guard_client(tracer) -> AIGuardClient:
    """Create an AI Guard client for testing."""
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        return new_ai_guard_client(tracer=tracer)
