import json

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


def traced_function(tracer):
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        import hashlib

        m = hashlib.new("md5")
        m.update(b"Nobody inspects")
        m.update(b" the spammish repetition")
        num_vulnerabilities = 10
        for _ in range(0, num_vulnerabilities):
            m.digest()
    return span


@pytest.mark.skip_iast_check_logs
def test_appsec_iast_processor(iast_context_defaults):
    """
    test_appsec_iast_processor.
    This test throws  'finished span not connected to a trace' log error
    """
    tracer = DummyTracer(iast_enabled=True)

    span = traced_function(tracer)
    tracer._on_span_finish(span)

    span_report = get_iast_reporter()
    result = span.get_tag(IAST.JSON)

    assert len(json.loads(result)["vulnerabilities"]) == 1
    assert len(span_report.vulnerabilities) == 1


@pytest.mark.parametrize("sampling_rate", ["0.0", "0.5", "1.0"])
def test_appsec_iast_processor_ensure_span_is_manual_keep(iast_context_defaults, sampling_rate):
    """
    test_appsec_iast_processor_ensure_span_is_manual_keep.
    This test throws  'finished span not connected to a trace' log error
    """
    with override_env({"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":%s]"}]' % (sampling_rate,)}):
        oce.reconfigure()
        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        result = span.get_tag(IAST.JSON)
        assert len(json.loads(result)["vulnerabilities"]) == 1
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP


@pytest.mark.skip_iast_check_logs
@pytest.mark.parametrize("sampling_rate", [0.0, "100"])
def test_appsec_iast_processor_ensure_span_is_sampled(iast_context_defaults, sampling_rate):
    """
    test_appsec_iast_processor_ensure_span_is_manual_keep.
    This test throws  'finished span not connected to a trace' log error
    """
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=sampling_rate,
        )
    ):
        oce.reconfigure()
        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        result = span.get_tag(IAST.JSON)
        if sampling_rate == 0.0:
            assert result is None
            assert span.get_metric(_SAMPLING_PRIORITY_KEY) is AUTO_KEEP
            assert span.get_metric(IAST.ENABLED) == 0.0
        else:
            assert len(json.loads(result)["vulnerabilities"]) == 1
            assert span.get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP
            assert span.get_metric(IAST.ENABLED) == 1.0
