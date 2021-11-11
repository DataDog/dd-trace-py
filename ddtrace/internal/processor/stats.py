from collections import defaultdict
import typing

from ddsketch.ddsketch import DDSketch
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


if typing.TYPE_CHECKING:
    from typing import DefaultDict
    from typing import List
    from typing import Optional
    from typing import Tuple

    from ddtrace import Span


log = get_logger(__name__)


def _is_top_level(span):
    # type: (Span) -> bool
    return (span._local_root is span) or (
        span._parent is not None and span._parent.service != span.service and span.service is not None
    )


def _is_measured(span):
    # type: (Span) -> bool
    return SPAN_MEASURED_KEY in span.metrics


"""
Stats points for spans need to be aggregated together.
"""
SpanAggrKey = typing.Tuple[
    str,  # name
    str,  # service
    str,  # resource
    str,  # type
    int,  # http status code
    bool,  # synthetics request
]


class SpanStats(object):
    def __init__(self):
        self.hits = 0
        self.top_level_hits = 0
        self.errors = 0
        self.duration = 0
        self.ok_distribution = LogCollapsingLowestDenseDDSketch(0.00775, offset=1.8761281912861705, bin_limit=2048)
        self.err_distribution = LogCollapsingLowestDenseDDSketch(0.00775, offset=1.8761281912861705, bin_limit=2048)

    def to_dict(self, aggr_key):
        # type: (SpanAggrKey) -> Dict
        name, service, resource, _type, http_status, synthetics = aggr_key
        return {
            "HTTP_status_code": http_status,
            "name": name,
            "service": service,
            "resource": resource,
            "DB_type": _type,
            "hits": self.hits,
            "topLevelHits": self.top_level_hits,
            "duration": self.duration,
            "errors": self.errors,
            # TODO: these are probably stupid expensive operations
            "okSummary": DDSketchProto.to_proto(self.ok_distribution).SerializeToString(),
            "errSummary": DDSketchProto.to_proto(self.err_distribution).SerializeToString(),
        }


class _StatsBucket(object):
    def __init__(self, start, duration):
        # type: (int, int) -> None
        self._start = start
        self._duration = duration
        self.stats = defaultdict(lambda: SpanStats())  # type: DefaultDict[SpanAggrKey, SpanStats]

    def to_dict(self):
        # type: () -> Dict
        return {
            "start": self._start,
            "duration": self._duration,
            "stats": [stats.to_dict(aggr_key) for aggr_key, stats in self.stats.items()],
        }


def _span_aggr_key(span):
    # type: (Span) -> SpanAggrKey
    """Return a hashable key that can be used to uniquely refer to the "same"
    span.
    """

    service = span.service or "unnamed_service"
    if len(service) > 100:
        service = service[:100]
    resource = span.resource or span.name
    duration = span.duration_ns
    if duration < 0:
        duration = 0
    elif duration > 2 ** 64 - span.start_ns:
        duration = 0
    _type = span.span_type
    if _type and len(_type) > 100:
        _type = _type[:100]

    status_code = span.metrics.get("http.status_code", None)
    synthetics = span.context.dd_origin == "synthetics"  # TODO: verify this is the right check
    return (span.name, service, resource, _type, status_code, synthetics)


class SpanStatsProcessor(PeriodicService, SpanProcessor):
    def __init__(self, agent_url, interval=10.0, timeout=1.0):
        # type: (str, float, float) -> None
        super(SpanStatsProcessor, self).__init__(interval=interval)
        self.start()
        self._agent_url = agent_url
        self._timeout = timeout
        self._bucket_size_ns = 10.0 * 10e9  # type: float
        self._buckets = {}  # type: Dict[int, _StatsBucket]

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        is_top_level = _is_top_level(span)
        if not is_top_level and not _is_measured(span):
            return

        span_end = span.start_ns + span.duration_ns
        bucket_time = span_end - span_end % self._bucket_size_ns
        if bucket_time in self._buckets:
            bucket = self._buckets[bucket_time]
        else:
            bucket = self._buckets[bucket_time] = _StatsBucket(bucket_time, self._bucket_size_ns)

        aggr_key = _span_aggr_key(span)
        stats = bucket.stats[aggr_key]
        stats.hits += 1
        if is_top_level:
            stats.top_level_hits += 1
        if span.error:
            stats.errors += 1
            stats.err_distribution.add(span.duration_ns)
        else:
            stats.ok_distribution.add(span.duration_ns)

    def periodic(self):
        # type: (...) -> None
        stat_buckets = [bucket.to_dict() for bucket in self._buckets.values()]
        payload = msgpack.packb(
            {
                "hostname": config.report_hostname,
                "env": config.env,
                "version": config.version,
                "stats": stat_buckets,
            },
            use_bin_type=True,
        )
        headers = {}
        try:
            conn = get_connection(self._agent_url, self._timeout)
            conn.request("PUT", "/v0.6/stats", payload, headers)
        except Exception:
            log.error("failed to submit span stats to the Datadog agent at %s", self._agent_url, exc_info=True)
        else:
            resp = get_connection_response(conn)
            if resp.status != 200:
                log.error("failed to send stats payload, %s response from agent", resp.status)

    on_shutdown = periodic

    def shutdown(self):
        self.periodic()
        self.stop()
