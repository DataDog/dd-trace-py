"""Proof of the shared HTTP contract against real, explicitly wrapped Starlette apps."""

import asyncio
from urllib.parse import urlsplit

import anyio
import pytest
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse
from starlette.responses import StreamingResponse
from starlette.routing import Mount
from starlette.routing import Route

from ddtrace import config
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.http_asgi import AsgiLifecycleDriver
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.starlette.patch import instrument_http
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.contrib.http_contract import HttpContractTests
from tests.contrib.http_contract import block_response
from tests.contrib.http_contract import observe_http_requests as observe_http_requests
from tests.contrib.http_contract import register_http_block_checker as register_http_block_checker
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


@pytest.fixture(autouse=True)
def isolate_event_loop_policy():
    # asyncio.run clears the active loop. Keep that state local to these tests
    # so legacy Starlette TestClient can still use the suite's event-loop policy.
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


@pytest.fixture
def tracer():
    with scoped_tracer() as tracer:
        yield tracer


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def settings():
    return HttpLifecycleConfig("starlette", "starlette-proof", IntegrationConfig(config, "starlette"))


def scope_for(path="/items/one", method="GET"):
    url = urlsplit(path)
    return dict(
        type="http",
        asgi={"version": "3.0", "spec_version": "2.4"},
        method=method,
        path=url.path,
        raw_path=url.path.encode(),
        root_path="",
        query_string=url.query.encode(),
        headers=[(b"host", b"localhost")],
        scheme="http",
        server=("localhost", 80),
        client=("127.0.0.1", 1234),
    )


async def invoke(app, path="/items/one", method="GET", send=None):
    messages = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def collect(message):
        messages.append(message)

    await app(scope_for(path, method), receive, send or collect)
    return messages


class Adapter:
    route_pattern = "/items/{name}"

    def __init__(self, settings):
        self.app = Starlette()
        self.settings = settings

    def route(self):
        def register(handler):
            async def endpoint(request):
                result = handler(request.path_params["name"])
                return PlainTextResponse(result) if isinstance(result, str) else result

            self.app.router.routes.append(Route(self.route_pattern, endpoint))
            return handler

        return register

    def response(self, body, status=200):
        return PlainTextResponse(body, status_code=status)

    def request(self, path="/items/one", method="GET"):
        with instrument_http(self.app, self.settings) as wrapped:
            messages = asyncio.run(invoke(wrapped, path, method))
            return b"".join(message.get("body", b"") for message in messages)


@pytest.fixture
def http_adapter(settings, tracer):
    return Adapter(settings)


class TestStarletteContract(HttpContractTests):
    pass


def test_child_send_finishes_before_background_and_restores_context(settings, tracer, test_spans, observations):
    async def app(scope, receive, send):
        span = tracer.current_span()
        assert span is not None

        async def stream():
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            assert not span.finished
            await send({"type": "http.response.body", "body": b"last"})
            assert span.finished

        await asyncio.create_task(stream())
        assert observations[-1].phase == HttpRequestPhase.COMPLETED

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    with tracer.trace("parent") as parent:
        asyncio.run(invoke(driver.wrap(app)))
        assert tracer.current_span() is parent
    assert len(test_spans.pop()) == 2


def test_starlette_background_and_mounted_resource(settings, tracer, test_spans, observations):
    async def background():
        assert observations[-1].phase == HttpRequestPhase.COMPLETED
        assert test_spans.get_spans()[0].finished

    async def endpoint(request):
        return PlainTextResponse("done", background=BackgroundTask(background))

    app = Starlette(routes=[Mount("/api", app=Starlette(routes=[Route("/items/{name}", endpoint)]))])
    with instrument_http(app, settings) as wrapped:
        asyncio.run(invoke(wrapped, "/api/items/one"))
    (span,) = test_spans.pop()
    assert span.resource == "GET /api/items/{name}"
    assert observations[-1].request.route_pattern == "/items/{name}"
    assert observations[-1].request.route == "/api/items/{name}"


@pytest.mark.parametrize("failure", ["start", "body", "cancel", "return"])
def test_transport_failure_completes_once(settings, tracer, observations, failure):
    async def send(message):
        if message["type"] == "http.response." + failure:
            raise OSError("send failed")

    async def app(scope, receive, send):
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "return":
            return
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"done"})

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    expected = asyncio.CancelledError if failure == "cancel" else RuntimeError if failure == "return" else OSError
    with pytest.raises(expected):
        asyncio.run(invoke(driver.wrap(app), send=send))
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1
    assert observations[-1].errors
    assert observations[-1].response_committed is (failure == "body")


def test_trailers_delay_completion_and_receive_is_untouched(settings, tracer, observations):
    disconnect = {"type": "http.disconnect"}

    async def receive():
        return disconnect

    async def send(message):
        pass

    async def app(scope, recv, send):
        assert await recv() is disconnect
        await send({"type": "http.response.start", "status": 200, "trailers": True})
        await send({"type": "http.response.body", "body": b"done"})
        assert observations[-1].phase != HttpRequestPhase.COMPLETED
        await send({"type": "http.response.trailers", "headers": [], "more_trailers": False})
        assert observations[-1].phase == HttpRequestPhase.COMPLETED

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    asyncio.run(driver.wrap(app)(scope_for(), receive, send))


def test_replacement_suppresses_caught_block_and_original_body(settings, tracer, observations, block_checker):
    def inspect(event):
        if event.phase == HttpRequestPhase.RESPONSE_READY:
            raise block_response()

    block_checker(inspect)

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.start", "status": 200})
        except BaseException:
            pass
        await send({"type": "http.response.body", "body": b"original"})

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    messages = asyncio.run(invoke(driver.wrap(app)))
    assert len(messages) == 2
    assert messages[0]["status"] == 403
    assert messages[1]["body"] == b"blocked"


