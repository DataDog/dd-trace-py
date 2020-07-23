import re
import asyncio

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
    patch()
    original_tracer = ddtrace.tracer
    tracer = get_dummy_tracer()
    setattr(ddtrace, "tracer", tracer)
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)
    unpatch()


@pytest.fixture
def app(tracer):
    app = Sanic(__name__)

    @app.route("/hello")
    async def hello(request):
        return json({"hello": "world"})

    @app.route("/stream_response")
    async def stream_response(request):
        async def sample_streaming_fn(response):
            await response.write("foo,")
            await response.write("bar")

        return stream(sample_streaming_fn, content_type="text/csv")

    @app.route("/error")
    async def error(request):
        raise ServerError("Something bad happened", status_code=500)

    @app.route("/sleep")
    async def sleep(request):
        timeout = int(request.args.get("timeout", 1))
        await asyncio.sleep(timeout)
        return json({"sleep": timeout})

    yield app


@pytest.fixture
def test_cli(loop, app, test_client):
    return loop.run_until_complete(test_client(app))


async def test_basic_app(tracer, test_cli):
    """Test Sanic Patching"""
    response = await test_cli.get("/hello")
    assert response.status == 200
    response_json = await response.json()
    assert response_json == {"hello": "world"}

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/hello", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


async def test_query_string(tracer, test_cli):
    """Test query string"""
    with BaseTestCase.override_http_config("sanic", dict(trace_query_string=True)):
        response = await test_cli.get("/hello", params=[("foo", "bar")])
        assert response.status == 200
        response_json = await response.json()
        assert response_json == {"hello": "world"}

        spans = tracer.writer.pop_traces()
        assert len(spans) == 1
        assert len(spans[0]) == 1
        request_span = spans[0][0]
        assert request_span.name == "sanic.request"
        assert request_span.error == 0
        assert request_span.get_tag("http.method") == "GET"
        assert re.match("http://127.0.0.1:\\d+/hello", request_span.get_tag("http.url"))
        assert request_span.get_tag("http.status_code") == "200"
        assert request_span.get_tag("http.query.string") == "foo=bar"


async def test_distributed_tracing(tracer, test_cli):
    """Test distributed tracing"""
    headers = [(http_propagation.HTTP_HEADER_PARENT_ID, "1234"), (http_propagation.HTTP_HEADER_TRACE_ID, "5678")]

    response = await test_cli.get("/hello", headers=headers)
    assert response.status == 200
    response_json = await response.json()
    assert response_json == {"hello": "world"}

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/hello", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678


async def test_streaming_response(tracer, test_cli):
    response = await test_cli.get("/stream_response")
    assert response.status == 200
    response_text = await response.text()
    assert response_text == "foo,bar"

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


async def test_error_app(tracer, test_cli):
    response = await test_cli.get("/nonexistent")
    assert response.status == 404
    response_text = await response.text()
    assert "not found" in response_text

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/nonexistent", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "404"


async def test_exception(tracer, test_cli):
    response = await test_cli.get("/error")
    assert response.status == 500
    response_text = await response.text()
    assert "Something bad happened" in response_text

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"


async def test_multiple_requests(tracer, test_cli):
    response_sleep = await test_cli.get("/sleep", params={"timeout": 2})
    response_hello = await test_cli.get("/hello")
    assert response_sleep.status == 200
    response_sleep_json = await response_sleep.json()
    assert response_sleep_json == {"sleep": 2}
    assert response_hello.status == 200
    response_hello_json = await response_hello.json()
    assert response_hello_json == {"hello": "world"}

    spans = tracer.writer.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 1
    assert len(spans[1]) == 1

    request_sleep_span = spans[0][0]
    assert request_sleep_span.name == "sanic.request"
    assert request_sleep_span.error == 0
    assert request_sleep_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/sleep", request_sleep_span.get_tag("http.url"))
    assert request_sleep_span.get_tag("http.status_code") == "200"

    request_hello_span = spans[1][0]
    assert request_hello_span.name == "sanic.request"
    assert request_hello_span.error == 0
    assert request_hello_span.get_tag("http.method") == "GET"
    assert re.match("http://127.0.0.1:\\d+/hello", request_hello_span.get_tag("http.url"))
    assert request_hello_span.get_tag("http.status_code") == "200"
