import time

import pytest

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._constants import TELEMETRY_DEBUG_VERBOSITY
from ddtrace.appsec._constants import TELEMETRY_INFORMATION_NAME
from ddtrace.appsec._constants import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec._constants import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec._iast._handlers import _on_django_patch
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._metrics import metric_verbosity
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_UNTRUSTED_SERIALIZATION
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.appsec._iast.constants import VULN_XSS
from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor
from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
from ddtrace.appsec._iast.taint_sinks.untrusted_serialization import patch as untrusted_serialization_patch
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import patch as unvalidated_redirect_patch
from ddtrace.appsec._iast.taint_sinks.weak_hash import patch as weak_hash_patch
from ddtrace.appsec._iast.taint_sinks.xss import patch as xss_patch
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.utils import asm_context
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def empty_telemetry_session(request):
    """Start every telemetry test with an empty session.

    These tests assert on metric names, counts and exact lists, so a metric emitted by
    whatever ran before is enough to fail them. Draining the native worker first pushes out
    anything it still holds, so clear() actually leaves nothing behind.

    Only for tests that already use the session: requesting it unconditionally would make the
    pure metric_verbosity cases xfail when no test agent is running.
    """
    if "test_agent_session" in request.fixturenames:
        request.getfixturevalue("telemetry_writer").periodic(force_flush=True)
        request.getfixturevalue("test_agent_session").clear()
    yield


def _get_iast_metrics(test_agent_session, telemetry_writer):
    """Flush the native worker and return the iast-namespace generate-metrics series."""
    telemetry_writer.periodic(force_flush=True)
    metrics = []
    for series in test_agent_session.get_metrics():
        if series.get("namespace") == TELEMETRY_NAMESPACE.IAST.value:
            metrics.append(series)
    return metrics


def _wait_for_iast_metrics(test_agent_session, telemetry_writer, select, tries=10, delay=0.1):
    """Keep flushing until select() finds the series the caller is waiting for.

    One force_flush is not enough: the native worker keeps its own flush schedule, so a series can
    reach the test agent several flushes later. Returns (selected, every series seen), the latter
    for assertion messages.
    """
    all_metrics = []
    for _ in range(tries):
        all_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
        selected = select(all_metrics)
        if selected:
            return selected, all_metrics
        time.sleep(delay)
    return [], all_metrics


def _get_iast_logs(test_agent_session, telemetry_writer):
    """Flush and return all telemetry log entries captured by the session."""
    telemetry_writer.periodic(force_flush=True)
    logs = []
    for event in test_agent_session.get_events("logs"):
        logs += event["payload"]["logs"]
    return logs


def _assert_instrumented_sink(test_agent_session, telemetry_writer, vuln_type):
    # Select the series this test is about instead of asserting on the whole session: the worker
    # flushes on its own schedule, so the session also holds series queued before it was cleared
    # (executed.sink from the lib's own agent traffic, or an earlier test's).
    expected_tags = [f"vulnerability_type:{vuln_type.lower()}"]

    def select(metrics):
        return [
            metric for metric in metrics if metric["metric"] == "instrumented.sink" and metric["tags"] == expected_tags
        ]

    generate_metrics, all_metrics = _wait_for_iast_metrics(test_agent_session, telemetry_writer, select)
    assert generate_metrics, (
        f"Expected an instrumented.sink metric tagged {expected_tags}, got "
        f"{[(metric['metric'], metric['tags']) for metric in all_metrics]}"
    )
    # Check every series rather than how many: the native worker can flush mid-patching and
    # split one metric over several, but each of them still has to be well formed.
    for metric in generate_metrics:
        assert metric["type"] == "count"
    assert sum(point[1] for metric in generate_metrics for point in metric["points"]) >= 1


