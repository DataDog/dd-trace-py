# coding: utf-8
from collections import defaultdict
import os
from typing import Optional
from typing import Union

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal import compat
from ddtrace.internal import process_tags
from ddtrace.internal.settings._config import config
from ddtrace.internal.threads import Lock
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.version import __version__

from ...constants import _SPAN_MEASURED_KEY
from .. import agent
from .._encoding import packb
from ..hostname import get_hostname
from ..logger import get_logger
from ..periodic import PeriodicService
from ..writer import _human_size


try:
    from ddtrace.internal.native import DDSketch
except ImportError:
    DDSketch = None  # type: ignore


log = get_logger(__name__)

# AIDEV-NOTE: Trilean values for is_trace_root, matching Go reference implementation.
# TRUE (1) means parentID == 0 (i.e., the span is the trace root).
# FALSE (2) means parentID != 0.
_TRILEAN_TRUE = 1
_TRILEAN_FALSE = 2

# AIDEV-NOTE: Span kinds that qualify a span for stats computation even if
# it is not top-level or explicitly measured. This matches the Go reference.
_STATS_ELIGIBLE_SPAN_KINDS = frozenset(
    {
        SpanKind.SERVER,
        SpanKind.CLIENT,
        SpanKind.PRODUCER,
        SpanKind.CONSUMER,
    }
)

# AIDEV-NOTE: Only client, producer, and consumer spans contribute peer tags
# to stats aggregation, matching Go reference implementation.
_PEER_TAG_SPAN_KINDS = frozenset(
    {
        SpanKind.CLIENT,
        SpanKind.PRODUCER,
        SpanKind.CONSUMER,
    }
)

# AIDEV-NOTE: Default peer tag keys used for stats aggregation. These are the
# fallback set when the agent /info endpoint does not provide peer_tags_keys.
# Matches the Go agent's /info response default peer tag keys exactly.
DEFAULT_PEER_TAG_KEYS = (
    "_dd.base_service",
    "peer.service",
    "peer.hostname",
    "out.host",
    "db.instance",
    "db.system",
    "messaging.destination",
    "network.destination.name",
)

# AIDEV-NOTE: gRPC status code tag keys checked in priority order,
# matching Go reference implementation.
_GRPC_STATUS_CODE_KEYS = (
    "rpc.grpc.status_code",
    "grpc.code",
    "rpc.grpc.status.code",
    "grpc.status.code",
)


def _is_measured(span: Span) -> bool:
    """Return whether the span is flagged to be measured or not."""
    return span._metrics.get(_SPAN_MEASURED_KEY) == 1


def _has_eligible_span_kind(span: Span) -> bool:
    """Return whether the span has a span.kind that qualifies it for stats."""
    kind = span.get_tag(SPAN_KIND)
    return kind in _STATS_ELIGIBLE_SPAN_KINDS if kind else False


def _get_grpc_status_code(span: Span) -> int:
    """Extract gRPC status code from span tags.

    Checks multiple tag keys in priority order matching Go reference.
    Returns 0 if no gRPC status code is found.
    """
    for key in _GRPC_STATUS_CODE_KEYS:
        val = span.get_tag(key)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    return 0


