import pytest

from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.litellm.utils import get_request_vcr
from tests.utils import DummyTracer
from tests.utils import DummyWriter


@pytest.fixture
def litellm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("COHERE_API_KEY", "<not-a-real-key>")
    patch()
    import litellm

    yield litellm
    unpatch()


@pytest.fixture
def mock_tracer(litellm):
    pin = Pin.get_from(litellm)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(litellm, tracer=mock_tracer)
    pin.tracer.configure()
    yield mock_tracer


@pytest.fixture
def request_vcr():
    return get_request_vcr()
