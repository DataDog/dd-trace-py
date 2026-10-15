from dataclasses import FrozenInstanceError
from dataclasses import fields
from io import BytesIO
from threading import Thread
from typing import Any
from typing import cast

import bottle
import pytest
import webtest

from ddtrace import config
from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpDecisionAcceptance
from ddtrace.contrib._events.http import HttpEnforcementAction
from ddtrace.contrib._events.http import HttpInspectionDecision
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestInspectionEvent
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib.internal.bottle.trace import TracePlugin
from ddtrace.internal import core


@pytest.fixture
def app(tracer):
    application = bottle.Bottle()
    application.install(TracePlugin(service="bottle-contract"))
    return application


@pytest.fixture
def snapshots():
    observed = []

    def observe(event):
        observed.append(event.snapshot)

    core.on(HttpRequestDataEvent.event_name, observe)
    try:
        yield observed
    finally:
        core.reset_listeners(HttpRequestDataEvent.event_name, observe)


def request(app, path="/items/one", start_response=None, **environ):
    request_environ = webtest.TestRequest.blank(path).environ
    request_environ.update(environ)
    return app(request_environ, start_response or (lambda status, headers, exc_info=None: None))


def consume(body):
    try:
        return b"".join(body)
    finally:
        body.close()


def test_phase_order_and_field_availability(app, snapshots, test_spans):
    @app.route("/items/<name>")
    def handler(name):
        assert snapshots[-1].phase == HttpRequestPhase.BEFORE_HANDLER
        assert not snapshots[-1].handler_started
        return bottle.HTTPResponse("done", status=201)

    assert consume(request(app, "/items/one?tag=a&tag=b&empty=")) == b"done"
    assert [snapshot.phase for snapshot in snapshots] == list(HttpRequestPhase)
    first, route, before, ready, committed, completed = snapshots
    assert all(snapshot.request_id is first.request_id for snapshot in snapshots)
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
    assert before.supported_actions == frozenset((HttpEnforcementAction.RESPOND,))
    assert committed.supported_actions == frozenset((HttpEnforcementAction.TERMINATE,))
    assert completed.supported_actions == frozenset()
    (span,) = test_spans.pop()
    assert span.resource == "GET /items/<name>"
    assert span.get_tag("http.status_code") == "201"
    assert not {"resource", "service", "integration_config", "operation_name"} & {f.name for f in fields(first)}


def test_retained_snapshots_are_immutable_and_keep_duplicate_headers(app, snapshots):
    @app.route("/items/<name>")
    def handler(name):
        result = bottle.HTTPResponse("done")
        result.add_header("Set-Cookie", "a=1")
        result.add_header("Set-Cookie", "b=2")
        return result

    def start_response(status, headers, exc_info):
        headers.append(("X-Later", "not-in-snapshot"))

    consume(request(app, start_response=start_response))
    first = snapshots[0]
    ready = next(snapshot for snapshot in snapshots if snapshot.phase == HttpRequestPhase.RESPONSE_READY)
    assert first.request.route is None
    assert first.response is None
    assert [value for name, value in ready.response.headers if name.lower() == "set-cookie"] == ["a=1", "b=2"]
    assert ("X-Later", "not-in-snapshot") not in ready.response.headers
    with pytest.raises(FrozenInstanceError):
        first.handler_started = True
    with pytest.raises(FrozenInstanceError):
        first.request.method = "DELETE"
    with pytest.raises(FrozenInstanceError):
        ready.response.status_code = 500


def test_empty_query_is_available(app, snapshots):
    @app.route("/items/<name>")
    def handler(name):
        return "done"

    consume(request(app))
    assert snapshots[0].request.query_string == ""
    assert snapshots[0].request.query_parameters == ()
    assert snapshots[0].request.body is None


def test_data_consumer_without_tracing(app, snapshots, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)

    @app.route("/items/<name>")
    def handler(name):
        assert tracer.current_span() is None
        return "done"

    assert consume(request(app)) == b"done"
    assert [snapshot.phase for snapshot in snapshots] == list(HttpRequestPhase)
    assert snapshots[-1].request.route == "/items/<name>"
    assert test_spans.pop() == []


