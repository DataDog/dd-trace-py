import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import override_global_config


@pytest.fixture
def llama_index(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    patch()
    import llama_index

    yield llama_index
    unpatch()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def llama_index_llmobs(tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {
            "_dd_api_key": "<not-a-real-api_key>",
            "_llmobs_ml_app": "<ml-app-name>",
            "service": "tests.contrib.llama_index",
        }
    ):
        llmobs_service.enable(_tracer=tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(llama_index_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events
