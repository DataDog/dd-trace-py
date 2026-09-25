import os
from unittest import mock

import pytest

from ddtrace.aiguard._initialization import load_ai_guard
from ddtrace.contrib.internal.langchain.patch import patch
from ddtrace.contrib.internal.langchain.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.aiguard.utils import override_ai_guard_config
from tests.utils import override_env
from tests.utils import override_global_config


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
        load_ai_guard()


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
def openai_stream_evaluation(langchain_openai):
    """Patch the OpenAI integration with stream response evaluation on, underneath LangChain.

    The flag must be on before patching: the buffered-stream wrappers are installed at patch time.
    """
    from ddtrace.contrib.internal.openai.patch import patch as openai_patch
    from ddtrace.contrib.internal.openai.patch import unpatch as openai_unpatch

    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
        openai_patch()
        try:
            yield
        finally:
            openai_unpatch()


@pytest.fixture
def openai_url() -> str:
    """
    Use the request recording endpoint of the testagent to capture requests to OpenAI
    """
    return "http://localhost:9126/vcr/openai"


@pytest.fixture
def llmobs(tracer):
    """Enable LLM Observability with a mocked span writer; assertions read the tags off the spans."""
    LLMObs.disable()
    with override_global_config({"_dd_api_key": "<not-a-real-key>"}):
        # agentless would swap the tracer's DummyWriter and break test_spans.
        LLMObs.enable(
            _tracer=tracer, ml_app="aiguard_langchain_test", integrations_enabled=False, agentless_enabled=False
        )
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()
