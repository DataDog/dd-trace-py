#!/usr/bin/env python3
import pytest

from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes


@pytest.fixture(
    params=[
        {"appsec_enabled": True, "appsec_standalone_enabled": True},
        {"appsec_enabled": True, "appsec_standalone_enabled": False},
        {"appsec_enabled": False, "appsec_standalone_enabled": False},
        {"appsec_enabled": False, "appsec_standalone_enabled": True},
        {"appsec_enabled": True},
        {"appsec_enabled": False},
    ]
)
def tracer_appsec_standalone(request, tracer):
    tracer.configure(api_version="v0.4", **request.param)
    yield tracer, request.param
    # Reset tracer configuration
    tracer.configure(api_version="v0.4", appsec_enabled=False, appsec_standalone_enabled=False)


def test_appsec_standalone_apm_enabled_metric(tracer_appsec_standalone):
    tracer, args = tracer_appsec_standalone
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    if args == {"appsec_enabled": True, "appsec_standalone_enabled": True}:
        assert span.get_metric("_dd.apm.enabled") == 0.0
    else:
        assert span.get_metric("_dd.apm.enabled") is None
