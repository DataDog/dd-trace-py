import pytest

from tests.utils import scoped_tracer


@pytest.fixture
def tracer(use_dummy_writer):
    with scoped_tracer(use_dummy_writer) as tracer:
        yield tracer
