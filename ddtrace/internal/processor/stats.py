# coding: utf-8
from collections import defaultdict
import os
from typing import DefaultDict
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.internal import compat
from ddtrace.internal.native import DDSketch
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.settings._config import config
from ddtrace.version import get_version

from ...constants import _SPAN_MEASURED_KEY
from .. import agent
from .._encoding import packb
from ..forksafe import Lock
from ..hostname import get_hostname
from ..logger import get_logger
from ..periodic import PeriodicService
from ..writer import _human_size


log = get_logger(__name__)


def _is_measured(span: Span) -> bool:
    """Return whether the span is flagged to be measured or not."""
    return span._metrics.get(_SPAN_MEASURED_KEY) == 1


"""
To aggregate metrics for spans they need to be "uniquely" identified (as
best as possible). This enables the compression of stat points.

Aggregation can be done using primary and secondary attributes from the span
stored in a tuple which is hashable in Python.
"""
SpanAggrKey = Tuple[
    str,  # name
    str,  # service
    str,  # resource
    str,  # type
    int,  # http status code
    bool,  # synthetics request
]


class SpanAggrStats(object):
    """Aggregated span statistics."""

    __slots__ = ("hits", "top_level_hits", "errors", "duration", "ok_distribution", "err_distribution")

    def __init__(self):
        self.hits = 0
        self.top_level_hits = 0
        self.errors = 0
        self.duration = 0
        # Match the relative accuracy of the sketch implementation used in the backend
        # which is 0.775%.
        self.ok_distribution = DDSketch()
        self.err_distribution = DDSketch()


def _span_aggr_key(span: Span) -> SpanAggrKey:
    """Return a hashable key that can be used to aggregate similar spans."""
    service = span.service or ""
    resource = span.resource or ""
    _type = span.span_type or ""
    status_code = span.get_tag("http.status_code") or 0
    synthetics = span.context.dd_origin == "synthetics"
    return span.name, service, resource, _type, int(status_code), synthetics


class SpanStatsProcessorV06(PeriodicService, SpanProcessor):
    """SpanProcessor for computing, collecting and submitting span metrics to the Datadog Agent."""

    def __init__(
        self,
        agent_url: Optional[str] = None,
        interval: Optional[float] = None,
        timeout: float = 1.0,
        retry_attempts: int = 3,
    ):
        if interval is None:
            interval = float(os.getenv("_DD_TRACE_STATS_WRITER_INTERVAL") or 10.0)
        super(SpanStatsProcessorV06, self).__init__(interval=interval)
        self._agent_url = agent_url or agent.config.trace_agent_url
        self._endpoint = "/v0.6/stats"
        self._agent_endpoint = "%s%s" % (self._agent_url, self._endpoint)
        self._timeout = timeout
        # Have the bucket size match the interval in which flushes occur.
        self._bucket_size_ns: int = int(interval * 1e9)
        self._buckets: DefaultDict[int, DefaultDict[SpanAggrKey, SpanAggrStats]] = defaultdict(
            lambda: defaultdict(SpanAggrStats)
        )
        self._headers: Dict[str, str] = {
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Tracer-Version": get_version(),
            "Content-Type": "application/msgpack",
        }
        self._hostname = ""
        if config._report_hostname:
            self._hostname = get_hostname()
        self._lock = Lock()
        self._enabled = True

        self._flush_stats_with_backoff = fibonacci_backoff_with_jitter(
            attempts=retry_attempts,
            initial_wait=0.618 * self.interval / (1.618**retry_attempts) / 2,
        )(self._flush_stats)

        self.start()

    def on_span_start(self, span: Span):
        pass

    def on_span_finish(self, span: Span) -> None:
        if not self._enabled:
            return

        if not (is_top_level := span._is_top_level) and not _is_measured(span):
            return

        with self._lock:
            # Align the span into the corresponding stats bucket
            assert span.duration_ns is not None
            span_end_ns = span.start_ns + span.duration_ns
            bucket_time_ns = span_end_ns - (span_end_ns % self._bucket_size_ns)
            aggr_key = _span_aggr_key(span)
            stats = self._buckets[bucket_time_ns][aggr_key]

            stats.hits += 1
            stats.duration += span.duration_ns
            if is_top_level:
                stats.top_level_hits += 1
            if span.error:
                stats.errors += 1
                stats.err_distribution.add(span.duration_ns)
            else:
                stats.ok_distribution.add(span.duration_ns)

    def _serialize_buckets(self) -> List[Dict]:
        """Serialize and update the buckets.

        The current bucket is left in case any other spans are added.
        """
        serialized_buckets = []
        serialized_bucket_keys = []
        for bucket_time_ns, bucket in self._buckets.items():
            bucket_aggr_stats = []
            serialized_bucket_keys.append(bucket_time_ns)

            for aggr_key, stat_aggr in bucket.items():
                name, service, resource, _type, http_status, synthetics = aggr_key
                serialized_bucket = {
                    "Name": compat.ensure_text(name),
                    "Resource": compat.ensure_text(resource),
                    "Synthetics": synthetics,
                    "HTTPStatusCode": http_status,
                    "Hits": stat_aggr.hits,
                    "TopLevelHits": stat_aggr.top_level_hits,
                    "Duration": stat_aggr.duration,
                    "Errors": stat_aggr.errors,
                    "OkSummary": stat_aggr.ok_distribution.to_proto(),
                    "ErrorSummary": stat_aggr.err_distribution.to_proto(),
                }
                if service:
                    serialized_bucket["Service"] = compat.ensure_text(service)
                if _type:
                    serialized_bucket["Type"] = compat.ensure_text(_type)
                bucket_aggr_stats.append(serialized_bucket)
            serialized_buckets.append(
                {
                    "Start": bucket_time_ns,
                    "Duration": self._bucket_size_ns,
                    "Stats": bucket_aggr_stats,
                }
            )

        # Clear out buckets that have been serialized
        for key in serialized_bucket_keys:
            del self._buckets[key]

        return serialized_buckets

    def _flush_stats(self, payload: bytes) -> None:
        try:
            conn = agent.get_connection(self._agent_url, self._timeout)
            conn.request("PUT", self._endpoint, payload, self._headers)
            resp = conn.getresponse()
        except Exception:
            log.error("failed to submit span stats to the Datadog agent at %s", self._agent_endpoint, exc_info=True)
            raise
        else:
            if resp.status == 404:
                log.error(
                    "Datadog agent does not support tracer stats computation, disabling, please upgrade your agent"
                )
                self._enabled = False
                return
            elif resp.status >= 400:
                log.error(
                    "failed to send stats payload, %s (%s) (%s) response from Datadog agent at %s",
                    resp.status,
                    resp.reason,
                    resp.read(),
                    self._agent_endpoint,
                )
            else:
                log.info("sent %s to %s", _human_size(len(payload)), self._agent_endpoint)

    def periodic(self):
        with self._lock:
            serialized_stats = self._serialize_buckets()

        if not serialized_stats:
            # No stats to report, short-circuit.
            return
        raw_payload: Dict[str, Union[List[Dict], str]] = {
            "Stats": serialized_stats,
            "Hostname": self._hostname,
        }
        if config.env:
            raw_payload["Env"] = compat.ensure_text(config.env)
        if config.version:
            raw_payload["Version"] = compat.ensure_text(config.version)

        payload = packb(raw_payload)
        try:
            self._flush_stats_with_backoff(payload)
        except Exception:
            log.error("retry limit exceeded submitting span stats to the Datadog agent at %s", self._agent_endpoint)

    def shutdown(self, timeout: Optional[float]) -> None:
        self.periodic()
        self.stop(timeout)