def _get_peer_tags(span: Span, peer_tag_keys: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Extract peer tags from a span for stats aggregation.

    Only applicable for client/producer/consumer span kinds.
    Returns a sorted tuple of (key, value) pairs for hashability.

    For internal+service-override spans, _dd.base_service is used.
    """
    kind = span.get_tag(SPAN_KIND)
    # AIDEV-NOTE: Internal spans with _dd.base_service (service override) should
    # include _dd.base_service as a peer tag, matching Go agent behavior.
    if kind == SpanKind.INTERNAL:
        base_svc = span.get_tag("_dd.base_service")
        if base_svc:
            return (("_dd.base_service", base_svc),)
        return ()
    if kind not in _PEER_TAG_SPAN_KINDS:
        return ()

    tags = []
    for key in peer_tag_keys:
        val = span.get_tag(key)
        if val:
            tags.append((key, val))
    return tuple(sorted(tags))


"""
To aggregate metrics for spans they need to be "uniquely" identified (as
best as possible). This enables the compression of stat points.

Aggregation can be done using primary and secondary attributes from the span
stored in a tuple which is hashable in Python.

AIDEV-NOTE: Extended to include span_kind, is_trace_root (Trilean),
peer_tags, and grpc_status_code to match Go reference implementation.
"""
SpanAggrKey = tuple[
    str,  # name
    str,  # service
    str,  # resource
    str,  # type
    int,  # http status code
    bool,  # synthetics request
    str,  # http method
    str,  # http endpoint
    str,  # span_kind
    int,  # is_trace_root (Trilean: 1=TRUE, 2=FALSE)
    tuple[tuple[str, str], ...],  # peer_tags (sorted key-value pairs)
    int,  # grpc_status_code
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


def _span_aggr_key(span: Span, peer_tag_keys: tuple[str, ...] = DEFAULT_PEER_TAG_KEYS) -> SpanAggrKey:
    """Return a hashable key that can be used to aggregate similar spans.

    AIDEV-NOTE: Extended to include span_kind, is_trace_root, peer_tags,
    and grpc_status_code dimensions matching the Go reference implementation.
    """
    service = span.service or ""
    resource = span.resource or ""
    _type = span.span_type or ""
    status_code = span.get_tag("http.status_code") or 0
    method = span.get_tag("http.method") or ""
    endpoint = span.get_tag("http.endpoint") or span.get_tag("http.route") or ""
    synthetics = span.context.dd_origin == "synthetics"
    span_kind = span.get_tag(SPAN_KIND) or ""
    # AIDEV-NOTE: In Python, root spans have parent_id=None (not 0 like Go).
    # Both None and 0 indicate trace root.
    is_trace_root = _TRILEAN_TRUE if not span.parent_id else _TRILEAN_FALSE
    peer_tags = _get_peer_tags(span, peer_tag_keys)
    grpc_status_code = _get_grpc_status_code(span)
    return (
        span.name,
        service,
        resource,
        _type,
        int(status_code),
        synthetics,
        method,
        endpoint,
        span_kind,
        is_trace_root,
        peer_tags,
        grpc_status_code,
    )


class SpanStatsProcessorV06(PeriodicService, SpanProcessor):
    """SpanProcessor for computing, collecting and submitting span metrics to the Datadog Agent."""

    def __init__(
        self,
        agent_url: Optional[str] = None,
        interval: Optional[float] = None,
        timeout: float = 1.0,
        retry_attempts: int = 3,
        peer_tag_keys: Optional[tuple[str, ...]] = None,
    ):
        if interval is None:
            interval = float(os.getenv("_DD_TRACE_STATS_WRITER_INTERVAL") or 10.0)
        super(SpanStatsProcessorV06, self).__init__(interval=interval)
        self._enabled: bool = True
        # AIDEV-NOTE: peer_tag_keys can be provided from agent /info endpoint
        # or defaults to DEFAULT_PEER_TAG_KEYS matching Go reference.
        self._peer_tag_keys: tuple[str, ...] = peer_tag_keys if peer_tag_keys is not None else DEFAULT_PEER_TAG_KEYS
        # DDSketch is not included in slim builds
        if DDSketch is None:
            self._enabled: bool = False  # type: ignore[no-redef]
            return
        self._agent_url = agent_url or agent.config.trace_agent_url
        self._endpoint = "/v0.6/stats"
        self._agent_endpoint = "%s%s" % (self._agent_url, self._endpoint)
        self._timeout = timeout
        # Have the bucket size match the interval in which flushes occur.
        self._bucket_size_ns: int = int(interval * 1e9)
        self._buckets: defaultdict[int, defaultdict[SpanAggrKey, SpanAggrStats]] = defaultdict(
            lambda: defaultdict(SpanAggrStats)
        )
        self._headers: dict[str, str] = {
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Tracer-Version": __version__,
            "Content-Type": "application/msgpack",
        }
        self._hostname = ""
        if config._report_hostname:
            self._hostname = get_hostname()
        self._lock = Lock()

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

        # AIDEV-NOTE: Span eligibility now includes span.kind in {server, client, producer, consumer}
        # in addition to top-level and measured, matching Go reference implementation.
        is_top_level = span._is_top_level
        if not is_top_level and not _is_measured(span) and not _has_eligible_span_kind(span):
            return

        with self._lock:
            # Align the span into the corresponding stats bucket
            assert span.duration_ns is not None
            span_end_ns = span.start_ns + span.duration_ns
            bucket_time_ns = span_end_ns - (span_end_ns % self._bucket_size_ns)
            aggr_key = _span_aggr_key(span, self._peer_tag_keys)
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

    def _serialize_buckets(self) -> list[dict]:
        """Serialize and update the buckets.

        The current bucket is left in case any other spans are added.
        """
        serialized_buckets = []
        serialized_bucket_keys = []
        for bucket_time_ns, bucket in self._buckets.items():
            bucket_aggr_stats = []
            serialized_bucket_keys.append(bucket_time_ns)

            for aggr_key, stat_aggr in bucket.items():
                # AIDEV-NOTE: Destructure extended SpanAggrKey with new dimensions
                (
                    name,
                    service,
                    resource,
                    _type,
                    http_status,
                    synthetics,
                    http_method,
                    http_endpoint,
                    span_kind,
                    is_trace_root,
                    peer_tags,
                    grpc_status_code,
                ) = aggr_key
                serialized_bucket = {
                    "Name": compat.ensure_text(name),
                    "Resource": compat.ensure_text(resource),
                    "Synthetics": synthetics,
                    "HTTPStatusCode": http_status,
                    "HTTPMethod": http_method,
                    "HTTPEndpoint": http_endpoint,
                    "Hits": stat_aggr.hits,
                    "TopLevelHits": stat_aggr.top_level_hits,
                    "Duration": stat_aggr.duration,
                    "Errors": stat_aggr.errors,
                    "OkSummary": stat_aggr.ok_distribution.to_proto(),
                    "ErrorSummary": stat_aggr.err_distribution.to_proto(),
                    "SpanKind": span_kind,
                    "IsTraceRoot": is_trace_root,
                }
                if service:
                    serialized_bucket["Service"] = compat.ensure_text(service)
                if _type:
                    serialized_bucket["Type"] = compat.ensure_text(_type)
                if peer_tags:
                    serialized_bucket["PeerTags"] = [
                        compat.ensure_text(k) + ":" + compat.ensure_text(v) for k, v in peer_tags
                    ]
                if grpc_status_code:
                    # AIDEV-NOTE: Protobuf definition specifies GRPC_status_code
                    # as string type. Serialize as string to match Go agent.
                    serialized_bucket["GRPCStatusCode"] = str(grpc_status_code)
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

    def periodic(self) -> None:
        with self._lock:
            serialized_stats = self._serialize_buckets()

        if not serialized_stats:
            # No stats to report, short-circuit.
            return
        raw_payload: dict[str, Union[list[dict], str]] = {
            "Stats": serialized_stats,
            "Hostname": self._hostname,
        }
        if config.env:
            raw_payload["Env"] = compat.ensure_text(config.env)
        if config.version:
            raw_payload["Version"] = compat.ensure_text(config.version)
        if p_tags := process_tags.process_tags:
            raw_payload["ProcessTags"] = compat.ensure_text(p_tags)

        payload = packb(raw_payload)
        try:
            self._flush_stats_with_backoff(payload)
        except Exception:
            log.error("retry limit exceeded submitting span stats to the Datadog agent at %s", self._agent_endpoint)

    def shutdown(self, timeout: Optional[float]) -> None:
        self.periodic()
        self.stop(timeout)
