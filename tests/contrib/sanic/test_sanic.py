import re

import pytest
from sanic import Sanic
from sanic.response import json

import ddtrace
from ddtrace.contrib.sanic import patch, unpatch
from tests.test_tracer import get_dummy_tracer


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
        return json({"hello": "world"})

    request, response = app.test_client.get("/")
    assert response.status == 200
    assert response.body == b'{"hello":"world"}'

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