@pytest.mark.parametrize(
    "lvl, env_lvl, expected_result",
    [
        (TELEMETRY_DEBUG_VERBOSITY, "OFF", None),
        (TELEMETRY_MANDATORY_VERBOSITY, "OFF", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "OFF", None),
        (TELEMETRY_DEBUG_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_MANDATORY_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_INFORMATION_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_DEBUG_VERBOSITY, "INFORMATION", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "INFORMATION", 1),
        (TELEMETRY_MANDATORY_VERBOSITY, "INFORMATION", 1),
        (TELEMETRY_DEBUG_VERBOSITY, "MANDATORY", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "MANDATORY", None),
        (TELEMETRY_MANDATORY_VERBOSITY, "MANDATORY", 1),
    ],
)
def test_metric_verbosity(lvl, env_lvl, expected_result):
    with override_global_config(dict(_iast_telemetry_report_lvl=env_lvl)):
        assert metric_verbosity(lvl)(lambda: 1)() == expected_result


@pytest.mark.parametrize(
    "deduplication_enabled, expected_num_metrics",
    [
        (True, 10),
        (False, 10),
    ],
)
def test_metric_executed_sink(
    deduplication_enabled,
    expected_num_metrics,
    no_request_sampling,
    telemetry_writer,
    test_agent_session,
    caplog,
    tracer,
):
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_is_testing=True,
            _iast_deduplication_enabled=deduplication_enabled,
            # The no_request_sampling fixture only exports DD_IAST_REQUEST_SAMPLING; it does not
            # update asm_config (which keeps its 30% default), so set the sampling rate here so
            # the request is deterministically sampled in and executed.sink is emitted.
            _iast_request_sampling=100.0,
            _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME,
        )
    ):
        oce.reconfigure()
        weak_hash_patch()

        # Size the native taint-context slot array so the IAST span processor can create a
        # request context (otherwise start_request_context() returns no free slot,
        # is_iast_request_enabled() stays False, and executed.sink is never emitted). In
        # production this is done by enable_iast_propagation() at startup.
        initialize_native_state()
        tracer.configure(iast_enabled=True)
        # Clear any IAST context leaked from a previous test (e.g. tainted objects still in
        # a slot). Without this, a reused context would make request.tainted > 0 and break
        # the len == 1 assertion. After clearing, the span processor starts a fresh context
        # so that is_iast_request_enabled() returns True and executed.sink is emitted.
        # request.tainted is NOT emitted here because this test taints no objects.
        _iast_finish_request()
        AppSecIastSpanProcessor.enable()

        # weak_hash_patch() above emits an ``instrumented.sink`` metric; drop it from the session
        # so the request below is asserted on its ``executed.sink`` metric alone (the previous
        # Cython aggregator was flushed at this same point to the same effect).
        _get_iast_metrics(test_agent_session, telemetry_writer)
        test_agent_session.clear()

        try:
            with asm_context(tracer=tracer) as span:
                import hashlib

                m = hashlib.new("md5")
                m.update(b"Nobody inspects")
                m.update(b" the spammish repetition")
                num_vulnerabilities = 10
                for _ in range(0, num_vulnerabilities):
                    m.digest()
        finally:
            AppSecIastSpanProcessor.disable()

        # Select the weak_hash series this test produced: the session also holds sinks from the
        # lib's own agent traffic (http.client) and series the worker flushed late.
        filtered_metrics, generate_metrics = _wait_for_iast_metrics(
            test_agent_session,
            telemetry_writer,
            lambda metrics: [
                metric
                for metric in metrics
                if metric["metric"] == "executed.sink" and metric["tags"] == ["vulnerability_type:weak_hash"]
            ],
        )
        _testing_unpatch_iast()

    assert filtered_metrics, (
        "Expected an executed.sink metric tagged vulnerability_type:weak_hash, got "
        f"{[(metric['metric'], metric['tags']) for metric in generate_metrics]}"
    )
    assert span.get_metric("_dd.iast.telemetry.executed.sink.weak_hash") == expected_num_metrics
    # request.tainted metric is None because AST is not running in this test
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) is None


