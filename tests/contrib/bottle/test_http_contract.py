"""Shared subscriber tests; Bottle uses WebFrameworkRequestEvent directly."""

import gc
import threading
import weakref

import pytest

from ddtrace import config
from ddtrace._trace.subscribers.web_framework import _CONVENTIONS
from ddtrace._trace.subscribers.web_framework import _REQUEST_CONTEXTS
from ddtrace.contrib._events.http import HttpRequestCompletedEvent
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib._events.http import HttpRouteResolvedEvent
from ddtrace.contrib._events.http import HttpServerRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core


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


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("tracing_enabled", [False, True])
def test_framework_without_conventions_uses_generic_tracing(tracer, test_spans, monkeypatch, routed, tracing_enabled):
    monkeypatch.setattr(tracer, "enabled", tracing_enabled)
    framework = "generic-framework"
    assert framework not in _CONVENTIONS
    event = _start_event(object(), framework=framework)
    request_data = HttpRequestData(method="GET", url="http://localhost/items/one")

    with core.context_with_event(event) as ctx:
        span = ctx.get_item("_inner_span")
        assert (span is not None) is tracing_enabled
        assert (event.request_id in _REQUEST_CONTEXTS) is tracing_enabled
        if routed:
            route = _route_resolved(event.request_id)
            route.framework = framework
            request_data = route.request
            core.dispatch_event(route)
        core.dispatch_event(
            HttpRequestCompletedEvent(
                request_id=event.request_id,
                framework=framework,
                phase=HttpRequestPhase.COMPLETED,
                request=request_data,
                response=HttpResponseData(status_code=201 if routed else 404),
                response_committed=True,
            )
        )
        if tracing_enabled:
            assert span.resource == ("GET /items/<name>" if routed else "GET 404")
            assert span.get_tag("http.method") == "GET"
            assert span.get_tag("http.status_code") == ("201" if routed else "404")
            assert span.get_tag("http.route") == ("/items/<name>" if routed else None)
            assert span.span_type == "web"

    assert event.request_id not in _REQUEST_CONTEXTS
    spans = test_spans.get_spans()
    assert len(spans) == int(tracing_enabled)
    if tracing_enabled:
        assert spans[0] is span
        assert span.finished


def test_correlation_registry_does_not_retain_context_without_end_event(tracer, test_spans):
    event = _start_event(object())
    ctx = core.context_with_event(event, dispatch_end_event=False)
    span = None
    try:
        with ctx:
            span = ctx.get_item("_inner_span")
            assert _REQUEST_CONTEXTS[event.request_id] is ctx
            context_ref = weakref.ref(ctx)
            gc.collect()
            assert context_ref() is ctx

        # Simulate a producer that releases its context without dispatching completion.
        del ctx
        gc.collect()
        assert context_ref() is None
        assert event.request_id not in _REQUEST_CONTEXTS
    finally:
        _REQUEST_CONTEXTS.pop(event.request_id, None)
        if span is not None:
            span.finish()


@pytest.mark.parametrize("resource", [None, "custom-resource"])
def test_legacy_web_event_keeps_metadata_and_span_ownership(tracer, test_spans, resource):
    event = WebFrameworkRequestEvent(
        component="bottle",
        integration_config=config.bottle,
        service="legacy-web",
        http_operation="bottle.request",
        request_method="GET",
        request_url="http://localhost/legacy",
        request_headers={},
        headers_case_sensitive=True,
        allow_default_resource=True,
        resource=resource,
        trace_query_string=True,
        query="key=value",
        additional_tags={"legacy.tag": "kept"},
    )
    request_id = object()
    with core.context_with_event(event) as legacy_ctx:
        legacy_span = legacy_ctx.get_item("_inner_span")
        assert legacy_span is not None
        assert all(ctx is not legacy_ctx for ctx in _REQUEST_CONTEXTS.values())
        with core.context_with_event(_start_event(request_id)) as http_ctx:
            http_span = http_ctx.get_item("_inner_span")
            assert http_span is not legacy_span
            core.dispatch_event(_route_resolved(request_id))
            assert http_span.resource == "GET /items/<name>"
        assert request_id not in _REQUEST_CONTEXTS
        assert not legacy_span.finished
        event.response_status_code = 202
        event.request_route = "/legacy"

    spans = test_spans.pop()
    assert len(spans) == 2
    assert legacy_span.finished
    assert http_span.finished
    assert http_span.parent_id == legacy_span.span_id
    assert legacy_span.resource == (resource or "GET 202")
    assert legacy_span.get_tag("http.status_code") == "202"
    assert legacy_span.get_tag("http.route") == "/legacy"
    assert legacy_span.get_tag("http.query.string") == "key=value"
    assert legacy_span.get_tag("legacy.tag") == "kept"
