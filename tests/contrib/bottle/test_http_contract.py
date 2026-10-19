from io import BytesIO
import threading

import bottle
import pytest
import webtest

from ddtrace import config
from ddtrace._trace.subscribers.http_server import _CONVENTIONS
from ddtrace._trace.subscribers.http_server import _REQUEST_CONTEXTS
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.contrib._events.http import HttpServerRequestEvent
from ddtrace.contrib.internal.bottle.trace import TracePlugin
from ddtrace.internal import core
from tests.contrib.http_contract import HttpContractTests
from tests.contrib.http_contract import block_response
from tests.contrib.http_contract import observe_http_requests as observe_http_requests
from tests.contrib.http_contract import register_http_block_checker as register_http_block_checker


@pytest.fixture
def app(tracer):
    application = bottle.Bottle()
    application.install(TracePlugin(service="bottle-contract"))
    return application


def request(app, path="/items/one", start_response=None, **environ):
    request_environ = webtest.TestRequest.blank(path).environ
    request_environ.update(environ)
    return app(request_environ, start_response or (lambda status, headers, exc_info=None: None))


def consume(body):
    try:
        return b"".join(body)
    finally:
        body.close()


def test_contract_sets_span_resource_and_status(app, test_spans):
    @app.route("/items/<name>")
    def handler(name):
        return bottle.HTTPResponse("done", status=201)

    assert consume(request(app, "/items/one?tag=a&tag=b&empty=")) == b"done"
    (span,) = test_spans.pop()
    assert span.resource == "GET /items/<name>"
    assert span.get_tag("http.status_code") == "201"