@pytest.mark.parametrize(
    "patch_func, vuln",
    [
        (header_injection_patch, VULN_HEADER_INJECTION),
        (code_injection_patch, VULN_CODE_INJECTION),
        (untrusted_serialization_patch, VULN_UNTRUSTED_SERIALIZATION),
        (unvalidated_redirect_patch, VULN_UNVALIDATED_REDIRECT),
        (xss_patch, VULN_XSS),
        (weak_hash_patch, VULN_INSECURE_HASHING_TYPE),
    ],
)
def test_metric_instrumented_vulnerability(no_request_sampling, telemetry_writer, test_agent_session, patch_func, vuln):
    # We need to unpatch first because ddtrace.appsec._iast._patch_modules loads at runtime this patch function
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)
    ):
        patch_func()

    _assert_instrumented_sink(test_agent_session, telemetry_writer, vuln)


def test_metric_instrumented_propagation(no_request_sampling, telemetry_writer, test_agent_session):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

    # Select by name rather than by excluding the names known to leak in: the worker can flush
    # mid-patching (splitting instrumented.propagation across series) and can flush an earlier
    # test's series into this session.
    filtered_metrics, all_metrics = _wait_for_iast_metrics(
        test_agent_session,
        telemetry_writer,
        lambda metrics: [metric for metric in metrics if metric["metric"] == "instrumented.propagation"],
    )
    assert filtered_metrics, (
        f"Expected an instrumented.propagation metric, got {sorted({metric['metric'] for metric in all_metrics})}"
    )


def test_metric_request_tainted(no_request_sampling, telemetry_writer, test_agent_session, tracer):
    with override_global_config(
        dict(_iast_enabled=True, _iast_request_sampling=100.0, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)
    ):
        oce.reconfigure()
        # Size the native taint-context slot array so the IAST span processor can create a
        # request context (in production this is done by enable_iast_propagation() at startup).
        initialize_native_state()
        tracer.configure(iast_enabled=True)
        # Reset leaked request and processor state before starting a fresh request.
        # Otherwise taint_pyobject can emit executed.source against a freed native slot,
        # while request.tainted is missing when the span finishes.
        _iast_finish_request()
        initialize_native_state()
        AppSecIastSpanProcessor.disable()
        AppSecIastSpanProcessor.enable()
        try:
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                taint_pyobject(
                    pyobject="bar",
                    source_name="test_string_operator_add_two",
                    source_value="bar",
                    source_origin=OriginType.PARAMETER,
                )
        finally:
            AppSecIastSpanProcessor.disable()

    # Both series have to be there, but not alone: executed.sink comes from the lib's own agent
    # traffic (http.client), and the worker can flush an earlier test's series into this session.
    expected = {"executed.source", "request.tainted"}

    def select(metrics):
        names = {metric["metric"] for metric in metrics}
        return sorted(expected) if expected <= names else []

    filtered_metrics, all_metrics = _wait_for_iast_metrics(test_agent_session, telemetry_writer, select)
    assert filtered_metrics == sorted(expected), (
        f"Expected {sorted(expected)} among the iast metrics, got "
        f"{sorted({metric['metric'] for metric in all_metrics})}"
    )
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) > 0


