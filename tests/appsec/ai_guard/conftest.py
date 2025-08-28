import os

import pytest

from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.contrib.internal.langchain.patch import patch
from ddtrace.contrib.internal.langchain.patch import unpatch
from tests.utils import override_ai_guard_config
from tests.utils import override_env


# `pytest` automatically calls this function once when tests are run.
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
def ai_guard_client(tracer) -> AIGuardClient:
    """Create an AI Guard client for testing."""
    return new_ai_guard_client(
        endpoint="https://api.example.com/ai-guard",
        api_key="test-api-key",
        app_key="test-application-key",
        tracer=tracer,
    )


@pytest.fixture
def langchain():
    with override_env(
        dict(
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
        )
    ):
        patch()
        import langchain

        yield langchain
        unpatch()


@pytest.fixture
def langchain_openai(langchain):
    try:
        import langchain_openai

        yield langchain_openai
    except ImportError:
        yield


@pytest.fixture
def openai_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture requests to OpenAI
    """
    return "http://localhost:9126/vcr/openai"
