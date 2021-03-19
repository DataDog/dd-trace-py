import pytest

from tests import DummyTracer
from tests import TracerSpanContainer


@pytest.fixture
def tracer():
    return DummyTracer()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()
