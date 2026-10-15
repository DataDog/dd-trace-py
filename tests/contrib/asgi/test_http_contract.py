import asyncio
from dataclasses import FrozenInstanceError

import pytest

from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestInspectionEvent
from ddtrace.contrib._events.http import HttpRequestPhase as Phase
from ddtrace.contrib.internal.asgi.lifecycle import HttpLifecycleMiddleware
from ddtrace.internal import core


@pytest.fixture
def observed():
    snapshots = []

    def observe(event):
        snapshots.append(event.snapshot)

    core.on(HttpRequestDataEvent.event_name, observe)
    yield snapshots
    core.reset_listeners(HttpRequestDataEvent.event_name, observe)


def scope():
    return {
        "type": "http",
        "method": "GET",
        "path": "/items",
        "scheme": "http",
        "headers": [(b"host", b"example.test"), (b"x-test", b"one"), (b"x-test", b"two")],
        "query_string": b"tag=a&tag=b&empty=",
    }


async def unread():
    raise AssertionError("adapter must not read the request body")


@pytest.mark.asyncio
async def test_streaming_and_completion(observed):
    messages = [
        {"type": "http.response.start", "status": 201, "headers": [(b"set-cookie", b"a=1"), (b"set-cookie", b"b=2")]},
        {"type": "http.response.body", "body": b"one", "more_body": True},
        {"type": "http.response.body", "body": b"two"},
    ]
    sent = []

    async def send(message):
        if message["type"] == "http.response.start":
            assert observed[-1].phase == Phase.RESPONSE_READY
            assert not observed[-1].response_committed
        sent.append(message)

    async def app(scope, receive, send):
        for message in messages:
            await send(message)
        assert observed[-1].phase == Phase.RESPONSE_COMMITTED
        await asyncio.sleep(0)

    request_scope = scope()
    await HttpLifecycleMiddleware(HttpLifecycleMiddleware(app))(request_scope, unread, send)
    assert sent == messages
    assert all(a is b for a, b in zip(sent, messages))
    assert [s.phase for s in observed] == [
        Phase.REQUEST_STARTED,
        Phase.RESPONSE_READY,
        Phase.RESPONSE_COMMITTED,
        Phase.COMPLETED,
    ]
    assert len({s.request_id for s in observed}) == 1
    assert observed[0].request.query_parameters == (("tag", "a"), ("tag", "b"), ("empty", ""))
    assert observed[0].request.headers[-2:] == (("x-test", "one"), ("x-test", "two"))
    assert observed[-1].response.headers == (("set-cookie", "a=1"), ("set-cookie", "b=2"))
    assert observed[-1].response.body is None
    assert all(not s.supported_actions for s in observed)
    assert "datadog.http_lifecycle" not in request_scope
    with pytest.raises(FrozenInstanceError):
        observed[0].request.method = "POST"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("disconnected"), asyncio.CancelledError()])
async def test_send_failure_and_cancellation(observed, error):
    async def send(message):
        raise error

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})

    request_scope = scope()
    with pytest.raises(type(error)):
        await HttpLifecycleMiddleware(app)(request_scope, unread, send)
    assert observed[-1].phase == Phase.COMPLETED
    assert not observed[-1].response_committed
    assert observed[-1].errors[0].type == type(error).__name__
    assert "datadog.http_lifecycle" not in request_scope


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["websocket", "lifespan"])
async def test_non_http_passthrough(observed, kind):
    request_scope = {"type": kind}

    async def app(actual, receive, send):
        assert actual is request_scope
        assert receive is unread and send is unread

    await HttpLifecycleMiddleware(app)(request_scope, unread, unread)
    assert observed == []


@pytest.mark.asyncio
async def test_no_inspection_dispatch(observed):
    inspected = []

    def inspect(event):
        inspected.append(event)

    async def app(scope, receive, send):
        pass

    core.on(HttpRequestInspectionEvent.event_name, inspect)
    try:
        await HttpLifecycleMiddleware(app)(scope(), unread, unread)
    finally:
        core.reset_listeners(HttpRequestInspectionEvent.event_name, inspect)
    assert inspected == []


@pytest.mark.asyncio
async def test_error_formatting_preserves_original_exception(observed):
    class UnprintableError(Exception):
        def __str__(self):
            raise ValueError("formatting failed")

    error = UnprintableError()

    async def app(scope, receive, send):
        raise error

    with pytest.raises(UnprintableError) as raised:
        await HttpLifecycleMiddleware(app)(scope(), unread, unread)
    assert raised.value is error
    assert observed[-1].errors[0].message is None


@pytest.mark.asyncio
async def test_unicode_path_without_raw_path(observed):
    request_scope = scope()
    request_scope["path"] = "/café"

    async def app(scope, receive, send):
        pass

    await HttpLifecycleMiddleware(app)(request_scope, unread, unread)
    assert observed[0].request.url.startswith("http://example.test/café?")
