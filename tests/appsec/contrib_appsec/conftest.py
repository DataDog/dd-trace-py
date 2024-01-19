import pytest

from tests.utils import TracerSpanContainer


@pytest.fixture
def test_spans(interface):
    container = TracerSpanContainer(interface.tracer)
    yield container
    container.reset()


@pytest.fixture
def root_span(test_spans):
    yield test_spans.get_root_span
