import pytest

from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client


@pytest.fixture
def ai_guard_client(tracer) -> AIGuardClient:
    """Create an AI Guard client for testing."""
    return new_ai_guard_client(
        endpoint="https://api.example.com/ai-guard",
        api_key="test-api-key",
        app_key="test-application-key",
        tracer=tracer,
    )