def test_response_headers_are_copied_before_wsgi_delivery(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        result = bottle.HTTPResponse("done")
        result.add_header("Set-Cookie", "a=1")
        result.add_header("Set-Cookie", "b=2")
        return result

    def start_response(status, headers, exc_info):
        headers.append(("X-Later", "not-in-observation"))

    consume(request(app, start_response=start_response))
    ready = next(observation for observation in observations if observation.phase == HttpRequestPhase.RESPONSE_READY)
    assert [value for name, value in ready.response.headers if name.lower() == "set-cookie"] == ["a=1", "b=2"]
    assert ("X-Later", "not-in-observation") not in ready.response.headers


def test_request_body_is_not_consumed(app, observations):
    class UnreadableInput(BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("the HTTP contract must not consume the request body")

        def readline(self, *args, **kwargs):
            raise AssertionError("the HTTP contract must not consume the request body")

    @app.route("/items/<name>", method="POST")
    def handler(name):
        return "done"

    consume(request(app, REQUEST_METHOD="POST", CONTENT_LENGTH="6", **{"wsgi.input": UnreadableInput(b"secret")}))
    assert all(observation.request.body is None for observation in observations)


def test_stream_observations_end_on_close(app, observations):
    produced = []

    @app.route("/items/<name>")
    def handler(name):
        for value in (b"one", b"two"):
            produced.append(value)
            yield value

    body = request(app)
    assert produced == [b"one"]  # Bottle peeks at the first chunk to choose response encoding.
    assert observations[-1].phase == HttpRequestPhase.RESPONSE_COMMITTED
    assert all(observation.response is None or observation.response.body is None for observation in observations)
    assert next(body) == b"one"
    body.close()
    body.close()
    assert produced == [b"one"]
    assert [observation.phase for observation in observations].count(HttpRequestPhase.COMPLETED) == 1


def test_stream_failure_preserves_committed_response(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        yield b"one"
        raise ValueError("stream failed")

    body = request(app)
    before_error = observations[-1]
    assert next(body) == b"one"
    with pytest.raises(ValueError, match="stream failed"):
        next(body)
    body.close()
    completed = observations[-1]
    assert before_error.errors == ()
    assert completed.phase == HttpRequestPhase.COMPLETED
    assert completed.response_committed
    assert completed.response.status_code == 200
    assert completed.errors[0].type == "builtins.ValueError"
    assert completed.errors[0].message == "stream failed"
    assert not hasattr(completed.errors[0], "__traceback__")


def test_start_response_failure_is_not_committed(app, observations):
    app.catchall = False

    @app.route("/items/<name>")
    def handler(name):
        return "done"

    def start_response(status, headers, exc_info):
        raise OSError("connection closed")

    with pytest.raises(OSError, match="connection closed"):
        request(app, start_response=start_response)
    assert HttpRequestPhase.RESPONSE_COMMITTED not in [observation.phase for observation in observations]
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert not observations[-1].response_committed
    assert observations[-1].response.status_code == 200  # Prepared response, never accepted.
    assert observations[-1].errors[0].type == "builtins.OSError"


def test_interleaved_requests_have_distinct_tokens(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        yield name.encode()
        yield b"done"

    first = request(app, "/items/one")
    first_token = observations[0].request_id
    second = request(app, "/items/two")
    second_token = observations[-1].request_id
    assert first_token is not second_token
    first.close()
    assert observations[-1].request_id is first_token
    second.close()
    assert observations[-1].request_id is second_token


@pytest.mark.parametrize(
    "phase",
    [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.ROUTE_RESOLVED,
        HttpRequestPhase.BEFORE_HANDLER,
        HttpRequestPhase.RESPONSE_READY,
    ],
)
def test_block_sets_span_resource_and_status(app, block_checker, test_spans, phase):
    calls = []

    @app.route("/items/<name>")
    def handler(name):
        calls.append(name)
        return "original"

    def inspect(event):
        if event.phase == phase:
            raise block_response()

    block_checker(inspect)
    assert consume(request(app)) == b"blocked"
    assert calls == (["one"] if phase == HttpRequestPhase.RESPONSE_READY else [])
    (span,) = test_spans.pop()
    assert span.get_tag("http.status_code") == "403"
    assert span.error == 0
    assert span.resource == ("GET 403" if phase == HttpRequestPhase.REQUEST_STARTED else "GET /items/<name>")


def test_checks_stop_before_commit_and_retained_events_have_no_control(app, block_checker):
    retained = []

    @app.route("/items/<name>")
    def handler(name):
        yield b"first"
        yield b"second"

    block_checker(retained.append)
    body = request(app)
    assert next(body) == b"first"
    assert [event.phase for event in retained] == list(HttpRequestPhase)[:4]
    assert all(not hasattr(event, "submit") for event in retained)
    assert consume(body) == b"second"
    assert len(retained) == 4


def test_stream_replacement_closes_original(app, observations, block_checker):
    progress = []

    @app.route("/items/<name>")
    def handler(name):
        try:
            progress.append("first")
            yield b"first"
            progress.append("second")
            yield b"second"
        finally:
            progress.append("closed")

    def check(event):
        if event.phase == HttpRequestPhase.RESPONSE_READY:
            raise block_response()

    block_checker(check)
    assert consume(request(app)) == b"blocked"
    assert progress == ["first", "closed"]
    assert observations[-1].response.status_code == 403
    assert sum(s.phase == HttpRequestPhase.COMPLETED for s in observations) == 1


@pytest.mark.parametrize(
    "phase", [HttpRequestPhase.ROUTE_RESOLVED, HttpRequestPhase.BEFORE_HANDLER, HttpRequestPhase.RESPONSE_READY]
)
def test_failed_replacement_does_not_leak_into_framework_retry(app, block_checker, observations, phase):
    statuses = []

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.phase == phase:
            raise block_response()

    def start_response(status, headers, exc_info):
        statuses.append(status)
        if len(statuses) == 1:
            raise OSError("first response rejected")

    block_checker(inspect)
    body = consume(request(app, start_response=start_response))
    assert body != b"blocked"
    assert b"Critical error" in body
    assert [status.split()[0] for status in statuses] == ["403", "500"]
    assert observations[-1].response.status_code == 500
    assert observations[-1].response_committed


def test_replacement_close_failure_completes_request(app, block_checker, observations):
    @app.route("/items/<name>")
    def handler(name):
        try:
            yield b"first"
            yield b"second"
        finally:
            raise RuntimeError("close failed")

    def inspect(event):
        if event.phase == HttpRequestPhase.RESPONSE_READY:
            raise block_response()

    block_checker(inspect)
    with pytest.raises(RuntimeError, match="close failed"):
        request(app)
    assert observations[-1].phase == HttpRequestPhase.COMPLETED
    assert observations[-1].response.status_code == 403
    assert observations[-1].response_committed
    assert observations[-1].errors[0].message == "close failed"
    assert sum(s.phase == HttpRequestPhase.COMPLETED for s in observations) == 1


def _start_event(request_id, framework="bottle"):
    return HttpServerRequestEvent(
        service="bottle-contract",
        component=config.bottle.integration_name,
        request_id=request_id,
        framework=framework,
        request_headers={},
        integration_config=config.bottle,
        activate_distributed_headers=False,
    )


def _route_resolved(request_id, route="/items/<name>"):
    return HttpRouteResolvedEvent(
        request_id=request_id,
        framework="bottle",
        phase=HttpRequestPhase.ROUTE_RESOLVED,
        request=HttpRequestData(
            method="GET",
            url="http://localhost" + route,
            route=route,
            route_pattern=route,
        ),
    )


def test_events_reach_their_span_from_another_thread(tracer, test_spans):
    # Correlation is by request_id, so a consumer may dispatch from a thread that
    # never entered the request context. A framework that reports a response from
    # somewhere other than the code that opened the context depends on this.
    request_id = object()
    with core.context_with_event(_start_event(request_id)) as ctx:
        span = ctx.get_item("_inner_span")
        assert span is not None

        worker = threading.Thread(target=lambda: core.dispatch_event(_route_resolved(request_id)))
        worker.start()
        worker.join()

        assert span.resource == "GET /items/<name>"


def test_event_for_another_request_leaves_the_active_span_alone(tracer, test_spans):
    # An unrecognized request_id must tag nothing, rather than falling back to
    # whichever span happens to be active.
    with core.context_with_event(_start_event(object())) as ctx:
        span = ctx.get_item("_inner_span")
        assert span is not None
        resource = span.resource

        core.dispatch_event(_route_resolved(object(), route="/other/<name>"))

        assert span.resource == resource


def test_unported_framework_is_not_traced(tracer, test_spans):
    # _CONVENTIONS membership is how an integration opts in. A framework that is absent
    # gets no span rather than one assembled from guessed conventions, so a half-ported
    # integration cannot emit misnamed spans just by dispatching the shared event.
    assert "not-a-ported-framework" not in _CONVENTIONS
    event = _start_event(object(), framework="not-a-ported-framework")

    with core.context_with_event(event) as ctx:
        assert ctx.get_item("_inner_span") is None
        assert event.request_id not in _REQUEST_CONTEXTS

    assert test_spans.get_spans() == []


def test_finished_requests_leave_no_correlation_entry(app, tracer, test_spans):
    # Correlation state is keyed per request, so a request that ends -- cleanly or not --
    # must take its entry with it.
    @app.route("/items/<name>")
    def handler(name):
        return "done"

    @app.route("/boom")
    def boom():
        raise Exception("oh no")

    # Other tests may hold entries for streams they have not closed yet.
    before = len(_REQUEST_CONTEXTS)

    assert consume(request(app)) == b"done"
    assert len(_REQUEST_CONTEXTS) == before

    consume(request(app, "/boom"))
    assert len(_REQUEST_CONTEXTS) == before


class BottleContractAdapter:
    route_pattern = "/items/<name>"

    def __init__(self, app):
        self.app = app

    def route(self):
        return self.app.route(self.route_pattern)

    def response(self, body, status=200):
        return bottle.HTTPResponse(body, status=status)

    def request(self, path="/items/one", method="GET"):
        return consume(request(self.app, path, REQUEST_METHOD=method))


@pytest.fixture
def http_adapter(app):
    return BottleContractAdapter(app)


class TestBottleHttpContract(HttpContractTests):
    pass


@pytest.mark.parametrize("phase", [HttpRequestPhase.ROUTE_RESOLVED, HttpRequestPhase.BEFORE_HANDLER])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("hook_action", ["replace", "mutate"])
def test_accepted_block_survives_after_request(app, observations, block_checker, phase, method, hook_action):
    checks = []
    delivered = []

    @app.route("/items/<name>")
    def handler(name):
        pytest.fail("blocked handler was invoked")

    @app.hook("after_request")
    def overwrite_response():
        if hook_action == "replace":
            raise bottle.HTTPResponse("hook response", status=200, headers={"X-Hook": "yes"})
        bottle.response.status = 200
        bottle.response.set_header("Content-Length", "999")
        bottle.response.set_header("X-Hook", "yes")

    def check(event):
        checks.append(event.phase)
        if event.phase == phase:
            raise block_response()

    def start_response(status, headers, exc_info):
        delivered.append((status, dict(headers)))

    block_checker(check)
    assert consume(request(app, start_response=start_response, REQUEST_METHOD=method)) == (
        b"" if method == "HEAD" else b"blocked"
    )
    assert delivered == [("403 Forbidden", {"Content-Type": "text/plain", "Content-Length": "7"})]
    assert checks == list(HttpRequestPhase)[: list(HttpRequestPhase).index(phase) + 1]
    assert observations[-1].response.status_code == 403
    assert not observations[-1].handler_started
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1


@pytest.mark.parametrize("skip", [True, "trace", TracePlugin, "instance", None])
@pytest.mark.parametrize("tracing_enabled", [True, False])
@pytest.mark.parametrize("blocked", [True, False])
def test_handler_checks_ignore_trace_plugin_skips(
    app, observations, block_checker, tracer, monkeypatch, skip, tracing_enabled, blocked
):
    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    if skip == "instance":
        skip = next(plugin for plugin in app.plugins if isinstance(plugin, TracePlugin))
    calls = []
    checks = []

    @app.route("/items/<name>", skip=skip)
    def handler(name):
        calls.append(name)
        assert bottle.request.route is app.routes[0]
        return "done"

    def check(event):
        checks.append(event.phase)
        if event.phase == HttpRequestPhase.BEFORE_HANDLER and blocked:
            raise block_response()

    block_checker(check)
    # Resetting a route rebuilds the plugin chain and must retain the outer check.
    for reset in [False, True]:
        if reset:
            app.routes[0].reset()
        observations.clear()
        checks.clear()
        calls.clear()
        assert consume(request(app)) == (b"blocked" if blocked else b"done")
        assert calls == ([] if blocked else ["one"])
        assert checks.count(HttpRequestPhase.BEFORE_HANDLER) == 1
        assert sum(event.phase == HttpRequestPhase.BEFORE_HANDLER for event in observations) == 1
        assert observations[-1].handler_started is (not blocked)
        assert observations[-1].response.status_code == (403 if blocked else 200)


@pytest.mark.parametrize("phase", [HttpRequestPhase.ROUTE_RESOLVED, HttpRequestPhase.BEFORE_HANDLER])
def test_early_block_closes_hook_replacement_stream(app, observations, block_checker, phase):
    progress = []

    @app.route("/items/<name>")
    def handler(name):
        pytest.fail("blocked handler was invoked")

    def stream():
        try:
            progress.append("first")
            yield b"first"
            progress.append("second")
            yield b"second"
        finally:
            progress.append("closed")

    @app.hook("after_request")
    def replace_response():
        raise bottle.HTTPResponse(stream(), status=200)

    def check(event):
        if event.phase == phase:
            raise block_response()

    block_checker(check)
    assert consume(request(app)) == b"blocked"
    assert progress == ["first", "closed"]
    assert observations[-1].response.status_code == 403
    assert sum(event.phase == HttpRequestPhase.COMPLETED for event in observations) == 1
