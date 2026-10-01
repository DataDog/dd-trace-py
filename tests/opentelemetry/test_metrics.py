import importlib.util

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])
try:
    HAS_OTEL_PROTO = (
        importlib.util.find_spec("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2") is not None
    )
except ModuleNotFoundError:
    HAS_OTEL_PROTO = False

# v1.15.0 is the minimum opentelemetry-api version ddtrace supports for metrics.
requires_metrics_api = pytest.mark.skipif(
    OTEL_VERSION < (1, 15, 0),
    reason="opentelemetry-api 1.15.0 or higher is required for these tests",
)


@requires_metrics_api
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_METRICS_OTEL_ENABLED": "true"}, err=None)
def test_otel_metrics_enabled():
    """The native MeterProvider is installed automatically when DD_METRICS_OTEL_ENABLED is set.

    It does not require the opentelemetry-sdk or opentelemetry-exporter-otlp packages: metrics are
    aggregated and exported by libdatadog behind the opentelemetry-api surface.
    """
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.opentelemetry._native_metrics_provider import MeterProvider

    meter_provider = get_meter_provider()
    assert isinstance(meter_provider, MeterProvider), (
        "DD_METRICS_OTEL_ENABLED should install the native MeterProvider, got %r" % type(meter_provider).__name__
    )


@requires_metrics_api
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_METRICS_OTEL_ENABLED": [None, "false"]}, err=None)
def test_otel_metrics_disabled_and_unset():
    """The native MeterProvider is NOT installed when DD_METRICS_OTEL_ENABLED is unset or false."""
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.opentelemetry._native_metrics_provider import MeterProvider

    meter_provider = get_meter_provider()
    assert not isinstance(meter_provider, MeterProvider), (
        "OpenTelemetry metrics should not be configured automatically."
    )


