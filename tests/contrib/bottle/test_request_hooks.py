"""Bottle maps framework metadata into the shared request subscriber."""

from unittest.mock import Mock

import bottle
import pytest
import webtest

from ddtrace.contrib.internal.bottle import trace
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal import core
from tests.utils import assert_span_http_status_code
from tests.utils import override_http_config


class Cancelled(BaseException):
    pass


@pytest.fixture
def app(tracer):
    app = bottle.Bottle()
    app.install(TracePlugin())
    return app


@pytest.mark.parametrize(
    "outcome,status", [("return", 201), ("raise", 403), ("abort", 404), ("error", 500), ("local", 202)]
)
def test_bottle_outcomes_reach_shared_tracing_subscriber(app, tracer, test_spans, outcome, status):
    calls = []

    @app.route("/hello/<name>")
    def hello(name):
        calls.append(name)
        assert tracer.current_span() is not None
        if outcome == "error":
            raise ValueError("callback failed")
        if outcome == "abort":
            bottle.abort(status)
        if outcome == "local":
            bottle.response.status = status
            return "body"
        result = bottle.HTTPResponse("body", status=status)
        if outcome == "raise":
            raise result
        return result

    response = webtest.TestApp(app).get("/hello/world", expect_errors=True)
    assert response.status_int == status
    assert calls == ["world"]
    (span,) = test_spans.pop()
    assert span.finished
    assert span.resource == "GET /hello/<name>"
    assert span.get_tag("http.route") == "/hello/<name>"
    assert_span_http_status_code(span, status)
    if outcome == "error":
        assert span.get_tag("error.message") == "callback failed"
    assert tracer.current_span() is None


@pytest.mark.parametrize("raised", [False, True])
def test_http_response_headers_reach_request_subscriber(app, tracer, test_spans, raised):
    status = 403 if raised else 201

    @app.route("/response")
    def handler():
        result = bottle.HTTPResponse("body", status=status, headers={"X-Response": "from-response"})
        if raised:
            raise result
        return result

    with override_http_config("bottle", {"trace_headers": ["x-response"]}):
        response = webtest.TestApp(app).get("/response", expect_errors=True)

    assert response.status_int == status
    assert response.headers["X-Response"] == "from-response"
    (span,) = test_spans.pop()
    assert span.get_tag("http.response.headers.x-response") == "from-response"
    assert_span_http_status_code(span, status)
    assert span.finished


def test_disabled_tracing_bypasses_request_subscriber(app, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)
    started = Mock()
    ended = Mock()
    start_span = Mock(wraps=tracer.start_span)
    monkeypatch.setattr(tracer, "start_span", start_span)

    @app.route("/hello")
    def hello():
        assert tracer.current_span() is None
        return "hello"

    core.on("context.started.web.request", started)
    core.on("context.ended.web.request", ended)
    try:
        response = webtest.TestApp(app).get("/hello")
    finally:
        core.reset_listeners("context.started.web.request", started)
        core.reset_listeners("context.ended.web.request", ended)

    assert response.text == "hello"
    started.assert_not_called()
    ended.assert_not_called()
    start_span.assert_not_called()
    assert test_spans.pop() == []


def test_span_finishes_before_after_request_hook(app, tracer, test_spans):
    captured = []

    @app.route("/hello")
    def hello():
        captured.append(tracer.current_span())
        return "body"

    @app.hook("after_request")
    def after():
        assert captured[0].finished
        assert tracer.current_span() is None
        bottle.response.status = 202

    assert webtest.TestApp(app).get("/hello").status_int == 202
    (span,) = test_spans.pop()
    assert_span_http_status_code(span, 200)


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_tracing_toggle_during_request_preserves_entry_decision(app, tracer, monkeypatch, initially_enabled):
    monkeypatch.setattr(tracer, "enabled", initially_enabled)
    spans = []

    @app.route("/toggle")
    def toggle():
        spans.append(tracer.current_span())
        tracer.enabled = not tracer.enabled
        return "ok"

    client = webtest.TestApp(app)
    assert client.get("/toggle").text == "ok"
    assert client.get("/toggle").text == "ok"
    for enabled, span in zip((initially_enabled, not initially_enabled), spans):
        if enabled:
            assert span is not None
            assert span.finished
        else:
            assert span is None
    assert tracer.current_span() is None


