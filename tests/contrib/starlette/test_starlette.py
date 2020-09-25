import asyncio
import nest_asyncio
import sys
import os

import httpx
import pytest
from ddtrace.contrib.starlette import TraceMiddleware
from ddtrace.propagation import http as http_propagation
from starlette.testclient import TestClient
from tests import override_http_config
from tests.tracer.test_tracer import get_dummy_tracer
from app import get_app, file_clean_up


@pytest.fixture
def tracer():
    tracer = get_dummy_tracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())
    yield tracer


@pytest.fixture
async def app(tracer):
    app = get_app(tracer)
    yield app


@pytest.fixture
async def client(app):
    client = TestClient(app)
    yield client


@pytest.mark.asyncio
async def test_200(app, client, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    r = client.get("/200")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/200"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None


@pytest.mark.asyncio
async def test_200_query_string(app, tracer):
    with override_http_config("starlette", dict(trace_query_string=True)):
        app.add_middleware(TraceMiddleware, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            r = await client.get("http://testserver/?foo=bar")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "foo=bar"


@pytest.mark.asyncio
async def test_404(app, client, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    r = client.get("/404")

    assert r.status_code == 404
    assert r.text == "Not Found"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/404"
    assert request_span.get_tag("http.status_code") == "404"


@pytest.mark.asyncio
async def test_500error(app, client, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    with pytest.raises(RuntimeError):
        client.get("/500")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/500"
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag("error.msg") == "Server error"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Server error")' in request_span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_distributed_tracing(app, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID.encode(), "1234".encode()),
        (http_propagation.HTTP_HEADER_TRACE_ID.encode(), "5678".encode()),
    ]
    async with httpx.AsyncClient(app=app) as client:
        r = await client.get("http://testserver/", headers=headers)

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678


@pytest.mark.asyncio
async def test_multiple_requests(app, tracer):
    with override_http_config("starlette", dict(trace_query_string=True)):
        app.add_middleware(TraceMiddleware, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            responses = await asyncio.gather(
                client.get("http://testserver/", params={"sleep": True}),
                client.get("http://testserver/", params={"sleep": True}),
            )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.text for r in responses] == ["Success"] * 2

    spans = tracer.writer.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 1
    assert len(spans[1]) == 1

    r1_span = spans[0][0]
    assert r1_span.name == "starlette.request"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/"
    assert r1_span.get_tag("http.query.string") == "sleep=true"

    r2_span = spans[0][0]
    assert r2_span.name == "starlette.request"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/"
    assert r2_span.get_tag("http.query.string") == "sleep=true"

@pytest.mark.asyncio
async def test_streaming_response(app, client, tracer):    
    app.add_middleware(TraceMiddleware, tracer=tracer)
    r = client.get("/stream")

    assert r.status_code == 200

    assert r.text.endswith("streaming")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/stream"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"

@pytest.mark.asyncio
async def test_file_response(app, client, tracer):    
    app.add_middleware(TraceMiddleware, tracer=tracer)
    r = client.get("/file")

    assert r.status_code == 200
    assert r.text == "Datadog is the best!"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/file"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    file_clean_up()

nest_asyncio.apply()
