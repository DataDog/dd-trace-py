from typing import TYPE_CHECKING

from opentelemetry import version
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter as OtelMeter
from opentelemetry.metrics import MeterProvider as OtelMeterProvider

import ddtrace
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.logger import get_logger
from ddtrace.settings._agent import config as agent_config

from .instrument import Counter, UpDownCounter, Gauge, ObservableCounter, ObservableUpDownCounter, ObservableGauge, Histogram
from .metric_points import MetricsData


if TYPE_CHECKING:
    from typing import Dict  # noqa:F401
    from typing import Iterator  # noqa:F401
    from typing import Mapping  # noqa:F401
    from typing import Optional  # noqa:F401
    from typing import Sequence  # noqa:F401
    from typing import Union  # noqa:F401

    from opentelemetry.trace import Link as OtelLink  # noqa:F401
    from opentelemetry.util.types import AttributeValue as OtelAttributeValue  # noqa:F401

    from ddtrace._trace.span import _MetaDictType  # noqa:F401
    from ddtrace.trace import Tracer as DDTracer  # noqa:F401


log = get_logger(__name__)


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])

class PeriodicExportingMetricReader():
    def __init__(self, exporter):
        self._exporter = exporter
        super().__init__()
    
    def collect(self, collection_context):
        return self._exporter.collect()

    def _receive_metrics(
            self,
            metrics_data: MetricsData,
            timeout_millis: float = 10_000,
            **kwargs,
        ) -> None:
            token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
            # pylint: disable=broad-exception-caught,invalid-name
            try:
                with self._export_lock:
                    self._exporter.export(
                        metrics_data, timeout_millis=timeout_millis
                    )
            except Exception:
                _logger.exception("Exception while exporting metrics")
            detach(token)

class MeterProvider(OtelMeterProvider):
    """
    Entry point of the OpenTelemetry API and provides access to OpenTelemetry compatible Meters.
    One MeterProvider should be initialized and set per application.
    """

    def __init__(self) -> None:
        self._exporter = OTLPMetricExporter()
        super().__init__()

    if OTEL_VERSION >= (1, 26):
        # OpenTelemetry 1.26+ has a new get_tracer signature
        # https://github.com/open-telemetry/opentelemetry-python/commit/78c19dcd764983be83d07faeca21abf3d2061a52
        # The new signature includes an `attributes` parameter which is used by opentelemetry internals.
        def get_meter(self, name, version = None, schema_url = None, attributes = None):
            return Meter(name, version, schema_url, self._exporter)
    else:
        def get_meter(self, name, version = None, schema_url = None):
            return Meter(name, version, schema_url, self._exporter)

class Meter(OtelMeter):
    """Starts and/or activates OpenTelemetry compatible Metrics using the global Datadog Meter."""

    def __init__(self, name, version = None, schema_url = None, exporter = None):
        self._name = name
        self._version = version
        self._schema_url = schema_url
        self._exporter = exporter
        super().__init__(name, version=version, schema_url=schema_url)

    def create_counter(self, name, unit = "", description = "") -> Counter:
        log.debug("Executed create_counter(name=%s, unit=%s, description=%s)", name, unit, description)
        return Counter(self._exporter, name, unit, description)

    def create_up_down_counter(self, name, unit = "", description = "") -> UpDownCounter:
        log.debug("Executed create_up_down_counter(name=%s, unit=%s, description=%s)", name, unit, description)
        return UpDownCounter(self._exporter, name, unit, description)

    def create_gauge(self, name, unit = "", description = "") -> ObservableGauge:
        log.debug("Executed create_gauge(name=%s, unit=%s, description=%s)", name, unit, description)
        return Gauge(self._exporter, name, unit, description)

    def create_observable_counter(self, name, callbacks = None, unit = "", description = "") -> ObservableCounter:
        log.debug("Executed create_observable_counter(name=%s, callbacks=<OBJ>, unit=%s, description=%s)", name, unit, description)
        return ObservableCounter(self._exporter, name, callbacks, unit, description)

    def create_observable_up_down_counter(self, name, callbacks = None,  unit = "", description = "") -> ObservableUpDownCounter:
        log.debug("Executed create_observable_up_down_counter(name=%s, callbacks=<OBJ>, unit=%s, description=%s)", name, unit, description)
        return ObservableUpDownCounter(self._exporter, name, callbacks, unit, description)

    def create_observable_gauge(self, name, callbacks = None, unit = "", description = "") -> ObservableGauge:
        log.debug("Executed create_observable_gauge(name=%s, callbacks=<OBJ>, unit=%s, description=%s)", name, unit, description)
        return ObservableGauge(self._exporter, name, callbacks, unit, description)

    def create_histogram(self, name, unit = "", description = "") -> Histogram:
        log.debug("Executed create_histogram(name=%s, unit=%s, description=%s)", name, unit, description)
        return Histogram(self._exporter, name, unit, description)