@pytest.mark.parametrize("failure", [ValueError("callback failed"), Cancelled("cancelled")])
def test_callback_exception_identity_and_parent_are_preserved(app, tracer, test_spans, failure):
    app.catchall = False
    completions = []

    @app.route("/failure")
    def fail():
        raise failure

    def ended(ctx, exc_info):
        completions.append((ctx.identifier, exc_info[1]))

    core.on("context.ended.web.request", ended)
    try:
        with tracer.trace("parent") as parent:
            with pytest.raises(type(failure)) as caught:
                webtest.TestApp(app).get("/failure")
            assert caught.value is failure
            assert caught.value.__context__ is None
            assert tracer.current_span() is parent
            assert not parent.finished
    finally:
        core.reset_listeners("context.ended.web.request", ended)

    assert completions == [("web.request", failure)]
    spans = test_spans.pop()
    assert len(spans) == 2
    request_span = next(span for span in spans if span is not parent)
    assert request_span.finished
    assert request_span.error == 1
    assert request_span.get_tag("error.message") == str(failure)
    assert tracer.current_span() is None


def test_success_inside_an_exception_handler_is_not_an_error(app, tracer, test_spans):
    @app.route("/success")
    def success():
        return "ok"

    try:
        raise ValueError("unrelated exception")
    except ValueError:
        assert webtest.TestApp(app).get("/success").text == "ok"

    (span,) = test_spans.pop()
    assert span.error == 0
    assert_span_http_status_code(span, 200)


def test_disabled_tracing_keeps_active_parent_with_incoming_headers(app, tracer, monkeypatch):
    @app.route("/hello")
    def hello():
        assert tracer.current_span() is parent
        assert tracer.context_provider.active() is parent
        return "hello"

    with tracer.trace("parent") as parent:
        monkeypatch.setattr(tracer, "enabled", False)
        start_span = Mock(wraps=tracer.start_span)
        monkeypatch.setattr(tracer, "start_span", start_span)
        result = webtest.TestApp(app).get("/hello", headers={"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"})
        assert result.text == "hello"
        start_span.assert_not_called()
        assert tracer.context_provider.active() is parent
        assert not parent.finished
    assert tracer.current_span() is None


@pytest.mark.parametrize("callback_fails", [False, True])
def test_response_metadata_failure_preserves_callback_outcome(app, tracer, test_spans, monkeypatch, callback_fails):
    class BrokenResponse:
        status_code = 200

        @property
        def headers(self):
            raise RuntimeError("response metadata failed")

    monkeypatch.setattr(trace, "response", BrokenResponse())
    app.catchall = False
    failure = ValueError("callback failed")

    @app.route("/metadata")
    def callback():
        if callback_fails:
            raise failure
        return "ok"

    with tracer.trace("parent") as parent:
        if callback_fails:
            with pytest.raises(ValueError) as caught:
                webtest.TestApp(app).get("/metadata")
            assert caught.value is failure
            assert caught.value.__context__ is None
        else:
            result = webtest.TestApp(app).get("/metadata")
            assert result.status_int == 200
            assert result.text == "ok"
        assert tracer.current_span() is parent
        assert not parent.finished

    spans = test_spans.pop()
    assert len(spans) == 2
    request_span = next(span for span in spans if span is not parent)
    assert request_span.finished
    assert request_span.parent_id == parent.span_id
    assert request_span.error == int(callback_fails)
    assert_span_http_status_code(request_span, 500 if callback_fails else 200)
    assert request_span.get_tag("error.message") == (str(failure) if callback_fails else None)
    assert tracer.current_span() is None


def test_callback_error_status_does_not_read_thread_local_status(app, test_spans, monkeypatch):
    class BrokenStatusResponse:
        headers = {}

        @property
        def status_code(self):
            raise RuntimeError("response status unavailable")

    monkeypatch.setattr(trace, "response", BrokenStatusResponse())
    app.catchall = False
    failure = ValueError("callback failed")

    @app.route("/failure")
    def fail():
        raise failure

    with pytest.raises(ValueError) as caught:
        webtest.TestApp(app).get("/failure")

    assert caught.value is failure
    assert caught.value.__context__ is None
    (span,) = test_spans.pop()
    assert span.finished
    assert span.error == 1
    assert_span_http_status_code(span, 500)
    assert span.get_tag("error.message") == str(failure)
