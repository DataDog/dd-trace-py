from unittest import mock

from ddtrace._trace.http_tags import set_client_address_tags
from ddtrace._trace.http_tags import set_query_string_tag
from ddtrace._trace.otel.http import tags
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace.ext import http
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.trace import Span
from ddtrace.trace import tracer
from tests.utils import override_global_config


def test_set_method_tag_removes_stale_original_method():
    span = Span("web.request")

    set_method_tag(span, "custom")
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) == "custom"

    set_method_tag(span, "GET")

    assert span.get_tag(otel_http.REQUEST_METHOD) == "GET"
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) is None


def test_set_query_string_tag_uses_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_query_string_tag(datadog_span, "token=secret")

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with mock.patch.object(tags, "_obfuscated_query", return_value="token=redacted"):
            with tracer.trace("web.request") as otel_span:
                set_query_string_tag(otel_span, "token=secret")

    assert datadog_span.get_tag(http.QUERY_STRING) == "token=secret"
    assert otel_span.get_tag(otel_http.URL_QUERY) == "token=redacted"


def test_standalone_client_address_tags_use_active_semantics():
    with override_global_config(dict(_otel_trace_semantics_enabled=False)):
        with tracer.trace("web.request") as datadog_span:
            set_client_address_tags(datadog_span, "192.0.2.1")

    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as otel_span:
            set_client_address_tags(otel_span, "192.0.2.2")

    assert datadog_span.get_tag(http.CLIENT_IP) == "192.0.2.1"
    # Without a peer address there is no network.client.ip; the client IP may come from a header.
    assert datadog_span.get_tag("network.client.ip") is None
    assert otel_span.get_tag(otel_http.CLIENT_ADDRESS) == "192.0.2.2"
    assert otel_span.get_tag(otel_http.NETWORK_PEER_ADDRESS) is None


def test_client_address_tags_include_the_peer_address():
    with override_global_config(dict(_otel_trace_semantics_enabled=True)):
        with tracer.trace("web.request") as span:
            set_client_address_tags(span, "203.0.113.10", "10.0.0.5")

    assert span.get_tag(otel_http.CLIENT_ADDRESS) == "203.0.113.10"
    assert span.get_tag(otel_http.NETWORK_PEER_ADDRESS) == "10.0.0.5"
