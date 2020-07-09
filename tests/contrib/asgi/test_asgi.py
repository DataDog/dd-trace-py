import pytest
from asgiref.testing import ApplicationCommunicator

from ddtrace.contrib.asgi import TraceMiddleware
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
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://127.0.0.1/"
    assert request_span.get_tag("http.query.string") is None
