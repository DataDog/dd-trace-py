import sys

import pytest

import ddtrace
from ddtrace.contrib.sanic import patch, unpatch
from tests.tracer.test_tracer import get_dummy_tracer


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = get_dummy_tracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())
    setattr(ddtrace, "tracer", tracer)
    patch()
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)
    unpatch()
