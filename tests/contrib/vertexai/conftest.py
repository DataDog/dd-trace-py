import pytest

from ddtrace.contrib.vertexai import patch
from ddtrace.contrib.vertexai import unpatch
from ddtrace.pin import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config
from tests.contrib.vertexai.utils import MockPredictionServiceClient
from tests.contrib.vertexai.utils import MockAsyncPredictionServiceClient

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
        pin.override(vertexai, tracer=mock_tracer)
        pin.tracer.configure()
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
def vertexai(ddtrace_global_config, ddtrace_config_vertexai, mock_client):
    global_config = ddtrace_global_config
    with override_global_config(global_config):
        with override_config("vertexai", ddtrace_config_vertexai):
            patch()
            import vertexai
            
            yield vertexai
            unpatch()
