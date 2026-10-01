"""OpenTelemetry metrics API objects backed by libdatadog's Rust SDK."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Optional
from typing import TypeVar
from typing import Union

from opentelemetry import metrics as otel
from opentelemetry.context import Context
from opentelemetry.util.types import Attributes
from opentelemetry.util.types import AttributeValue

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import build_otel_metrics_provider as _build_native_metrics_provider
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


MeasurementValue = Union[int, float]
AttributePairs = list[tuple[str, AttributeValue]]
_InstrumentT = TypeVar("_InstrumentT", bound="_Instrument")


def _attrs(attributes: Attributes) -> AttributePairs:
    """Freeze OTel attribute sequences while preserving their value types."""
    if not attributes:
        return []
    pairs: AttributePairs = []
    for key, value in attributes.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            value = tuple(value)
        pairs.append((key, value))
    return pairs


class _Instrument:
    def __init__(self, native: Any, instrument_id: int = 0) -> None:
        self._native = native
        self._id = instrument_id
        self._registration: tuple[Any, ...] = ()

    def _record(self, amount: MeasurementValue, attributes: Attributes) -> None:
        self._native.record(self._id, float(amount), _attrs(attributes))


class Counter(_Instrument, otel.Counter):
    def add(self, amount: MeasurementValue, attributes: Attributes = None, context: Optional[Context] = None) -> None:
        self._record(amount, attributes)


class UpDownCounter(_Instrument, otel.UpDownCounter):
    def add(self, amount: MeasurementValue, attributes: Attributes = None, context: Optional[Context] = None) -> None:
        self._record(amount, attributes)


class Histogram(_Instrument, otel.Histogram):
    def record(
        self, amount: MeasurementValue, attributes: Attributes = None, context: Optional[Context] = None
    ) -> None:
        self._record(amount, attributes)


class ObservableCounter(_Instrument, otel.ObservableCounter):
    pass


class ObservableGauge(_Instrument, otel.ObservableGauge):
    pass


class ObservableUpDownCounter(_Instrument, otel.ObservableUpDownCounter):
    pass


_GaugeBase = getattr(otel, "Gauge", object)


class Gauge(_Instrument, _GaugeBase):  # type: ignore[misc,valid-type]
    def set(self, amount: MeasurementValue, attributes: Attributes = None, context: Optional[Context] = None) -> None:
        self._record(amount, attributes)


def _native_observable_callback(
    callbacks: Optional[Iterable[Callable[[otel.CallbackOptions], Iterable[otel.Observation]]]],
) -> Callable[[], list[tuple[float, AttributePairs]]]:
    """Adapt OTel callbacks to the primitive measurements returned to Rust during collection."""
    callbacks = list(callbacks or ())

    def collect() -> list[tuple[float, AttributePairs]]:
        measurements: list[tuple[float, AttributePairs]] = []
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
    def __init__(
        self,
        native: Any,
        name: str,
        version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Attributes = None,
    ) -> None:
        super().__init__(name, version=version, schema_url=schema_url)
        self._native = native
        self._scope = (name, version, schema_url, _attrs(attributes))
        self._instruments: list[_Instrument] = []

    def _create(
        self,
        cls: type[_InstrumentT],
        name: str,
        kind: str,
        unit: str,
        description: str,
        callbacks: Optional[Iterable[Callable[[otel.CallbackOptions], Iterable[otel.Observation]]]] = None,
        observable: bool = False,
    ) -> _InstrumentT:
        callback = _native_observable_callback(callbacks) if observable else None
        instrument = cls(self._native)
        instrument._registration = (name, kind, unit or None, description or None, *self._scope, callback)
        instrument._id = int(self._native.register_instrument(*instrument._registration))
        self._instruments.append(instrument)
        return instrument

    def _rebind(self, native: Any) -> None:
        self._native = native
        for instrument in self._instruments:
            instrument._native = native
            instrument._id = int(native.register_instrument(*instrument._registration))

    def create_counter(self, name: str, unit: str = "", description: str = "") -> Counter:
        return self._create(Counter, name, "counter", unit, description)

    def create_up_down_counter(self, name: str, unit: str = "", description: str = "") -> UpDownCounter:
        return self._create(UpDownCounter, name, "up_down_counter", unit, description)

    def create_histogram(
        self,
        name: str,
        unit: str = "",
        description: str = "",
        *,
        explicit_bucket_boundaries_advisory: Optional[Sequence[float]] = None,
    ) -> Histogram:
        return self._create(Histogram, name, "histogram", unit, description)

    def create_gauge(self, name: str, unit: str = "", description: str = "") -> Gauge:
        return self._create(Gauge, name, "observable_gauge", unit, description)

    def create_observable_counter(
        self,
        name: str,
        callbacks: Optional[Iterable[Callable[[otel.CallbackOptions], Iterable[otel.Observation]]]] = None,
        unit: str = "",
        description: str = "",
    ) -> ObservableCounter:
        return self._create(ObservableCounter, name, "observable_counter", unit, description, callbacks, True)

    def create_observable_gauge(
        self,
        name: str,
        callbacks: Optional[Iterable[Callable[[otel.CallbackOptions], Iterable[otel.Observation]]]] = None,
        unit: str = "",
        description: str = "",
    ) -> ObservableGauge:
        return self._create(ObservableGauge, name, "observable_gauge", unit, description, callbacks, True)

    def create_observable_up_down_counter(
        self,
        name: str,
        callbacks: Optional[Iterable[Callable[[otel.CallbackOptions], Iterable[otel.Observation]]]] = None,
        unit: str = "",
        description: str = "",
    ) -> ObservableUpDownCounter:
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
        self._meters: dict[tuple[str, Optional[str], Optional[str], tuple[tuple[str, AttributeValue], ...]], Meter] = {}
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

    def get_meter(
        self,
        name: str,
        version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Attributes = None,
    ) -> Meter:
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
    resource_attributes: Mapping[str, AttributeValue],
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
            _attrs(resource_attributes),
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
