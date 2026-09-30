"""An OpenTelemetry ``MeterProvider`` backed by libdatadog's native telemetry aggregator.

This implements the ``opentelemetry-api`` metrics interfaces (``MeterProvider`` / ``Meter`` / the
instrument types) as a thin shim: every call is forwarded into the native ``OtelMetricsAggregator``
over a primitives-only boundary — opaque instrument ids, float values, and string attribute pairs.
libdatadog owns aggregation, resource building, and OTLP export, so ddtrace needs neither the
``opentelemetry-sdk`` nor ``opentelemetry-exporter-otlp`` packages for metrics.

Classes mirror the OpenTelemetry SDK's own naming (``MeterProvider``, ``Meter``, ``Counter`` …)
so this reads as a drop-in OTel implementation. Only the metrics signal is handled here; logs
still use the SDK path.
"""

from typing import Any
from typing import Iterable
from typing import Optional

from opentelemetry import metrics as otel

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import OtelMetricsAggregatorBuilder


log = get_logger(__name__)


def _attrs(attributes: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    """Flatten OTel attributes to the string key/value pairs the native boundary accepts.

    The native aggregator currently only accepts string attribute values; non-string values are
    stringified. Sequence values are joined so a single instrument call never explodes into many.
    """
    if not attributes:
        return []
    pairs = []
    for key, value in attributes.items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        pairs.append((str(key), str(value)))
    return pairs


def _iter_observations(callback: Any, options: "otel.CallbackOptions") -> Iterable[Any]:
    """Invoke a single observable-instrument callback and return its ``Observation``s.

    Supports the plain-callable form ``cb(options) -> Iterable[Observation]``, which is what
    virtually all instrumentation uses. Generator-style callbacks are best-effort iterated.
    """
    result = callback(options) if callable(callback) else callback
    if result is None:
        return []
    return list(result)


class Counter(otel.Counter):
    def __init__(self, aggregator, instrument_id, name, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id

    def add(self, amount, attributes=None, context=None):
        self._aggregator.record_counter(self._id, float(amount), _attrs(attributes))


class UpDownCounter(otel.UpDownCounter):
    def __init__(self, aggregator, instrument_id, name, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id

    def add(self, amount, attributes=None, context=None):
        self._aggregator.record_up_down_counter(self._id, float(amount), _attrs(attributes))


class Histogram(otel.Histogram):
    def __init__(
        self, aggregator, instrument_id, name, unit="", description="", explicit_bucket_boundaries_advisory=None
    ):
        self._aggregator = aggregator
        self._id = instrument_id

    def record(self, amount, attributes=None, context=None):
        self._aggregator.record_histogram(self._id, float(amount), _attrs(attributes))


class ObservableCounter(otel.ObservableCounter):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


class ObservableGauge(otel.ObservableGauge):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


class ObservableUpDownCounter(otel.ObservableUpDownCounter):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


# Synchronous Gauge: subclass opentelemetry's `Gauge` when the running API exports it, otherwise a
# plain object. `Meter.create_gauge` exists as a base no-op from ~1.22, but some versions don't
# re-export the `Gauge` class from `opentelemetry.metrics`, so never depend on it being importable —
# the caller only needs `.set()`.
_GaugeBase = getattr(otel, "Gauge", object)


class Gauge(_GaugeBase):  # type: ignore[misc,valid-type]
    def __init__(self, aggregator, instrument_id, name, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id

    def set(self, amount, attributes=None, context=None):
        self._aggregator.observe_gauge(self._id, float(amount), _attrs(attributes))


def _native_observable_callback(callbacks):
    """Adapt OTel callbacks to the primitive measurements returned to Rust during collection."""
    callbacks = list(callbacks or ())

    def collect() -> list[tuple[float, list[tuple[str, str]]]]:
        measurements = []
        options = otel.CallbackOptions()
        for callback in callbacks:
            try:
                for observation in _iter_observations(callback, options):
                    measurements.append((float(observation.value), _attrs(observation.attributes)))
            except Exception:
                log.debug("Error collecting OpenTelemetry observable instrument", exc_info=True)
        return measurements

    return collect


class Meter(otel.Meter):
    """A ``Meter`` whose instruments forward to the native aggregator."""

    def __init__(self, aggregator, name, version=None, schema_url=None):
        super().__init__(name, version=version, schema_url=schema_url)
        self._aggregator = aggregator
        # Preserve this meter's instrumentation scope so exported metrics carry it (not the
        # crate's internal meter name).
        self._meter_name = name
        self._meter_version = version
        self._meter_schema_url = schema_url
        self._instruments = []

    def _track(self, instrument, name, kind, unit, description, native_callback=None):
        instrument._name = name
        instrument._kind = kind
        instrument._unit = unit
        instrument._description = description
        instrument._native_callback = native_callback
        self._instruments.append(instrument)
        return instrument

    def _rebind(self, aggregator):
        self._aggregator = aggregator
        for instrument in self._instruments:
            if instrument._native_callback is None:
                instrument_id = self._register(
                    instrument._name,
                    instrument._kind,
                    instrument._unit,
                    instrument._description,
                )
            else:
                instrument_id = int(
                    aggregator.register_observable_instrument(
                        instrument._name,
                        instrument._kind,
                        instrument._unit or None,
                        instrument._description or None,
                        self._meter_name,
                        self._meter_version,
                        self._meter_schema_url,
                        instrument._native_callback,
                    )
                )
            instrument._aggregator = aggregator
            instrument._id = instrument_id

    def _register(self, name, kind, unit, description) -> int:
        return int(
            self._aggregator.register_instrument(
                name,
                kind,
                unit or None,
                description or None,
                self._meter_name,
                self._meter_version,
                self._meter_schema_url,
            )
        )

    def _register_observable(self, name, kind, callbacks, unit, description):
        native_callback = _native_observable_callback(callbacks)
        instrument_id = int(
            self._aggregator.register_observable_instrument(
                name,
                kind,
                unit or None,
                description or None,
                self._meter_name,
                self._meter_version,
                self._meter_schema_url,
                native_callback,
            )
        )
        return instrument_id, native_callback

    def create_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "counter", unit, description)
        return self._track(
            Counter(self._aggregator, instrument_id, name, unit, description),
            name,
            "counter",
            unit,
            description,
        )

    def create_up_down_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "up_down_counter", unit, description)
        return self._track(
            UpDownCounter(self._aggregator, instrument_id, name, unit, description),
            name,
            "up_down_counter",
            unit,
            description,
        )

    def create_histogram(self, name, unit="", description="", *, explicit_bucket_boundaries_advisory=None):
        instrument_id = self._register(name, "histogram", unit, description)
        return self._track(
            Histogram(self._aggregator, instrument_id, name, unit, description),
            name,
            "histogram",
            unit,
            description,
        )

    def create_gauge(self, name, unit="", description=""):
        # Synchronous gauge, backed by the same SDK gauge handle as an observable gauge; set()
        # pushes the value via observe_gauge. Always return our implementation (never the API's
        # base no-op), regardless of whether the running API re-exports the Gauge class.
        instrument_id = self._register(name, "observable_gauge", unit, description)
        return self._track(
            Gauge(self._aggregator, instrument_id, name, unit, description),
            name,
            "observable_gauge",
            unit,
            description,
        )

    def create_observable_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id, native_callback = self._register_observable(
            name, "observable_counter", callbacks, unit, description
        )
        return self._track(
            ObservableCounter(self._aggregator, instrument_id, name, callbacks, unit, description),
            name,
            "observable_counter",
            unit,
            description,
            native_callback,
        )

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        instrument_id, native_callback = self._register_observable(
            name, "observable_gauge", callbacks, unit, description
        )
        return self._track(
            ObservableGauge(self._aggregator, instrument_id, name, callbacks, unit, description),
            name,
            "observable_gauge",
            unit,
            description,
            native_callback,
        )

    def create_observable_up_down_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id, native_callback = self._register_observable(
            name, "observable_up_down_counter", callbacks, unit, description
        )
        return self._track(
            ObservableUpDownCounter(self._aggregator, instrument_id, name, callbacks, unit, description),
            name,
            "observable_up_down_counter",
            unit,
            description,
            native_callback,
        )


class MeterProvider(otel.MeterProvider):
    """A ``MeterProvider`` backed by the native ``OtelMetricsAggregator``."""

    def __init__(self, aggregator, aggregator_factory):
        self._aggregator = aggregator
        self._aggregator_factory = aggregator_factory
        self._orphaned_after_fork = []
        self._meters: dict[tuple[str, Optional[str], Optional[str]], Meter] = {}
        self._lock = forksafe.Lock()
        self._shutdown = False
        self._atexit = self.shutdown
        atexit.register(self._atexit)
        forksafe.register(self._after_fork)

    def _after_fork(self):
        if self._shutdown:
            return
        old_aggregator = self._aggregator
        self._aggregator = self._aggregator_factory()
        # Its native reader thread disappeared at fork, so defer destruction until process exit.
        self._orphaned_after_fork.append(old_aggregator)
        for meter in self._meters.values():
            meter._rebind(self._aggregator)

    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        key = (name, version, schema_url)
        with self._lock:
            meter = self._meters.get(key)
            if meter is None:
                meter = Meter(self._aggregator, name, version, schema_url)
                self._meters[key] = meter
            return meter

    def force_flush(self, timeout_millis=10000):
        try:
            self._aggregator.force_flush()
        except Exception:
            log.debug("Error flushing native OpenTelemetry metrics", exc_info=True)
            return False
        return True

    def shutdown(self, timeout_millis=30000):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        atexit.unregister(self._atexit)
        forksafe.unregister(self._after_fork)
        try:
            self._aggregator.shutdown()
        except Exception:
            log.debug("Error shutting down native OpenTelemetry metrics", exc_info=True)


def _parse_headers(headers: str) -> list[tuple[str, str]]:
    """Parse an ``OTEL_EXPORTER_OTLP_*_HEADERS`` string (``k1=v1,k2=v2``) into pairs."""
    pairs = []
    for item in headers.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        pairs.append((key.strip(), value.strip()))
    return pairs


def build_meter_provider(
    service: Optional[str],
    env: Optional[str],
    version: Optional[str],
    resource_attributes: dict[str, str],
    endpoint: str,
    protocol: str,
    timeout_ms: int,
    headers: str,
    temporality: str,
    export_interval_ms: int,
) -> MeterProvider:
    """Construct a native-backed ``MeterProvider`` wired to the OTLP metrics exporter.

    ``service``/``env``/``version`` are passed as primitives; the native ResourceBuilder owns the
    mapping to OTel semantic-convention keys (``service.name`` etc.) and Datadog's precedence
    rules, so this shim never hardcodes those keys. ``resource_attributes`` carries only the
    remaining generic attributes (e.g. DD_TAGS, host.name).
    """

    def build_aggregator():
        builder = OtelMetricsAggregatorBuilder()
        if service:
            builder = builder.set_resource_service(service)
        if env:
            builder = builder.set_resource_env(env)
        if version:
            builder = builder.set_resource_version(version)
        for key, value in resource_attributes.items():
            builder = builder.set_resource_attribute(str(key), str(value))
        builder = builder.set_metrics_exporter(endpoint, protocol, timeout_ms, _parse_headers(headers))
        builder = builder.set_metrics_temporality(temporality)
        builder = builder.set_export_interval(export_interval_ms)

        aggregator, warnings = builder.build()
        for warning in warnings:
            log.warning("OpenTelemetry metrics aggregator build warning: %s", warning)
        return aggregator

    return MeterProvider(build_aggregator(), build_aggregator)
