import os

import pytest
import vcr

from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import override_global_config


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(is_agentless=True, interval=1.0, timeout=1.0)


@pytest.fixture
def llmobs_events(pydantic_ai_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture(autouse=True)
def pydantic_ai(ddtrace_global_config, monkeypatch):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
        patch()
        import pydantic_ai

        yield pydantic_ai
        unpatch()


@pytest.fixture
def pydantic_ai_llmobs(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def mock_tracer(pydantic_ai):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(pydantic_ai)
    pin._override(pydantic_ai, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def request_vcr(ignore_localhost=True):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        ignore_localhost=ignore_localhost,
    )
