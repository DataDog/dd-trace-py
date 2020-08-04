import asyncio
from functools import partial
import random
import sys

import httpx
import pytest
from asgiref.testing import ApplicationCommunicator
from ddtrace.contrib.asgi import TraceMiddleware
from ddtrace.propagation import http as http_propagation
from tests.base import BaseTestCase
from tests.tracer.test_tracer import get_dummy_tracer


@pytest.fixture(
    params=[
        {},
        {"server": None},
        {"server": ("dev", 8000)},
        {"http_version": "1.0", "asgi": {}},
        {"http_version": "1.0", "asgi": {"version": "3.2"}},
        {"http_version": "1.0", "asgi": {"spec_version": "2.1",}},
        {"http_version": "1.0", "asgi": {"version": "3.2", "spec_version": "2.1",}},
    ]
)
def scope(request):
    s = {
        "client": ("127.0.0.1", 32767),
        "headers": [],
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "type": "http",
    }
    s.update(request.param)
    return {k: v for (k, v) in s.items() if v is not None}


@pytest.fixture
def tracer():
    tracer = get_dummy_tracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())
    yield tracer


async def basic_app(scope, receive, send):
    assert scope["type"] == "http"
    message = await receive()
    if message.get("type") == "http.request":
        await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
        query_string = scope.get("query_string")
        if query_string and query_string == b"sleep=true":
            await asyncio.sleep(random.random())
            await send({"type": "http.response.body", "body": b"sleep"})
        else:
            await send({"type": "http.response.body", "body": b"*"})


async def error_app(scope, receive, send):
    raise RuntimeError("Test")


def double_callable_app(scope):
    """
    A double-callable application for legacy ASGI 2.0 support
    """
    return partial(basic_app, scope)


def _check_span_tags(scope, span):
    assert span.get_tag("http.method") == scope["method"]
    server = scope.get("server")
    expected_http_url = (
        "http://{}{}".format(server[0], ":{}/".format(server[1]) if server[1] != 80 else "/") if server else None
    )
    assert expected_http_url or span.get_tag("http.url") == expected_http_url
    assert (
        scope.get("query_string") is None
        or scope.get("query_string") == b""
        or span.get_tag("http.query.string") == scope["query_string"]
    )
    assert scope.get("http_version") is None or span.get_tag("http.version") == scope["http_version"]
    assert (
        scope.get("asgi") is None
        or scope["asgi"].get("version") is None
        or span.get_tag("asgi.version") == scope["asgi"]["version"]
    )
    assert (
        scope.get("asgi") is None
        or scope["asgi"].get("spec_version") is None
        or span.get_tag("asgi.spec_version") == scope["asgi"]["spec_version"]
    )


@pytest.mark.asyncio
async def test_basic_asgi(scope, tracer):
    app = TraceMiddleware(basic_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.error == 0
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_double_callable_asgi(scope, tracer):
    app = TraceMiddleware(double_callable_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.error == 0
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_query_string(scope, tracer):
    with BaseTestCase.override_http_config("asgi", dict(trace_query_string=True)):
        app = TraceMiddleware(basic_app, tracer=tracer)
        scope["query_string"] = "foo=bar"
        instance = ApplicationCommunicator(app, scope)
        await instance.send_input({"type": "http.request", "body": b""})
        response_start = await instance.receive_output(1)
        assert response_start == {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"Content-Type", b"text/plain"]],
        }
        response_body = await instance.receive_output(1)
        assert response_body == {
            "type": "http.response.body",
            "body": b"*",
        }

        spans = tracer.writer.pop_traces()
        assert len(spans) == 1
        assert len(spans[0]) == 1
        request_span = spans[0][0]
        assert request_span.name == "asgi.request"
        assert request_span.error == 0
        _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_asgi_error(scope, tracer):
    app = TraceMiddleware(error_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    with pytest.raises(RuntimeError):
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.error == 1
    assert request_span.get_tag("error.msg") == "Test"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Test")' in request_span.get_tag("error.stack")
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_distributed_tracing(scope, tracer):
    app = TraceMiddleware(basic_app, tracer=tracer)
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID.encode(), "1234".encode()),
        (http_propagation.HTTP_HEADER_TRACE_ID.encode(), "5678".encode()),
    ]
    scope["headers"] = headers
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_multiple_requests(tracer):
    with BaseTestCase.override_http_config("asgi", dict(trace_query_string=True)):
        app = TraceMiddleware(basic_app, tracer=tracer)
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
    assert r1_span.name == "asgi.request"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/"
    assert r1_span.get_tag("http.query.string") == "sleep=true"

    r2_span = spans[0][0]
    assert r2_span.name == "asgi.request"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/"
    assert r2_span.get_tag("http.query.string") == "sleep=true"
