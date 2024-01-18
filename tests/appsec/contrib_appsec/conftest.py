import pytest

from ddtrace import Pin
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


@pytest.fixture
def tracer(interface):
    tracer = DummyTracer()
    # Patch Django and override tracer to be our test tracer
    pin = Pin.get_from(interface.framework)
    original_tracer = pin.tracer
    Pin.override(interface.framework, tracer=tracer)

    # Yield to our test
    yield tracer
    tracer.pop()

    Pin.override(interface.framework, tracer=original_tracer)


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def root_span(test_spans):
    yield test_spans.get_root_span
