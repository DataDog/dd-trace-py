import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def skipif(
    exporter_installed: bool = False, exporter_not_installed: bool = False, unsupported_otel_version: bool = False
):
    """
    Returns a pytest skip marker based on OpenTelemetry version and exporter installation.
    Parameters:
    - exporter_installed: If True, skip tests that require OpenTelemetry exporters.
    - exporter_not_installed: If True, skip tests that do not require OpenTelemetry exporters.
    - unsupported_otel_version: If True, skip tests that require OpenTelemetry version 1.12 or higher.
      - v1.12.0 is the first version that exposes metrics in the public API
    """
    if unsupported_otel_version and OTEL_VERSION < (1, 12):
        return pytest.mark.skipif(True, reason="OpenTelemetry version 1.12 or higher is required for these tests")

    has_exporter = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed and has_exporter:
        return pytest.mark.skipif(True, reason="Tests not compatible with the opentelemetry exporters")
    elif exporter_not_installed and not has_exporter:
        return pytest.mark.skipif(True, reason="Tests only compatible with the opentelemetry exporters")
    return pytest.mark.skipif(False, reason="No skip condition met for OpenTelemetry logs exporter tests")


@skipif(exporter_installed=True, unsupported_otel_version=True)
def test_otel_metrics_exporter_not_installed_by_default(caplog):
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    assert (
        "Install opentelemetry-exporter-otlp, which includes both gRPC and HTTP exporters, before enabling "
        "OTLP metrics export."
    ) in caplog.text

    # The OpenTelemetry exporter dependencies are not installed.
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_installed=True)
def test_otel_metrics_exporter_import_warning(caplog):
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    assert _import_exporter("grpc") is None
    assert (
        "Install opentelemetry-exporter-otlp, which includes both gRPC and HTTP exporters, before enabling "
        "OTLP metrics export."
    ) in caplog.text


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    # Check if the GRPC/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for gRPC protobuf should be available")

    # Check if HTTP/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for HTTP/protobuf should be available")


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_METRICS_OTEL_ENABLED": "true"})
def test_otel_metrics_enabled():
    """
    Test that the OpenTelemetry metrics exporter is automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert meter_provider, "OpenTelemetry metrics exporter should be configured automatically."


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_SERVICE": "test_service",
        "DD_VERSION": "test_version",
        "DD_ENV": "test_env",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env",
    },
)
def test_otel_metrics_resource_uses_canonical_semantic_attributes():
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "test_service"
    assert resource.attributes["service.version"] == "test_version"
    assert resource.attributes["deployment.environment.name"] == "test_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env"
    },
)
def test_otel_metrics_resource_remaps_semantic_tags():
    from ddtrace import config
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    assert config.service == "tag_service"
    assert config.version == "tag_version"
    assert config.env == "tag_env"
    assert config.tags == {"env": "tag_env"}
    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "tag_service"
    assert resource.attributes["service.version"] == "tag_version"
    assert resource.attributes["deployment.environment.name"] == "tag_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_METRICS_OTEL_ENABLED": [None, "false"]})
def test_otel_metrics_disabled_and_unset():
    """
    Test that the OpenTelemetry metrics exporter is NOT automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert (meter_provider is None) or (type(meter_provider).__name__ == "_ProxyMeterProvider"), (
        "OpenTelemetry mterics exporter should not be configured automatically."
    )


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "30",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_TRACES_EXPORTER": "otlp",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", "http/protobuf"]},
)
def test_otel_metrics_export_does_not_generate_client_spans():
    import os

    from opentelemetry.metrics import get_meter
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.trace import tracer
    from tests.opentelemetry.utils import capture_otlp_metrics
    from tests.opentelemetry.utils import capture_otlp_traces
    from tests.opentelemetry.utils import metric_names

    with (
        capture_otlp_metrics(os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]) as metric_requests,
        capture_otlp_traces("http/protobuf", port=14318) as trace_requests,
    ):
        get_meter("test.metrics").create_counter("test.metric").add(1)
        assert get_meter_provider().force_flush()
        tracer.flush()

    assert "test.metric" in metric_names(metric_requests)
    assert trace_requests == []


