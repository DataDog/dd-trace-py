import os
import sys
from unittest import mock

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
    "DD_DOGSTATSD_URL": "udp://127.0.0.1:18125",
}


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_exported_over_otlp():
    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import _capture_http_requests
    from tests.opentelemetry.utils import capture_dogstatsd_packets

    with capture_dogstatsd_packets(18125) as dogstatsd_packets:
        with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
            assert get_meter_provider().force_flush()
        # Leave nothing to export at exit, once the capturing server is gone. A DogStatsD worker would flush here.
        RuntimeMetrics.disable()
    assert not [packet for packet in dogstatsd_packets if b"runtime.python." in packet], dogstatsd_packets

    points = {}
    monotonic = {}
    for request in requests:
        for resource_metrics in request.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                if scope_metrics.scope.name != "datadog.runtime_metrics":
                    continue
                for metric in scope_metrics.metrics:
                    data = getattr(metric, metric.WhichOneof("data"))
                    monotonic[metric.name] = getattr(data, "is_monotonic", None)
                    for data_point in data.data_points:
                        attributes = {
                            attribute.key: getattr(attribute.value, attribute.value.WhichOneof("value"))
                            for attribute in data_point.attributes
                        }
                        points.setdefault(metric.name, []).append(attributes)

    assert monotonic == {
        "cpython.gc.collections": True,
        "cpython.gc.collected_objects": True,
        "cpython.gc.uncollectable_objects": True,
        "process.runtime.cpython.gc_count": None,
        "process.cpu.time": True,
        "process.cpu.utilization": None,
        "process.memory.usage": False,
        "process.thread.count": False,
        "process.context_switches": True,
    }
    for name in ("cpython.gc.collections", "cpython.gc.collected_objects", "cpython.gc.uncollectable_objects"):
        assert {point["cpython.gc.generation"] for point in points[name]} == {0, 1, 2}, points[name]
    assert {point["count"] for point in points["process.runtime.cpython.gc_count"]} == {"0", "1", "2"}
    assert {point["cpu.mode"] for point in points["process.cpu.time"]} == {"system", "user"}
    assert {point["process.context_switch.type"] for point in points["process.context_switches"]} == {
        "involuntary",
        "voluntary",
    }
    assert all(
        point == {"datadog.runtime_id": get_runtime_id()}
        for name in ("process.cpu.utilization", "process.memory.usage", "process.thread.count")
        for point in points[name]
    ), points
    assert all(point["datadog.runtime_id"] == get_runtime_id() for name in points for point in points[name]), points


