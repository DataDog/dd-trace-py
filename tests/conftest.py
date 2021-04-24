import pytest

from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


@pytest.fixture
def tracer():
    return DummyTracer()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()
