import hashlib

import pytest

from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._exploit_prevention.stack_traces import report_stack
from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import get_hash_object_tracking_len
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from tests.appsec.iast.iast_utils import load_iast_report
from tests.utils import override_env
from tests.utils import override_global_config


def traced_function(tracer, sampling_rate="100"):
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        import hashlib

        m = hashlib.new("md5")
        m.update(b"Nobody inspects")
        m.update(b" the spammish repetition")
        num_vulnerabilities = 10
        for _ in range(0, num_vulnerabilities):
            m.digest()
        if sampling_rate == "0.0":
            assert get_hash_object_tracking_len() == 0
        else:
            assert get_hash_object_tracking_len() > 0
    return span


def test_appsec_iast_processor(iast_context_defaults, tracer):
    """
    test_appsec_iast_processor.
    This test throws  'finished span not connected to a trace' log error
    """
    tracer.configure(iast_enabled=True)

    span = traced_function(tracer)
    tracer._on_span_finish(span)
    assert get_hash_object_tracking_len() == 0
    result = load_iast_report(span)
    assert result is not None
    assert len(result["vulnerabilities"]) == 1


@pytest.mark.parametrize("use_metastruct", [False, True])
@pytest.mark.parametrize("end_with_context", [False, True])
def test_iast_reports_stay_on_each_request(iast_context_defaults, tracer, use_metastruct, end_with_context):
    tracer.configure(iast_enabled=True)

    with override_global_config(dict(_use_metastruct_for_iast=use_metastruct)):
        with tracer.trace("parent", service="parent") as parent:
            for algorithm in ("md5", "sha1"):
                with tracer.trace("request", span_type=SpanTypes.WEB, service="web") as span:
                    hashlib.new(algorithm).digest()
                    if end_with_context:
                        with core.context_with_data("test.request", req_span=span) as ctx:
                            _iast_end_request(ctx)

                report = load_iast_report(span)
                assert report is not None
                assert len(report["vulnerabilities"]) == 1
                vulnerability = report["vulnerabilities"][0]
                assert vulnerability["evidence"]["value"] == algorithm
                assert span.get_metric(IAST.ENABLED) == 1.0

            assert load_iast_report(parent) is None
            assert parent.get_metric(IAST.ENABLED) is None


def test_iast_stack_traces_stay_on_each_request(tracer):
    with override_global_config(dict(_iast_enabled=True, _ep_stack_trace_enabled=True)):
        with tracer.trace("parent") as parent:
            for stack_id in ("first-request", "second-request"):
                with tracer.trace("request", span_type=SpanTypes.WEB) as span:
                    assert report_stack(span=span, stack_id=stack_id, namespace=STACK_TRACE.IAST)

                stacks = span._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.IAST]
                assert len(stacks) == 1
                assert stacks[0]["id"] == stack_id

            assert parent._get_struct_tag(STACK_TRACE.TAG) is None


@pytest.mark.parametrize("sampling_rate", ["0.0", "0.5", "1.0"])
def test_appsec_iast_processor_ensure_span_is_manual_keep(iast_context_defaults, sampling_rate, tracer):
    """
    test_appsec_iast_processor_ensure_span_is_manual_keep.
    This test throws  'finished span not connected to a trace' log error
    """
    with override_env({"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":%s]"}]' % (sampling_rate,)}):
        oce.reconfigure()
        tracer.configure(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)
        assert get_hash_object_tracking_len() == 0
        result = load_iast_report(span)
        assert result is not None
        assert len(result["vulnerabilities"]) == 1
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP


@pytest.mark.parametrize("sampling_rate", ["0.0", "100"])
def test_appsec_iast_processor_ensure_span_is_sampled(iast_context_defaults, sampling_rate, tracer):
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
        tracer.configure(iast_enabled=True)
        span = traced_function(tracer, sampling_rate=sampling_rate)
        tracer._on_span_finish(span)

        result = load_iast_report(span)
        if sampling_rate == "0.0":
            assert result is None
            assert span.get_metric(_SAMPLING_PRIORITY_KEY) is AUTO_KEEP
            assert span.get_metric(IAST.ENABLED) == 0.0
        else:
            assert result is not None
            assert len(result["vulnerabilities"]) == 1
            assert span.get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP
            assert span.get_metric(IAST.ENABLED) == 1.0
        assert get_hash_object_tracking_len() == 0
