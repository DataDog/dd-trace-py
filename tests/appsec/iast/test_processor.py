from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
from threading import Event

import pytest

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import get_hash_object_tracking_len
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.internal.security._constants import STACK_TRACE
from tests.appsec.iast.iast_utils import load_iast_report
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def processor_iast_context(iast_context_defaults, request):
    if getattr(request, "param", False):
        return  # Keep the helper environment to exercise rejected admission.
    # The taint fixture starts analysis on a synthetic span. Let the processor
    # admit and own the actual request under test instead.
    environment = _get_iast_env()
    _iast_finish_request(environment.span)


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


def test_appsec_iast_processor(processor_iast_context, tracer):
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


@pytest.mark.parametrize("sampling_rate", ["0.0", "0.5", "1.0"])
def test_appsec_iast_processor_ensure_span_is_manual_keep(processor_iast_context, sampling_rate, tracer):
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


@pytest.mark.parametrize(
    "sampling_rate,processor_iast_context", [("0.0", True), ("100", False)], indirect=["processor_iast_context"]
)
def test_appsec_iast_processor_ensure_span_is_sampled(processor_iast_context, sampling_rate, tracer):
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


def test_concurrent_web_spans_release_their_native_contexts(processor_iast_context, tracer):
    first_started, second_started, first_finished = Event(), Event(), Event()
    baseline = debug_context_array_free_slots_number()

    def request(first):
        if not first:
            assert first_started.wait(timeout=5)
        with tracer.trace("request", span_type=SpanTypes.WEB):
            if first:
                first_started.set()
                assert second_started.wait(timeout=5)
            else:
                assert debug_context_array_free_slots_number() == baseline - 2
                second_started.set()
                assert first_finished.wait(timeout=5)
        if first:
            first_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request, first) for first in (True, False)]
        for future in futures:
            future.result(timeout=10)
    assert debug_context_array_free_slots_number() == baseline


@pytest.mark.parametrize("metastruct", [False, True])
def test_iast_report_identity_matches_analysis_owner(processor_iast_context, tracer, metastruct):
    with override_global_config(dict(_use_metastruct_for_iast=metastruct)):
        tracer.configure(iast_enabled=True)
        baseline = debug_context_array_free_slots_number()
        with tracer.trace("upstream", service="upstream") as upstream:
            with tracer.trace("entry", service="local", span_type=SpanTypes.WEB) as entry:
                with tracer.trace("request", service="local", span_type=SpanTypes.WEB) as request:
                    child = traced_function(tracer)
                    assert _get_iast_env().span is entry
                assert not entry.finished
            report = load_iast_report(entry)
            assert entry.get_metric(IAST.ENABLED) == 1.0
            vulnerability = report["vulnerabilities"][0]
            assert vulnerability["location"]["spanId"] == entry.span_id
            stacks = entry._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.IAST]
            assert vulnerability["location"]["stackId"] in {stack["id"] for stack in stacks}
            assert entry.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".weak_hash") > 0
            assert upstream.context.sampling_priority == USER_KEEP
            for span in (upstream, request, child):
                assert load_iast_report(span) is None
                assert span.get_metric(IAST.ENABLED) is None
            assert _get_iast_env() is None
            assert debug_context_array_free_slots_number() == baseline


@pytest.mark.parametrize("parent_kind", ["custom", "finished_request", "inferred_proxy"])
def test_independent_requests_keep_separate_reports(processor_iast_context, tracer, parent_kind):
    tracer.configure(iast_enabled=True)
    name = "aws.apigateway" if parent_kind == "inferred_proxy" else "parent"
    span_type = None if parent_kind == "custom" else SpanTypes.WEB
    with tracer.trace(name, service="local", span_type=span_type) as parent:
        with tracer.trace("background", service="local") as background:
            context = copy_context()
            if parent_kind == "finished_request":
                parent.finish()
            requests = [context.run(traced_function, tracer) for _ in range(2)]
            assert all(request.parent_id == background.span_id for request in requests)
            assert all(request._service_entry_span is parent for request in requests)
        assert load_iast_report(parent) is None
        assert parent.get_metric(IAST.ENABLED) == (1.0 if parent_kind == "finished_request" else None)
        for request in requests:
            report = load_iast_report(request)
            assert len(report["vulnerabilities"]) == 1
            assert report["vulnerabilities"][0]["location"]["spanId"] == request.span_id
            stacks = request._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.IAST]
            assert report["vulnerabilities"][0]["location"]["stackId"] in {stack["id"] for stack in stacks}
            assert request.get_metric(IAST.ENABLED) == 1.0


@pytest.mark.parametrize("finish_parent", [False, True])
def test_fresh_analyses_after_entry_admission_failed(processor_iast_context, tracer, finish_parent):
    tracer.configure(iast_enabled=True)
    baseline = debug_context_array_free_slots_number()
    held = [start_request_context() for _ in range(baseline)]
    try:
        with tracer.trace("entry", span_type=SpanTypes.WEB) as entry:
            assert _get_iast_env() is None
            finish_request_context(held.pop())
            for algorithm in ("md5", "sha1"):
                with tracer.trace("request", span_type=SpanTypes.WEB) as child:
                    if finish_parent:
                        entry.finish()
                    hashlib.new(algorithm, b"value").hexdigest()
                report = load_iast_report(child)
                assert [v["evidence"]["value"] for v in report["vulnerabilities"]] == [algorithm]
                assert report["vulnerabilities"][0]["location"]["spanId"] == child.span_id
        assert load_iast_report(entry) is None
    finally:
        for context_id in held:
            finish_request_context(context_id)
    assert _get_iast_env() is None
    assert debug_context_array_free_slots_number() == baseline


@pytest.mark.parametrize(
    "metastruct,method",
    [(True, "build_and_scrub_value_parts"), (False, "build_and_scrub_value_parts"), (False, "_to_str")],
)
def test_iast_reporting_failure_allows_retry(processor_iast_context, tracer, monkeypatch, metastruct, method):
    def fail(*args):
        raise ValueError("reporting failed")

    original = getattr(IastSpanReporter, method)
    with override_global_config(dict(_use_metastruct_for_iast=metastruct, _iast_max_vulnerabilities_per_requests=1)):
        tracer.configure(iast_enabled=True)
        baseline = debug_context_array_free_slots_number()
        # A failed report must allow retry; a successful one still advances the endpoint limit.
        for report_method, reported in ((fail, False), (original, True), (original, False)):
            monkeypatch.setattr(IastSpanReporter, method, report_method)
            with tracer.trace("request", span_type=SpanTypes.WEB) as span:
                hashlib.md5(b"value").hexdigest()
            assert (load_iast_report(span) is not None) is reported
            assert _get_iast_env() is None
            assert debug_context_array_free_slots_number() == baseline