OTLP_RUNTIME_METRICS = {
    "process.cpu.time",
    "process.cpu.utilization",
    "process.context_switches",
    "process.memory.usage",
    "process.thread.count",
    "cpython.gc.collections",
    "cpython.gc.collected_objects",
    "cpython.gc.uncollectable_objects",
    "process.runtime.cpython.gc_count",
}


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_RUNTIME_METRICS_ENABLED": "true",
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", "http/protobuf"]},
)
def test_otlp_runtime_metrics_exported():
    """Runtime metrics are exported through the MeterProvider that ddtrace sets up once, after bootstrap."""
    import os
    import sys

    assert "opentelemetry.sdk" not in sys.modules, "the OpenTelemetry SDK must not be imported at bootstrap"

    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.opentelemetry import metrics
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from tests.opentelemetry.test_metrics import OTLP_RUNTIME_METRICS
    from tests.opentelemetry.utils import capture_otlp_metrics
    from tests.opentelemetry.utils import metric_names

    worker = RuntimeWorker._instance
    assert worker._otlp_pending
    worker.periodic()
    assert worker._otlp_started
    assert metrics.DD_METRICS_PROVIDER_CONFIGURED

    with capture_otlp_metrics(os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]) as requests:
        get_meter_provider().shutdown()

    names = metric_names(requests)
    assert OTLP_RUNTIME_METRICS <= names, OTLP_RUNTIME_METRICS - names
    assert not {name for name in names if name.startswith("runtime.python.")}
    scopes = {
        scope_metrics.scope.name
        for request in requests
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
    }
    assert scopes == {"datadog.python.runtime"}


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(env={"DD_METRICS_OTEL_ENABLED": "true"}, err=None)
def test_otlp_runtime_metrics_instruments():
    import gc

    from opentelemetry.metrics import set_meter_provider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import Gauge
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.export import Sum

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.test_metrics import OTLP_RUNTIME_METRICS

    reader = InMemoryMetricReader()
    set_meter_provider(MeterProvider(metric_readers=[reader]))

    # Registers on the application's MeterProvider and ignores the DogStatsD URL in OTLP mode.
    RuntimeMetrics.enable(dogstatsd_url="udp://127.0.0.1:1")
    RuntimeWorker._instance.periodic()
    assert RuntimeWorker._instance._otlp_started
    gc.collect()

    def collect():
        metrics_data = reader.get_metrics_data()
        if metrics_data is None:
            return {}
        return {
            metric.name: metric
            for resource_metrics in metrics_data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            if scope_metrics.scope.name == "datadog.python.runtime"
            for metric in scope_metrics.metrics
        }

    def attributes(metric):
        return [dict(point.attributes) for point in metric.data.data_points]

    metrics = collect()
    assert set(metrics) == OTLP_RUNTIME_METRICS

    expected = {
        "process.cpu.time": ("s", Sum, True),
        "process.cpu.utilization": ("1", Gauge, None),
        "process.context_switches": ("{context_switch}", Sum, True),
        "process.memory.usage": ("By", Sum, False),
        "process.thread.count": ("{thread}", Sum, False),
        "cpython.gc.collections": ("{collection}", Sum, True),
        "cpython.gc.collected_objects": ("{object}", Sum, True),
        "cpython.gc.uncollectable_objects": ("{object}", Sum, True),
        "process.runtime.cpython.gc_count": ("{object}", Sum, True),
    }
    for name, (unit, data_type, monotonic) in expected.items():
        metric = metrics[name]
        assert metric.unit == unit, name
        assert isinstance(metric.data, data_type), name
        if monotonic is not None:
            assert metric.data.is_monotonic is monotonic, name

    assert sorted(a["type"] for a in attributes(metrics["process.cpu.time"])) == ["system", "user"]
    assert attributes(metrics["process.cpu.utilization"]) == [{}]
    assert attributes(metrics["process.memory.usage"]) == [{}]
    assert metrics["process.memory.usage"].data.data_points[0].value > 0
    assert metrics["process.thread.count"].data.data_points[0].value >= 1
    assert sorted(a["cpython.gc.generation"] for a in attributes(metrics["cpython.gc.collections"])) == [0, 1, 2]
    assert sorted(a["count"] for a in attributes(metrics["process.runtime.cpython.gc_count"])) == ["0", "1", "2"]
    collections = {
        p.attributes["cpython.gc.generation"]: p.value for p in metrics["cpython.gc.collections"].data.data_points
    }
    assert collections[2] >= 1

    RuntimeMetrics.disable()
    assert not RuntimeWorker.enabled
    assert not collect()

    RuntimeMetrics.enable()
    RuntimeWorker._instance.periodic()
    assert set(collect()) == OTLP_RUNTIME_METRICS


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize={
        "DD_RUNTIME_METRICS_ENABLED": [None, "false", "true"],
        "DD_METRICS_OTEL_ENABLED": [None, "true"],
        "OTEL_METRICS_EXPORTER": [None, "none"],
    },
    err=None,
)
def test_otlp_runtime_metrics_gating():
    import os

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    runtime_metrics_requested = os.environ.get("DD_RUNTIME_METRICS_ENABLED") == "true"
    otel_metrics_requested = os.environ.get("DD_METRICS_OTEL_ENABLED") == "true"
    exporter_disabled = os.environ.get("OTEL_METRICS_EXPORTER") == "none"

    if not runtime_metrics_requested or exporter_disabled:
        assert not RuntimeWorker.enabled
        assert RuntimeWorker._instance is None
    else:
        worker = RuntimeWorker._instance
        assert RuntimeWorker.enabled
        assert worker._otlp_pending is otel_metrics_requested
        worker.periodic()
        assert worker._otlp_started is otel_metrics_requested


