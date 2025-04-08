import mock

import pytest
from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.trace import Pin
from ddtrace.contrib.internal.litellm.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config
from tests.contrib.litellm.utils import get_request_vcr
from ddtrace.llmobs import LLMObs


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture()
def mock_llmobs_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


@pytest.fixture
def litellm(ddtrace_global_config, monkeypatch):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "<not-a-real-key>")
        monkeypatch.setenv("COHERE_API_KEY", "<not-a-real-key>")
        patch()
        import litellm

        yield litellm
        unpatch()


@pytest.fixture
def mock_tracer(litellm, ddtrace_global_config):
    pin = Pin.get_from(litellm)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(litellm, tracer=mock_tracer)
    pin.tracer.configure()

    if ddtrace_global_config.get("_llmobs_enabled", False):
        # Have to disable and re-enable LLMObs to use the mock tracer.
        LLMObs.disable()
        enable_integrations = ddtrace_global_config.get("_integrations_enabled", False)
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=enable_integrations)

    yield mock_tracer

    LLMObs.disable()


@pytest.fixture
def request_vcr():
    return get_request_vcr()

@pytest.fixture
def request_vcr_include_localhost():
    return get_request_vcr(ignore_localhost=False)
