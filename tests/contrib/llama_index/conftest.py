import os

import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.llama_index.utils import get_request_vcr
from tests.llmobs._utils import TestLLMObsSpanWriter
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
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False)
        yield test_spans
    finally:
        LLMObs.disable()


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
                    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import llama_index

                yield llama_index
                unpatch()


@pytest.fixture
def llama_index_llmobs(tracer, llmobs_span_writer):
    LLMObs.disable()
    with override_global_config(
        {
            "_dd_api_key": "<not-a-real-api_key>",
            "_llmobs_ml_app": "<ml-app-name>",
            "service": "tests.contrib.llama_index",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer = llmobs_span_writer
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def llmobs_events(llama_index_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()
