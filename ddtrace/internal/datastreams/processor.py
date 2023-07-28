# coding: utf-8
import base64
from collections import defaultdict
import gzip
import os
import struct
import threading
import time
import typing
from typing import DefaultDict
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddsketch import LogCollapsingLowestDenseDDSketch
from ddsketch.pb.proto import DDSketchProto
import six

import ddtrace
from ddtrace import config
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter

from .._encoding import packb
from ..agent import get_connection
from ..compat import get_connection_response
from ..forksafe import Lock
from ..hostname import get_hostname
from ..logger import get_logger
from ..periodic import PeriodicService
from ..writer import _human_size
from .encoding import decode_var_int_64
from .encoding import encode_var_int_64
from .fnv import fnv1_64


if six.PY3:

    def gzip_compress(payload):
        return gzip.compress(payload, 1)


else:
    import StringIO

    def gzip_compress(payload):
        compressed_data = StringIO.StringIO()
        gzipper = gzip.GzipFile(fileobj=compressed_data, mode="wb", compresslevel=1)
        gzipper.write(payload)
        gzipper.close()
        return compressed_data.getvalue()


"""
The data streams processor aggregate stats about pathways (linked chains of services and topics)
And example of a pathway would be:

service 1 --> Kafka topic A --> service 2 --> kafka topic B --> service 3

The processor flushes stats periodically (every 10 sec) to the Datadog agent.
This powers the data streams monitoring product. More details about the product can be found here:
https://docs.datadoghq.com/data_streams/
"""


log = get_logger(__name__)

PROPAGATION_KEY = "dd-pathway-ctx"
PROPAGATION_KEY_BASE_64 = "dd-pathway-ctx-base64"

"""
PathwayAggrKey uniquely identifies a pathway to aggregate stats on.
"""
PathwayAggrKey = typing.Tuple[
    str,  # edge tags
    int,  # hash_value
    int,  # parent hash
]


class PathwayStats(object):
    """Aggregated pathway statistics."""

    __slots__ = ("full_pathway_latency", "edge_latency")

    def __init__(self):
        self.full_pathway_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.edge_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)


