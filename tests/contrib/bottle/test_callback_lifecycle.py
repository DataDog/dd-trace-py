"""Bottle callback notifications do not require tracing or transport wrappers."""

import bottle
import pytest
import webtest

import ddtrace
from ddtrace.contrib._events.http import HttpBaseEvent
from ddtrace.contrib._events.web_framework_callback import WebFrameworkCallbackCompletedEvent
from ddtrace.contrib._events.web_framework_callback import WebFrameworkCallbackStartedEvent
from ddtrace.contrib.internal.bottle import trace
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal import core


@pytest.fixture
def app(tracer):
    app = bottle.Bottle()
    app.install(TracePlugin())
    return app


@pytest.fixture
def events():
    received = []
    core.on(WebFrameworkCallbackStartedEvent.event_name, received.append)
    core.on(WebFrameworkCallbackCompletedEvent.event_name, received.append)
    try:
        yield received
    finally:
        core.reset_listeners(WebFrameworkCallbackStartedEvent.event_name, received.append)
        core.reset_listeners(WebFrameworkCallbackCompletedEvent.event_name, received.append)


@pytest.mark.parametrize("tracing", [False, True])
def test_callback_notifications_without_changing_span_lifetime(app, events, tracer, test_spans, monkeypatch, tracing):
    monkeypatch.setattr(tracer, "enabled", tracing)
    spans = []

    @app.route("/hello/<name>")
    def hello(name):
        assert len(events) == 1
        spans.append(tracer.current_span())
        return "hello " + name

    assert webtest.TestApp(app).get("/hello/world?key=value").text == "hello world"
    started, completed = events
    assert type(started) is WebFrameworkCallbackStartedEvent
    assert type(completed) is WebFrameworkCallbackCompletedEvent
    assert isinstance(started, HttpBaseEvent)
    assert isinstance(completed, HttpBaseEvent)
    assert started.response_status_code is None
    assert started.response_headers == {}
    assert started.callback_id is completed.callback_id
    assert started.request_route == "/hello/<name>"
    assert started.query == "key=value"
    assert started.request_method == "GET"
    assert completed.response_status_code == 200
    assert completed.error_type is None
    finished = test_spans.pop()
    assert len(finished) == int(tracing)
    if tracing:
        assert spans == finished
        assert finished[0].finished
    else:
        assert spans == [None]


@pytest.mark.parametrize("outcome,status", [("return", 201), ("raise", 403), ("error", 500)])
def test_completion_reports_callback_outcome(app, events, outcome, status):
    @app.route("/hello")
    def hello():
        if outcome == "error":
            raise ValueError("callback failed")
        result = bottle.HTTPResponse("body", status=status)
        result.add_header("Set-Cookie", "a=1")
        result.add_header("Set-Cookie", "b=2")
        if outcome == "raise":
            raise result
        return result

    # An uncaught callback error reaches wsgi.errors, and webtest fails any request
    # that logged there. expect_errors skips that check and the status check, so assert
    # the status directly.
    response = webtest.TestApp(app).get("/hello", expect_errors=True)
    assert response.status_int == status
    assert len(events) == 2
    completed = events[-1]
    assert completed.response_status_code == status
    if outcome == "error":
        assert completed.error_type == "builtins.ValueError"
        assert completed.error_message == "callback failed"
    else:
        assert completed.error_type is None
        assert [v for k, v in completed.response_header_items if k.lower() == "set-cookie"] == ["a=1", "b=2"]


def test_completion_precedes_stream_and_after_request_hooks(app, events, tracer):
    @app.hook("after_request")
    def after():
        assert len(events) == 2
        bottle.response.status = 202

    @app.route("/stream")
    def stream():
        span = tracer.current_span()

        def body():
            assert len(events) == 2
            assert span.finished
            yield b"body"

        return body()

    assert webtest.TestApp(app).get("/stream").status_int == 202
    assert events[-1].response_status_code == 200


def test_nested_callbacks_have_separate_ids_and_complete_in_order(app, events):
    inner = bottle.Bottle()
    inner.install(TracePlugin())

    @inner.route("/inner")
    def inner_handler():
        return "inner"

    @app.route("/outer")
    def outer_handler():
        return webtest.TestApp(inner).get("/inner").text

    assert webtest.TestApp(app).get("/outer").text == "inner"
    outer_start, inner_start, inner_end, outer_end = events
    assert outer_start.callback_id is outer_end.callback_id
    assert inner_start.callback_id is inner_end.callback_id
    assert outer_start.callback_id is not inner_start.callback_id


def test_completion_only_listener_keeps_disabled_tracing_path_active(app, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)
    received = []

    @app.route("/hello")
    def hello():
        return "hello"

    core.on(WebFrameworkCallbackCompletedEvent.event_name, received.append)
    try:
        webtest.TestApp(app).get("/hello")
    finally:
        core.reset_listeners(WebFrameworkCallbackCompletedEvent.event_name, received.append)
    assert len(received) == 1
    assert received[0].response_status_code == 200
    assert test_spans.pop() == []


