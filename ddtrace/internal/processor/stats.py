from collections import defaultdict
import typing

from ddsketch.ddsketch import DDSketch
import msgpack

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..periodic import PeriodicService
from . import SpanProcessor

if typing.TYPE_CHECKING:
    from typing import List
    from typing import Optional
    from typing import Tuple
    from ddtrace import Span


log = get_logger(__name__)


def _is_top_level(span):
    # type: (Span) -> bool
    return (span._local_root is span) or\
           (span._parent is not None and
            span._parent.service != span.service and
            span.service is not None)


class DDStatsCollector(object):
    def __init__(self):
        self._counts = defaultdict(lambda: {
            "count": 0,
            "tags": [],
        })
        self._distributions = defaultdict(lambda: {
            "value": DDSketch(1.015625, offset=1.8761281912861705),
            "tags": [],
        })

    def __repr__(self):
        # type: () -> str
        return "<DDStatsCollector counts=%s, distributions=%s>" % (len(self._counts), len(self._distributions))

    def clear(self):
        # type: () -> None
        self._counts.clear()
        self._distributions.clear()

    def pop_counts(self):
        # type: () -> List[Tuple[str, int, List[str]]]
        counts = [(name, d["count"], d["tags"]) for name, d in self._counts.items()]
        self._counts.clear()
        return counts

    def pop_distributions(self):
        # type: () -> List[Tuple[str, DDSketch, List[str]]]
        dists = [(name, d["value"], d["tags"]) for name, d in self._distributions.items()]
        self._distributions.clear()
        return dists

    def increment(self, name, value=1, tags=None):
        # type: (str, int, Optional[List[str]]) -> None
        self._counts[name]["count"] += value
        if tags is not None:
            self._counts[name]["tags"].extend(tags)

    def decrement(self, name, value=1, tags=None):
        # type: (str, int, Optional[List[str]]) -> None
        self._counts[name]["count"] -= value
        if tags is not None:
            self._counts[name]["tags"].extend(tags)

    def count(self, name, value, tags=None):
        # type: (str, int, Optional[List[str]]) -> None
        self._counts[name]["count"] = value
        if tags is not None:
            self._counts[name]["tags"].extend(tags)

    def distribution(self, name, value, tags=None):
        # type: (str, float, Optional[List[str]]) -> None
        self._distributions[name]["value"].add(value)
        if tags is not None:
            self._distributions[name]["tags"].extend(tags)


class StatsProcessor(PeriodicService, SpanProcessor):
    def __init__(self, agent_url, interval=10.0, timeout=1.0):
        # type: (str, float, float) -> None
        super(StatsProcessor, self).__init__(interval=interval)
        self.start()
        self._agent_url = agent_url
        self._timeout = timeout
        self._stats = DDStatsCollector()

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if not _is_top_level(span):
            return

        if span.error:
            self._stats.increment("trace.%s.errors" % span.name)
        self._stats.increment("trace.%s.hits" % span.name)
        self._stats.distribution("trace.%s.duration" % span.name, span.duration_ns)
        print(self._stats)
        print(str(self._stats))

    def periodic(self):
        # type: (...) -> None
        counts = self._stats.pop_counts()
        dists = self._stats.pop_distributions()
        data = msgpack.packb(counts)
        headers = {}
        try:
            conn = get_connection(self._agent_url, self._timeout)
            conn.request("PUT", "/v0.6/trace/stats", data, headers)
        except Exception:
            log.error("failed to submit stats to the Datadog agent at %s", self._agent_url, exc_info=True)
        else:
            resp = get_connection_response(conn)

    on_shutdown = periodic

    def shutdown(self):
        self.periodic()
        self.stop()
