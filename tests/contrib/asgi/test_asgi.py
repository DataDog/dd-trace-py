import pytest
from asgiref.testing import ApplicationCommunicator

from ddtrace.contrib.asgi import TraceMiddleware
from ddtrace.propagation import http as http_propagation
from tests.test_tracer import get_dummy_tracer


@pytest.fixture
def scope():
    return {
        "client": ("127.0.0.1", 32767),
        "headers": [],
        "http_version": "1.0",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "type": "http",
    }


@pytest.fixture
def tracer():
    tracer = get_dummy_tracer()
    yield tracer


async def basic_app(scope, receive, send):
    message = await receive()
    assert scope["type"] == "http"
    if message.get("type") == "http.request":
        await send(
            {"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]],}
        )
        await send({"type": "http.response.body", "body": b"*"})


async def error_app(scope, receive, send):
    raise RuntimeError("Test")


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
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://127.0.0.1/"
    assert request_span.get_tag("http.query.string") is None


@pytest.mark.asyncio
async def test_query_string(scope, tracer):
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
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://127.0.0.1/?foo=bar"
    assert request_span.get_tag("http.query.string") == "foo=bar"


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
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://127.0.0.1/"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("error.msg") == "Test"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Test")' in request_span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_distributed_tracing(scope, tracer):
    app = TraceMiddleware(basic_app, tracer=tracer)
    headers = [(http_propagation.HTTP_HEADER_PARENT_ID, "1234"), (http_propagation.HTTP_HEADER_TRACE_ID, "5678")]
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
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://127.0.0.1/"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678
