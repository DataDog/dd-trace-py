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
from typing import NamedTuple
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
from ddtrace.internal.utils.fnv import fnv1_64


def gzip_compress(payload):
    return gzip.compress(payload, 1)


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


PartitionKey = NamedTuple("PartitionKey", [("topic", str), ("partition", int)])
ConsumerPartitionKey = NamedTuple("ConsumerPartitionKey", [("group", str), ("topic", str), ("partition", int)])
Bucket = NamedTuple(
    "Bucket",
    [
        ("pathway_stats", DefaultDict[PathwayAggrKey, PathwayStats]),
        ("latest_produce_offsets", DefaultDict[PartitionKey, int]),
        ("latest_commit_offsets", DefaultDict[ConsumerPartitionKey, int]),
    ],
)


class DataStreamsProcessor(PeriodicSeyyrvice):
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
            lambda: Bucket(defaultdict(PathwayStats), defaultdict(int), defaultdict(int))
        )  # type: DefaultDict[int, Bucket]
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
            stats = self._buckets[bucket_time_ns].pathway_stats[aggr_key]
            stats.full_pathway_latency.add(full_pathway_latency_sec)
            stats.edge_latency.add(edge_latency_sec)
            self._buckets[bucket_time_ns].pathway_stats[aggr_key] = stats

    def track_kafka_produce(self, topic, partition, offset, now_sec):
        now_ns = int(now_sec * 1e9)
        key = PartitionKey(topic, partition)
        with self._lock:
            bucket_time_ns = now_ns - (now_ns % self._bucket_size_ns)
            self._buckets[bucket_time_ns].latest_produce_offsets[key] = max(
                offset, self._buckets[bucket_time_ns].latest_produce_offsets[key]
            )

    def track_kafka_commit(self, group, topic, partition, offset, now_sec):
        now_ns = int(now_sec * 1e9)
        key = ConsumerPartitionKey(group, topic, partition)
        with self._lock:
            bucket_time_ns = now_ns - (now_ns % self._bucket_size_ns)
            self._buckets[bucket_time_ns].latest_commit_offsets[key] = max(
                offset, self._buckets[bucket_time_ns].latest_commit_offsets[key]
            )

    def _serialize_buckets(self):
        # type: () -> List[Dict]
        """Serialize and update the buckets."""
        serialized_buckets = []
        serialized_bucket_keys = []
        for bucket_time_ns, bucket in self._buckets.items():
            bucket_aggr_stats = []
            backlogs = []
            serialized_bucket_keys.append(bucket_time_ns)

            for aggr_key, stat_aggr in bucket.pathway_stats.items():
                edge_tags, hash_value, parent_hash = aggr_key
                serialized_bucket = {
                    u"EdgeTags": [six.ensure_text(tag) for tag in edge_tags.split(",")],
                    u"Hash": hash_value,
                    u"ParentHash": parent_hash,
                    u"PathwayLatency": DDSketchProto.to_proto(stat_aggr.full_pathway_latency).SerializeToString(),
                    u"EdgeLatency": DDSketchProto.to_proto(stat_aggr.edge_latency).SerializeToString(),
                }
                bucket_aggr_stats.append(serialized_bucket)
            for consumer_key, offset in bucket.latest_commit_offsets.items():
                backlogs.append(
                    {
                        u"Tags": [
                            "type:kafka_commit",
                            "consumer_group:" + consumer_key.group,
                            "topic:" + consumer_key.topic,
                            "partition:" + str(consumer_key.partition),
                        ],
                        u"Value": offset,
                    }
                )
            for producer_key, offset in bucket.latest_produce_offsets.items():
                backlogs.append(
                    {
                        u"Tags": [
                            "type:kafka_produce",
                            "topic:" + producer_key.topic,
                            "partition:" + str(producer_key.partition),
                        ],
                        u"Value": offset,
                    }
                )
            serialized_buckets.append(
                {
                    u"Start": bucket_time_ns,
                    u"Duration": self._bucket_size_ns,
                    u"Stats": bucket_aggr_stats,
                    u"Backlogs": backlogs,
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

    def new_pathway(self, now_sec=None):
        """
        type: (Optional[int]) -> DataStreamsCtx
        :param now_sec: optional start time of this path. Use for services like Kinesis which
                           we aren't getting path information for.
        """

        if not now_sec:
            now_sec = time.time()
        ctx = DataStreamsCtx(self, 0, now_sec, now_sec)
        return ctx

    def set_checkpoint(self, tags, now_sec=None):
        if not now_sec:
            now_sec = time.time()
        if hasattr(self._current_context, "value"):
            ctx = self._current_context.value
        else:
            ctx = self.new_pathway()
            self._current_context.value = ctx
        ctx.set_checkpoint(tags, now_sec=now_sec)
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

    def set_checkpoint(self, tags, now_sec=None, edge_start_sec_override=None, pathway_start_sec_override=None):
        """
        type: (List[str], float, float, float) -> None

        :param tags: an list of tags identifying the pathway and direction
        :param now_sec: The time in seconds to count as "now" when computing latencies
        :param edge_start_sec_override: Use this to override the starting time of an edge
        :param pathway_start_sec_override: Use this to override the starting time of a pathway
        """
        if not now_sec:
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

        if edge_start_sec_override:
            self.current_edge_start_sec = edge_start_sec_override

        if pathway_start_sec_override:
            self.pathway_start_sec = pathway_start_sec_override

        parent_hash = self.hash
        hash_value = self._compute_hash(tags, parent_hash)
        edge_latency_sec = now_sec - self.current_edge_start_sec
        pathway_latency_sec = now_sec - self.pathway_start_sec
        self.hash = hash_value
        self.current_edge_start_sec = now_sec
        self.processor.on_checkpoint_creation(
            hash_value, parent_hash, tags, now_sec, edge_latency_sec, pathway_latency_sec
        )


"""
We need to pass:
* root hash
* parent path hash (including this node)
* 
payload = 
{
    node: NodeId : {
        service: ""
        env: ""
        host: ""
    },
    paths: PathwayInfo : {
        start: 0
        duration: 5
        stats: [ PathwayStats: {
            edge:  EdgeId: {
                type: EdgeType: {
                    UNKNOWN|HTTP|GRPC
                },
                name: ""
            },
            info: PathayInfo: {
            },
            request_latency: 0,
            response_latency: 0
        }]
    }
}
"""

"""
We need to pass:
* Path information

Metrics are generated
"""

def inject_context(headers):
    log.debug("teague.bick - attempting to inject acupath headers into %r", headers)
    headers[HTTP_HEADER_ACUPATH_PATH_ID] = generate_current_path_id_v0(tags=[])

    current_id, current_time = generate_current_service_node_id_v0()
    headers[HTTP_HEADER_ACUPATH_PARENT_ID] = current_id
    headers[HTTP_HEADER_ACUPATH_PARENT_TIME] = current_time 

    root_id, root_time = generate_root_node_id_v0()
    headers[HTTP_HEADER_ACUPATH_ROOT_ID] = root_id 
    headers[HTTP_HEADER_ACUPATH_ROOT_TIME] = root_time

    log.debug("teague.bick - injected acupath headers into %r", headers)


def generate_current_service_node_id_v0():
    import json
    service = os.environ.get("DD_SERVICE", "unnamed-python-service")
    env = os.environ.get("DD_ENV", "none")
    host = os.environ.get("DD_HOSTNAME", "")

    service_node = dict(
            service=service,
            env=env,
            host=host,
        )

    return (json.dumps(service_node), str(time.time()))


def generate_node_hash_v0(node_info):
    import json
    def get_bytes(s):
        return bytes(s, encoding="utf-8")
    
    if isinstance(node_info, str):
        node_info = json.loads(node_info)

    b = get_bytes(node_info['service']) + get_bytes(node_info['env']) + get_bytes(node_info['host'])
    node_hash = fnv1_64(b)
    return fnv1_64(struct.pack("<Q", node_hash))


def generate_current_path_id_v0(tags=[]):
    from ddtrace.internal import core
    import json
    log.debug("teague.bick - a")
    parent_pathway_hash = int(core.get_item(PARENT_PATHWAY_ID) or 0)
    log.debug("teague.bick - b")

    current_node = generate_current_service_node_id_v0()[0]
    log.debug("teague.bick - c")

    def get_bytes(s):
        return bytes(s, encoding="utf-8")

    b = get_bytes(json.loads(current_node)['service']) + get_bytes(json.loads(current_node)['env']) + get_bytes(json.loads(current_node)['host'])
    log.debug("teague.bick - d")
    for t in tags:
        b += get_bytes(t)
    log.debug("teague.bick - e")
    node_hash = fnv1_64(b)
    log.debug("teague.bick - f")
    result = fnv1_64(struct.pack("<Q", node_hash) + struct.pack("<Q", parent_pathway_hash))
    log.debug("teague.bick - g")
    return result


def generate_root_node_id_v0():
    from ddtrace.internal import core
    return (str(core.get_item(ROOT_NODE_ID) or generate_current_service_node_id_v0()[0]), str(core.get_item(ROOT_NODE_TIME) or time.time()))


def extract_acupath_information(headers):
    extract_root_node_id_v0(headers)
    extract_parent_service_node_id_v0(headers)
    extract_path_info_v0(headers)


def extract_root_node_id_v0(headers):
    from ddtrace.internal import core
    root_node_id_header = headers[HTTP_HEADER_ACUPATH_ROOT_ID]
    root_node_time = headers[HTTP_HEADER_ACUPATH_ROOT_TIME]
    core.set_item(ROOT_NODE_TIME, float(root_node_time))
    core.set_item(ROOT_NODE_ID, root_node_id_header)
    log.debug("teague.bick - extracted root id header value: %s", root_node_id_header)
    log.debug("teague.bick - extracted root time header value: %s", root_node_time)


def extract_parent_service_node_id_v0(headers):
    from ddtrace.internal import core
    parent_node_id = headers[HTTP_HEADER_ACUPATH_PARENT_ID]
    parent_time = headers[HTTP_HEADER_ACUPATH_PARENT_TIME]
    core.set_item(PARENT_NODE_ID, parent_node_id)
    core.set_item(PARENT_NODE_TIME, float(parent_time))
    log.debug("teague.bick - extracted parent service info of: %r", parent_node_id)
    log.debug("teague.bick - extracted parent service time info of: %r", parent_time)

def extract_path_info_v0(headers):
    from ddtrace.internal import core
    parent_path_id = headers[HTTP_HEADER_ACUPATH_PATH_ID]
    core.set_item(PARENT_PATHWAY_ID, parent_path_id)

def generate_parent_service_node_info_v0():
    from ddtrace.internal import core
    return (str(core.get_item(PARENT_NODE_ID) or ""), int(core.get_item(PARENT_NODE_TIME) or 0))


def generate_payload_v0():
    # Protobuf 
    # https://protobuf.dev/getting-started/pythontutorial/
    """
    to regenerate the proto:
    protoc -I=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/ \
        --python_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/acupath/ \
        --pyi_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/acupath/ \
        /Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/payload.proto 
    """
    log.debug("teague.bick - starting to generate payload")
    from ddtrace.internal.acupath.payload_pb2 import DataPathAPIPayload
    from ddtrace.internal.acupath.payload_pb2 import NodeID
    from ddtrace.internal.acupath.payload_pb2 import EdgeType
    from ddtrace.internal.acupath.payload_pb2 import EdgeID
    from ddtrace.internal.acupath.payload_pb2 import PathwayInfo
    from ddtrace.internal.acupath.payload_pb2 import PathwayStats
    from ddtrace.internal.acupath.payload_pb2 import Paths
    import json
    log.debug("teague.bick - payload 0")
    now = time.time()
    log.debug("teague.bick - payload 1")
    root_info, root_time = generate_root_node_id_v0()
    log.debug("teague.bick - payload 2")
    node_info = json.loads(generate_current_service_node_id_v0()[0])
    log.debug("teague.bick - payload 3")
    current_node_hash = generate_node_hash_v0(node_info)
    log.debug("teague.bick - payload 4")
    root_node_hash = generate_node_hash_v0(root_info)
    log.debug("teague.bick - payload 5")
    pathway_hash = generate_current_path_id_v0()
    log.debug("teague.bick - payload 6")
    parent_hash, parent_time = generate_parent_service_node_info_v0()

    # ADD THIS NODE TO THE PAYLOAD
    log.debug("teague.bick - payload a")
    node = NodeID()
    node.service = node_info['service']
    node.env = node_info['env']
    node.host = node_info['host']

    # REPRESENT THIS EDGE
    log.debug("teague.bick - payload b")
    edge = EdgeID()
    edge.type = EdgeType.HTTP
    edge.name = "foo"  # What are the requirements for the name?


    # REPRESENT PATHWAY
    log.debug("teague.bick - payload c")
    pathway = PathwayInfo()
    pathway.root_service_hash =root_node_hash 
    pathway.node_hash = current_node_hash 
    pathway.upstream_pathway_hash = pathway_hash
    pathway.downstream_pathway_hash = 0

    # PATHWAY STATS
    log.debug("teague.bick - payload d")
    pathway_stats = PathwayStats()
    pathway_stats.info.CopyFrom(pathway)
    pathway_stats.edge.CopyFrom(edge)
    full_pathway_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
    full_pathway_latency.add(now - int(float(root_time)* 1e3))
    edge_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
    edge_latency.add(now - int(float(parent_time)*1e3))
    from_root_latency = DDSketchProto.to_proto(full_pathway_latency).SerializeToString()
    from_downstream_latency = DDSketchProto.to_proto(edge_latency).SerializeToString()
    pathway_stats.request_latency = from_root_latency
    pathway_stats.response_latency = from_downstream_latency

    # PATHS info
    log.debug("teague.bick - payload e")
    paths = Paths()
    paths.start = int(now - (now%ACCUPATH_COLLECTION_DURATION))
    paths.duration = ACCUPATH_COLLECTION_DURATION
    paths.stats.append(pathway_stats)

    # PAYLOAD
    log.debug("teague.bick - payload f")
    payload = DataPathAPIPayload()
    payload.node.CopyFrom(node)
    payload.paths.CopyFrom(paths)

    msg = "teague.bick - checking initialized: %s" % payload.IsInitialized()
    log.debug(msg)
    #log.debug("teague.bick - serializing payload: %s" % payload.__str__())
    return payload.SerializeToString()

def report_information_to_backend():
    log.debug("teague.bick - beginning to report to backend")
    payload = generate_payload_v0()
    log.debug("teague.bick - payload generated")
    headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
    log.debug("teague.bick - headers are: %s" % headers)
    try:
        conn = get_connection(ACCUPATH_BASE_URL, ACCUPATH_TIMEOUT)
        conn.request("POST", ACCUPATH_ENDPOINT, payload, headers)
        resp = get_connection_response(conn)
    except Exception:
        raise
    else:
        if resp.status == 404:
            log.error("Error sending data, response is: %s and conn is: %s" % (resp.__dict__, conn.__dict__))
            return
        elif resp.status >= 400:
            log.error(
                "failed to send data stream stats payload, %s (%s) (%s) response from Datadog agent at %s",
                resp.status,
                resp.reason,
                resp.read(),
                ACCUPATH_BASE_URL
            )
        else:
            log.debug("sent %s to %s", _human_size(len(payload)), ACCUPATH_BASE_URL)


# Quick Fix Constants
ACCUPATH_BASE_URL = "https://trace-internal.agent.datad0g.com"
ACCUPATH_TIMEOUT = 10
ACCUPATH_ENDPOINT = "/api/v0.2/datapaths"
ACCUPATH_COLLECTION_DURATION = 10  # 10 seconds?



# Core Constants
ROOT_NODE_ID = "acupath_root_node_id"
ROOT_NODE_TIME = "acupath_root_node_time"
PARENT_NODE_ID = "acupath_parent_node_id"
PARENT_NODE_TIME = "acupath_parent_node_time"
PARENT_PATHWAY_ID = "acupath_parent_pathway_id"

# Headers
HTTP_HEADER_ACUPATH_PARENT_ID = "x-datadog-acupath-parent-id"  # Current node hash
HTTP_HEADER_ACUPATH_PARENT_TIME = "x-datadog-acupath-parent-time"  # Current node hash
HTTP_HEADER_ACUPATH_PATH_ID = "x-datadog-acupath-path-id"  # Pathway up to (and incuding) current node hash
HTTP_HEADER_ACUPATH_ROOT_ID = "x-datadog-acupath-root-id"  # Hash for first node in pathway
HTTP_HEADER_ACUPATH_ROOT_TIME = "x-datadog-acupath-root-time"  # Hash for first node in pathway

# Assumptions we need to fix/validate eventually
"""
* Every tracer supports these headers (especially upstream)
* Efficiency of data transmitted (hashing/unhashing)
* Context propagation only woks through the 'datadog' propagator (others not supported -_-)
* core is implemented and will always find the right items
* How is information back-propagated?
* Make the metrics a periodic service (instead of synchronous)
* Actually aggregate metrics
"""
