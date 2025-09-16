import contextlib
import os

import pytest

import ddtrace
from ddtrace.appsec._ai_guard import init_ai_guard
from ddtrace.contrib.internal.langchain.patch import patch
from ddtrace.contrib.internal.langchain.patch import unpatch
from ddtrace.settings.asm import ai_guard_config
from tests.utils import override_env


@contextlib.contextmanager
def override_ai_guard_config(values):
    """
    Temporarily override an ai_guard configuration:

        >>> with self.override_ai_guard_config(dict(name=value,...)):
            # Your test
    """
    # List of global variables we allow overriding
    global_config_keys = [
        "_dd_api_key",
    ]

    ai_guard_config_keys = ai_guard_config._ai_guard_config_keys

    # Grab the current values of all keys
    originals = dict((key, getattr(ddtrace.config, key)) for key in global_config_keys)
    ai_guard_originals = dict((key, getattr(ai_guard_config, key)) for key in ai_guard_config_keys)

    # Override from the passed in keys
    for key, value in values.items():
        if key in global_config_keys:
            setattr(ddtrace.config, key, value)
    # rebuild ai guard config from env vars and global config
    for key, value in values.items():
        if key in ai_guard_config_keys:
            setattr(ai_guard_config, key, value)

    try:
        yield
    finally:
        # Reset all to their original values
        for key, value in originals.items():
            setattr(ddtrace.config, key, value)

        ai_guard_config.reset()
        for key, value in ai_guard_originals.items():
            setattr(ai_guard_config, key, value)

        ddtrace.config._reset()


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
