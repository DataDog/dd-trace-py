import starlette
import sys
from ddtrace.pin import Pin
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, Mount, WebSocketRoute
from starlette.staticfiles import StaticFiles
from ddtrace.contrib.starlette.patch import patch as byte_patch
from ddtrace.contrib.starlette.patch import unpatch as byte_unpatch

from ddtrace.contrib.starlette.starlette_normal import patch as normal_patch
from ddtrace.contrib.starlette.starlette_normal import patch as normal_unpatch

from ddtrace.contrib.sqlalchemy import patch as sql_patch
from ddtrace.contrib.sqlalchemy import unpatch as sql_unpatch
import requests
from starlette.testclient import TestClient
import sqlalchemy

from tests.contrib.starlette.app import get_app


@pytest.fixture
def engine():
    sql_patch()
    engine = sqlalchemy.create_engine("sqlite:///test.db")
    yield engine
    sql_unpatch()


@pytest.fixture
def app(tracer, engine):
    app = get_app(engine)
    yield app
    

@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def test_byte_code_wrap(benchmark, client):
    byte_patch()
    def func():
        for i in range(1000):
            client.get("/200")

    benchmark(func)
    byte_unpatch()



def test_normal_wrap(benchmark, client):
    normal_patch()
    def func():
        for i in range(1000):
            client.get("/200")

    benchmark(func)
    normal_unpatch()