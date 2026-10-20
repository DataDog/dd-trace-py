"""Independent ASGI proof using Falcon's native resource middleware hook."""

import asyncio
from urllib.parse import urlsplit

import falcon.asgi
import pytest

from ddtrace import config
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.http_asgi import AsgiLifecycleDriver
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.contrib.http_contract import HttpContractTests
from tests.contrib.http_contract import observe_http_requests as observe_http_requests
from tests.contrib.http_contract import register_http_block_checker as register_http_block_checker
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


@pytest.fixture
def tracer():
    with scoped_tracer() as tracer:
        yield tracer


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


class RouteHook:
    def __init__(self, driver):
        self.driver = driver

    async def process_resource(self, req, resp, resource, params):
        await self.driver.before_handler(req.scope, route=req.uri_template, route_pattern=req.uri_template)


class Adapter:
    route_pattern = "/items/{name}"

    def __init__(self):
        settings = HttpLifecycleConfig("falcon", "falcon-proof", IntegrationConfig(config, "falcon"))
        self.driver = AsgiLifecycleDriver(get_config=lambda: settings)
        self.app = falcon.asgi.App(middleware=[RouteHook(self.driver)])

    def route(self):
        def register(handler):
            class Resource:
                async def on_get(self, req, resp, name):
                    result = handler(name)
                    resp.text, resp.status = result if isinstance(result, tuple) else (result, 200)

                on_head = on_get

            self.app.add_route(self.route_pattern, Resource())
            return handler

        return register

    def response(self, body, status=200):
        return body, status

    def request(self, path="/items/one", method="GET"):
        url = urlsplit(path)
        scope = dict(
            type="http",
            asgi={"version": "3.0", "spec_version": "2.4"},
            http_version="1.1",
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
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.driver.wrap(self.app)(scope, receive, send))
        finally:
            loop.close()
        return b"".join(message.get("body", b"") for message in messages)


@pytest.fixture
def http_adapter(tracer):
    return Adapter()


class TestFalconAsgiContract(HttpContractTests):
    pass


def test_falcon_route_attribution(http_adapter, observations, test_spans):
    @http_adapter.route()
    def handler(name):
        return "done"

    assert http_adapter.request() == b"done"
    (span,) = test_spans.pop()
    assert span.resource == "GET /items/{name}"
    assert span.get_tag("http.route") == "/items/{name}"
    assert span.finished
    assert observations[-1].phase == HttpRequestPhase.COMPLETED


def test_falcon_handled_exception(http_adapter, observations):
    @http_adapter.route()
    def handler(name):
        raise RuntimeError("endpoint failed")

    http_adapter.request()
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert observations[-1].response.status_code == 500


def test_falcon_streaming(http_adapter, observations):
    class Resource:
        async def on_get(self, req, resp):
            async def stream():
                yield b"first"
                assert observations[-1].phase == HttpRequestPhase.RESPONSE_COMMITTED
                yield b"last"

            resp.stream = stream()

    http_adapter.app.add_route("/stream", Resource())
    assert http_adapter.request("/stream") == b"firstlast"
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert observations[-1].request.route == "/stream"


def test_falcon_middleware_short_circuit(http_adapter, observations):
    class ShortCircuit:
        async def process_request(self, req, resp):
            resp.text = "middleware"
            resp.complete = True

    http_adapter.app.add_middleware(ShortCircuit())

    @http_adapter.route()
    def handler(name):
        pytest.fail("short-circuited handler was invoked")

    assert http_adapter.request() == b"middleware"
    assert [event.phase for event in observations] == [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.RESPONSE_READY,
        HttpRequestPhase.RESPONSE_COMMITTED,
        HttpRequestPhase.COMPLETED,
    ]
