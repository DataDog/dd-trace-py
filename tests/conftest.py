import pytest

from tests import DummyTracer


@pytest.fixture
def tracer():
    return DummyTracer()
