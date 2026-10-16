from dataclasses import FrozenInstanceError
from dataclasses import fields
from io import BytesIO
from typing import Any
from typing import cast

import bottle
import pytest
import webtest

from ddtrace import config
from ddtrace.contrib._events.http import HTTP_REQUEST_DATA_EVENTS
from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestBlockCheckEvent
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.bottle.trace import TracePlugin
from ddtrace.internal import core


@pytest.fixture
def app(tracer):
    application = bottle.Bottle()
    application.install(TracePlugin(service="bottle-contract"))
    return application


@pytest.fixture
def observations():
    observed = []

    def observe(event):
        observed.append(event)

    names = [event.event_name for event in HTTP_REQUEST_DATA_EVENTS.values()]
    for name in names:
        core.on(name, observe)
    try:
        yield observed
    finally:
        for name in names:
            core.reset_listeners(name, observe)


def request(app, path="/items/one", start_response=None, **environ):
    request_environ = webtest.TestRequest.blank(path).environ
    request_environ.update(environ)
    return app(request_environ, start_response or (lambda status, headers, exc_info=None: None))


def consume(body):
    try:
        return b"".join(body)
    finally:
        body.close()


def test_phase_order_and_field_availability(app, observations, test_spans):
    @app.route("/items/<name>")
    def handler(name):
        assert observations[-1].phase == HttpRequestPhase.BEFORE_HANDLER
        assert not observations[-1].handler_started
        return bottle.HTTPResponse("done", status=201)

    assert consume(request(app, "/items/one?tag=a&tag=b&empty=")) == b"done"
    assert [observation.phase for observation in observations] == list(HttpRequestPhase)
    first, route, before, ready, committed, completed = observations
    assert all(observation.request_id is first.request_id for observation in observations)
    assert first.request.route is None
    assert first.response is None
    assert first.request.method == "GET"
    assert first.request.query_string == "tag=a&tag=b&empty="
    assert first.request.query_parameters == (("tag", "a"), ("tag", "b"), ("empty", ""))
    assert first.request.body is None
    assert route.request.route == "/items/<name>"
    assert not route.handler_started
    assert ready.handler_started
    assert ready.response.status_code == 201
    assert not ready.response_committed
    assert committed.response_committed
    assert completed.response_committed
    assert completed.errors == ()
    (span,) = test_spans.pop()
    assert span.resource == "GET /items/<name>"
    assert span.get_tag("http.status_code") == "201"
    assert not {"resource", "service", "integration_config", "operation_name"} & {f.name for f in fields(first)}