def test_request_body_is_not_consumed(app, snapshots):
    class UnreadableInput(BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("the HTTP contract must not consume the request body")

        def readline(self, *args, **kwargs):
            raise AssertionError("the HTTP contract must not consume the request body")

    @app.route("/items/<name>", method="POST")
    def handler(name):
        return "done"

    consume(request(app, REQUEST_METHOD="POST", CONTENT_LENGTH="6", **{"wsgi.input": UnreadableInput(b"secret")}))
    assert all(snapshot.request.body is None for snapshot in snapshots)


def test_stream_snapshots_end_on_close(app, snapshots):
    produced = []

    @app.route("/items/<name>")
    def handler(name):
        for value in (b"one", b"two"):
            produced.append(value)
            yield value

    body = request(app)
    assert produced == [b"one"]  # Bottle peeks at the first chunk to choose response encoding.
    assert snapshots[-1].phase == HttpRequestPhase.RESPONSE_COMMITTED
    assert all(snapshot.response is None or snapshot.response.body is None for snapshot in snapshots)
    assert next(body) == b"one"
    body.close()
    body.close()
    assert produced == [b"one"]
    assert [snapshot.phase for snapshot in snapshots].count(HttpRequestPhase.COMPLETED) == 1


def test_stream_failure_preserves_committed_response(app, snapshots):
    @app.route("/items/<name>")
    def handler(name):
        yield b"one"
        raise ValueError("stream failed")

    body = request(app)
    before_error = snapshots[-1]
    assert next(body) == b"one"
    with pytest.raises(ValueError, match="stream failed"):
        next(body)
    body.close()
    completed = snapshots[-1]
    assert before_error.errors == ()
    assert completed.phase == HttpRequestPhase.COMPLETED
    assert completed.response_committed
    assert completed.response.status_code == 200
    assert completed.errors[0].type == "builtins.ValueError"
    assert completed.errors[0].message == "stream failed"
    assert not hasattr(completed.errors[0], "__traceback__")


def test_route_miss_has_no_handler_phase(app, snapshots):
    consume(request(app, "/missing"))
    assert [snapshot.phase for snapshot in snapshots] == [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.RESPONSE_READY,
        HttpRequestPhase.RESPONSE_COMMITTED,
        HttpRequestPhase.COMPLETED,
    ]
    assert snapshots[-1].request.route is None
    assert not snapshots[-1].handler_started
    assert snapshots[-1].response.status_code == 404


def test_start_response_failure_is_not_committed(app, snapshots):
    app.catchall = False

    @app.route("/items/<name>")
    def handler(name):
        return "done"

    def start_response(status, headers, exc_info):
        raise OSError("connection closed")

    with pytest.raises(OSError, match="connection closed"):
        request(app, start_response=start_response)
    assert HttpRequestPhase.RESPONSE_COMMITTED not in [snapshot.phase for snapshot in snapshots]
    assert snapshots[-1].phase == HttpRequestPhase.COMPLETED
    assert not snapshots[-1].response_committed
    assert snapshots[-1].response.status_code == 200  # Prepared response, never accepted.
    assert snapshots[-1].errors[0].type == "builtins.OSError"


def test_interleaved_requests_have_distinct_tokens(app, snapshots):
    @app.route("/items/<name>")
    def handler(name):
        yield name.encode()
        yield b"done"

    first = request(app, "/items/one")
    first_token = snapshots[0].request_id
    second = request(app, "/items/two")
    second_token = snapshots[-1].request_id
    assert first_token is not second_token
    first.close()
    assert snapshots[-1].request_id is first_token
    second.close()
    assert snapshots[-1].request_id is second_token


@pytest.fixture
def inspector():
    listeners = []

    def register(listener):
        listeners.append(listener)
        core.on(HttpRequestInspectionEvent.event_name, listener)

    yield register
    for listener in listeners:
        core.reset_listeners(HttpRequestInspectionEvent.event_name, listener)


def block_response(body=b"blocked", status=403):
    return HttpInspectionDecision(
        HttpEnforcementAction.RESPOND,
        HttpBlockingResponse(status, (("Content-Type", "text/plain"),), body),
    )


@pytest.mark.parametrize(
    "phase",
    [
        HttpRequestPhase.REQUEST_STARTED,
        HttpRequestPhase.ROUTE_RESOLVED,
        HttpRequestPhase.BEFORE_HANDLER,
        HttpRequestPhase.RESPONSE_READY,
    ],
)
def test_block_at_supported_boundary(app, snapshots, inspector, test_spans, phase):
    calls = []

    @app.route("/items/<name>")
    def handler(name):
        calls.append(name)
        return "original"

    def inspect(event):
        if event.snapshot.phase == phase:
            assert event.submit(block_response()) == HttpDecisionAcceptance.ACCEPTED

    inspector(inspect)
    assert consume(request(app)) == b"blocked"
    assert calls == (["one"] if phase == HttpRequestPhase.RESPONSE_READY else [])
    assert snapshots[-1].response.status_code == 403
    assert snapshots[-1].errors == ()
    (span,) = test_spans.pop()
    assert span.get_tag("http.status_code") == "403"
    assert span.error == 0
    assert span.resource == ("GET 403" if phase == HttpRequestPhase.REQUEST_STARTED else "GET /items/<name>")


def test_inspection_without_tracing_or_data_listener(app, inspector, tracer, monkeypatch, test_spans):
    monkeypatch.setattr(tracer, "enabled", False)

    @app.route("/items/<name>")
    def handler(name):
        pytest.fail("blocked handler was invoked")

    inspector(lambda event: event.submit(block_response()))
    assert consume(request(app)) == b"blocked"
    assert test_spans.pop() == []


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
def test_inspection_failure_allows_request(app, inspector, failure, monkeypatch):
    # The suite enables DD_TESTING_RAISE; exercise production listener isolation.
    monkeypatch.setattr(config, "_raise", False)

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        raise failure("inspection failed")

    inspector(inspect)
    assert consume(request(app)) == b"original"


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        HttpInspectionDecision(cast(Any, "respond"), HttpBlockingResponse()),
        HttpInspectionDecision(HttpEnforcementAction.RESPOND),
        HttpInspectionDecision(response=HttpBlockingResponse()),
        HttpInspectionDecision(HttpEnforcementAction.RESPOND, HttpBlockingResponse(99)),
        HttpInspectionDecision(HttpEnforcementAction.RESPOND, HttpBlockingResponse(204, body=b"bad")),
        HttpInspectionDecision(HttpEnforcementAction.RESPOND, HttpBlockingResponse(headers=(("X-Test", "bad\r\n"),))),
        HttpInspectionDecision(
            HttpEnforcementAction.RESPOND, HttpBlockingResponse(headers=(("Content-Length", "99"),))
        ),
        HttpInspectionDecision(
            HttpEnforcementAction.RESPOND, HttpBlockingResponse(headers=(("Transfer-Encoding", "chunked"),))
        ),
        HttpInspectionDecision(HttpEnforcementAction.RESPOND, HttpBlockingResponse(body=cast(Any, "not bytes"))),
    ],
)
def test_invalid_decisions_allow_request(app, inspector, candidate):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.snapshot.phase == HttpRequestPhase.BEFORE_HANDLER:
            assert event.submit(candidate) == HttpDecisionAcceptance.INVALID

    inspector(inspect)
    assert consume(request(app)) == b"original"


