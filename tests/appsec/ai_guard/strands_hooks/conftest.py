import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from tests.appsec.ai_guard.utils import override_ai_guard_config


_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-application-key",
)


def pytest_configure():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        init_ai_guard()


@pytest.fixture
def ai_guard_strands_hook():
    with override_ai_guard_config(_AI_GUARD_CONFIG):
        from ddtrace.appsec.ai_guard import AIGuardStrandsHookProvider

        yield AIGuardStrandsHookProvider()
