"""Exercise the shared driver without a Bottle app or Bottle response objects."""

from dataclasses import dataclass
from http import HTTPStatus

import pytest

from ddtrace import config
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_wsgi import WsgiLifecycleDriver
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.contrib.http_contract import block_response
from tests.contrib.http_contract import observe_http_requests as observe_http_requests
from tests.contrib.http_contract import register_http_block_checker as register_http_block_checker


@dataclass
class Response:
    status_code: int
    headerlist: list[tuple[str, str]]
    body: bytes

    @property
    def status(self):
        return f"{self.status_code} {HTTPStatus(self.status_code).phrase}"


class Hooks:
    def request_data(self, environ):
        return HttpRequestData(method="GET", url="http://localhost/items/one", headers=())

    def blocking_response(self, block):
        return Response(block.status_code, list(block.headers) + [("Content-Length", str(len(block.body)))], block.body)


@pytest.fixture
def driver():
    settings = IntegrationConfig(config, "test_wsgi")
    return WsgiLifecycleDriver(
        hooks=Hooks(), get_config=lambda: HttpLifecycleConfig("test_wsgi", "test-service", settings)
    )


@pytest.mark.parametrize("blocked", [False, True])
def test_shared_driver_preserves_early_blocks_and_completes_streams(driver, observations, block_checker, blocked):
    closed = []
    delivered = []

    def inspect(event):
        if blocked and event.phase == HttpRequestPhase.ROUTE_RESOLVED:
            raise block_response()

    block_checker(inspect)

    class Body:
        def __iter__(self):
            return iter([b"original"])

        def close(self):
            closed.append(True)

    def application(environ, start_response):
        lifecycle = driver.current(environ)
        block = lifecycle.route_resolved("/items/{name}", "/items/{name}")
        if block is None:
            assert lifecycle.before_handler() is None
        # Model a framework hook overriding an early blocking response.
        start_response("200 OK", [("X-Original", "yes")])
        return Body()

    environ = {}
    body = driver.wrap(application)(environ, lambda status, headers, exc_info: delivered.append((status, headers)))
    assert driver.current(environ) is None
    assert all(event.phase != HttpRequestPhase.COMPLETED for event in observations)
    try:
        assert b"".join(body) == (b"blocked" if blocked else b"original")
    finally:
        body.close()
        body.close()
    assert closed == [True]
    assert delivered[0][0].startswith("403" if blocked else "200")
    assert all(event.framework == "test_wsgi" for event in observations)
    assert len({id(event.request_id) for event in observations}) == 1
    assert observations[-1].response_committed
    assert observations[-1].handler_started is (not blocked)
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1


def test_shared_driver_finishes_failed_stream_once(driver, observations):
    def application(environ, start_response):
        start_response("200 OK", [])

        def body():
            yield b"first"
            raise ValueError("stream failure")

        return body()

    body = driver.wrap(application)({}, lambda *args: None)
    assert next(body) == b"first"
    with pytest.raises(ValueError, match="stream failure"):
        next(body)
    body.close()
    assert observations[-1].errors[0].type == "builtins.ValueError"
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1


def test_shared_driver_bypasses_uninstalled_integration(observations):
    driver = WsgiLifecycleDriver(hooks=Hooks(), get_config=lambda: None)
    result = [b"original"]
    assert driver.wrap(lambda *args: result)({}, lambda *args: None) is result
    assert observations == []
