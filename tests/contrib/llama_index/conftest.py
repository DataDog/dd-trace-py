"""Pytest fixtures for llama_index LLMObs tests.

These fixtures follow the pattern from tests/contrib/anthropic/conftest.py.
"""
import os

import mock
import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.llama_index.utils import get_request_vcr
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_llama_index():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def test_spans(ddtrace_global_config, test_spans):
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False)
        yield test_spans
    finally:
        LLMObs.disable()


@pytest.fixture
def mock_llmobs_writer(scope="session"):
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def llama_index(ddtrace_global_config, ddtrace_config_llama_index):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("llama_index", ddtrace_config_llama_index):
            with override_env(
                dict(
                    # TODO: Set the appropriate API key env var for this library
                    # LLAMA_INDEX_API_KEY=os.getenv("LLAMA_INDEX_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import llama_index

                yield llama_index
                unpatch()


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()
