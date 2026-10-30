"""Bottle supplies request metadata to the shared tracing subscriber."""

from unittest.mock import Mock

import bottle
import pytest
import webtest

from ddtrace._trace.subscribers import _base as tracing_subscribers
from ddtrace._trace.subscribers import web_framework as web_subscribers
from ddtrace.contrib.internal.bottle import trace
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal import core
from tests.utils import assert_span_http_status_code


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


def test_disabled_tracing_publishes_request_metadata(app, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)
    observations = []
    calls = []
    start_span = Mock(wraps=tracer.start_span)
    extract_headers = Mock(wraps=tracing_subscribers.trace_utils.activate_distributed_headers)
    set_http_meta = Mock(wraps=web_subscribers.trace_utils.set_http_meta)
    monkeypatch.setattr(tracer, "start_span", start_span)
    monkeypatch.setattr(tracing_subscribers.trace_utils, "activate_distributed_headers", extract_headers)
    monkeypatch.setattr(web_subscribers.trace_utils, "set_http_meta", set_http_meta)

    def on_started(ctx):
        event = ctx.event
        observations.append(("started", event.request_method, event.request_route, event.query))

    def on_ended(ctx, exc_info):
        event = ctx.event
        observations.append(("ended", event.response_status_code, event.response_headers.get("X-Response")))

    @app.route("/hello/<name>")
    def hello(name):
        calls.append(name)
        assert tracer.current_span() is None
        return bottle.HTTPResponse("hello", status=201, headers={"X-Response": "observed"})

    core.on("context.started.bottle.request", on_started)
    core.on("context.ended.bottle.request", on_ended)
    try:
        response = webtest.TestApp(app).get("/hello/world?key=value")
    finally:
        core.reset_listeners("context.started.bottle.request", on_started)
        core.reset_listeners("context.ended.bottle.request", on_ended)

    assert response.status_int == 201
    assert response.text == "hello"
    start_span.assert_not_called()
    extract_headers.assert_not_called()
    set_http_meta.assert_not_called()
    assert calls == ["world"]
    assert observations == [
        ("started", "GET", "/hello/<name>", "key=value"),
        ("ended", 201, "observed"),
    ]
    assert test_spans.pop() == []
    assert tracer.current_span() is None


@pytest.mark.parametrize("stage", ["_prepare_request", "_complete_request"])
def test_metadata_hook_failure_keeps_bottle_callback_result(app, tracer, test_spans, monkeypatch, stage):
    hook = Mock(side_effect=ValueError("metadata failed"))
    monkeypatch.setattr(trace, stage, hook)
    calls = []

    @app.route("/hello")
    def hello():
        calls.append("hello")
        return bottle.HTTPResponse("body", status=201)

    response = webtest.TestApp(app).get("/hello")
    assert response.status_int == 201
    assert response.text == "body"
    assert calls == ["hello"]
    hook.assert_called_once()
    spans = test_spans.pop()
    assert len(spans) == int(stage == "_complete_request")
    if spans:
        assert spans[0].finished
        assert spans[0].error == 0
    assert tracer.current_span() is None


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
