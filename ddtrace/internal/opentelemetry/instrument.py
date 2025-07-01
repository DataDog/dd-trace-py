from enum import IntEnum
from typing import Iterable, List, Generator
from opentelemetry.metrics import Counter as OtelApiCounter
from opentelemetry.metrics import Histogram as OtelApiHistogram
from opentelemetry.metrics import Meter as OtelApiMeter
from opentelemetry.metrics import MeterProvider as APIMeterProvider
from opentelemetry.metrics import ObservableCounter as OtelApiObservableCounter
from opentelemetry.metrics import ObservableGauge as OtelApiObservableGauge
from opentelemetry.metrics import (
    ObservableUpDownCounter as OtelApiObservableUpDownCounter,
)
from opentelemetry.metrics import UpDownCounter as OtelApiUpDownCounter
from opentelemetry.metrics import _Gauge as OtelApiGauge
from opentelemetry.metrics import CallbackOptions, CallbackT
from time import time_ns

from opentelemetry.sdk.metrics.export import (
    ExponentialHistogram,
    Gauge,
    Histogram,
    HistogramDataPoint,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.metrics.export import Gauge as OtelGauge
from opentelemetry.sdk.metrics.export import Histogram as OtelHistogram
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.sdk.instrumentation import InstrumentationScope
from ddtrace.internal.opentelemetry.sdk.resources import Resource

from typing import Sequence

log = get_logger(__name__)

class AggregationTemporality(IntEnum):
    """
    The temporality to use when aggregating data.

    Can be one of the following values:
    """

    UNSPECIFIED = 0
    DELTA = 1
    CUMULATIVE = 2

_DEFAULT_EXPLICIT_BUCKET_HISTOGRAM_AGGREGATION_BOUNDARIES: Sequence[float] = (
    0.0,
    5.0,
    10.0,
    25.0,
    50.0,
    75.0,
    100.0,
    250.0,
    500.0,
    750.0,
    1000.0,
    2500.0,
    5000.0,
    7500.0,
    10000.0,
)

class Counter(OtelApiCounter):
    def __init__(self, exporter, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

    def add(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        log.debug("Executed counter.add(name=%s, amount=%s)", self.name, amount)
        if amount < 0:
            log.warning(
                "Add amount must be non-negative on Counter %s.", self.name
            )
            return
        
        # POC: Create one metric point and export it
        metrics = []
        metrics.append(Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=Sum(
                            aggregation_temporality=AggregationTemporality.DELTA,
                            data_points=[
                                NumberDataPoint(
                                    attributes=attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=amount,
                                )
                            ],
                            is_monotonic=True,
                        ),
                    ))

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

        self._exporter.export(
            metrics_data, timeout_millis=10_000
        )

class UpDownCounter(OtelApiUpDownCounter):
    def __init__(self, exporter, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

    def add(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        log.debug("Executed updowncounter.add(name=%s, amount=%s)", self.name, amount)
        # POC: Create one metric point and export it
        metrics = []
        metrics.append(Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=Sum(
                            aggregation_temporality=AggregationTemporality.CUMULATIVE,
                            data_points=[
                                NumberDataPoint(
                                    attributes=attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=amount,
                                )
                            ],
                            is_monotonic=False,
                        ),
                    ))

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
                    scope_metrics=[ScopeMetrics(scope=InstrumentationScope(name="dd-trace-py"),metrics=metrics, schema_url="")],
                    schema_url="",
                )
            ]
        )

        self._exporter.export(
            metrics_data, timeout_millis=10_000
        )

