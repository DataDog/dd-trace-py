"""Exercise the shared driver without a Bottle app or Bottle response objects."""

import pytest

from ddtrace import config
from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_wsgi import WsgiLifecycleDriver
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.contrib.http_contract import block_response
from tests.contrib.http_contract import observe_http_requests as observe_http_requests
from tests.contrib.http_contract import register_http_block_checker as register_http_block_checker


def environ_for_request():
    return {
        "REQUEST_METHOD": "GET",
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/items/one",
        "QUERY_STRING": "",
    }


@pytest.fixture
def driver():
    settings = IntegrationConfig(config, "test_wsgi")
    return WsgiLifecycleDriver(get_config=lambda: HttpLifecycleConfig("test_wsgi", "test-service", settings))


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

    environ = environ_for_request()
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

    body = driver.wrap(application)(environ_for_request(), lambda *args: None)
    assert next(body) == b"first"
    with pytest.raises(ValueError, match="stream failure"):
        next(body)
    body.close()
    assert observations[-1].errors[0].type == "builtins.ValueError"
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1


def test_shared_driver_bypasses_uninstalled_integration(observations):
    driver = WsgiLifecycleDriver(get_config=lambda: None)
    result = [b"original"]
    assert driver.wrap(lambda *args: result)({}, lambda *args: None) is result
    assert observations == []


def _settings():
    return HttpLifecycleConfig("test_wsgi", "test-service", IntegrationConfig(config, "test_wsgi"))


def test_shared_driver_bypasses_requests_nothing_would_consume(tracer, monkeypatch):
    # Deliberately no observations/block_checker fixture: registering either one is
    # itself a consumer. The tracing subscriber registers its context listener at
    # import time whether or not tracing is on, so the bypass has to ask the tracer
    # rather than probe that listener.
    monkeypatch.setattr(tracer, "enabled", False)
    result = [b"original"]

    def request_data(environ):
        pytest.fail("a bypassed request must not be extracted")

    driver = WsgiLifecycleDriver(get_config=_settings, request_data=request_data)
    assert driver.wrap(lambda environ, start_response: result)({}, lambda *args: None) is result


def test_shared_driver_instruments_for_an_observer_without_tracing(tracer, monkeypatch, observations, test_spans):
    # The other half of the gate: tracing declines, but a registered consumer still
    # gets the full lifecycle.
    monkeypatch.setattr(tracer, "enabled", False)

    def application(environ, start_response):
        start_response("200 OK", [])
        return [b"done"]

    driver = WsgiLifecycleDriver(get_config=_settings)
    body = driver.wrap(application)(environ_for_request(), lambda *args: None)
    try:
        assert b"".join(body) == b"done"
    finally:
        body.close()
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert test_spans.pop() == []


@pytest.mark.parametrize("host", [None, "api.example:8443"])
def test_default_extraction_needs_no_framework_and_does_not_read_body(driver, observations, host):
    class UnreadableInput:
        def read(self, *args):
            pytest.fail("request extraction consumed the body")

    environ = environ_for_request()
    environ.update(
        {
            "wsgi.url_scheme": "https",
            "SERVER_PORT": "443",
            "SCRIPT_NAME": "/api",
            "PATH_INFO": "/items/a b",
            "QUERY_STRING": "tag=a&tag=b&empty=",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": "6",
            "HTTP_X_REQUEST_ID": "one",
            "wsgi.input": UnreadableInput(),
        }
    )
    if host:
        environ["HTTP_HOST"] = host

    def application(environ, start_response):
        start_response("200 OK", [])
        return [b"done"]

    body = driver.wrap(application)(environ, lambda *args: None)
    try:
        assert b"".join(body) == b"done"
    finally:
        body.close()
    request = observations[0].request
    assert request.url == f"https://{host or 'localhost'}/api/items/a%20b?tag=a&tag=b&empty="
    assert request.query_parameters == (("tag", "a"), ("tag", "b"), ("empty", ""))
    assert request.body is None
    assert dict(request.headers)["Content-Type"] == "application/json"
    assert dict(request.headers)["Content-Length"] == "6"
    assert dict(request.headers)["X-Request-Id"] == "one"
    assert observations[-1].phase == HttpRequestPhase.COMPLETED


@pytest.mark.parametrize("status,method", [(403, "GET"), (403, "HEAD"), (204, "GET"), (499, "GET")])
def test_default_blocking_response_needs_no_framework(driver, observations, block_checker, status, method):
    body_value = b"" if status == 204 else b"blocked"

    def inspect(event):
        raise HttpRequestBlock(status, headers=(("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")), body=body_value)

    block_checker(inspect)
    delivered = []
    environ = environ_for_request()
    environ["REQUEST_METHOD"] = method

    def application(*args):
        pytest.fail("blocked application ran")

    body = driver.wrap(application)(environ, lambda status, headers, exc_info: delivered.append((status, headers)))
    try:
        assert b"".join(body) == (b"" if method == "HEAD" else body_value)
    finally:
        body.close()
    assert delivered[0][0].startswith(str(status) + " ")
    headers = delivered[0][1]
    assert [value for name, value in headers if name == "Set-Cookie"] == ["a=1", "b=2"]
    assert ("Content-Length" in dict(headers)) is (status != 204)
    if status != 204:
        assert dict(headers)["Content-Length"] == str(len(body_value))
    assert observations[-1].response.status_code == status


def test_request_extraction_can_be_overridden_without_hooks_class(observations):
    settings = IntegrationConfig(config, "test_wsgi")
    driver = WsgiLifecycleDriver(
        get_config=lambda: HttpLifecycleConfig("test_wsgi", "test-service", settings),
        request_data=lambda environ: HttpRequestData(method="GET", url="https://custom.example/"),
    )

    def application(environ, start_response):
        start_response("200 OK", [])
        return [b"done"]

    body = driver.wrap(application)({}, lambda *args: None)
    try:
        assert b"".join(body) == b"done"
    finally:
        body.close()
    assert observations[0].request.url == "https://custom.example/"
