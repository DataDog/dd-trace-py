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


def _get_iast_metrics(test_agent_session, telemetry_writer):
    """Flush the native worker and return the iast-namespace generate-metrics series."""
    telemetry_writer.periodic(force_flush=True)
    metrics = []
    for series in test_agent_session.get_metrics():
        if series.get("namespace") == TELEMETRY_NAMESPACE.IAST.value:
            metrics.append(series)
    return metrics


def _get_iast_logs(test_agent_session, telemetry_writer):
    """Flush and return all telemetry log entries captured by the session."""
    telemetry_writer.periodic(force_flush=True)
    logs = []
    for event in test_agent_session.get_events("logs"):
        logs += event["payload"]["logs"]
    return logs


def _assert_instrumented_sink(test_agent_session, telemetry_writer, vuln_type):
    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric["metric"] for metric in generate_metrics] == ["instrumented.sink"]
    assert [metric["tags"] for metric in generate_metrics] == [[f"vulnerability_type:{vuln_type.lower()}"]]
    assert [metric["points"][0][1] for metric in generate_metrics][0] >= 1
    assert [metric["type"] for metric in generate_metrics] == ["count"]


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

        generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
        _testing_unpatch_iast()

    assert len(generate_metrics) == 1
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric for metric in generate_metrics if metric["tags"][0] == "vulnerability_type:weak_hash"]
    assert [metric["tags"] for metric in filtered_metrics] == [["vulnerability_type:weak_hash"]]
    assert [metric["metric"] for metric in filtered_metrics] == ["executed.sink"]
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
    # Drain metrics emitted before this test so the session below holds only our own.
    _get_iast_metrics(test_agent_session, telemetry_writer)
    test_agent_session.clear()

    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    # A set, not a list: the native telemetry worker can flush mid-patching, splitting
    # instrumented.propagation across series. executed.*/instrumented.sink come from the lib's own
    # internal usage, not the module under test.
    filtered_metrics = {
        metric["metric"]
        for metric in generate_metrics
        if metric["metric"] != "instrumented.sink" and not metric["metric"].startswith("executed.")
    }
    assert filtered_metrics == {"instrumented.propagation"}


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

    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric["metric"] for metric in generate_metrics if metric["metric"] != "executed.sink"]
    assert filtered_metrics == ["executed.source", "request.tainted"]
    assert len(filtered_metrics) == 2, "Expected 2 generate_metrics"
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

    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    metrics_source_tags_result = [metric["tags"][0] for metric in generate_metrics]

    assert len(metrics_source_tags_result) == 9
    assert f"source_type:{origin_to_str(OriginType.HEADER_NAME)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.HEADER)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.PATH_PARAMETER)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.PATH)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.COOKIE)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.COOKIE_NAME)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.PARAMETER)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.PARAMETER_NAME)}" in metrics_source_tags_result
    assert f"source_type:{origin_to_str(OriginType.BODY)}" in metrics_source_tags_result


def test_django_instrumented_metrics_iast_disabled(telemetry_writer, test_agent_session):
    with override_global_config(dict(_iast_enabled=False)):
        _on_django_patch()

    generate_metrics = _get_iast_metrics(test_agent_session, telemetry_writer)
    assert generate_metrics == []
