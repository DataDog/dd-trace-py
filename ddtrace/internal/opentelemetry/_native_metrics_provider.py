"""A ``MeterProvider`` backed by libdatadog's native OTel telemetry aggregator.

This implements the ``opentelemetry-api`` metrics interfaces (``MeterProvider`` /
``Meter`` / the instrument types) and forwards every call into the native
``TelemetryAggregator`` over a primitives-only boundary — opaque instrument ids,
float values, and string attribute pairs. It lets ddtrace aggregate and export
OTLP metrics without depending on the ``opentelemetry-sdk`` or
``opentelemetry-exporter-otlp`` packages.

Only the metrics signal is handled here; logs still use the SDK path.
"""

from threading import Lock
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Sequence

from opentelemetry.metrics import CallbackOptions
from opentelemetry.metrics import Counter
from opentelemetry.metrics import Histogram
from opentelemetry.metrics import Meter
from opentelemetry.metrics import MeterProvider
from opentelemetry.metrics import ObservableCounter
from opentelemetry.metrics import ObservableGauge
from opentelemetry.metrics import ObservableUpDownCounter
from opentelemetry.metrics import UpDownCounter

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import TelemetryAggregatorBuilder
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


def _iter_observations(callback: Any, options: CallbackOptions) -> Iterable[Any]:
    """Invoke a single observable-instrument callback and yield its ``Observation``s.

    Supports the plain-callable form ``cb(options) -> Iterable[Observation]``, which is what
    virtually all instrumentation uses. Generator-style callbacks are best-effort iterated.
    """
    result = callback(options) if callable(callback) else callback
    if result is None:
        return []
    return result


class _NativeCounter(Counter):
    def __init__(self, aggregator, instrument_id, name, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id

    def add(self, amount, attributes=None, context=None):
        self._aggregator.record_counter(self._id, float(amount), _attrs(attributes))


class _NativeUpDownCounter(UpDownCounter):
    def __init__(self, aggregator, instrument_id, name, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id

    def add(self, amount, attributes=None, context=None):
        self._aggregator.record_up_down_counter(self._id, float(amount), _attrs(attributes))


class _NativeHistogram(Histogram):
    def __init__(
        self, aggregator, instrument_id, name, unit="", description="", explicit_bucket_boundaries_advisory=None
    ):
        self._aggregator = aggregator
        self._id = instrument_id

    def record(self, amount, attributes=None, context=None):
        self._aggregator.record_histogram(self._id, float(amount), _attrs(attributes))


class _NativeObservableCounter(ObservableCounter):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


class _NativeObservableGauge(ObservableGauge):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


class _NativeObservableUpDownCounter(ObservableUpDownCounter):
    def __init__(self, aggregator, instrument_id, name, callbacks=None, unit="", description=""):
        self._aggregator = aggregator
        self._id = instrument_id


class _ObservableScheduler(PeriodicService):
    """Polls registered observable-instrument callbacks once per export interval.

    Callback *scheduling* is the tracer's responsibility (only it can run a user closure); the
    native aggregator just receives the resolved values. Each resolved value is pushed through the
    ``feed`` function matching the instrument kind (``observe_counter`` / ``observe_gauge`` /
    ``record_up_down_counter``).
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
        self._collect()

    def on_shutdown(self) -> None:
        self._collect()

    def _collect(self) -> None:
        options = CallbackOptions()
        with self._lock:
            observables = list(self._observables)
        for instrument_id, feed, callbacks in observables:
            for callback in callbacks:
                try:
                    for observation in _iter_observations(callback, options):
                        feed(instrument_id, float(observation.value), _attrs(observation.attributes))
                except Exception:
                    log.debug("Error collecting OpenTelemetry observable instrument", exc_info=True)


class NativeMeter(Meter):
    """A ``Meter`` whose instruments forward to the native aggregator."""

    def __init__(self, aggregator, scheduler, name, version=None, schema_url=None):
        super().__init__(name, version=version, schema_url=schema_url)
        self._aggregator = aggregator
        self._scheduler = scheduler

    def _register(self, name, kind, unit, description) -> int:
        return self._aggregator.register_instrument(name, kind, unit or None, description or None)

    def create_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "counter", unit, description)
        return _NativeCounter(self._aggregator, instrument_id, name, unit, description)

    def create_up_down_counter(self, name, unit="", description=""):
        instrument_id = self._register(name, "up_down_counter", unit, description)
        return _NativeUpDownCounter(self._aggregator, instrument_id, name, unit, description)

    def create_histogram(self, name, unit="", description="", *, explicit_bucket_boundaries_advisory=None):
        instrument_id = self._register(name, "histogram", unit, description)
        return _NativeHistogram(self._aggregator, instrument_id, name, unit, description)

    def create_observable_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_counter", unit, description)
        self._scheduler.register(instrument_id, self._aggregator.observe_counter, callbacks)
        return _NativeObservableCounter(self._aggregator, instrument_id, name, callbacks, unit, description)

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_gauge", unit, description)
        self._scheduler.register(instrument_id, self._aggregator.observe_gauge, callbacks)
        return _NativeObservableGauge(self._aggregator, instrument_id, name, callbacks, unit, description)

    def create_observable_up_down_counter(self, name, callbacks=None, unit="", description=""):
        instrument_id = self._register(name, "observable_up_down_counter", unit, description)
        self._scheduler.register(instrument_id, self._aggregator.record_up_down_counter, callbacks)
        return _NativeObservableUpDownCounter(self._aggregator, instrument_id, name, callbacks, unit, description)


class NativeMeterProvider(MeterProvider):
    """A ``MeterProvider`` backed by the native ``TelemetryAggregator``."""

    def __init__(self, aggregator, scheduler):
        self._aggregator = aggregator
        self._scheduler = scheduler
        self._meters: dict[tuple[str, Optional[str], Optional[str]], NativeMeter] = {}
        self._lock = Lock()

    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        key = (name, version, schema_url)
        with self._lock:
            meter = self._meters.get(key)
            if meter is None:
                meter = NativeMeter(self._aggregator, self._scheduler, name, version, schema_url)
                self._meters[key] = meter
            return meter

    def force_flush(self, timeout_millis=10000):
        # Resolve observable instruments first so their latest values are included in the flush.
        self._scheduler._collect()
        try:
            self._aggregator.force_flush()
        except Exception:
            log.debug("Error flushing native OpenTelemetry metrics", exc_info=True)
            return False
        return True

    def shutdown(self, timeout_millis=30000):
        try:
            self._scheduler.stop()
        except Exception:
            log.debug("Error stopping observable metrics scheduler", exc_info=True)
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


def build_native_meter_provider(
    resource_attributes: dict[str, str],
    endpoint: str,
    protocol: str,
    timeout_ms: int,
    headers: str,
    temporality: str,
    export_interval_ms: int,
) -> NativeMeterProvider:
    """Construct a ``NativeMeterProvider`` wired to the native OTLP metrics exporter."""
    builder = TelemetryAggregatorBuilder()
    for key, value in resource_attributes.items():
        builder = builder.set_resource_attribute(str(key), str(value))
    builder = builder.set_metrics_exporter(endpoint, protocol, timeout_ms, _parse_headers(headers))
    builder = builder.set_metrics_temporality(temporality)
    builder = builder.set_export_interval(export_interval_ms)

    aggregator, warnings = builder.build(get_native_runtime())
    for warning in warnings:
        log.warning("OpenTelemetry metrics aggregator build warning: %s", warning)

    scheduler = _ObservableScheduler(aggregator, export_interval_ms / 1000.0)
    scheduler.start()
    return NativeMeterProvider(aggregator, scheduler)
