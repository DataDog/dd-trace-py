import re

import pytest
from sanic import Sanic
from sanic.exceptions import ServerError
from sanic.response import json, stream

import ddtrace
from ddtrace.contrib.sanic import patch, unpatch
from ddtrace.propagation import http as http_propagation
from tests.base import BaseTestCase
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


def test_query_string(app, tracer):
    """Test query string"""
    with BaseTestCase.override_http_config("sanic", dict(trace_query_string=True)):

        @app.route("/")
        async def test(request):
            return json({"hello": "world"})

        request, response = app.test_client.get("/", params=[("foo", "bar")])
        assert response.status == 200
        assert response.body == b'{"hello":"world"}'

        spans = tracer.writer.pop_traces()
        assert len(spans) == 1
        assert len(spans[0]) == 1
        request_span = spans[0][0]
        assert request_span.name == "sanic.request"
        assert request_span.error == 0
        assert request_span.get_tag("http.method") == "GET"
        assert re.match("http://127.0.0.1:\\d+/[?]foo=bar", request_span.get_tag("http.url"))
        assert request_span.get_tag("http.status_code") == "200"
        assert request_span.get_tag("http.query.string") == "foo=bar"


def test_distributed_tracing(app, tracer):
    """Test distributed tracing"""

    @app.route("/")
    async def test(request):
        return json({"hello": "world"})

    headers = [(http_propagation.HTTP_HEADER_PARENT_ID, "1234"), (http_propagation.HTTP_HEADER_TRACE_ID, "5678")]

    request, response = app.test_client.get("/", headers=headers)
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
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678


def test_streaming_response(app, tracer):
    @app.route("/")
    async def test(request):
        async def sample_streaming_fn(response):
            await response.write("foo,")
            await response.write("bar")

        return stream(sample_streaming_fn, content_type="text/csv")

    request, response = app.test_client.get("/")
    assert response.status == 200
    assert response.text == "foo,bar"

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

def test_error_app(app, tracer):
    @app.route("/")
    async def test(request):
        return json({"hello": "world"})

    request, response = app.test_client.get("/error")
    assert response.status == 404
    assert b"not found" in response.body

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    # assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "404"
    

def test_exception(app, tracer):
    @app.route('/')
    async def i_am_ready_to_die(request):
        raise ServerError("Something bad happened", status_code=500)

    request, response = app.test_client.get("/")
    assert response.status == 500
    assert b"Something bad happened" in response.body

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    # assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"

    assert request_span.get_tag("error.msg") == ""
    assert request_span.get_tag("error.type") == ""