import pytest

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._constants import TELEMETRY_DEBUG_VERBOSITY
from ddtrace.appsec._constants import TELEMETRY_INFORMATION_NAME
from ddtrace.appsec._constants import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec._constants import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec._iast._handlers import _on_django_patch
from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._metrics import metric_verbosity
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
from ddtrace.appsec._iast.taint_sinks.code_injection import unpatch as code_injection_unpatch
from ddtrace.appsec._iast.taint_sinks.command_injection import patch as cmdi_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import unpatch as header_injection_unpatch
from ddtrace.contrib.internal.sqlalchemy.patch import patch as sqli_sqlalchemy_patch
from ddtrace.contrib.internal.sqlite3.patch import patch as sqli_sqlite3_patch
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.utils import asm_context
from tests.utils import DummyTracer
from tests.utils import override_global_config


def _assert_instrumented_sink(telemetry_writer, vuln_type):
    metrics_result = telemetry_writer._namespace.flush()
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.IAST.value]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric["metric"] for metric in generate_metrics] == ["instrumented.sink"]
    assert [metric["tags"] for metric in generate_metrics] == [[f"vulnerability_type:{vuln_type.lower()}"]]
    assert [metric["points"][0][1] for metric in generate_metrics] == [1]
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


def test_metric_executed_sink(no_request_sampling, telemetry_writer, caplog):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        patch_iast()

        tracer = DummyTracer(iast_enabled=True)

        telemetry_writer._namespace.flush()
        with asm_context(tracer=tracer) as span:
            import hashlib

            m = hashlib.new("md5")
            m.update(b"Nobody inspects")
            m.update(b" the spammish repetition")
            num_vulnerabilities = 10
            for _ in range(0, num_vulnerabilities):
                m.digest()

        metrics_result = telemetry_writer._namespace.flush()

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.IAST.value]
    assert len(generate_metrics) == 1
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric for metric in generate_metrics if metric["tags"][0] == "vulnerability_type:weak_hash"]
    assert [metric["tags"] for metric in filtered_metrics] == [["vulnerability_type:weak_hash"]]
    assert span.get_metric("_dd.iast.telemetry.executed.sink.weak_hash") == 2
    # request.tainted metric is None because AST is not running in this test
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) is None


def test_metric_instrumented_cmdi(no_request_sampling, telemetry_writer):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        cmdi_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_CMDI)


def test_metric_instrumented_header_injection(no_request_sampling, telemetry_writer):
    # We need to unpatch first because ddtrace.appsec._iast._patch_modules loads at runtime this patch function
    header_injection_unpatch()
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        header_injection_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_HEADER_INJECTION)


def test_metric_instrumented_code_injection(no_request_sampling, telemetry_writer):
    # We need to unpatch first because ddtrace.appsec._iast._patch_modules loads at runtime this patch function
    code_injection_unpatch()
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        code_injection_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_CODE_INJECTION)


def test_metric_instrumented_sqli_sqlite3(no_request_sampling, telemetry_writer):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        sqli_sqlite3_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)


def test_metric_instrumented_sqli_sqlalchemy_patch(no_request_sampling, telemetry_writer):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        sqli_sqlalchemy_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)


def test_metric_instrumented_propagation(no_request_sampling, telemetry_writer):
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

    metrics_result = telemetry_writer._namespace.flush()
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.IAST.value]
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric["metric"] for metric in generate_metrics if metric["metric"] != "executed.sink"]
    assert filtered_metrics == ["instrumented.propagation"]


def test_metric_request_tainted(no_request_sampling, telemetry_writer):
    with override_global_config(
        dict(_iast_enabled=True, _iast_request_sampling=100.0, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)
    ):
        oce.reconfigure()
        tracer = DummyTracer(iast_enabled=True)

        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            taint_pyobject(
                pyobject="bar",
                source_name="test_string_operator_add_two",
                source_value="bar",
                source_origin=OriginType.PARAMETER,
            )

    metrics_result = telemetry_writer._namespace.flush()

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.IAST.value]
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric["metric"] for metric in generate_metrics if metric["metric"] != "executed.sink"]
    assert filtered_metrics == ["executed.source", "request.tainted"]
    assert len(filtered_metrics) == 2, "Expected 2 generate_metrics"
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) > 0
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_parameter") > 0


@pytest.mark.skip_iast_check_logs
def test_log_metric(telemetry_writer):
    with override_global_config(dict(_iast_debug=True)):
        _set_iast_error_metric("test_format_key_error_and_no_log_metric raises")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 1
    assert list_metrics_logs[0]["message"] == "test_format_key_error_and_no_log_metric raises"
    assert str(list_metrics_logs[0]["stack_trace"]).startswith('  File "/')


@pytest.mark.skip_iast_check_logs
def test_log_metric_debug_disabled(telemetry_writer):
    with override_global_config(dict(_iast_debug=False)):
        _set_iast_error_metric("test_log_metric_debug_disabled raises")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "test_log_metric_debug_disabled raises"
        assert "stack_trace" not in list_metrics_logs[0].keys()


@pytest.mark.skip_iast_check_logs
def test_log_metric_debug_disabled_deduplication(telemetry_writer):
    with override_global_config(dict(_iast_debug=False)):
        for i in range(10):
            _set_iast_error_metric("test_log_metric_debug_disabled_deduplication raises")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "test_log_metric_debug_disabled_deduplication raises"
        assert "stack_trace" not in list_metrics_logs[0].keys()


@pytest.mark.skip_iast_check_logs
def test_log_metric_debug_disabled_deduplication_different_messages(telemetry_writer):
    with override_global_config(dict(_iast_debug=False)):
        for i in range(10):
            _set_iast_error_metric(f"test_format_key_error_and_no_log_metric raises {i}")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 10
        assert list_metrics_logs[0]["message"].startswith("test_format_key_error_and_no_log_metric raises")
        assert "stack_trace" not in list_metrics_logs[0].keys()


def test_django_instrumented_metrics(telemetry_writer):
    with override_global_config(dict(_iast_enabled=True, _iast_debug=True)):
        _on_django_patch()

    metrics_result = telemetry_writer._namespace.flush()
    metrics_source_tags_result = [metric["tags"][0] for metric in metrics_result["generate-metrics"]["iast"]]

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


def test_django_instrumented_metrics_iast_disabled(telemetry_writer):
    with override_global_config(dict(_iast_enabled=False)):
        _on_django_patch()

    metrics_result = telemetry_writer._namespace.flush()
    assert "iast" not in metrics_result["generate-metrics"]
