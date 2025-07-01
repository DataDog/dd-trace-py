from os import environ
from typing import TYPE_CHECKING, Optional
from threading import Event, Lock, Thread
import math
import weakref
import os

from opentelemetry import version
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter as OtelMeter
from opentelemetry.metrics import MeterProvider as OtelMeterProvider
from opentelemetry.metrics import CallbackOptions

import ddtrace
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.logger import get_logger
from ddtrace.settings._agent import config as agent_config

from ddtrace.internal.opentelemetry.sdk.resources import Resource
from ddtrace.internal.opentelemetry.sdk.instrumentation import InstrumentationScope
from ddtrace.internal.opentelemetry.instrument import Counter, UpDownCounter, Gauge, ObservableCounter, ObservableUpDownCounter, ObservableGauge, Histogram
from ddtrace.internal.opentelemetry.metric_points import MetricsData, ResourceMetrics, ScopeMetrics

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
    def __init__(self, exporter, export_interval_millis: Optional[float] = None,):
        super().__init__()

        # This lock is held whenever calling self._exporter.export() to prevent concurrent
        # execution of MetricExporter.export()
        # https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/metrics/sdk.md#exportbatch
        self._export_lock = Lock()

        # Combine the functionality of the SDK's SynchronousMeasurementConsumer into this class
        self._async_instruments_lock = Lock()
        self._async_instruments = []

        self._exporter = exporter
        self._export_interval_millis = export_interval_millis
        if export_interval_millis is None:
            try:
                export_interval_millis = float(
                    environ.get("OTEL_METRIC_EXPORT_INTERVAL", 10000)
                )
            except ValueError:
                log.warning(
                    "Found invalid value for export interval, using default"
                )
                export_interval_millis = 10000
        log.warning("Export interval: %s", export_interval_millis)
        self._export_interval_millis = export_interval_millis
        self._export_timeout_millis = 30000
        self._shutdown = False
        self._shutdown_event = Event()
        self._daemon_thread = None
        if (
            self._export_interval_millis > 0
            and self._export_interval_millis < math.inf
        ):
            self._daemon_thread = Thread(
                name="DatadogPeriodicExportingMetricReader",
                target=self._ticker,
                daemon=True,
            )
            self._daemon_thread.start()
            if hasattr(os, "register_at_fork"):
                weak_at_fork = weakref.WeakMethod(self._at_fork_reinit)

                os.register_at_fork(
                    after_in_child=lambda: weak_at_fork()()  # pylint: disable=unnecessary-lambda
                )
        elif self._export_interval_millis <= 0:
            raise ValueError(
                f"interval value {self._export_interval_millis} is invalid \
                and needs to be larger than zero."
            )
    def _at_fork_reinit(self):
        self._daemon_thread = Thread(
            name="DatadogPeriodicExportingMetricReader",
            target=self._ticker,
            daemon=True,
        )
        self._daemon_thread.start()

    def _ticker(self) -> None:
        interval_secs = self._export_interval_millis / 1e3
        while not self._shutdown_event.wait(interval_secs):
            try:
                log.warning("PeriodicExportingMetricReader._ticker: Collecting metrics")
                self.collect(timeout_millis=self._export_timeout_millis)
            except Exception:
                log.exception("Exception while collecting metrics")
                log.warning(
                    "Metric collection timed out. Will try again after %s seconds.",
                    interval_secs,
                )
        # one last collection below before shutting down completely
        try:
            self.collect(timeout_millis=self._export_timeout_millis)
        except Exception:
            log.exception("Exception while collecting metrics")
            log.warning(
                "Metric collection timed out.",
            )

    def collect(self, timeout_millis: float = 10_000):
        metrics = self._collect(timeout_millis=timeout_millis)
        if len(metrics) == 0:
            return

        metrics_data = MetricsData(
            resource_metrics=[
                ResourceMetrics(
                    resource=Resource(
                        {
                            "language": "python",
                            "telemetry.sdk.name": "datadog",
                            "poc": "true",
                        }
                    ),
                    scope_metrics=[ScopeMetrics(scope=InstrumentationScope(name="dd-trace-py"), metrics=metrics, schema_url="")],
                    schema_url="",
                )
            ]
        )
        log.warning("PeriodicExportingMetricReader.collect: Exporting metrics")
        return self._exporter.export(metrics_data)

    def _collect(self, timeout_millis: float = 10_000):
        metrics = []
        callback_options = CallbackOptions()
        for async_instrument in self._async_instruments:
            resulting_metrics = async_instrument.callback(callback_options)
            for result in resulting_metrics:
                metrics.append(result)

        return metrics


    def register_async_instrument(self, instrument):
        with self._async_instruments_lock:
            self._async_instruments.append(instrument)


class MeterProvider(OtelMeterProvider):
    """
    Entry point of the OpenTelemetry API and provides access to OpenTelemetry compatible Meters.
    One MeterProvider should be initialized and set per application.
    """

    def __init__(self, metric_reader = None) -> None:
        self._metric_reader = metric_reader
        self._exporter = metric_reader._exporter
        super().__init__()

    if OTEL_VERSION >= (1, 26):
        # OpenTelemetry 1.26+ has a new get_tracer signature
        # https://github.com/open-telemetry/opentelemetry-python/commit/78c19dcd764983be83d07faeca21abf3d2061a52
        # The new signature includes an `attributes` parameter which is used by opentelemetry internals.
        def get_meter(self, name, version = None, schema_url = None, attributes = None):
            return Meter(name, version, schema_url, self._exporter, self._metric_reader)
    else:
        def get_meter(self, name, version = None, schema_url = None):
            return Meter(name, version, schema_url, self._exporter, self._metric_reader)

class Meter(OtelMeter):
    """Starts and/or activates OpenTelemetry compatible Metrics using the global Datadog Meter."""

    def __init__(self, name, version = None, schema_url = None, exporter = None, metric_reader = None):
        self._name = name
        self._version = version
        self._schema_url = schema_url
        self._exporter = exporter
        self._metric_reader = metric_reader
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
        instrument = ObservableCounter(self._exporter, name, callbacks, unit, description)
        self._metric_reader.register_async_instrument(instrument)
        return instrument

    def create_observable_up_down_counter(self, name, callbacks = None,  unit = "", description = "") -> ObservableUpDownCounter:
        log.debug("Executed create_observable_up_down_counter(name=%s, callbacks=<OBJ>, unit=%s, description=%s)", name, unit, description)
        instrument = ObservableUpDownCounter(self._exporter, name, callbacks, unit, description)
        self._metric_reader.register_async_instrument(instrument)
        return instrument

    def create_observable_gauge(self, name, callbacks = None, unit = "", description = "") -> ObservableGauge:
        log.debug("Executed create_observable_gauge(name=%s, callbacks=<OBJ>, unit=%s, description=%s)", name, unit, description)
        instrument = ObservableGauge(self._exporter, name, callbacks, unit, description)
        self._metric_reader.register_async_instrument(instrument)
        return instrument

    def create_histogram(self, name, unit = "", description = "") -> Histogram:
        log.debug("Executed create_histogram(name=%s, unit=%s, description=%s)", name, unit, description)
        instrument = Histogram(self._exporter, name, unit, description)
        return instrument