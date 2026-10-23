"""Bottle supplies metadata while the shared request API owns tracing execution."""

from unittest.mock import Mock

import bottle
import pytest
import webtest

from ddtrace.contrib.internal.bottle import trace
from ddtrace.contrib.internal.bottle.patch import TracePlugin
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


def test_disabled_tracing_does_not_read_bottle_metadata(app, tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)

    class UnreadableMetadata:
        def __getattr__(self, name):
            pytest.fail("disabled plugin extracted metadata")

    monkeypatch.setattr(trace, "request", UnreadableMetadata())
    monkeypatch.setattr(trace, "response", UnreadableMetadata())

    @app.route("/hello")
    def hello():
        return "hello"

    assert webtest.TestApp(app).get("/hello").text == "hello"
    assert test_spans.pop() == []


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