@requires_exporter
@pytest.mark.subprocess()
def test_runtime_metrics_report_the_collected_values():
    from unittest import mock

    from opentelemetry.metrics import set_meter_provider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    import pytest

    from ddtrace.internal.runtime import otlp_runtime_metrics

    reader = InMemoryMetricReader()
    set_meter_provider(MeterProvider(metric_readers=[reader]))
    gc_stats = [
        {"collections": 10, "collected": 100, "uncollectable": 1},
        {"collections": 5, "collected": 50, "uncollectable": 0},
        {"collections": 2, "collected": 20, "uncollectable": 3},
    ]
    # CPU times (system, user) in nanoseconds, context switches (voluntary, involuntary), threads, RSS in bytes.
    process_metrics_at_start = (1_500_000_000, 250_000_000, 5, 1, 4, 100_000)
    process_metrics = (2_500_000_000, 1_250_000_000, 7, 3, 4, 123_456)
    with mock.patch("gc.get_stats", return_value=gc_stats), mock.patch("gc.get_count", return_value=(700, 3, 1)):
        with mock.patch("ddtrace.internal.native.process_metrics", return_value=process_metrics_at_start) as native:
            with mock.patch.object(otlp_runtime_metrics, "monotonic", return_value=100.0) as clock:
                with mock.patch.object(otlp_runtime_metrics, "_available_cpus", return_value=2):
                    otlp_runtime_metrics.start()
                    # 2 CPU seconds in 4 seconds, on 2 CPUs.
                    native.return_value = process_metrics
                    clock.return_value = 104.0
                    metrics = {
                        metric.name: metric
                        for resource_metrics in reader.get_metrics_data().resource_metrics
                        for scope_metrics in resource_metrics.scope_metrics
                        for metric in scope_metrics.metrics
                    }

    def values(name, attribute=None):
        return {data_point.attributes.get(attribute): data_point.value for data_point in metrics[name].data.data_points}

    assert {name: metric.unit for name, metric in metrics.items()} == {
        "cpython.gc.collections": "{collection}",
        "cpython.gc.collected_objects": "{object}",
        "cpython.gc.uncollectable_objects": "{object}",
        "process.runtime.cpython.gc_count": "{count}",
        "process.cpu.time": "s",
        "process.cpu.utilization": "1",
        "process.memory.usage": "By",
        "process.thread.count": "{thread}",
        "process.context_switches": "{context_switch}",
    }
    assert values("cpython.gc.collections", "cpython.gc.generation") == {0: 10, 1: 5, 2: 2}
    assert values("cpython.gc.collected_objects", "cpython.gc.generation") == {0: 100, 1: 50, 2: 20}
    assert values("cpython.gc.uncollectable_objects", "cpython.gc.generation") == {0: 1, 1: 0, 2: 3}
    assert values("process.runtime.cpython.gc_count", "count") == {"0": 700, "1": 3, "2": 1}
    assert values("process.cpu.time", "cpu.mode") == {"user": pytest.approx(1.25), "system": pytest.approx(2.5)}
    assert values("process.cpu.utilization") == {None: pytest.approx(0.25)}
    assert values("process.context_switches", "process.context_switch.type") == {"voluntary": 7, "involuntary": 3}
    assert values("process.thread.count") == {None: 4}
    assert values("process.memory.usage") == {None: 123_456}


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_wait_for_the_application_to_import_opentelemetry():
    import sys

    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import capture_dogstatsd_packets

    # A DogStatsD worker would flush when the runtime metrics are disabled.
    with capture_dogstatsd_packets(18125) as dogstatsd_packets:
        RuntimeMetrics.disable()
    assert not [packet for packet in dogstatsd_packets if b"runtime.python." in packet], dogstatsd_packets
    assert "ddtrace.internal.runtime.otlp_runtime_metrics" not in sys.modules
    assert "opentelemetry" not in sys.modules


@requires_exporter
@pytest.mark.parametrize(
    "code",
    [
        "import opentelemetry",
        # Enabled again once the meter provider is set up, which also registers its exit handler.
        "import opentelemetry\n"
        "from ddtrace.runtime import RuntimeMetrics\n"
        "RuntimeMetrics.disable()\n"
        "RuntimeMetrics.enable()",
    ],
    ids=["enabled_at_startup", "enabled_again"],
)
def test_runtime_metrics_exported_when_the_application_exits(python_process, code):
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from tests.opentelemetry.utils import _capture_http_requests
    from tests.opentelemetry.utils import metric_names

    env = os.environ.copy()
    env.update(OTLP_RUNTIME_METRICS_ENV)
    # The application exits before its first export interval, so only the export at shutdown can reach the server.
    env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = "http://127.0.0.1:14322/v1/metrics"
    env["OTEL_METRIC_EXPORT_INTERVAL"] = "60000"
    with _capture_http_requests(14322, ExportMetricsServiceRequest) as requests:
        _, err, status, _ = python_process(code, ddtrace_run=True, env=env, timeout=60)

    assert status == 0, err
    assert {
        "cpython.gc.collections",
        "cpython.gc.collected_objects",
        "cpython.gc.uncollectable_objects",
        "process.runtime.cpython.gc_count",
        "process.cpu.time",
        "process.cpu.utilization",
        "process.memory.usage",
        "process.thread.count",
        "process.context_switches",
    } <= metric_names(requests)


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
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=OTLP_RUNTIME_METRICS_ENV,
    parametrize={"OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": ["delta", "cumulative"]},
)
def test_runtime_metrics_counters_stay_monotonic_across_fork():
    import os
    import time
    import warnings

    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import _capture_http_requests

    def points(requests, *names):
        return [
            (
                getattr(data_point, data_point.WhichOneof("value")),
                {attribute.key: attribute.value.string_value for attribute in data_point.attributes},
            )
            for request in requests
            for resource_metrics in request.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            if metric.name in names
            for data_point in metric.sum.data_points
        ]

    # Use CPU in the parent, so that a child continuing its counters would report the CPU time of its parent.
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        pass
    with _capture_http_requests(14321, ExportMetricsServiceRequest) as parent_requests:
        assert get_meter_provider().force_flush()
    parent_runtime_id = get_runtime_id()
    parent_cpu_time = sum(value for value, _ in points(parent_requests, "process.cpu.time"))

    with warnings.catch_warnings():
        # Python 3.12+ warns when a multi-threaded process forks.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
                flushed = get_meter_provider().force_flush()
            counters = points(requests, "process.cpu.time", "process.context_switches")
            child_runtime_id = get_runtime_id()
            # The child reports only its own usage, under its own runtime ID.
            valid = (
                child_runtime_id != parent_runtime_id
                and all(attributes["datadog.runtime_id"] == child_runtime_id for _, attributes in counters)
                and all(value >= 0 for value, _ in counters)
                and sum(value for value, attributes in counters if "cpu.mode" in attributes) < parent_cpu_time
            )
            exit_code = 0 if flushed and counters and valid else 1
        finally:
            os._exit(exit_code)

    _, status = os.waitpid(pid, 0)
    RuntimeMetrics.disable()
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "The child must report its own usage"