class Gauge(OtelApiGauge):
    def __init__(self, exporter, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

    def set(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        log.debug("Executed gauge.set(name=%s, amount=%s)", self.name, amount)
        # POC: Create one metric point and export it
        metrics = []
        metrics.append(Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=OtelGauge(
                            data_points=[
                                NumberDataPoint(
                                    attributes=attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=amount,
                                )
                            ]
                        ),
                    ))

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

        self._exporter.export(
            metrics_data, timeout_millis=10_000
        )

class ObservableCounter(OtelApiObservableCounter):
    def __init__(self, exporter, name, callbacks = None, unit = "", description = ""):
        super().__init__(name, callbacks, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

        self._callbacks: List[CallbackT] = []

        if callbacks is not None:
            for callback in callbacks:
                if isinstance(callback, Generator):
                    # advance generator to it's first yield
                    next(callback)

                    def inner(
                        options: CallbackOptions,
                        callback=callback,
                    ) -> Iterable[Metric]:
                        try:
                            return callback.send(options)
                        except StopIteration:
                            return []

                    self._callbacks.append(inner)
                else:
                    self._callbacks.append(callback)

    def callback(
        self, callback_options: CallbackOptions
    ) -> Iterable[Metric]:
        for callback in self._callbacks:
            try:
                for api_measurement in callback(callback_options):
                    yield Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=Sum(
                            aggregation_temporality=AggregationTemporality.DELTA,
                            data_points=[
                                NumberDataPoint(
                                    attributes=api_measurement.attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=api_measurement.value,
                                )
                            ],
                            is_monotonic=True,
                        ),
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception(
                    "Callback failed for instrument %s.", self.name
                )


class ObservableUpDownCounter(OtelApiObservableUpDownCounter):
    def __init__(self, exporter, name, callbacks = None, unit = "", description = ""):
        super().__init__(name, callbacks, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

        self._callbacks: List[CallbackT] = []

        if callbacks is not None:
            for callback in callbacks:
                if isinstance(callback, Generator):
                    # advance generator to it's first yield
                    next(callback)

                    def inner(
                        options: CallbackOptions,
                        callback=callback,
                    ) -> Iterable[Metric]:
                        try:
                            return callback.send(options)
                        except StopIteration:
                            return []

                    self._callbacks.append(inner)
                else:
                    self._callbacks.append(callback)

    def callback(
        self, callback_options: CallbackOptions
    ) -> Iterable[Metric]:
        for callback in self._callbacks:
            try:
                for api_measurement in callback(callback_options):
                    yield Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=Sum(
                            aggregation_temporality=AggregationTemporality.CUMULATIVE,
                            data_points=[
                                NumberDataPoint(
                                    attributes=api_measurement.attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=api_measurement.value,
                                )
                            ],
                            is_monotonic=True,
                        ),
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception(
                    "Callback failed for instrument %s.", self.name
                )

class ObservableGauge(OtelApiObservableGauge):
    def __init__(self, exporter, name, callbacks = None, unit = "", description = ""):
        super().__init__(name, callbacks, unit=unit, description=description)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description

        self._callbacks: List[CallbackT] = []

        if callbacks is not None:
            for callback in callbacks:
                if isinstance(callback, Generator):
                    # advance generator to it's first yield
                    next(callback)

                    def inner(
                        options: CallbackOptions,
                        callback=callback,
                    ) -> Iterable[Metric]:
                        try:
                            return callback.send(options)
                        except StopIteration:
                            return []

                    self._callbacks.append(inner)
                else:
                    self._callbacks.append(callback)

    def callback(
        self, callback_options: CallbackOptions
    ) -> Iterable[Metric]:
        for callback in self._callbacks:
            try:
                for api_measurement in callback(callback_options):
                    yield Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=OtelGauge(
                            data_points=[
                                NumberDataPoint(
                                    attributes=api_measurement.attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    value=api_measurement.value,
                                )
                            ]
                        ),
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception(
                    "Callback failed for instrument %s.", self.name
                )

class Histogram(OtelApiHistogram):
    def __init__(self, exporter, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description, explicit_bucket_boundaries_advisory=None)
        self._exporter = exporter
        self.name = name
        self.unit = unit
        self.description = description
        self._boundaries = (
            _DEFAULT_EXPLICIT_BUCKET_HISTOGRAM_AGGREGATION_BOUNDARIES
        )

    def record(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        log.debug("Executed histogram.record(name=%s, amount=%s)", self.name, amount)
        if amount < 0:
            log.warning(
                "Add amount must be non-negative on Counter %s.", self.name
            )
            return
        
        # POC: Create one metric point and export it
        metrics = []
        metrics.append(Metric(
                        name=self.name,
                        description=self.description,
                        unit=self.unit,
                        data=OtelHistogram(
                            data_points=[
                                HistogramDataPoint(
                                    attributes=attributes,
                                    start_time_unix_nano=time_ns(),
                                    time_unix_nano=time_ns(),
                                    count=1,
                                    sum=amount,
                                    min=amount,
                                    max=amount,
                                    bucket_counts=[0] * (len(self._boundaries) + 1), # This is all zeroes but we can pretend right?
                                    explicit_bounds=self._boundaries,
                                )
                            ],
                            aggregation_temporality=AggregationTemporality.DELTA,
                        ),
                    ))

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

        self._exporter.export(
            metrics_data, timeout_millis=10_000
        )