def test_log_metric(telemetry_writer, test_agent_session):
    # Reset the deduplication cache to ensure clean state
    _set_iast_error_metric._reset_cache()

    with override_global_config(
        dict(_iast_enabled=True, _iast_debug=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _set_iast_error_metric("test_format_key_error_and_no_log_metric raises")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"] == "test_format_key_error_and_no_log_metric raises"
    ]
    assert len(list_metrics_logs) == 1, f"Expected 1 log entry, got {len(list_metrics_logs)}"
    assert list_metrics_logs[0]["message"] == "test_format_key_error_and_no_log_metric raises"
    assert not list_metrics_logs[0].get("stack_trace")


def test_log_metric_debug_disabled(telemetry_writer, test_agent_session):
    _set_iast_error_metric._reset_cache()
    with override_global_config(
        dict(_iast_enabled=True, _iast_debug=False, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _set_iast_error_metric("test_log_metric_debug_disabled raises")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"] == "test_log_metric_debug_disabled raises"
    ]
    assert len(list_metrics_logs) == 0


def test_log_metric_debug_deduplication(telemetry_writer, test_agent_session):
    # Reset the deduplication cache to ensure clean state
    _set_iast_error_metric._reset_cache()

    with override_global_config(
        dict(_iast_enabled=True, _iast_debug=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        for i in range(10):
            _set_iast_error_metric("test_log_metric_debug_deduplication raises 2")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"] == "test_log_metric_debug_deduplication raises 2"
    ]
    assert len(list_metrics_logs) == 1, f"Expected 1 log entry, got {len(list_metrics_logs)}"
    assert list_metrics_logs[0]["message"] == "test_log_metric_debug_deduplication raises 2"
    assert not list_metrics_logs[0].get("stack_trace")


def test_log_metric_debug_disabled_deduplication(telemetry_writer, test_agent_session):
    _set_iast_error_metric._reset_cache()
    with override_global_config(dict(_iast_debug=False)):
        for i in range(10):
            _set_iast_error_metric("test_log_metric_debug_disabled_deduplication raises")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"] == "test_log_metric_debug_disabled_deduplication raises"
    ]
    assert len(list_metrics_logs) == 0


def test_log_metric_debug_deduplication_different_messages(telemetry_writer, test_agent_session):
    # Reset the deduplication cache to ensure clean state
    _set_iast_error_metric._reset_cache()

    with override_global_config(
        dict(_iast_enabled=True, _iast_debug=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        for i in range(10):
            _set_iast_error_metric(f"test_log_metric_debug_deduplication_different_messages raises {i}")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"].startswith("test_log_metric_debug_deduplication_different_messages raises")
    ]
    assert len(list_metrics_logs) == 10, f"Expected 10 log entries, got {len(list_metrics_logs)}"
    assert list_metrics_logs[0]["message"].startswith("test_log_metric_debug_deduplication_different_messages raises")
    assert not list_metrics_logs[0].get("stack_trace")


def test_log_metric_debug_disabled_deduplication_different_messages(telemetry_writer, test_agent_session):
    _set_iast_error_metric._reset_cache()
    with override_global_config(dict(_iast_debug=False)):
        for i in range(10):
            _set_iast_error_metric(f"test_log_metric_debug_disabled_deduplication_different_messages raises {i}")

    list_metrics_logs = [
        log
        for log in _get_iast_logs(test_agent_session, telemetry_writer)
        if log["message"].startswith("test_log_metric_debug_disabled_deduplication_different_messages raises")
    ]
    assert len(list_metrics_logs) == 0


def test_django_instrumented_metrics(telemetry_writer, test_agent_session):
    with override_global_config(dict(_iast_enabled=True, _iast_debug=True)):
        _on_django_patch()

    expected_source_types = {
        f"source_type:{origin_to_str(origin)}"
        for origin in (
            OriginType.HEADER_NAME,
            OriginType.HEADER,
            OriginType.PATH_PARAMETER,
            OriginType.PATH,
            OriginType.COOKIE,
            OriginType.COOKIE_NAME,
            OriginType.PARAMETER,
            OriginType.PARAMETER_NAME,
            OriginType.BODY,
        )
    }

    # Only instrumented.source carries a source_type tag; instrumented.propagation has none.
    # Wait for the whole set: the worker can flush the source series several flushes apart.
    def select(metrics):
        tags = {metric["tags"][0] for metric in metrics if metric["metric"] == "instrumented.source" and metric["tags"]}
        return tags if expected_source_types <= tags else set()

    source_types, all_metrics = _wait_for_iast_metrics(test_agent_session, telemetry_writer, select)

    assert source_types == expected_source_types, (
        f"Missing {sorted(expected_source_types - source_types)}, unexpected "
        f"{sorted(source_types - expected_source_types)}, in "
        f"{sorted({metric['metric'] for metric in all_metrics})}"
    )


def test_django_instrumented_metrics_iast_disabled(telemetry_writer, test_agent_session):
    with override_global_config(dict(_iast_enabled=False)):
        _on_django_patch()

    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    assert generate_metrics == []
