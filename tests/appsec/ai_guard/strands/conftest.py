import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from tests.appsec.ai_guard.utils import override_ai_guard_config


def pytest_configure():
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        init_ai_guard()


@pytest.fixture
def ai_guard_strands_hook():
    from ddtrace.appsec.ai_guard import new_ai_guard_client

    with override_ai_guard_config(
        dict(
            _ai_guard_enabled="True",
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        from ddtrace.appsec.ai_guard.strands import AIGuardStrandsHookProvider

        client = new_ai_guard_client()
        yield AIGuardStrandsHookProvider(client=client)