@requires_exporter
@pytest.mark.subprocess(ddtrace_run=True, env=OTLP_RUNTIME_METRICS_ENV)
def test_runtime_metrics_gc_since_fork_when_opentelemetry_is_imported_after_the_fork():
    import gc
    import os
    import warnings

    # Like a server that forks its workers before loading the application, which imports OpenTelemetry.
    parent_collections = 50
    for _ in range(parent_collections):
        gc.collect()

    with warnings.catch_warnings():
        # Python 3.12+ warns when a multi-threaded process forks.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            from opentelemetry.metrics import get_meter_provider
            from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

            from ddtrace.runtime import RuntimeMetrics
            from tests.opentelemetry.utils import _capture_http_requests

            with _capture_http_requests(14321, ExportMetricsServiceRequest) as requests:
                flushed = get_meter_provider().force_flush()
            RuntimeMetrics.disable()
            full_collections = [
                data_point.as_int
                for request in requests
                for resource_metrics in request.resource_metrics
                for scope_metrics in resource_metrics.scope_metrics
                for metric in scope_metrics.metrics
                if metric.name == "cpython.gc.collections"
                for data_point in metric.sum.data_points
                if {attribute.key: attribute.value.int_value for attribute in data_point.attributes}.get(
                    "cpython.gc.generation"
                )
                == 2
            ]
            # The child reports its own full collections, not those of its parent.
            exit_code = 0 if flushed and full_collections and sum(full_collections) < parent_collections else 1
        finally:
            os._exit(exit_code)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "The child must report its own GC collections"


@requires_exporter
@pytest.mark.subprocess(parametrize={"TEMPORALITY": ["delta", "cumulative"]})
def test_runtime_metrics_after_fork_with_an_application_reader():
    import os
    import time
    import warnings

    from opentelemetry.metrics import set_meter_provider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics import ObservableCounter
    from opentelemetry.sdk.metrics.export import AggregationTemporality
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.internal.runtime import otlp_runtime_metrics

    temporality = getattr(AggregationTemporality, os.environ["TEMPORALITY"].upper())
    reader = InMemoryMetricReader(preferred_temporality={ObservableCounter: temporality})
    set_meter_provider(MeterProvider(metric_readers=[reader]))
    otlp_runtime_metrics.start()

    def cpu_time_points():
        return [
            (data_point.value, dict(data_point.attributes))
            for resource_metrics in reader.get_metrics_data().resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            if metric.name == "process.cpu.time"
            for data_point in metric.data.data_points
        ]

    # Use CPU in the parent, so that a child continuing its counters would report the CPU time of its parent.
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        pass
    parent_runtime_id = get_runtime_id()
    parent_points = cpu_time_points()
    assert parent_points and all(
        attributes["datadog.runtime_id"] == parent_runtime_id for _, attributes in parent_points
    )
    parent_cpu_time = sum(value for value, _ in parent_points)

    with warnings.catch_warnings():
        # Python 3.12+ warns when a multi-threaded process forks.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            child_points = cpu_time_points()
            child_runtime_id = get_runtime_id()
            valid = (
                child_runtime_id != parent_runtime_id
                and all(attributes["datadog.runtime_id"] == child_runtime_id for _, attributes in child_points)
                and all(value >= 0 for value, _ in child_points)
                and sum(value for value, _ in child_points) < parent_cpu_time
            )
            exit_code = 0 if child_points and valid else 1
        finally:
            os._exit(exit_code)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "The child must report its own usage"


