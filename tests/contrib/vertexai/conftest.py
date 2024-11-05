import mock
import pytest

from ddtrace.contrib.vertexai import patch
from ddtrace.contrib.vertexai import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.pin import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config

@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_vertexai():
    return {}


@pytest.fixture
def mock_tracer(ddtrace_global_config, vertexai):
    try:
        pin = Pin.get_from(vertexai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin.override(vertexai, tracer=mock_tracer)
        pin.tracer.configure()
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
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
def vertexai(ddtrace_global_config, ddtrace_config_vertexai):
    global_config = ddtrace_global_config
    with override_global_config(global_config):
        with override_config("vertexai", ddtrace_config_vertexai):
            patch()
            import vertexai
            from vertexai.generative_models import GenerativeModel, GenerationConfig

            yield vertexai
            unpatch()