def test_late_decision_cannot_change_stream(app, inspector):
    retained = []

    @app.route("/items/<name>")
    def handler(name):
        yield b"first"
        yield b"second"

    inspector(retained.append)
    body = request(app)
    assert next(body) == b"first"
    for event in retained:
        assert event.submit(block_response()) == HttpDecisionAcceptance.CLOSED
    assert consume(body) == b"second"


def test_first_block_wins_and_allow_cannot_override_it(app, inspector):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.snapshot.phase == HttpRequestPhase.BEFORE_HANDLER:
            assert event.submit(block_response()) == HttpDecisionAcceptance.ACCEPTED
            assert event.submit(HttpInspectionDecision()) == HttpDecisionAcceptance.ACCEPTED
            assert event.submit(block_response(b"second")) == HttpDecisionAcceptance.ALREADY_DECIDED

    inspector(inspect)
    assert consume(request(app)) == b"blocked"


@pytest.mark.parametrize("terminate", [False, True])
def test_stream_replacement_or_termination_closes_original(app, snapshots, inspector, terminate):
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

    phase = HttpRequestPhase.RESPONSE_COMMITTED if terminate else HttpRequestPhase.RESPONSE_READY

    def inspect(event):
        if event.snapshot.phase == phase:
            decision = HttpInspectionDecision(HttpEnforcementAction.TERMINATE) if terminate else block_response()
            assert event.submit(decision) == HttpDecisionAcceptance.ACCEPTED

    inspector(inspect)
    assert consume(request(app)) == (b"" if terminate else b"blocked")
    assert progress == ["first", "closed"]
    assert snapshots[-1].response.status_code == (200 if terminate else 403)
    assert sum(s.phase == HttpRequestPhase.COMPLETED for s in snapshots) == 1


def test_unsupported_decisions_are_rejected(app, inspector):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        candidate = (
            block_response()
            if event.snapshot.response_committed
            else HttpInspectionDecision(HttpEnforcementAction.TERMINATE)
        )
        assert event.submit(candidate) == HttpDecisionAcceptance.UNSUPPORTED

    inspector(inspect)
    assert consume(request(app)) == b"original"


