from fastapi.testclient import TestClient
import pytest

from ddtrace.contrib.internal.fastapi.patch import patch as fastapi_patch
from ddtrace.contrib.internal.fastapi.patch import unpatch as fastapi_unpatch
from tests.utils import scoped_tracer

from . import app


@pytest.fixture
def tracer():
    fastapi_patch()
    with scoped_tracer() as (tracer, _):
        yield tracer
    fastapi_unpatch()


@pytest.fixture
def fastapi_application(tracer):
    application = app.get_app()
    yield application


@pytest.fixture
def client(tracer, fastapi_application):
    with TestClient(fastapi_application) as test_client:
        yield test_client
