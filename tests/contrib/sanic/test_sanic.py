import asyncio
import random
import re

import httpx
import pytest
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.propagation import http as http_propagation
from sanic import Sanic
from sanic.exceptions import ServerError
from sanic.response import json, stream, text

from tests import override_config, override_http_config


@pytest.fixture
def app(tracer):
    app = Sanic(__name__)

    @tracer.wrap()
    async def random_sleep():
        await asyncio.sleep(random.random())

    @app.route("/hello")
    async def hello(request):
        await random_sleep()
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

    @app.route("/invalid")
    async def invalid(request):
        return "This should fail"

    @app.route("/empty")
    async def empty(request):
        pass

    @app.exception(ServerError)
    def handler_exception(request, exception):
        return text(exception.args[0], exception.status_code)

    yield app


@pytest.fixture
def client(app):
    yield httpx.AsyncClient(app=app, base_url="http://testserver")


@pytest.fixture(
    params=[
        dict(),
        dict(service="mysanicsvc"),
        dict(analytics_enabled=False),
        dict(analytics_enabled=True),
        dict(analytics_enabled=True, analytics_sample_rate=0.5),
        dict(analytics_enabled=False, analytics_sample_rate=0.5),
        dict(distributed_tracing=False),
    ],
    ids=[
        "default",
        "service_override",
        "disable_analytics",
        "enable_analytics_default_sample_rate",
        "enable_analytics_custom_sample_rate",
        "disable_analytics_custom_sample_rate",
        "disable_distributed_tracing",
    ],
)
def integration_config(request):
    return request.param


@pytest.fixture(
    params=[dict(), dict(trace_query_string=False), dict(trace_query_string=True),],
    ids=["default", "disable trace query string", "enable trace query string",],
)
def integration_http_config(request):
    return request.param


@pytest.mark.asyncio
async def test_basic_app(tracer, client, integration_config, integration_http_config):
    """Test Sanic Patching"""
    with override_http_config("sanic", integration_http_config):
        with override_config("sanic", integration_config):
            headers = [
                (http_propagation.HTTP_HEADER_PARENT_ID, "1234"),
                (http_propagation.HTTP_HEADER_TRACE_ID, "5678"),
            ]
            response = await client.get("/hello", params=[("foo", "bar")], headers=headers)
            assert response.status_code == 200
            assert response.json() == {"hello": "world"}

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/hello$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.status_code") == "200"

    sleep_span = spans[0][1]
    assert sleep_span.name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert sleep_span.parent_id == request_span.span_id

    if integration_config.get("service"):
        assert request_span.service == integration_config["service"]
    else:
        assert request_span.service == "sanic"

    if integration_http_config.get("trace_query_string"):
        assert request_span.get_tag("http.query.string") == "foo=bar"
    else:
        assert request_span.get_tag("http.query.string") is None

    if integration_config.get("analytics_enabled"):
        analytics_sample_rate = integration_config.get("analytics_sample_rate") or 1.0
        assert request_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == analytics_sample_rate
    else:
        assert request_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    if integration_config.get("distributed_tracing", True):
        assert request_span.parent_id == 1234
        assert request_span.trace_id == 5678
    else:
        assert request_span.parent_id is None
        assert request_span.trace_id is not None and request_span.trace_id != 5678


@pytest.mark.asyncio
async def test_streaming_response(tracer, client):
    response = await client.get("/stream_response")
    assert response.status_code == 200
    assert response.text.endswith("foo,\r\n3\r\nbar\r\n0\r\n\r\n")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/stream_response$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


@pytest.mark.asyncio
async def test_error_app(tracer, client):
    response = await client.get("/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.text

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/nonexistent$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "404"


@pytest.mark.asyncio
async def test_exception(tracer, client):
    response = await client.get("/error")
    assert response.status_code == 500
    assert "Something bad happened" in response.text

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/error$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"


@pytest.mark.asyncio
async def test_multiple_requests(tracer, client):
    responses = await asyncio.gather(client.get("/hello"), client.get("/hello"),)

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.json() for r in responses] == [{"hello": "world"}] * 2

    spans = tracer.writer.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 2
    assert len(spans[1]) == 2

    assert spans[0][0].name == "sanic.request"
    assert spans[0][1].name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert spans[0][0].parent_id is None
    assert spans[0][1].parent_id == spans[0][0].span_id

    assert spans[1][0].name == "sanic.request"
    assert spans[1][1].name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert spans[1][0].parent_id is None
    assert spans[1][1].parent_id == spans[1][0].span_id


@pytest.mark.asyncio
async def test_invalid_response_type_str(tracer, client):
    response = await client.get("/invalid")
    assert response.status_code == 500
    assert response.text == "Invalid response type"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/invalid$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"


@pytest.mark.asyncio
async def test_invalid_response_type_empty(tracer, client):
    response = await client.get("/empty")
    assert response.status_code == 500
    assert response.text == "Invalid response type"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/empty$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"
