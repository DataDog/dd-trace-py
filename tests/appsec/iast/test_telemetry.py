import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._metrics import TELEMETRY_DEBUG_VERBOSITY
from ddtrace.appsec._iast._metrics import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec._iast._metrics import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._metrics import metric_verbosity
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_IAST
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


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


def test_metric_executed_sink(telemetry_writer):
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

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == [
        "executed.sink",
    ]
    assert span.get_metric("_dd.iast.telemetry.executed.sink.weak_hash") > 0
    # request.tainted metric is None because AST is not running in this test
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) is None


def test_metric_instrumented_propagation(telemetry_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

    metrics_result = telemetry_writer._namespace._metrics_data
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == ["instrumented.propagation"]


def test_metric_request_tainted(telemetry_writer):
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
    assert len(generate_metrics) == 2, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == ["executed.source", "request.tainted"]
    assert span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) > 0


def test_log_metric(telemetry_writer):
    _set_iast_error_metric("test_format_key_error_and_no_log_metric raises")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 1
    assert list_metrics_logs[0]["message"] == "test_format_key_error_and_no_log_metric raises"
    assert str(list_metrics_logs[0]["stack_trace"]).startswith('  File "/')
