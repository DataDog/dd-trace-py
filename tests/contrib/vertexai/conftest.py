import mock
from mock import PropertyMock
import pytest

from ddtrace.contrib.internal.vertexai.patch import patch
from ddtrace.contrib.internal.vertexai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.contrib.vertexai.utils import MockAsyncPredictionServiceClient
from tests.contrib.vertexai.utils import MockPredictionServiceClient
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_vertexai():
    return {}


@pytest.fixture
def mock_client():
    yield MockPredictionServiceClient()


@pytest.fixture
def mock_async_client():
    yield MockAsyncPredictionServiceClient()


@pytest.fixture
def mock_tracer(ddtrace_global_config, vertexai):
    try:
        pin = Pin.get_from(vertexai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(vertexai, tracer=mock_tracer)
        pin.tracer.configure()
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use the mock tracer.
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False, agentless_enabled=False)
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
def vertexai(ddtrace_global_config, ddtrace_config_vertexai, mock_client, mock_async_client):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("vertexai", ddtrace_config_vertexai):
            patch()
            import vertexai
            from vertexai.generative_models import GenerativeModel

            with mock.patch.object(
                GenerativeModel, "_prediction_client", new_callable=PropertyMock
            ) as mock_client_property, mock.patch.object(
                GenerativeModel, "_prediction_async_client", new_callable=PropertyMock
            ) as mock_async_client_property:
                mock_client_property.return_value = mock_client
                mock_async_client_property.return_value = mock_async_client
                yield vertexai

            unpatch()
