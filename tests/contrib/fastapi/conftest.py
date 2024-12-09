from fastapi.testclient import TestClient
import pytest

import ddtrace
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
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
def snapshot_app_with_middleware():
    fastapi_patch()

    application = app.get_app()

    @application.middleware("http")
    async def traced_middlware(request, call_next):
        with ddtrace.tracer.trace("traced_middlware"):
            response = await call_next(request)
            return response

    yield application

    fastapi_unpatch()


@pytest.fixture
def client(tracer, fastapi_application):
    with TestClient(fastapi_application) as test_client:
        yield test_client


@pytest.fixture
def snapshot_app():
    fastapi_patch()
    application = app.get_app()
    yield application
    fastapi_unpatch()


@pytest.fixture
def snapshot_client(snapshot_app):
    with TestClient(snapshot_app) as test_client:
        yield test_client


@pytest.fixture
def snapshot_app_with_tracer(tracer):
    fastapi_patch()
    application = app.get_app()
    yield application
    fastapi_unpatch()


@pytest.fixture
def snapshot_client_with_tracer(snapshot_app_with_tracer):
    with TestClient(snapshot_app_with_tracer) as test_client:
        yield test_client
