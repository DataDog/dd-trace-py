import pytest

import ddtrace
from ddtrace.contrib.internal.sanic.patch import patch
from ddtrace.contrib.internal.sanic.patch import unpatch
from tests.utils import DummyTracer


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    ddtrace.tracer = tracer
    patch()
    yield tracer
    ddtrace.tracer = original_tracer
    unpatch()