def test_skip_and_uninstall_follow_bottle_plugin_rules(app, events):
    @app.route("/skipped", skip=["trace"])
    def skipped():
        return "skipped"

    @app.route("/hello")
    def hello():
        return "hello"

    client = webtest.TestApp(app)
    client.get("/skipped")
    client.get("/missing", status=404)
    assert events == []
    client.get("/hello")
    assert len(events) == 2
    app.uninstall("trace")
    client.get("/hello")
    assert len(events) == 2
    app.install(TracePlugin())
    app.reset()
    client.get("/hello")
    assert len(events) == 4


def test_disabled_without_consumers_does_not_extract_request(app, tracer, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)

    class UnreadableRequest:
        def __getattr__(self, name):
            pytest.fail("disabled plugin extracted request metadata")

    monkeypatch.setattr(trace, "request", UnreadableRequest())

    @app.route("/hello")
    def hello():
        return "hello"

    assert webtest.TestApp(app).get("/hello").text == "hello"


def test_completion_runs_for_base_exception(app, events):
    class Cancelled(BaseException):
        pass

    @app.route("/cancel")
    def cancel():
        raise Cancelled("cancelled")

    with pytest.raises(Cancelled):
        webtest.TestApp(app).get("/cancel")
    assert len(events) == 2
    assert events[-1].error_type.endswith("Cancelled")
    assert events[-1].error_message == "cancelled"


def test_retained_metadata_and_envelopes_are_independent(app, events):
    def alter_envelope(event):
        event.request_route = "/changed"
        event.request_headers["X-Example"] = "listener mutation"

    @app.route("/hello")
    def hello():
        assert bottle.request.headers["X-Example"] == "original"
        bottle.request.environ["HTTP_X_EXAMPLE"] = "changed"
        return "hello"

    core.on(WebFrameworkCallbackStartedEvent.event_name, alter_envelope)
    try:
        webtest.TestApp(app).get("/hello", headers={"X-Example": "original"})
    finally:
        core.reset_listeners(WebFrameworkCallbackStartedEvent.event_name, alter_envelope)
    completed = events[-1]
    assert completed.request_route == "/hello"
    assert dict(completed.request_headers)["X-Example"] == "original"


@pytest.mark.parametrize("fail", [False, True])
def test_consumer_owns_setup_and_cleanup_without_tracing(app, tracer, monkeypatch, fail):
    monkeypatch.setattr(tracer, "enabled", False)
    state = {}

    def started(event):
        state[event.callback_id] = event.request_route

    def completed(event):
        assert state.pop(event.callback_id) == event.request_route

    @app.route("/work")
    def work():
        assert list(state.values()) == ["/work"]
        if fail:
            raise ValueError("failure")
        return "done"

    core.on(WebFrameworkCallbackStartedEvent.event_name, started)
    core.on(WebFrameworkCallbackCompletedEvent.event_name, completed)
    try:
        response = webtest.TestApp(app).get("/work", expect_errors=True)
        assert response.status_int == (500 if fail else 200)
        assert state == {}
    finally:
        core.reset_listeners(WebFrameworkCallbackStartedEvent.event_name, started)
        core.reset_listeners(WebFrameworkCallbackCompletedEvent.event_name, completed)


def test_callback_events_do_not_depend_on_tracing():
    """The callback contract must stay importable without pulling in tracing.

    These events exist so a listener that only observes handler invocations can
    subscribe without depending on spans. That guarantee is structural, not a naming
    convention, so walk the module's import closure instead of trusting the header.
    """
    import ast
    import pathlib

    root = pathlib.Path(ddtrace.__file__).parent

    def module_path(name):
        base = root.joinpath(*name.split(".")[1:])
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    seen = set()
    pending = ["ddtrace.contrib._events.web_framework_callback"]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = module_path(name)
        if path is None:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module]
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            else:
                continue
            pending.extend(name for name in imported if name.startswith("ddtrace."))

    assert not [name for name in seen if name.startswith("ddtrace._trace")]
    assert not [name for name in seen if name.startswith("ddtrace.contrib._events.web_framework.")]
    assert "ddtrace.contrib._events.web_framework" not in seen


def test_callback_event_copies_base_http_fields_and_set_response():
    request_headers = {"X-Request": "original"}
    response_headers = {"X-Response": "original"}
    event = WebFrameworkCallbackStartedEvent(
        callback_id=object(),
        framework="bottle",
        request_method="GET",
        request_url="http://localhost/hello",
        request_route="/hello",
        request_headers=request_headers,
        response_headers=response_headers,
        query="",
    )
    request_headers["X-Request"] = "changed"
    response_headers["X-Response"] = "changed"
    assert event.request_headers == {"X-Request": "original"}
    assert event.response_headers == {"X-Response": "original"}

    response = bottle.HTTPResponse("body", status=201, headers={"X-Result": "created"})
    event.set_response(response)
    response.headers["X-Result"] = "changed"
    assert event.response_status_code == 201
    assert event.response_headers["X-Result"] == "created"
    assert dict(event.response_header_items)["X-Result"] == "created"
