import pytest

from tests.utils import TracerSpanContainer
from tests.utils import _build_tree


@pytest.fixture
def test_spans(interface):
    container = TracerSpanContainer(interface.tracer)
    yield container
    container.reset()


@pytest.fixture
def root_span(test_spans):
    # get the first root span
    def get_root_span():
        for span in test_spans.spans:
            if span.parent_id is None:
                return _build_tree(test_spans.spans, span)

    return get_root_span


@pytest.fixture
def get_tag(root_span):
    yield lambda name: root_span().get_tag(name)
