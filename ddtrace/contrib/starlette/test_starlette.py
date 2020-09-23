import asyncio
import random
import sys

import httpx
import pytest
from ddtrace.contrib.starlette import TraceMiddleware
from ddtrace.propagation import http as http_propagation
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from tests import override_http_config
from tests.tracer.test_tracer import get_dummy_tracer


@pytest.fixture
def tracer():
    tracer = get_dummy_tracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())
    yield tracer

async def hello(request: Request) -> Response:
    if "sleep" in request.query_params:
        await asyncio.sleep(random.random())
        return PlainTextResponse("sleep")

    return PlainTextResponse("hello")


async def error(request: Request) -> Response:
    raise RuntimeError("Test")


@pytest.fixture
async def app(tracer):
    app = Starlette(routes=[Route("/", hello), Route("/error/", error)])
    yield app


@pytest.mark.asyncio
async def test_200(app, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        r = await client.get("http://testserver/")

    assert r.status_code == 200
    assert r.text == "hello"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
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
    assert r.text == "hello"

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
async def test_404(app, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        r = await client.get("http://testserver/notfound")

    assert r.status_code == 404
    assert r.text == "Not Found"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/notfound"
    assert request_span.get_tag("http.status_code") == "404"


@pytest.mark.asyncio
async def test_error(app, tracer):
    app.add_middleware(TraceMiddleware, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        with pytest.raises(RuntimeError):
            await client.get("http://testserver/error/")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "starlette.request"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/error/"
    # FIXME: asgi middleware should set status code when exception raised
    assert request_span.get_tag("http.status_code") is None
    assert request_span.get_tag("error.msg") == "Test"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Test")' in request_span.get_tag("error.stack")


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
    assert r.text == "hello"

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
    assert [r.text for r in responses] == ["sleep"] * 2

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