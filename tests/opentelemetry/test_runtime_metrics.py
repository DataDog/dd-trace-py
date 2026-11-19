import os

from opentelemetry import version
import pytest


SDK_EXPORTER_INSTALLED = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])

requires_exporter = pytest.mark.skipif(
    not SDK_EXPORTER_INSTALLED or OTEL_VERSION < (1, 16, 0),
    reason="OpenTelemetry exporters and API version 1.16 or higher are required",
)
requires_no_exporter = pytest.mark.skipif(SDK_EXPORTER_INSTALLED, reason="The OpenTelemetry exporters are installed")

OTLP_RUNTIME_METRICS_ENV = {
    "DD_RUNTIME_METRICS_ENABLED": "true",
    "DD_METRICS_OTEL_ENABLED": "true",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://127.0.0.1:14321/v1/metrics",
}


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_exported_over_otlp():
    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import _capture_http_requests

    assert RuntimeWorker.enabled
    assert RuntimeWorker._instance is None, "The DogStatsD runtime metrics worker must not run"

    with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
        assert get_meter_provider().force_flush()
    # Leave nothing to export at exit, once the capturing server is gone.
    RuntimeMetrics.disable()

    points = {}
    monotonic = {}
    for request in requests:
        for resource_metrics in request.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                if scope_metrics.scope.name != "datadog.runtime_metrics":
                    continue
                for metric in scope_metrics.metrics:
                    monotonic[metric.name] = metric.sum.is_monotonic
                    for data_point in metric.sum.data_points:
                        attributes = {
                            attribute.key: getattr(attribute.value, attribute.value.WhichOneof("value"))
                            for attribute in data_point.attributes
                        }
                        points.setdefault(metric.name, []).append(attributes)

    assert monotonic == {
        "cpython.gc.collections": True,
        "cpython.gc.collected_objects": True,
        "cpython.gc.uncollectable_objects": True,
        "process.cpu.time": True,
        "process.memory.usage": False,
        "process.thread.count": False,
        "process.context_switches": True,
    }
    for name in ("cpython.gc.collections", "cpython.gc.collected_objects", "cpython.gc.uncollectable_objects"):
        assert {point["cpython.gc.generation"] for point in points[name]} == {0, 1, 2}, points[name]
    assert {point["cpu.mode"] for point in points["process.cpu.time"]} == {"system", "user"}
    assert {point["process.context_switch.type"] for point in points["process.context_switches"]} == {
        "involuntary",
        "voluntary",
    }
    assert all(point == {} for point in points["process.memory.usage"] + points["process.thread.count"]), points


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_wait_for_the_application_to_import_opentelemetry():
    import sys

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    assert RuntimeWorker.enabled
    assert RuntimeWorker._instance is None, "The DogStatsD runtime metrics worker must not run"
    assert "ddtrace.internal.runtime.otlp_runtime_metrics" not in sys.modules
    assert "opentelemetry" not in sys.modules


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_disable_stops_otlp_export():
    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import _capture_http_requests
    from tests.opentelemetry.utils import metric_names

    RuntimeMetrics.disable()
    with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
        assert get_meter_provider().force_flush()
    assert not {name for name in metric_names(requests) if name.startswith(("cpython.", "process."))}

    RuntimeMetrics.enable()
    with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
        assert get_meter_provider().force_flush()
    RuntimeMetrics.disable()
    assert {"cpython.gc.collections", "process.cpu.time"} <= metric_names(requests)


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_counters_stay_monotonic_across_fork():
    import os
    import time

    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import _capture_http_requests

    # Use CPU in the parent, so that a child restarting its CPU time from zero would report a negative delta.
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        pass
    with _capture_http_requests(14321, ExportMetricsServiceRequest):
        assert get_meter_provider().force_flush()

    pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
                flushed = get_meter_provider().force_flush()
            values = [
                getattr(data_point, data_point.WhichOneof("value"))
                for request in requests
                for resource_metrics in request.resource_metrics
                for scope_metrics in resource_metrics.scope_metrics
                for metric in scope_metrics.metrics
                if metric.name in ("process.cpu.time", "process.context_switches")
                for data_point in metric.sum.data_points
            ]
            exit_code = 0 if flushed and values and all(value >= 0 for value in values) else 1
        finally:
            os._exit(exit_code)

    _, status = os.waitpid(pid, 0)
    RuntimeMetrics.disable()
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "The child must report monotonic counters"


# The DogStatsD client also logs the packets it drops when nothing listens on the DogStatsD port.
@requires_exporter
@pytest.mark.subprocess(
    env=OTLP_RUNTIME_METRICS_ENV,
    err=lambda err: "Runtime metrics are sent to DogStatsD: use ddtrace-run or import ddtrace.auto to export them over "
    "OTLP.\n" in err,
)
def test_runtime_metrics_enabled_without_the_bootstrap_are_sent_to_dogstatsd():
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.runtime import RuntimeMetrics

    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is not None, (
        "Without the bootstrap, nothing sets up the OpenTelemetry meter provider"
    )


# The DogStatsD client also logs the packets it drops when nothing listens on the DogStatsD port.
@requires_no_exporter
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=OTLP_RUNTIME_METRICS_ENV,
    err=lambda err: "Runtime metrics are sent to DogStatsD: install opentelemetry-exporter-otlp 1.16.0 or higher to "
    "export them over OTLP.\n" in err,
)
def test_runtime_metrics_fall_back_to_dogstatsd_without_otel_sdk():
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    assert RuntimeWorker.enabled
    assert RuntimeWorker._instance is not None, "Runtime metrics must be sent to DogStatsD without OpenTelemetry"


# err=None: the DogStatsD client logs the packets it drops when nothing listens on the DogStatsD port.
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_RUNTIME_METRICS_ENABLED": "true"}, err=None)
def test_runtime_metrics_sent_to_dogstatsd_by_default():
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    assert RuntimeWorker.enabled
    assert RuntimeWorker._instance is not None
