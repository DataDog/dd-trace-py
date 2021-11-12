from collections import defaultdict
import threading
import typing

from ddsketch.ddsketch import LogCollapsingLowestDenseDDSketch
from ddsketch.pb.proto import DDSketchProto
import msgpack

from ddtrace import config

from . import SpanProcessor
from ...constants import SPAN_MEASURED_KEY
from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..periodic import PeriodicService
from ..writer import _human_size


if typing.TYPE_CHECKING:
    from typing import DefaultDict
    from typing import Dict
    from typing import Optional

    from ddtrace import Span

    from ..agent import ConnectionType


log = get_logger(__name__)


def _is_top_level(span):
    # type: (Span) -> bool
    """Return whether the span is a "top level" span."""
    return (span._local_root is span) or (
        span._parent is not None and span._parent.service != span.service and span.service is not None
    )


def _is_measured(span):
    # type: (Span) -> bool
    """Return whether the span is flagged to be measured or not."""
    return SPAN_MEASURED_KEY in span.metrics


"""
To aggregate metrics for spans they need to be "uniquely" identified (as
best as possible). This enables the compression of stat points.

Aggregation can be done using primary and secondary attributes from the span
stored in a tuple which is hashable in Python.
"""
SpanAggrKey = typing.Tuple[
    str,  # name
    str,  # service
    str,  # resource
    str,  # type
    int,  # http status code
    bool,  # synthetics request
]


class SpanAggrStats(object):
    """Aggregated span statistics."""

    def __init__(self):
        self.hits = 0
        self.top_level_hits = 0
        self.errors = 0
        self.duration = 0
        self.ok_distribution = LogCollapsingLowestDenseDDSketch(0.00775, offset=1.8761281912861705, bin_limit=2048)
        self.err_distribution = LogCollapsingLowestDenseDDSketch(0.00775, offset=1.8761281912861705, bin_limit=2048)


def _span_aggr_key(span):
    # type: (Span) -> SpanAggrKey
    """Return a hashable key that can be used to uniquely refer to the "same"
    span.
    """
    service = span.service or "unnamed_service"
    if len(service) > 100:
        service = service[:100]
    resource = span.resource or span.name
    _type = span.span_type
    if _type and len(_type) > 100:
        _type = _type[:100]

    status_code = span.metrics.get("http.status_code", None)
    synthetics = span.context.dd_origin == "synthetics"  # TODO: verify this is the right variant
    return span.name, service, resource, _type, status_code, synthetics


class SpanStatsProcessor(PeriodicService, SpanProcessor):
    def __init__(self, agent_url, interval=10.0, timeout=1.0):
        # type: (str, float, float) -> None
        super(SpanStatsProcessor, self).__init__(interval=interval)
        self.start()
        self._agent_url = agent_url
        self._timeout = timeout
        self._bucket_size_ns = int(10 * 10e9)  # type: int
        self._buckets = defaultdict(
            lambda: defaultdict(SpanAggrStats)
        )  # type: DefaultDict[int, DefaultDict[SpanAggrKey, SpanAggrStats]]
        self._endpoint = "/v0.6/stats"
        self._connection = None  # type: Optional[ConnectionType]
        self._lock = threading.Lock()

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        is_top_level = _is_top_level(span)
        if not is_top_level and not _is_measured(span):
            return

        with self._lock:
            # Align the span into the corresponding stats bucket
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

    @property
    def _agent_endpoint(self):
        return "%s%s" % (self._agent_url, self._endpoint)

    def _serialize_buckets(self):
        # type: () -> List
        serialized_buckets = []
        for bucket_time_ns, bucket in self._buckets.items():
            bucket_aggr_stats = []
            for aggr_key, stat_aggr in bucket.items():
                name, service, resource, _type, http_status, synthetics = aggr_key
                bucket_aggr_stats.append(
                    {
                        "name": name,
                        "service": service,
                        "resource": resource,
                        "DB_type": _type,
                        "HTTP_status_code": http_status,
                        "synthetics": synthetics,
                        "hits": stat_aggr.hits,
                        "topLevelHits": stat_aggr.top_level_hits,
                        "duration": stat_aggr.duration,
                        "errors": stat_aggr.errors,
                        "okSummary": DDSketchProto.to_proto(stat_aggr.ok_distribution).SerializeToString(),
                        "errSummary": DDSketchProto.to_proto(stat_aggr.err_distribution).SerializeToString(),
                    }
                )
            serialized_buckets.append(
                {
                    "start": bucket_time_ns,
                    "duration": self._bucket_size_ns,
                    "stats": bucket_aggr_stats,
                }
            )
        return serialized_buckets

    def periodic(self):
        # type: (...) -> None
        with self._lock:
            serialized_stats = self._serialize_buckets()
            self._buckets = defaultdict(lambda: defaultdict(SpanAggrStats))
        payload = msgpack.packb(
            {
                "hostname": config.report_hostname,
                "env": config.env,
                "version": config.version,
                "stats": serialized_stats,
            },
            use_bin_type=True,
        )
        headers = {}
        try:
            self._connection = get_connection(self._agent_url, self._timeout)
            self._connection.request("PUT", self._endpoint, payload, headers)
            log.info("sent %s to %s", _human_size(len(payload)), self._agent_endpoint)
        except Exception:
            log.error("failed to submit span stats to the Datadog agent at %s", self._agent_endpoint, exc_info=True)
        else:
            resp = get_connection_response(self._connection)
            if resp.status == 404:
                log.error("Datadog agent does not support tracer stats computation, please upgrade your agent")
            elif resp.status != 200:
                log.error(
                    "failed to send stats payload, %s response from Datadog agent at %s",
                    resp.status,
                    self._agent_endpoint,
                )
        finally:
            self._connection.close()

    on_shutdown = periodic

    def shutdown(self):
        self.periodic()
        self.stop()
