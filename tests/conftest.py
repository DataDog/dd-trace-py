import pytest

from tests.tracer.test_tracer import DummyTracer


@pytest.fixture
def tracer():
    return DummyTracer()
