import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._handlers import _on_django_patch
from ddtrace.appsec._iast._metrics import TELEMETRY_DEBUG_VERBOSITY
from ddtrace.appsec._iast._metrics import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec._iast._metrics import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._metrics import metric_verbosity
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks.command_injection import patch as cmdi_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import unpatch as header_injection_unpatch
from ddtrace.contrib.sqlalchemy import patch as sqli_sqlalchemy_patch
from ddtrace.contrib.sqlite3 import patch as sqli_sqlite3_patch
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_IAST
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


def _assert_instrumented_sink(telemetry_writer, vuln_type):
    metrics_result = telemetry_writer._namespace._metrics_data
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == ["instrumented.sink"]
    assert [metric._tags for metric in generate_metrics.values()] == [(("vulnerability_type", vuln_type),)]
    assert [metric._points[0][1] for metric in generate_metrics.values()] == [1]
    assert [metric.metric_type for metric in generate_metrics.values()] == ["count"]


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
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY=env_lvl)):
        assert metric_verbosity(lvl)(lambda: 1)() == expected_result


def test_metric_executed_sink(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        patch_iast()

        tracer = DummyTracer(iast_enabled=True)

        telemetry_writer._namespace.flush()
        with _asm_request_context.asm_request_context_manager(), tracer.trace("test", span_type=SpanTypes.WEB) as span:
            import hashlib

            m = hashlib.new("md5")
            m.update(b"Nobody inspects")
            m.update(b" the spammish repetition")
            num_vulnerabilities = 10
            for _ in range(0, num_vulnerabilities):
                m.digest()

        metrics_result = telemetry_writer._namespace._metrics_data

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST].values()
    assert len(generate_metrics) >= 1
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric for metric in generate_metrics if metric._tags[0] == ("vulnerability_type", "WEAK_HASH")]
    assert [metric._tags for metric in filtered_metrics] == [(("vulnerability_type", "WEAK_HASH"),)]
    assert span.get_metric("_dd.iast.telemetry.executed.sink.weak_hash") > 0
    # request.tainted metric is None because AST is not running in this test
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) is None


def test_metric_instrumented_cmdi(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        cmdi_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_CMDI)


def test_metric_instrumented_path_traversal(no_request_sampling, telemetry_writer):
    # We need to unpatch first because ddtrace.appsec._iast._patch_modules loads at runtime this patch function
    unpatch_common_modules()
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        patch_common_modules()

    _assert_instrumented_sink(telemetry_writer, VULN_PATH_TRAVERSAL)


def test_metric_instrumented_header_injection(no_request_sampling, telemetry_writer):
    # We need to unpatch first because ddtrace.appsec._iast._patch_modules loads at runtime this patch function
    header_injection_unpatch()
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        header_injection_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_HEADER_INJECTION)


def test_metric_instrumented_sqli_sqlite3(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        sqli_sqlite3_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)


def test_metric_instrumented_sqli_sqlalchemy_patch(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        sqli_sqlalchemy_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)


def test_metric_instrumented_propagation(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

    metrics_result = telemetry_writer._namespace._metrics_data
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric.name for metric in generate_metrics.values() if metric.name != "executed.sink"]
    assert filtered_metrics == ["instrumented.propagation"]


def test_metric_request_tainted(no_request_sampling, telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        tracer = DummyTracer(iast_enabled=True)

        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            taint_pyobject(
                pyobject="bar",
                source_name="test_string_operator_add_two",
                source_value="bar",
                source_origin=OriginType.PARAMETER,
            )

    metrics_result = telemetry_writer._namespace._metrics_data

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    # Remove potential sinks from internal usage of the lib (like http.client, used to communicate with
    # the agent)
    filtered_metrics = [metric.name for metric in generate_metrics.values() if metric.name != "executed.sink"]
    assert filtered_metrics == ["executed.source", "request.tainted"]
    assert len(filtered_metrics) == 2, "Expected 2 generate_metrics"
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) > 0


def test_log_metric(telemetry_writer):
    _set_iast_error_metric("test_format_key_error_and_no_log_metric raises")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 1
    assert list_metrics_logs[0]["message"] == "test_format_key_error_and_no_log_metric raises"
    assert str(list_metrics_logs[0]["stack_trace"]).startswith('  File "/')


def test_django_instrumented_metrics(telemetry_writer):
    with override_global_config(dict(_iast_enabled=True)):
        _on_django_patch()

    metrics_result = telemetry_writer._namespace._metrics_data
    metrics_source_tags_result = [metric._tags[0][1] for metric in metrics_result["generate-metrics"]["iast"].values()]

    assert len(metrics_source_tags_result) == 9
    assert origin_to_str(OriginType.HEADER_NAME) in metrics_source_tags_result
    assert origin_to_str(OriginType.HEADER) in metrics_source_tags_result
    assert origin_to_str(OriginType.PATH_PARAMETER) in metrics_source_tags_result
    assert origin_to_str(OriginType.PATH) in metrics_source_tags_result
    assert origin_to_str(OriginType.COOKIE) in metrics_source_tags_result
    assert origin_to_str(OriginType.COOKIE_NAME) in metrics_source_tags_result
    assert origin_to_str(OriginType.PARAMETER) in metrics_source_tags_result
    assert origin_to_str(OriginType.PARAMETER_NAME) in metrics_source_tags_result
    assert origin_to_str(OriginType.BODY) in metrics_source_tags_result


def test_django_instrumented_metrics_iast_disabled(telemetry_writer):
    with override_global_config(dict(_iast_enabled=False)):
        _on_django_patch()

    metrics_result = telemetry_writer._namespace._metrics_data
    metrics_source_tags_result = [metric._tags[0][1] for metric in metrics_result["generate-metrics"]["iast"].values()]

    assert len(metrics_source_tags_result) == 0