def test_concurrent_requests_keep_identity_and_parentage(settings, tracer, test_spans, observations):
    async def app(scope, receive, send):
        span = tracer.current_span()
        await asyncio.sleep(0)
        assert tracer.current_span() is span
        await send({"type": "http.response.start", "status": 200})
        await asyncio.sleep(0)
        await send({"type": "http.response.body", "body": b"done"})

    driver = AsgiLifecycleDriver(get_config=lambda: settings)

    async def run():
        await asyncio.gather(invoke(driver.wrap(app), "/one"), invoke(driver.wrap(app), "/two"))

    asyncio.run(run())
    completed = [event for event in observations if event.phase == HttpRequestPhase.COMPLETED]
    assert len(completed) == 2
    assert completed[0].request_id is not completed[1].request_id
    spans = test_spans.pop()
    assert len(spans) == 2
    assert all(span.parent_id is None for span in spans)


def test_error_response_retains_exception(settings, tracer, test_spans, observations):
    async def endpoint(request):
        raise ValueError("handler failed")

    with instrument_http(Starlette(routes=[Route("/items/{name}", endpoint)]), settings) as wrapped:
        with pytest.raises(ValueError, match="handler failed"):
            asyncio.run(invoke(wrapped))
    (span,) = test_spans.pop()
    assert span.error == 1
    assert span.get_tag("error.message") == "handler failed"
    assert observations[-1].response.status_code == 500
    assert observations[-1].errors[0].type == "builtins.ValueError"


def test_real_starlette_stream_finishes_after_last_chunk(settings, tracer, observations):
    async def chunks():
        yield b"first"
        await asyncio.sleep(0)
        assert all(event.phase != HttpRequestPhase.COMPLETED for event in observations)
        yield b"last"

    async def endpoint(request):
        return StreamingResponse(chunks())

    with instrument_http(Starlette(routes=[Route("/items/{name}", endpoint)]), settings) as wrapped:
        messages = asyncio.run(invoke(wrapped))
    assert b"".join(message.get("body", b"") for message in messages) == b"firstlast"
    assert observations[-1].phase == HttpRequestPhase.COMPLETED


def test_block_in_task_group(settings, tracer, observations, block_checker):
    def inspect(event):
        if event.phase == HttpRequestPhase.RESPONSE_READY:
            raise block_response()

    block_checker(inspect)

    async def app(scope, receive, send):
        async def respond():
            await send({"type": "http.response.start", "status": 200})
            pytest.fail("blocked child task continued")

        async with anyio.create_task_group() as group:
            group.start_soon(respond)

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    messages = asyncio.run(invoke(driver.wrap(app)))
    assert messages[0]["status"] == 403
    assert observations[-1].errors == ()


@pytest.mark.parametrize(
    "headers,server,scheme,expected",
    [
        ([(b"host", b"public.example:8443")], ("internal", 80), "https", "https://public.example:8443/items/one"),
        ([], ("localhost", 80), "http", "http://localhost/items/one"),
        ([], ("localhost", 8443), "https", "https://localhost:8443/items/one"),
        ([], ("::1", 443), "https", "https://[::1]/items/one"),
        ([], ("localhost", None), "http", "http://localhost/items/one"),
        ([], None, "http", "/items/one"),
    ],
)
def test_plain_asgi_needs_no_adapter(settings, tracer, observations, headers, server, scheme, expected):
    scope = scope_for()
    scope.update(
        headers=headers + [(b"x-test", b"one"), (b"x-test", b"two")],
        server=server,
        scheme=scheme,
        query_string=b"tag=a&tag=b&empty=",
    )

    async def receive():
        pytest.fail("request extraction must not consume receive")

    async def send(message):
        pass

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"done"})

    driver = AsgiLifecycleDriver(get_config=lambda: settings)
    asyncio.run(driver.wrap(app)(scope, receive, send))
    request = observations[0].request
    assert request.url == expected + "?tag=a&tag=b&empty="
    assert request.query_parameters == (("tag", "a"), ("tag", "b"), ("empty", ""))
    assert request.headers[-2:] == (("x-test", "one"), ("x-test", "two"))
    assert request.body is None
    assert observations[-1].phase == HttpRequestPhase.COMPLETED


def test_route_installation_restores_hooks_after_failure(settings, tracer, observations):
    async def endpoint(request):
        return PlainTextResponse("done")

    route = Route("/items/{name}", endpoint)
    original = route.app
    app = Starlette(routes=[route])
    with pytest.raises(ValueError, match="server stopped"):
        with instrument_http(app, settings) as wrapped:
            assert route.app is not original
            asyncio.run(invoke(wrapped))
            raise ValueError("server stopped")
    assert route.app is original
    observations.clear()
    asyncio.run(invoke(app))
    assert observations == []
    with instrument_http(app, settings) as wrapped:
        asyncio.run(invoke(wrapped))
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert route.app is original


def test_route_installation_rejects_duplicate_without_disturbing_owner(settings, tracer, observations):
    async def endpoint(request):
        return PlainTextResponse("done")

    app = Starlette(routes=[Route("/items/{name}", endpoint)])
    with instrument_http(app, settings) as wrapped:
        with pytest.raises(RuntimeError, match="already installed"):
            with instrument_http(app, settings):
                pytest.fail("duplicate installation succeeded")
        asyncio.run(invoke(wrapped))
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1
