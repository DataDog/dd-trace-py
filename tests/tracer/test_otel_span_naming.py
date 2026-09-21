from unittest import mock

import pytest

from ddtrace._trace.http_semantics import normalize_http_method
from ddtrace._trace.otel_http_naming import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel_http_naming import RESOURCE_SET_BY_USER
from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import tracer


_INTEGRATION_RESOURCE = "composed by the integration"
_OTEL_SUBPROCESS_ENV = {
    "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
    "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": None,
}


def _integration_config():
    return IntegrationConfig(config, "test")


def _client_name(method=None, route=None, resource=_INTEGRATION_RESOURCE, span_hook=None):
    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with tracer.start_span("http.request", span_type=SpanTypes.HTTP, activate=False) as span:
            if resource is not None:
                span.resource = resource
                span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
            span._set_attribute(SPAN_KIND, "client")
            if span_hook is not None:
                span_hook(span)
            trace_utils.set_http_meta(span, _integration_config(), method=method, route=route)
            return span.resource


def test_client_is_bare_method_without_url_template():
    assert _client_name(method="GET") == "GET"


def test_client_does_not_take_http_route_as_its_target():
    assert _client_name(method="GET", route="/users/<int:id>") == "GET"


def test_client_unaccepted_method_uses_http_resource():
    assert _client_name(method="FROBNICATE") == "HTTP"


def test_client_raw_method_never_leaks_into_the_name():
    assert _client_name(method="get") == "GET"


def test_client_user_set_resource_is_preserved():
    def claim(span):
        span._set_ctx_item(RESOURCE_SET_BY_USER, True)

    assert _client_name(method="GET", span_hook=claim) == _INTEGRATION_RESOURCE


def test_client_resource_replaced_by_user_code_is_detected_and_kept():
    def replace(span):
        span.resource = "my custom name"

    assert _client_name(method="GET", resource=None, span_hook=replace) == "my custom name"


def test_client_span_without_a_method_is_left_alone():
    assert _client_name(method=None) == _INTEGRATION_RESOURCE


def test_unaccepted_client_method_is_normalized_at_start():
    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        with tracer.start_span("http.request", span_type=SpanTypes.HTTP, activate=False) as span:
            span._set_attribute(SPAN_KIND, "client")
            normalized, original = normalize_http_method("PROPFIND")
            set_otel_http_resource(span, normalized, original)
            at_sampling = span.resource
            trace_utils.set_http_meta(span, _integration_config(), method="PROPFIND")
            assert at_sampling == "HTTP"
            assert span.resource == at_sampling


def _client_event(method="GET"):
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent

    return HttpClientRequestEvent(
        http_operation="requests.request",
        service="svc",
        component="requests",
        resource="{} /actual/path/42".format(method),
        integration_config=config.requests,
        request_method=method,
        request_headers={},
        query="",
        request_url="http://host/actual/path/42",
    )


def _emit_client_span_through_the_subscriber():
    from ddtrace.internal import core
    from ddtrace.internal.span_bus import span_from_context

    with core.context_with_event(_client_event()) as ctx:
        span = span_from_context(ctx)
    return span


@pytest.mark.subprocess(env=_OTEL_SUBPROCESS_ENV, err=None)
def test_event_supplied_resource_is_replaced_not_treated_as_user_owned():
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.span_bus import span_from_context

    for method, expected in (("GET", "GET"), ("PROPFIND", "HTTP"), ("get", "GET")):
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="requests.request",
                service="svc",
                component="requests",
                resource="{} /actual/path/42".format(method),
                integration_config=config.requests,
                request_method=method,
                request_headers={},
                query="",
                request_url="http://host/actual/path/42",
            ),
        ) as ctx:
            span = span_from_context(ctx)
            assert span.resource == expected
            assert "/actual/path/42" not in span.resource


@pytest.mark.subprocess(env=_OTEL_SUBPROCESS_ENV, err=None)
def test_a_user_replacing_the_resource_after_start_still_wins():
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent
    from ddtrace.contrib.internal import trace_utils
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.span_bus import span_from_context

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="requests.request",
            service="svc",
            component="requests",
            resource="GET /actual/path/42",
            integration_config=config.requests,
            request_method="GET",
            request_headers={},
            query="",
            request_url="http://host/actual/path/42",
        ),
    ) as ctx:
        span = span_from_context(ctx)
        assert span.resource == "GET"
        span.resource = "my custom name"
        trace_utils.set_http_meta(span, config.requests, method="GET")
        assert span.resource == "my custom name"


@pytest.mark.subprocess(env=_OTEL_SUBPROCESS_ENV, err=None)
def test_span_start_processor_resource_is_preserved():
    from ddtrace._trace.processor import SpanProcessor
    from ddtrace.contrib._events.http_client import HttpClientRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.span_bus import span_from_context

    class SetResourceAtStart(SpanProcessor):
        def on_span_start(self, span):
            span.resource = "processor-owned"

        def on_span_finish(self, span):
            pass

    processor = SetResourceAtStart()
    processor.register()
    try:
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="requests.request",
                service="svc",
                component="requests",
                resource="GET /actual/path/42",
                integration_config=config.requests,
                request_method="GET",
                request_headers={},
                query="",
                request_url="http://host/actual/path/42",
            ),
        ) as ctx:
            span = span_from_context(ctx)
            assert span.resource == "processor-owned"
        assert span.resource == "processor-owned"
    finally:
        processor.unregister()


@pytest.mark.subprocess(
    env={
        **_OTEL_SUBPROCESS_ENV,
        "DD_TRACE_SAMPLING_RULES": '[{"resource": "GET", "sample_rate": 0.0}]',
    },
    err=None,
)
def test_a_sampling_rule_matches_the_otel_resource():
    from ddtrace.constants import USER_REJECT
    from tests.tracer.test_otel_span_naming import _emit_client_span_through_the_subscriber

    span = _emit_client_span_through_the_subscriber()
    assert span.resource == "GET"
    assert span.context.sampling_priority == USER_REJECT


@pytest.mark.subprocess(
    env={
        **_OTEL_SUBPROCESS_ENV,
        "DD_TRACE_SAMPLING_RULES": '[{"resource": "GET /actual/path/42", "sample_rate": 0.0}]',
    },
    err=None,
)
def test_a_sampling_rule_does_not_match_the_pre_rename_resource():
    from ddtrace.constants import AUTO_KEEP
    from tests.tracer.test_otel_span_naming import _emit_client_span_through_the_subscriber

    span = _emit_client_span_through_the_subscriber()
    assert span.context.sampling_priority == AUTO_KEEP
