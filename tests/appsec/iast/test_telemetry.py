import pytest


try:
    from ddtrace.appsec._iast._metrics import TELEMETRY_DEBUG_VERBOSITY
    from ddtrace.appsec._iast._metrics import TELEMETRY_INFORMATION_VERBOSITY
    from ddtrace.appsec._iast._metrics import TELEMETRY_MANDATORY_VERBOSITY
    from ddtrace.appsec._iast._metrics import metric_verbosity
    from ddtrace.appsec._iast._patch_modules import patch_iast
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    from ddtrace.appsec._iast._utils import _is_python_version_supported
    from ddtrace.ext import SpanTypes
    from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_IAST
    from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
    from tests.utils import DummyTracer
    from tests.utils import override_env
    from tests.utils import override_global_config
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


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


@pytest.mark.skipif(not _is_python_version_supported(), reason="Python version not supported by IAST")
def test_metric_executed_sink(mock_telemetry_lifecycle_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        patch_iast()

        tracer = DummyTracer(iast_enabled=True)

        mock_telemetry_lifecycle_writer._namespace.flush()
        with tracer.trace("test", span_type=SpanTypes.WEB):
            import hashlib

            m = hashlib.new("md5")
            m.update(b"Nobody inspects")
            m.update(b" the spammish repetition")
            num_vulnerabilities = 10
            for _ in range(0, num_vulnerabilities):
                m.digest()
        metrics_result = mock_telemetry_lifecycle_writer._namespace._metrics_data

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == [
        "executed.sink",
    ]


@pytest.mark.skipif(not _is_python_version_supported(), reason="Python version not supported by IAST")
def test_metric_instrumented_propagation(mock_telemetry_lifecycle_writer):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY="INFORMATION")), override_global_config(
        dict(_iast_enabled=True)
    ):
        _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

    metrics_result = mock_telemetry_lifecycle_writer._namespace._metrics_data
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 1, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == ["instrumented.propagation"]


@pytest.mark.skipif(not _is_python_version_supported(), reason="Python version not supported by IAST")
def test_metric_request_tainted(mock_telemetry_lifecycle_writer):
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
        tracer._on_span_finish(span)

    metrics_result = mock_telemetry_lifecycle_writer._namespace._metrics_data

    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_IAST]
    assert len(generate_metrics) == 2, "Expected 1 generate_metrics"
    assert [metric.name for metric in generate_metrics.values()] == ["executed.source", "request.tainted"]
