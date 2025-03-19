import os
import pytest
import vcr

from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import AGENTLESS_BASE_URL
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def request_vcr():
    yield vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setenv("_DD_LLMOBS_AUTO_SPAN_LINKING_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    patch()
    import agents

    yield agents
    unpatch()


@pytest.fixture
def mock_tracer(agents):
    pin = Pin.get_from(agents)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(agents, tracer=mock_tracer)
    yield mock_tracer


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def enqueue(self, event):
        self.events.append(event)


@pytest.fixture
def llmobs_service(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.agents"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_span_writer():
    agentless_url = "{}.{}".format(AGENTLESS_BASE_URL, "datad0g.com")
    yield TestLLMObsSpanWriter(is_agentless=True, agentless_url=agentless_url, interval=1.0, timeout=1.0)


@pytest.fixture
def llmobs_events(llmobs_service, llmobs_span_writer):
    return llmobs_span_writer.events