@requires_exporter
@pytest.mark.subprocess(
    env=OTLP_RUNTIME_METRICS_ENV,
    err="Runtime metrics are sent to DogStatsD: use ddtrace-run or import ddtrace.auto to export them over OTLP.\n",
)
def test_runtime_metrics_enabled_without_the_bootstrap_are_sent_to_dogstatsd():
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import capture_dogstatsd_packets

    # Without the bootstrap, nothing sets up the OpenTelemetry meter provider. The DogStatsD worker flushes when the
    # runtime metrics are disabled.
    with capture_dogstatsd_packets(18125) as dogstatsd_packets:
        RuntimeMetrics.enable()
        RuntimeMetrics.disable()
    assert [packet for packet in dogstatsd_packets if b"runtime.python." in packet]


@requires_no_exporter
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=OTLP_RUNTIME_METRICS_ENV,
    err=lambda err: err.startswith(
        "Runtime metrics are sent to DogStatsD: exporting them over OTLP requires opentelemetry-"
    ),
)
def test_runtime_metrics_fall_back_to_dogstatsd_without_otel_sdk():
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import capture_dogstatsd_packets

    # The DogStatsD worker flushes when the runtime metrics are disabled.
    with capture_dogstatsd_packets(18125) as dogstatsd_packets:
        RuntimeMetrics.disable()
    assert [packet for packet in dogstatsd_packets if b"runtime.python." in packet]


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_RUNTIME_METRICS_ENABLED": "true", "DD_DOGSTATSD_URL": "udp://127.0.0.1:18125"}
)
def test_runtime_metrics_sent_to_dogstatsd_by_default():
    from ddtrace.runtime import RuntimeMetrics
    from tests.opentelemetry.utils import capture_dogstatsd_packets

    # The DogStatsD worker flushes when the runtime metrics are disabled.
    with capture_dogstatsd_packets(18125) as dogstatsd_packets:
        RuntimeMetrics.disable()
    assert [packet for packet in dogstatsd_packets if b"runtime.python." in packet]


@pytest.mark.parametrize(
    "versions,expected",
    [
        ({"api": "1.15.0", "sdk": "1.16.0", "exporter": "1.16.0"}, ("api", "1.16.0", "1.15.0")),
        ({"api": "1.16.0", "sdk": "", "exporter": "1.16.0"}, ("sdk", "1.16.0", "none")),
        ({"api": "1.16.0", "sdk": "1.16.0", "exporter": "1.14.0"}, ("exporter", "1.15.0", "1.14.0")),
        ({"api": "1.16.0", "sdk": "1.16.0", "exporter": "1.15.0"}, None),
    ],
)
def test_runtime_metrics_otlp_dependency_checks(versions, expected):
    from ddtrace.internal.runtime import runtime_metrics
    from ddtrace.internal.settings._opentelemetry import otel_config

    distributions = {
        "api": "opentelemetry-api",
        "sdk": "opentelemetry-sdk",
        "exporter": runtime_metrics._OTLP_METRICS_EXPORTERS[otel_config.exporter.METRICS_PROTOCOL],
    }
    installed = {distributions[dependency]: version for dependency, version in versions.items()}

    with mock.patch.dict(sys.modules, {runtime_metrics._BOOTSTRAP_MODULE: mock.Mock()}):
        with mock.patch("ddtrace.internal.packages.get_version_for_package", side_effect=installed.get):
            with mock.patch.object(runtime_metrics.log, "warning") as warning:
                can_export = runtime_metrics._can_export_otlp_runtime_metrics()

    if expected is None:
        assert can_export
        warning.assert_not_called()
    else:
        dependency, minimum_version, installed_version = expected
        assert not can_export
        # The warning names the dependency that failed the check, with its own minimum version.
        assert warning.call_args.args[1:] == (distributions[dependency], minimum_version, installed_version)