@requires_metrics_api
@pytest.mark.subprocess(env={"DD_METRICS_OTEL_ENABLED": "true"}, err=None)
def test_native_meter_provider_records():
    """Every instrument kind can be created and recorded through the native path.

    OTLP metrics aggregation and export are bundled in libdatadog, so the path works with only
    opentelemetry-api installed and behaves identically whether or not the opentelemetry-sdk
    happens to be present.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    set_otel_meter_provider()

    from opentelemetry.metrics import CallbackOptions
    from opentelemetry.metrics import Observation
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.opentelemetry._native_metrics_provider import MeterProvider

    provider = get_meter_provider()
    assert isinstance(provider, MeterProvider)

    meter = provider.get_meter("ddtrace.test")

    counter = meter.create_counter("requests", unit="1", description="request count")
    counter.add(1, {"route": "/health"})

    updown = meter.create_up_down_counter("queue.size")
    updown.add(5)
    updown.add(-2)

    histogram = meter.create_histogram("latency", unit="ms")
    histogram.record(12.5, {"route": "/health"})

    # Synchronous gauge (parametric apps use meter.create_gauge().set(...)); must not be a no-op.
    gauge = meter.create_gauge("pool.inuse")
    assert gauge is not None
    gauge.set(7, {"pool": "default"})

    observations = []

    def _observe(options: CallbackOptions):
        observations.append(options)
        return [Observation(42, {"pool": "default"})]

    meter.create_observable_gauge("pool.depth", callbacks=[_observe])
    meter.create_observable_counter("cache.hits", callbacks=[_observe])
    meter.create_observable_up_down_counter("pool.available", callbacks=[_observe])

    # Flushing resolves the observable callbacks and drives the native exporter. It must never
    # raise and always returns a bool, whether or not a collector is actually reachable (export
    # failures are reported as False, not exceptions).
    assert not hasattr(provider, "_reader")
    assert isinstance(provider.force_flush(), bool)
    assert len(observations) >= 3
    provider.shutdown()


@requires_metrics_api
@pytest.mark.skipif(
    not HAS_OTEL_PROTO,
    reason="opentelemetry-proto is required to inspect OTLP payloads",
)
@pytest.mark.subprocess(err=None)
def test_native_meter_provider_preserves_attribute_types():
    """Resource, scope, and measurement attributes retain their OTel API types in OTLP."""
    from http.server import BaseHTTPRequestHandler
    from http.server import HTTPServer
    import threading

    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

    from ddtrace.internal.opentelemetry._native_metrics_provider import build_meter_provider

    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    attributes = {
        "bool": True,
        "int": 42,
        "float": 1.5,
        "string": "value",
        "bools": [True, False],
        "ints": (1, 2),
        "floats": [1.5, 2.5],
        "strings": ("a", "b"),
    }
    provider = build_meter_provider(
        None,
        None,
        None,
        attributes,
        "http://127.0.0.1:%d/v1/metrics" % server.server_port,
        "http/protobuf",
        1000,
        "",
        "delta",
        60000,
    )
    try:
        meter = provider.get_meter("ddtrace.test", attributes=attributes)
        meter.create_counter("requests").add(1, attributes)
        assert provider.force_flush() is True
    finally:
        provider.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join()

    def decode(encoded):
        decoded = {}
        for attribute in encoded:
            value = attribute.value
            value_type = value.WhichOneof("value")
            if value_type == "array_value":
                decoded[attribute.key] = [getattr(item, item.WhichOneof("value")) for item in value.array_value.values]
            else:
                decoded[attribute.key] = getattr(value, value_type)
        return decoded

    request = metrics_service_pb2.ExportMetricsServiceRequest.FromString(requests[0])
    resource_metrics = request.resource_metrics[0]
    scope_metrics = resource_metrics.scope_metrics[0]
    data_point = scope_metrics.metrics[0].sum.data_points[0]
    expected = {key: list(value) if isinstance(value, (list, tuple)) else value for key, value in attributes.items()}
    for encoded in (resource_metrics.resource.attributes, scope_metrics.scope.attributes, data_point.attributes):
        decoded = decode(encoded)
        assert {key: decoded[key] for key in expected} == expected


@requires_metrics_api
@pytest.mark.subprocess(
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_METRIC_EXPORT_INTERVAL": "50",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://127.0.0.1:9",
        "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT": "50",
    },
    err=None,
)
def test_native_reader_schedules_observable_callbacks():
    """The Rust reader invokes Python callbacks without a Python periodic worker."""
    import threading

    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    set_otel_meter_provider()

    from opentelemetry.metrics import Observation
    from opentelemetry.metrics import get_meter_provider

    provider = get_meter_provider()
    called = threading.Event()
    callback_thread = []

    def observe(options):
        callback_thread.append(threading.get_ident())
        called.set()
        return [Observation(1)]

    provider.get_meter("ddtrace.test").create_observable_gauge("queue.depth", callbacks=[observe])

    assert called.wait(5)
    assert callback_thread[0] != threading.get_ident()
    assert not hasattr(provider, "_reader")
    provider.shutdown()


@requires_metrics_api
@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="requires os.fork")
@pytest.mark.subprocess(
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_METRIC_EXPORT_INTERVAL": "60000",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://127.0.0.1:1",
        "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT": "50",
    },
    err=None,
)
def test_native_observable_callbacks_after_fork():
    """A child process can collect observables registered before fork."""
    import os
    import signal

    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    set_otel_meter_provider()

    from opentelemetry.metrics import Observation
    from opentelemetry.metrics import get_meter_provider

    provider = get_meter_provider()
    observations = []

    def observe(options):
        observations.append(os.getpid())
        return [Observation(1)]

    meter = provider.get_meter("ddtrace.test")
    counter = meter.create_counter("requests")
    meter.create_observable_gauge("queue.depth", callbacks=[observe])
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        signal.alarm(10)
        before = len(observations)
        counter.add(1)
        flushed = provider.force_flush()
        collected = len(observations) > before
        os.write(write_fd, f"{flushed}:{collected}".encode())
        os._exit(0)

    os.close(write_fd)
    result = os.read(read_fd, 64).decode()
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert result == "False:True"
    provider.shutdown()


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example:4318",
    },
)
def test_otlp_export_requests_are_not_traced():
    """OTLP exporter requests must not be traced.

    The OTLP HTTP metrics exporter omits the OTLP user-agent header that the trace and log
    exporters set, so detection falls back to matching the enabled export URLs by full path.
    """
    import requests

    from ddtrace.contrib.internal.requests.connection import is_otlp_export

    def prepared(url):
        return requests.Request("POST", url, headers={"User-Agent": "python-requests/2.34.2"}).prepare()

    # Exports for enabled signals are matched by their full URL.
    assert is_otlp_export(prepared("http://collector.example:4318/v1/logs")) is True
    assert is_otlp_export(prepared("http://collector.example:4318/v1/traces")) is True
    # A disabled signal's endpoint, a different path or scheme, and other hosts are user traffic.
    assert is_otlp_export(prepared("http://collector.example:4318/v1/metrics")) is False
    assert is_otlp_export(prepared("http://collector.example:4318/api/data")) is False
    assert is_otlp_export(prepared("https://collector.example:4318/v1/logs")) is False
    assert is_otlp_export(prepared("http://api.example:8080/v1/logs")) is False