def test_retained_observations_keep_frozen_values_and_duplicate_headers(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        result = bottle.HTTPResponse("done")
        result.add_header("Set-Cookie", "a=1")
        result.add_header("Set-Cookie", "b=2")
        return result

    def start_response(status, headers, exc_info):
        headers.append(("X-Later", "not-in-observation"))

    consume(request(app, start_response=start_response))
    first = observations[0]
    ready = next(observation for observation in observations if observation.phase == HttpRequestPhase.RESPONSE_READY)
    assert first.request.route is None
    assert first.response is None
    assert [value for name, value in ready.response.headers if name.lower() == "set-cookie"] == ["a=1", "b=2"]
    assert ("X-Later", "not-in-observation") not in ready.response.headers
    with pytest.raises(FrozenInstanceError):
        first.request.method = "DELETE"
    with pytest.raises(FrozenInstanceError):
        ready.response.status_code = 500


def test_empty_query_is_available(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        return "done"

    consume(request(app))
    assert observations[0].request.query_string == ""
    assert observations[0].request.query_parameters == ()
    assert observations[0].request.body is None


def test_data_consumer_without_tracing(app, observations, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)

    @app.route("/items/<name>")
    def handler(name):
        assert tracer.current_span() is None
        return "done"

    assert consume(request(app)) == b"done"
    assert [observation.phase for observation in observations] == list(HttpRequestPhase)
    assert observations[-1].request.route == "/items/<name>"
    assert test_spans.pop() == []


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


def test_route_miss_has_no_handler_phase(app, observations):
    consume(request(app, "/missing"))
    assert [observation.phase for observation in observations] == [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.RESPONSE_READY,
        HttpRequestPhase.RESPONSE_COMMITTED,
        HttpRequestPhase.COMPLETED,
    ]
    assert observations[-1].request.route is None
    assert not observations[-1].handler_started
    assert observations[-1].response.status_code == 404


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


@pytest.fixture
def block_checker():
    listeners = []

    def register(listener):
        listeners.append(listener)
        core.on(HttpRequestBlockCheckEvent.event_name, listener)

    yield register
    for listener in listeners:
        core.reset_listeners(HttpRequestBlockCheckEvent.event_name, listener)


def block_response(body=b"blocked", status=403):
    return HttpRequestBlock(status, (("Content-Type", "text/plain"),), body)


@pytest.mark.parametrize(
    "phase",
    [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.ROUTE_RESOLVED,
        HttpRequestPhase.BEFORE_HANDLER,
        HttpRequestPhase.RESPONSE_READY,
    ],
)
def test_block_at_supported_boundary(app, observations, block_checker, test_spans, phase):
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
    assert observations[-1].response.status_code == 403
    assert observations[-1].errors == ()
    (span,) = test_spans.pop()
    assert span.get_tag("http.status_code") == "403"
    assert span.error == 0
    assert span.resource == ("GET 403" if phase == HttpRequestPhase.REQUEST_STARTED else "GET /items/<name>")


def test_block_check_without_tracing_or_data_listener(app, block_checker, tracer, monkeypatch, test_spans):
    monkeypatch.setattr(tracer, "enabled", False)

    @app.route("/items/<name>")
    def handler(name):
        pytest.fail("blocked handler was invoked")

    def block(event):
        raise block_response()

    block_checker(block)
    assert consume(request(app)) == b"blocked"
    assert test_spans.pop() == []


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
def test_block_check_failure_allows_request(app, block_checker, failure, monkeypatch):
    # The suite enables DD_TESTING_RAISE; exercise production listener isolation.
    monkeypatch.setattr(config, "_raise", False)

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        raise failure("block_check failed")

    block_checker(inspect)
    assert consume(request(app)) == b"original"


@pytest.mark.parametrize(
    "candidate",
    [
        HttpRequestBlock(99),
        HttpRequestBlock(204, body=b"bad"),
        HttpRequestBlock(headers=(("X-Test", "bad\r\n"),)),
        HttpRequestBlock(headers=(("Content-Length", "99"),)),
        HttpRequestBlock(headers=(("Transfer-Encoding", "chunked"),)),
        HttpRequestBlock(body=cast(Any, "not bytes")),
    ],
)
def test_invalid_blocks_allow_request(app, block_checker, candidate):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def check(event):
        if event.phase == HttpRequestPhase.BEFORE_HANDLER:
            raise candidate

    block_checker(check)
    assert consume(request(app)) == b"original"


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


def test_first_block_skips_remaining_checks_and_preserves_observations(app, block_checker, observations):
    calls = []

    def first(event):
        calls.append("first")
        raise block_response()

    def second(event):
        calls.append("second")
        raise block_response(b"second")

    block_checker(first)
    block_checker(second)
    assert consume(request(app)) == b"blocked"
    assert calls == ["first"]
    assert [event.phase for event in observations] == [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.RESPONSE_READY,
        HttpRequestPhase.RESPONSE_COMMITTED,
        HttpRequestPhase.COMPLETED,
    ]


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


@pytest.mark.parametrize("phase", [HttpRequestPhase.REQUEST_STARTED, HttpRequestPhase.RESPONSE_READY])
def test_head_block_has_no_body(app, observations, block_checker, phase):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.phase == phase:
            raise block_response()

    block_checker(inspect)
    assert consume(request(app, REQUEST_METHOD="HEAD")) == b""
    assert observations[-1].response.status_code == 403
    assert dict(observations[-1].response.headers)["Content-Length"] == "7"


def test_listener_failure_does_not_skip_later_blocker(app, block_checker, monkeypatch):
    monkeypatch.setattr(config, "_raise", False)
    calls = []

    def failed(event):
        calls.append("failed")
        raise TimeoutError("consumer failed")

    def following(event):
        calls.append("following")
        raise block_response()

    block_checker(failed)
    block_checker(following)
    assert consume(request(app)) == b"blocked"
    assert calls == ["failed", "following"]


def test_process_control_exception_propagates_and_completes(app, block_checker, observations):
    def check(event):
        raise KeyboardInterrupt()

    block_checker(check)
    with pytest.raises(KeyboardInterrupt):
        request(app)
    assert observations[-1].phase == HttpRequestPhase.COMPLETED


def test_blocked_response_keeps_duplicate_headers(app, block_checker, observations):
    def check(event):
        raise HttpRequestBlock(headers=(("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")), body=b"blocked")

    block_checker(check)
    assert consume(request(app)) == b"blocked"
    assert [v for k, v in observations[-1].response.headers if k.lower() == "set-cookie"] == ["a=1", "b=2"]


def test_failed_replacement_does_not_leak_into_framework_retry(app, block_checker, observations):
    statuses = []

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.phase == HttpRequestPhase.RESPONSE_READY and event.response.status_code == 200:
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


def test_observation_mutation_does_not_change_request_state(app, observations):
    @app.route("/items/<name>")
    def handler(name):
        observations[0].handler_started = True
        observations[0].request = None
        return "done"

    assert consume(request(app)) == b"done"
    assert observations[-1].request.method == "GET"
    assert observations[1].request.route == "/items/<name>"
    assert not observations[1].handler_started
    assert len({id(event) for event in observations}) == len(observations)
