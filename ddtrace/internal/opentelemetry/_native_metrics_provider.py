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

from threading import Lock
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Sequence

from opentelemetry import metrics as otel

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import OtelMetricsAggregatorBuilder
from ddtrace.internal.native_runtime import get_native_runtime
from ddtrace.internal.periodic import PeriodicService


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


class _ObservableCallbackReader(PeriodicService):
    """Resolves registered observable-instrument callbacks once per export interval.

    Only the tracer can execute a user's Python callback, so this scheduling stays on the Python
    side of the primitives-only boundary — the native aggregator just receives resolved values.
    Each value is pushed through the ``feed`` function matching the instrument kind
    (``observe_counter`` / ``observe_gauge`` / ``record_up_down_counter``).
    """

    def __init__(self, aggregator, interval_seconds: float) -> None:
        super().__init__(interval=interval_seconds)
        self._aggregator = aggregator
        self._observables: list[tuple[int, Any, Sequence[Any]]] = []
        self._lock = Lock()

    def register(self, instrument_id: int, feed, callbacks) -> None:
        if not callbacks:
            return
        with self._lock:
            self._observables.append((instrument_id, feed, list(callbacks)))

    def periodic(self) -> None:
        self.collect()

    def on_shutdown(self) -> None:  # type: ignore[override]  # base hook is a no-op staticmethod
        self.collect()

    def collect(self) -> None:
        options = otel.CallbackOptions()
        with self._lock:
            observables = list(self._observables)
        for instrument_id, feed, callbacks in observables:
            for callback in callbacks:
                try:
                    for observation in _iter_observations(callback, options):
                        feed(instrument_id, float(observation.value), _attrs(observation.attributes))
                except Exception:
                    log.debug("Error collecting OpenTelemetry observable instrument", exc_info=True)


class Meter(otel.Meter):
    """A ``Meter`` whose instruments forward to the native aggregator."""

    def __init__(self, aggregator, reader, name, version=None, schema_url=None):
        super().__init__(name, version=version, schema_url=schema_url)
        self._aggregator = aggregator
        self._reader = reader

    def _register(self, name, kind, unit, description) -> int:
        return int(self._aggregator.register_instrument(name, kind, unit or None, description or None))

    def create_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "counter", unit, description)
        return Counter(self._aggregator, instrument_id, name, unit, description)

    def create_up_down_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "up_down_counter", unit, description)
        return UpDownCounter(self._aggregator, instrument_id, name, unit, description)

    def create_histogram(self, name, unit="", description="", *, explicit_bucket_boundaries_advisory=None):
        instrument_id = self._register(name, "histogram", unit, description)
        return Histogram(self._aggregator, instrument_id, name, unit, description)

    def create_observable_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_counter", unit, description)
        self._reader.register(instrument_id, self._aggregator.observe_counter, callbacks)
        return ObservableCounter(self._aggregator, instrument_id, name, callbacks, unit, description)

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_gauge", unit, description)
        self._reader.register(instrument_id, self._aggregator.observe_gauge, callbacks)
        return ObservableGauge(self._aggregator, instrument_id, name, callbacks, unit, description)

    def create_observable_up_down_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_up_down_counter", unit, description)
        self._reader.register(instrument_id, self._aggregator.record_up_down_counter, callbacks)
        return ObservableUpDownCounter(self._aggregator, instrument_id, name, callbacks, unit, description)


class MeterProvider(otel.MeterProvider):
    """A ``MeterProvider`` backed by the native ``OtelMetricsAggregator``."""

    def __init__(self, aggregator, reader):
        self._aggregator = aggregator
        self._reader = reader
        self._meters: dict[tuple[str, Optional[str], Optional[str]], Meter] = {}
        self._lock = Lock()

    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        key = (name, version, schema_url)
        with self._lock:
            meter = self._meters.get(key)
            if meter is None:
                meter = Meter(self._aggregator, self._reader, name, version, schema_url)
                self._meters[key] = meter
            return meter

    def force_flush(self, timeout_millis=10000):
        # Resolve observable instruments first so their latest values are included in the flush.
        self._reader.collect()
        try:
            self._aggregator.force_flush()
        except Exception:
            log.debug("Error flushing native OpenTelemetry metrics", exc_info=True)
            return False
        return True

    def shutdown(self, timeout_millis=30000):
        try:
            self._reader.stop()
        except Exception:
            log.debug("Error stopping observable metrics reader", exc_info=True)
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

    aggregator, warnings = builder.build(get_native_runtime())
    for warning in warnings:
        log.warning("OpenTelemetry metrics aggregator build warning: %s", warning)

    reader = _ObservableCallbackReader(aggregator, export_interval_ms / 1000.0)
    reader.start()
    return MeterProvider(aggregator, reader)
