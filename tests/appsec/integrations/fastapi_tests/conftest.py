from fastapi.testclient import TestClient
import pytest

import ddtrace
from ddtrace.contrib.internal.fastapi.patch import patch as fastapi_patch
from ddtrace.contrib.internal.fastapi.patch import unpatch as fastapi_unpatch
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer

from . import app


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()

    ddtrace.tracer = tracer
    fastapi_patch()
    yield tracer
    ddtrace.tracer = original_tracer
    fastapi_unpatch()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def fastapi_application(tracer):
    application = app.get_app()
    yield application


@pytest.fixture
def client(tracer, fastapi_application):
    with TestClient(fastapi_application) as test_client:
        yield test_client
