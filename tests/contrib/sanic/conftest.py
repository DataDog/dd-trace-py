import pytest

import ddtrace
from ddtrace.contrib.internal.sanic.patch import patch
from ddtrace.contrib.internal.sanic.patch import unpatch
from ddtrace.internal import core
from tests.utils import DummyTracer


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    ddtrace.tracer = tracer
    core.tracer = tracer
    patch()
    yield tracer
    ddtrace.tracer = original_tracer
    core.tracer = original_tracer
    unpatch()
