"""In-memory aggregation of finished spans into time/dimension buckets for OTLP stats export."""
from typing import TYPE_CHECKING

from ddtrace.constants import _ORIGIN_KEY
from ddtrace.ext import http


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


_TOP_LEVEL_KEY = "_dd.top_level"


class SpanAggKey:
    """Dimension key a span aggregates into. Mirrors the /v0.6/stats aggregation key."""

    __slots__ = ("name", "service", "resource", "type", "status_code", "synthetics", "method", "endpoint", "_key")

    def __init__(self, span: "Span") -> None:
        self.name = span.name or ""
        self.service = span.service or ""
        self.resource = span.resource or ""
        self.type = span.span_type or ""
        self.status_code = span._get_str_attribute(http.STATUS_CODE) or ""
        self.method = span._get_str_attribute(http.METHOD) or ""
        self.endpoint = span._get_str_attribute(http.ROUTE) or ""
        self.synthetics = span._get_str_attribute(_ORIGIN_KEY) == "synthetics"
        self._key = (
            self.name,
            self.service,
            self.resource,
            self.type,
            self.status_code,
            self.synthetics,
            self.method,
            self.endpoint,
        )

    def __hash__(self) -> int:
        return hash(self._key)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SpanAggKey) and self._key == other._key


class SpanAggStats:
    """Per-key counters split by top-level/error. Sums replace the DDSketch distributions."""

    __slots__ = (
        "agg_key",
        "hits",
        "errors",
        "top_level_hits",
        "top_level_errors",
        "duration",
        "error_duration",
        "top_level_duration",
        "top_level_error_duration",
    )

    def __init__(self, agg_key: SpanAggKey) -> None:
        self.agg_key = agg_key
        self.hits = 0
        self.errors = 0
        self.top_level_hits = 0
        self.top_level_errors = 0
        self.duration = 0
        self.error_duration = 0
        self.top_level_duration = 0
        self.top_level_error_duration = 0

    def record(self, span: "Span") -> None:
        duration_ns = span.duration_ns or 0
        is_top_level = span._get_numeric_attribute(_TOP_LEVEL_KEY) == 1
        is_error = bool(span.error)

        self.hits += 1
        self.duration += duration_ns
        if is_top_level:
            self.top_level_hits += 1
            self.top_level_duration += duration_ns
        if is_error:
            self.errors += 1
            self.error_duration += duration_ns
            if is_top_level:
                self.top_level_errors += 1
                self.top_level_error_duration += duration_ns


class SpanBuckets(dict):
    def for_span(self, span: "Span") -> SpanAggStats:
        key = SpanAggKey(span)
        stats = self.get(key)
        if stats is None:
            stats = SpanAggStats(key)
            self[key] = stats
        return stats


class TimeBuckets(dict):
    def for_time(self, time_ns: int) -> SpanBuckets:
        bucket = self.get(time_ns)
        if bucket is None:
            bucket = SpanBuckets()
            self[time_ns] = bucket
        return bucket