class DataStreamsProcessor(PeriodicService):
    """DataStreamsProcessor for computing, collecting and submitting data stream stats to the Datadog Agent."""

    def __init__(self, agent_url, interval=None, timeout=1.0, retry_attempts=3):
        # type: (str, Optional[float], float, int) -> None
        if interval is None:
            interval = float(os.getenv("_DD_TRACE_STATS_WRITER_INTERVAL") or 10.0)
        super(DataStreamsProcessor, self).__init__(interval=interval)
        self._agent_url = agent_url
        self._endpoint = "/v0.1/pipeline_stats"
        self._agent_endpoint = "%s%s" % (self._agent_url, self._endpoint)
        self._timeout = timeout
        # Have the bucket size match the interval in which flushes occur.
        self._bucket_size_ns = int(interval * 1e9)  # type: int
        self._buckets = defaultdict(
            lambda: defaultdict(PathwayStats)
        )  # type: DefaultDict[int, DefaultDict[PathwayAggrKey, PathwayStats]]
        self._headers = {
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Tracer-Version": ddtrace.__version__,
            "Content-Type": "application/msgpack",
            "Content-Encoding": "gzip",
        }  # type: Dict[str, str]
        self._hostname = six.ensure_text(get_hostname())
        self._service = six.ensure_text(config._get_service("unnamed-python-service"))
        self._lock = Lock()
        self._current_context = threading.local()
        self._enabled = True

        self._flush_stats_with_backoff = fibonacci_backoff_with_jitter(
            attempts=retry_attempts,
            initial_wait=0.618 * self.interval / (1.618 ** retry_attempts) / 2,
        )(self._flush_stats)

        self.start()

    def on_checkpoint_creation(
        self, hash_value, parent_hash, edge_tags, now_sec, edge_latency_sec, full_pathway_latency_sec
    ):
        # type: (int, int, List[str], float, float, float) -> None
        """
        on_checkpoint_creation is called every time a new checkpoint is created on a pathway. It records the
        latency to the previous checkpoint in the pathway (edge latency),
        and the latency from the very first element in the pathway (full_pathway_latency)
        the pathway is hashed to reduce amount of information transmitted in headers.

        :param hash_value: hash of the pathway, it's a hash of the edge leading to this point, and the parent hash.
        :param parent_hash: hash of the previous step in the pathway
        :param edge_tags: all tags associated with the edge leading to this step in the pathway
        :param now_sec: current time
        :param edge_latency_sec: latency of the direct edge between the previous point
            in the pathway, and the current step
        :param full_pathway_latency_sec: latency from the very start of the pathway.
        :return: Nothing
        """
        if not self._enabled:
            return

        now_ns = int(now_sec * 1e9)

        with self._lock:
            # Align the span into the corresponding stats bucket
            bucket_time_ns = now_ns - (now_ns % self._bucket_size_ns)
            aggr_key = (",".join(edge_tags), hash_value, parent_hash)
            stats = self._buckets[bucket_time_ns][aggr_key]
            stats.full_pathway_latency.add(full_pathway_latency_sec)
            stats.edge_latency.add(edge_latency_sec)

    def _serialize_buckets(self):
        # type: () -> List[Dict]
        """Serialize and update the buckets."""
        serialized_buckets = []
        serialized_bucket_keys = []
        for bucket_time_ns, bucket in self._buckets.items():
            bucket_aggr_stats = []
            serialized_bucket_keys.append(bucket_time_ns)

            for aggr_key, stat_aggr in bucket.items():
                edge_tags, hash_value, parent_hash = aggr_key
                serialized_bucket = {
                    u"EdgeTags": [six.ensure_text(tag) for tag in edge_tags.split(",")],
                    u"Hash": hash_value,
                    u"ParentHash": parent_hash,
                    u"PathwayLatency": DDSketchProto.to_proto(stat_aggr.full_pathway_latency).SerializeToString(),
                    u"EdgeLatency": DDSketchProto.to_proto(stat_aggr.edge_latency).SerializeToString(),
                }
                bucket_aggr_stats.append(serialized_bucket)
            serialized_buckets.append(
                {
                    u"Start": bucket_time_ns,
                    u"Duration": self._bucket_size_ns,
                    u"Stats": bucket_aggr_stats,
                }
            )

        # Clear out buckets that have been serialized
        for key in serialized_bucket_keys:
            del self._buckets[key]

        return serialized_buckets

    def _flush_stats(self, payload):
        # type: (bytes) -> None
        try:
            conn = get_connection(self._agent_url, self._timeout)
            conn.request("POST", self._endpoint, payload, self._headers)
            resp = get_connection_response(conn)
        except Exception:
            log.error("failed to submit pathway stats to the Datadog agent at %s", self._agent_endpoint, exc_info=True)
            raise
        else:
            if resp.status == 404:
                log.error("Datadog agent does not support data streams monitoring. Upgrade to 7.34+")
                return
            elif resp.status >= 400:
                log.error(
                    "failed to send data stream stats payload, %s (%s) (%s) response from Datadog agent at %s",
                    resp.status,
                    resp.reason,
                    resp.read(),
                    self._agent_endpoint,
                )
            else:
                log.debug("sent %s to %s", _human_size(len(payload)), self._agent_endpoint)

    def periodic(self):
        # type: () -> None

        with self._lock:
            serialized_stats = self._serialize_buckets()

        if not serialized_stats:
            log.debug("No data streams reported. Skipping flushing.")
            return
        raw_payload = {
            u"Service": self._service,
            u"TracerVersion": ddtrace.__version__,
            u"Lang": "python",
            u"Stats": serialized_stats,
            u"Hostname": self._hostname,
        }  # type: Dict[str, Union[List[Dict], str]]
        if config.env:
            raw_payload[u"Env"] = six.ensure_text(config.env)
        if config.version:
            raw_payload[u"Version"] = six.ensure_text(config.version)

        payload = packb(raw_payload)
        compressed = gzip_compress(payload)
        try:
            self._flush_stats_with_backoff(compressed)
        except Exception:
            log.error("retry limit exceeded submitting pathway stats to the Datadog agent at %s", self._agent_endpoint)

    def shutdown(self, timeout):
        # type: (Optional[float]) -> None
        self.periodic()
        self.stop(timeout)

    def decode_pathway(self, data):
        # type: (bytes) -> DataStreamsCtx
        try:
            hash_value = struct.unpack("<Q", data[:8])[0]
            data = data[8:]
            pathway_start_ms, data = decode_var_int_64(data)
            current_edge_start_ms, data = decode_var_int_64(data)
            ctx = DataStreamsCtx(self, hash_value, float(pathway_start_ms) / 1e3, float(current_edge_start_ms) / 1e3)
            # reset context of current thread every time we decode
            self._current_context.value = ctx
            return ctx
        except (EOFError, TypeError):
            return self.new_pathway()

    def decode_pathway_b64(self, data):
        # type: (Optional[str]) -> DataStreamsCtx
        if not data:
            return self.new_pathway()
        binary_pathway = data.encode("utf-8")
        encoded_pathway = base64.b64decode(binary_pathway)
        data_streams_context = self.decode_pathway(encoded_pathway)
        return data_streams_context

    def new_pathway(self):
        # type: () -> DataStreamsCtx
        now_sec = time.time()
        ctx = DataStreamsCtx(self, 0, now_sec, now_sec)
        return ctx

    def set_checkpoint(self, tags):
        if hasattr(self._current_context, "value"):
            ctx = self._current_context.value
        else:
            ctx = self.new_pathway()
            self._current_context.value = ctx
        ctx.set_checkpoint(tags)
        return ctx


