"""Fixtures for the AI Guard openai-agents MCP tests."""

import pytest

from ddtrace.aiguard._initialization import load_ai_guard
from ddtrace.contrib.internal.openai_agents.patch import patch
from ddtrace.contrib.internal.openai_agents.patch import unpatch
from tests.aiguard.utils import override_ai_guard_config


@pytest.fixture(scope="session", autouse=True)
def _ai_guard_session_init():
    with override_ai_guard_config(
        dict(
            _ai_guard_enabled=True,
            _ai_guard_endpoint="https://api.example.com/ai-guard",
            _ai_guard_collect_mcp_enabled=True,
            _dd_api_key="test-api-key",
            _dd_app_key="test-application-key",
        )
    ):
        load_ai_guard()
        yield


@pytest.fixture(autouse=True)
def openai_agents_patched():
    patch()
    yield
    unpatch()
