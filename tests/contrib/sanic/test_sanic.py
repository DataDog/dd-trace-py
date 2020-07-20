import ddtrace
import pytest

from ddtrace.contrib.sanic import patch, unpatch
from tests.base import BaseTracerTestCase
from tests.test_tracer import get_dummy_tracer

from sanic import Sanic, request, response
from sanic.response import json


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = get_dummy_tracer()
    setattr(ddtrace, "tracer", tracer)
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)


@pytest.fixture
def app(tracer):
    patch()
    app = Sanic(__name__)
    yield app
    unpatch()



def test_basic_app(app, tracer):
    """Test Sanic Patching"""
    @app.route("/")
    async def test(request):
        return json({ "hello": "world" })

    request, response = app.test_client.get('/')
    assert response.status == 200
    assert response.body == b'{"hello":"world"}'

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1