class DataStreamsCtx:
    def __init__(self, processor, hash_value, pathway_start_sec, current_edge_start_sec):
        # type: (DataStreamsProcessor, int, float, float) -> None
        self.processor = processor
        self.pathway_start_sec = pathway_start_sec
        self.current_edge_start_sec = current_edge_start_sec
        self.hash = hash_value
        self.service = six.ensure_text(config._get_service("unnamed-python-service"))
        self.env = six.ensure_text(config.env or "none")
        # loop detection logic
        self.previous_direction = ""
        self.closest_opposite_direction_hash = 0
        self.closest_opposite_direction_edge_start = current_edge_start_sec

    def encode(self):
        # type: () -> bytes
        return (
            struct.pack("<Q", self.hash)
            + encode_var_int_64(int(self.pathway_start_sec * 1e3))
            + encode_var_int_64(int(self.current_edge_start_sec * 1e3))
        )

    def encode_b64(self):
        # type: () -> str
        encoded_pathway = self.encode()
        binary_pathway = base64.b64encode(encoded_pathway)
        data_streams_context = binary_pathway.decode("utf-8")
        return data_streams_context

    def _compute_hash(self, tags, parent_hash):
        if six.PY3:

            def get_bytes(s):
                return bytes(s, encoding="utf-8")

        else:

            def get_bytes(s):
                return bytes(s)

        b = get_bytes(self.service) + get_bytes(self.env)
        for t in tags:
            b += get_bytes(t)
        node_hash = fnv1_64(b)
        return fnv1_64(struct.pack("<Q", node_hash) + struct.pack("<Q", parent_hash))

    def set_checkpoint(self, tags):
        # type: (List[str]) -> None
        now_sec = time.time()
        tags = sorted(tags)
        direction = ""
        for t in tags:
            if t.startswith("direction:"):
                direction = t
                break
        if direction == self.previous_direction:
            self.hash = self.closest_opposite_direction_hash
            if self.hash == 0:
                # if the closest hash from opposite direction is 0, that means we produce in a loop, without consuming
                # in that case, we don't want the pathway to be longer and longer, but we want to restart a new pathway.
                self.current_edge_start_sec = now_sec
                self.pathway_start_sec = now_sec
            else:
                self.current_edge_start_sec = self.closest_opposite_direction_edge_start
        else:
            self.previous_direction = direction
            self.closest_opposite_direction_hash = self.hash
            self.closest_opposite_direction_edge_start = now_sec
        parent_hash = self.hash
        hash_value = self._compute_hash(tags, parent_hash)
        edge_latency_sec = now_sec - self.current_edge_start_sec
        pathway_latency_sec = now_sec - self.pathway_start_sec
        self.hash = hash_value
        self.current_edge_start_sec = now_sec
        self.processor.on_checkpoint_creation(
            hash_value, parent_hash, tags, now_sec, edge_latency_sec, pathway_latency_sec
        )
