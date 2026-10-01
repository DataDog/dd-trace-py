"""OpenTelemetry metrics API objects backed by libdatadog's Rust SDK."""

from typing import Any
from typing import Optional

from opentelemetry import metrics as otel

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import build_otel_metrics_provider as _build_native_metrics_provider
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


def _attrs(attributes: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    """Convert OTel attributes to primitive string pairs."""
    if not attributes:
        return []
    pairs = []
    for key, value in attributes.items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        pairs.append((str(key), str(value)))
    return pairs


class _Instrument:
    def __init__(self, native, instrument_id=0):
        self._native = native
        self._id = instrument_id

    def _record(self, amount, attributes):
        self._native.record(self._id, float(amount), _attrs(attributes))


class Counter(_Instrument, otel.Counter):
    def add(self, amount, attributes=None, context=None):
        self._record(amount, attributes)


class UpDownCounter(_Instrument, otel.UpDownCounter):
    def add(self, amount, attributes=None, context=None):
        self._record(amount, attributes)


class Histogram(_Instrument, otel.Histogram):
    def record(self, amount, attributes=None, context=None):
        self._record(amount, attributes)


class ObservableCounter(_Instrument, otel.ObservableCounter):
    pass


class ObservableGauge(_Instrument, otel.ObservableGauge):
    pass


class ObservableUpDownCounter(_Instrument, otel.ObservableUpDownCounter):
    pass


_GaugeBase = getattr(otel, "Gauge", object)


class Gauge(_Instrument, _GaugeBase):  # type: ignore[misc,valid-type]
    def set(self, amount, attributes=None, context=None):
        self._record(amount, attributes)


def _native_observable_callback(callbacks):
    """Adapt OTel callbacks to the primitive measurements returned to Rust during collection."""
    callbacks = list(callbacks or ())

    def collect() -> list[tuple[float, list[tuple[str, str]]]]:
        measurements = []
        options = otel.CallbackOptions()
        for callback in callbacks:
            try:
                for observation in callback(options) or ():
                    measurements.append((float(observation.value), _attrs(observation.attributes)))
            except Exception:
                log.debug("Error collecting OpenTelemetry observable instrument", exc_info=True)
        return measurements

    return collect


class Meter(otel.Meter):
    def __init__(self, native, name, version=None, schema_url=None, attributes=None):
        super().__init__(name, version=version, schema_url=schema_url)
        self._native = native
        self._scope = (name, version, schema_url, _attrs(attributes))
        self._instruments = []

    def _create(self, cls, name, kind, unit, description, callbacks=None, observable=False):
        callback = _native_observable_callback(callbacks) if observable else None
        instrument = cls(self._native)
        instrument._registration = (name, kind, unit or None, description or None, *self._scope, callback)
        instrument._id = int(self._native.register_instrument(*instrument._registration))
        self._instruments.append(instrument)
        return instrument

    def _rebind(self, native):
        self._native = native
        for instrument in self._instruments:
            instrument._native = native
            instrument._id = int(native.register_instrument(*instrument._registration))

    def create_counter(self, name, unit="", description=""):
        return self._create(Counter, name, "counter", unit, description)

    def create_up_down_counter(self, name, unit="", description=""):
        return self._create(UpDownCounter, name, "up_down_counter", unit, description)

    def create_histogram(self, name, unit="", description="", *, explicit_bucket_boundaries_advisory=None):
        return self._create(Histogram, name, "histogram", unit, description)

    def create_gauge(self, name, unit="", description=""):
        return self._create(Gauge, name, "observable_gauge", unit, description)

    def create_observable_counter(self, name, callbacks=None, unit="", description=""):
        return self._create(ObservableCounter, name, "observable_counter", unit, description, callbacks, True)

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        return self._create(ObservableGauge, name, "observable_gauge", unit, description, callbacks, True)

    def create_observable_up_down_counter(self, name, callbacks=None, unit="", description=""):
        return self._create(
            ObservableUpDownCounter, name, "observable_up_down_counter", unit, description, callbacks, True
        )


class MeterProvider(otel.MeterProvider):
    """A ``MeterProvider`` backed by the Rust OpenTelemetry metrics SDK."""

    def __init__(self, native, native_factory, protocol):
        self._native = native
        self._native_factory = native_factory
        self._telemetry_tags = (("protocol", "grpc" if protocol == "grpc" else "http"), ("encoding", "protobuf"))
        self._reported_export_counters = (0, 0, 0)
        self._orphaned_after_fork = []
        self._meters: dict[tuple[str, Optional[str], Optional[str], tuple[tuple[str, str], ...]], Meter] = {}
        self._lock = forksafe.Lock()
        self._shutdown = False
        self._atexit = self.shutdown
        atexit.register(self._atexit)
        forksafe.register(self._after_fork)

    def _after_fork(self):
        if self._shutdown:
            return
        old_native = self._native
        self._native = self._native_factory()
        self._reported_export_counters = (0, 0, 0)
        # Its native reader thread disappeared at fork, so defer destruction until process exit.
        self._orphaned_after_fork.append(old_native)
        for meter in self._meters.values():
            meter._rebind(self._native)

    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        scope_attributes = tuple(sorted(_attrs(attributes)))
        key = (name, version, schema_url, scope_attributes)
        with self._lock:
            meter = self._meters.get(key)
            if meter is None:
                meter = Meter(self._native, name, version, schema_url, attributes)
                self._meters[key] = meter
            return meter

    def force_flush(self, timeout_millis=10000):
        try:
            self._native.force_flush()
        except Exception:
            log.debug("Error flushing native OpenTelemetry metrics", exc_info=True)
            self._report_export_telemetry()
            return False
        self._report_export_telemetry()
        return True

    def _report_export_telemetry(self):
        counters = self._native.export_counters()
        names = ("otel.metrics_export_attempts", "otel.metrics_export_successes", "otel.metrics_export_failures")
        for name, current, previous in zip(names, counters, self._reported_export_counters):
            if current > previous:
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.TRACERS, name, current - previous, self._telemetry_tags
                )
        self._reported_export_counters = counters

    def shutdown(self, timeout_millis=30000):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        atexit.unregister(self._atexit)
        forksafe.unregister(self._after_fork)
        try:
            self._native.shutdown()
        except Exception:
            log.debug("Error shutting down native OpenTelemetry metrics", exc_info=True)


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

    def build_native_provider():
        native, warnings = _build_native_metrics_provider(
            service,
            env,
            version,
            [(str(key), str(value)) for key, value in resource_attributes.items()],
            endpoint,
            protocol,
            timeout_ms,
            headers,
            temporality,
            export_interval_ms,
        )
        for warning in warnings:
            log.warning("OpenTelemetry metrics provider build warning: %s", warning)
        return native

    return MeterProvider(build_native_provider(), build_native_provider, protocol)
