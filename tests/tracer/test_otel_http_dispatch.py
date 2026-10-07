from unittest import mock

from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config
from ddtrace.trace import Span


def test_set_method_tag_removes_stale_original_method():
    span = Span("web.request")

    with mock.patch.object(config, "_otel_trace_semantics_enabled", True):
        set_method_tag(span, "custom")
        assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) == "custom"

        set_method_tag(span, "GET")

    assert span.get_tag(otel_http.REQUEST_METHOD) == "GET"
    assert span.get_tag(otel_http.REQUEST_METHOD_ORIGINAL) is None