@pytest.mark.parametrize("phase", [HttpRequestPhase.REQUEST_STARTED, HttpRequestPhase.RESPONSE_READY])
def test_head_block_has_no_body(app, snapshots, inspector, phase):
    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.snapshot.phase == phase:
            event.submit(block_response())

    inspector(inspect)
    assert consume(request(app, REQUEST_METHOD="HEAD")) == b""
    assert snapshots[-1].response.status_code == 403
    assert dict(snapshots[-1].response.headers)["Content-Length"] == "7"


def test_other_thread_cannot_submit_during_inspection(app, inspector):
    results = []

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        worker = Thread(target=lambda: results.append(event.submit(block_response())))
        worker.start()
        worker.join()

    inspector(inspect)
    assert consume(request(app)) == b"original"
    assert results and all(result == HttpDecisionAcceptance.CLOSED for result in results)


def test_failure_does_not_cancel_accepted_block_or_other_listeners(app, inspector, monkeypatch):
    monkeypatch.setattr(config, "_raise", False)
    observed = []

    @app.route("/items/<name>")
    def handler(name):
        pytest.fail("blocked handler invoked")

    def failed(event):
        if event.snapshot.phase == HttpRequestPhase.REQUEST_STARTED:
            observed.append(event.submit(block_response()))
            raise TimeoutError("after decision")

    def following(event):
        if event.snapshot.phase == HttpRequestPhase.REQUEST_STARTED:
            observed.append(event.submit(block_response(b"second")))

    inspector(failed)
    inspector(following)
    assert consume(request(app)) == b"blocked"
    assert observed == [HttpDecisionAcceptance.ACCEPTED, HttpDecisionAcceptance.ALREADY_DECIDED]


def test_process_control_exception_propagates_and_closes_submit(app, inspector, snapshots):
    retained = []

    def inspect(event):
        retained.append(event)
        raise KeyboardInterrupt()

    inspector(inspect)
    with pytest.raises(KeyboardInterrupt):
        request(app)
    assert retained[0].submit(block_response()) == HttpDecisionAcceptance.CLOSED
    assert snapshots[-1].phase == HttpRequestPhase.COMPLETED


def test_blocked_response_keeps_duplicate_headers(app, inspector, snapshots):
    def inspect(event):
        if event.snapshot.phase == HttpRequestPhase.REQUEST_STARTED:
            event.submit(
                HttpInspectionDecision(
                    HttpEnforcementAction.RESPOND,
                    HttpBlockingResponse(
                        headers=(("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")),
                        body=b"blocked",
                    ),
                )
            )

    inspector(inspect)
    assert consume(request(app)) == b"blocked"
    assert [v for k, v in snapshots[-1].response.headers if k.lower() == "set-cookie"] == ["a=1", "b=2"]


def test_failed_replacement_does_not_leak_into_framework_retry(app, inspector, snapshots):
    statuses = []

    @app.route("/items/<name>")
    def handler(name):
        return "original"

    def inspect(event):
        if event.snapshot.phase == HttpRequestPhase.RESPONSE_READY and event.snapshot.response.status_code == 200:
            event.submit(block_response())

    def start_response(status, headers, exc_info):
        statuses.append(status)
        if len(statuses) == 1:
            raise OSError("first response rejected")

    inspector(inspect)
    body = consume(request(app, start_response=start_response))
    assert body != b"blocked"
    assert b"Critical error" in body
    assert [status.split()[0] for status in statuses] == ["403", "500"]
    assert snapshots[-1].response.status_code == 500
    assert snapshots[-1].response_committed


def test_replacement_close_failure_completes_request(app, inspector, snapshots):
    @app.route("/items/<name>")
    def handler(name):
        try:
            yield b"first"
            yield b"second"
        finally:
            raise RuntimeError("close failed")

    def inspect(event):
        if event.snapshot.phase == HttpRequestPhase.RESPONSE_READY:
            event.submit(block_response())

    inspector(inspect)
    with pytest.raises(RuntimeError, match="close failed"):
        request(app)
    assert snapshots[-1].phase == HttpRequestPhase.COMPLETED
    assert snapshots[-1].response.status_code == 403
    assert snapshots[-1].response_committed
    assert snapshots[-1].errors[0].message == "close failed"
    assert sum(s.phase == HttpRequestPhase.COMPLETED for s in snapshots) == 1
