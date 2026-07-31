from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])

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

    def _observe(options: CallbackOptions):
        return [Observation(42, {"pool": "default"})]

    meter.create_observable_gauge("pool.depth", callbacks=[_observe])
    meter.create_observable_counter("cache.hits", callbacks=[_observe])
    meter.create_observable_up_down_counter("pool.available", callbacks=[_observe])

    # Flushing resolves the observable callbacks and drives the native exporter. It must never
    # raise and always returns a bool, whether or not a collector is actually reachable (export
    # failures are reported as False, not exceptions).
    assert isinstance(provider.force_flush(), bool)
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
