#!/usr/bin/env python3

from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes


def test_asm_standalone_enabled(tracer):
    tracer.configure(api_version="v0.4", appsec_enabled=True, appsec_standalone_enabled=True)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.get_metric("_dd.apm.enabled") == 0.0