@skipif(exporter_installed=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_RUNTIME_METRICS_ENABLED": "true", "DD_METRICS_OTEL_ENABLED": "true"},
    err=None,
)
def test_otlp_runtime_metrics_fall_back_to_dogstatsd():
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker

    worker = RuntimeWorker._instance
    assert worker._otlp_pending
    worker.periodic()
    assert not worker._otlp_started
    assert not worker._otlp_pending


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(env={"DD_METRICS_OTEL_ENABLED": "true"}, err=None)
def test_otlp_runtime_metrics_counters_monotonic_across_fork():
    import os

    from opentelemetry.metrics import set_meter_provider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics import ObservableCounter
    from opentelemetry.sdk.metrics.export import AggregationTemporality
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.runtime import RuntimeMetrics

    reader = InMemoryMetricReader(preferred_temporality={ObservableCounter: AggregationTemporality.DELTA})
    set_meter_provider(MeterProvider(metric_readers=[reader]))
    RuntimeMetrics.enable()
    RuntimeWorker._instance.periodic()

    def cpu_time_points():
        return [
            point.value
            for resource_metrics in reader.get_metrics_data().resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            if metric.name in ("process.cpu.time", "process.context_switches")
            for point in metric.data.data_points
        ]

    # Burn CPU so the parent's cumulative totals are well above what a fresh child reports.
    sum(i * i for i in range(3_000_000))
    assert cpu_time_points()

    pid = os.fork()
    if pid == 0:
        sum(i * i for i in range(100_000))
        deltas = cpu_time_points()
        os._exit(0 if deltas and all(delta >= 0 for delta in deltas) else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_RUNTIME_METRICS_ENABLED": "true",
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    },
    timeout=60,
)
def test_otlp_runtime_metrics_gevent():
    from gevent import monkey

    monkey.patch_all()

    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from tests.opentelemetry.test_metrics import OTLP_RUNTIME_METRICS
    from tests.opentelemetry.utils import capture_otlp_metrics
    from tests.opentelemetry.utils import metric_names

    RuntimeWorker._instance.periodic()
    with capture_otlp_metrics("http/protobuf") as requests:
        get_meter_provider().shutdown()

    assert OTLP_RUNTIME_METRICS <= metric_names(requests)
