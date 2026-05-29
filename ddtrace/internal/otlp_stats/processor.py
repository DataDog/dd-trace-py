"""SpanProcessor that aggregates spans and periodically exports them as OTLP metrics."""
from typing import Optional

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otlp_stats.aggregation import TimeBuckets
from ddtrace.internal.otlp_stats.exporter import OtlpStatsExporter
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.threads import Lock
from ddtrace.version import __version__


log = get_logger(__name__)

_TOP_LEVEL_KEY = "_dd.top_level"


def _build_resource_attrs() -> "dict[str, str]":
    attrs = dict(config.tags)
    attrs["service.name"] = config.service
    attrs["service.version"] = config.version
    attrs["deployment.environment"] = config.env
    if config._report_hostname and "host.name" not in attrs:
        attrs["host.name"] = get_hostname()
    return {k: str(v) if v is not None else "" for k, v in attrs.items()}


class OtlpSpanStatsProcessor(PeriodicService, SpanProcessor):
    """Aggregates finished top-level/measured spans into time buckets and exports them as OTLP metrics."""

    def __init__(self, exporter: Optional[OtlpStatsExporter] = None, interval: Optional[float] = None) -> None:
        if interval is None:
            interval = otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL / 1000.0
        super().__init__(interval=interval)
        self._enabled = True
        self._bucket_size_ns = int(interval * 1e9)
        self._buckets = TimeBuckets()
        self._lock = Lock()
        self._exporter = exporter or OtlpStatsExporter(
            otel_config.exporter.METRICS_ENDPOINT,
            otel_config.exporter.METRICS_PROTOCOL,
            otel_config.exporter.METRICS_HEADERS,
            otel_config.exporter.METRICS_TIMEOUT,
            __version__,
        )
        self._resource_attrs = _build_resource_attrs()
        self.start()

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_finish(self, span: Span) -> None:
        if not self._enabled:
            return
        is_top_level = span._get_numeric_attribute(_TOP_LEVEL_KEY) == 1
        if not is_top_level and span._get_numeric_attribute(_SPAN_MEASURED_KEY) != 1:
            return
        if span.duration_ns is None:
            return
        span_end_ns = span.start_ns + span.duration_ns
        bucket_time_ns = span_end_ns - (span_end_ns % self._bucket_size_ns)
        with self._lock:
            self._buckets.for_time(bucket_time_ns).for_span(span).record(span)

    def _drain(self) -> list:
        with self._lock:
            drained = list(self._buckets.items())
            self._buckets = TimeBuckets()
        return drained

    def periodic(self) -> None:
        drained = self._drain()
        if drained:
            self._exporter.export(drained, self._bucket_size_ns, self._resource_attrs)

    def shutdown(self, timeout: Optional[float]) -> None:
        self.periodic()
        self.stop(timeout)
