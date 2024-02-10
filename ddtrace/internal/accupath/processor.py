# coding: utf-8
import os
import logging
from collections import defaultdict
from typing import DefaultDict
from typing import NamedTuple
ROOT_NODE_ID = "accupath.service.root_node_info"#accupath_root_node_id"
ROOT_NODE_REQUEST_OUT_TIME = "accupath.service.root_out"
PARENT_NODE_ID = "accupath_parent_node_id"
PARENT_NODE_REQUEST_OUT_TIME = "accupath_parent_node_request_out_time"

from ddsketch.pb.proto import DDSketchProto
from ddsketch import LogCollapsingLowestDenseDDSketch
from ddtrace.internal import core
from ddtrace.internal.accupath.service_context import AccuPathServiceContext
from ddtrace.internal.accupath.pathway_context import _get_current_pathway_context
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.forksafe import Lock

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..writer import _human_size

# Protobuff
from ddtrace.internal.accupath.payload_pb2 import DataPathAPIPayload
from ddtrace.internal.accupath.payload_pb2 import NodeID
from ddtrace.internal.accupath.payload_pb2 import EdgeType
from ddtrace.internal.accupath.payload_pb2 import EdgeID
from ddtrace.internal.accupath.payload_pb2 import PathwayInfo
from ddtrace.internal.accupath.payload_pb2 import PathwayStats
from ddtrace.internal.accupath.payload_pb2 import Paths
from ddtrace.internal.accupath.payload_pb2 import Latencies


log = get_logger(f"accupath.{__name__}")
log.setLevel(logging.DEBUG)


# Quick Fix Constants
ACCUPATH_BASE_URL = "https://trace-internal.agent.datad0g.com"
ACCUPATH_TIMEOUT = 10
ACCUPATH_ENDPOINT = "/api/v0.2/datapaths"
ACCUPATH_COLLECTION_DURATION = 10  # 10 seconds

class _AccuPathProcessor(PeriodicService):
    def __init__(self, interval=10):
        super().__init__(interval=interval)
        self._interval = interval
        self._bucket_size_ns = int(interval * 1e9)  # type: int
        self._lock = Lock()
        self._counter = 0
        self.start()

    def set_metrics_bucket(self, buckets):
        self._buckets = buckets

    def add_bucket_data(
            self,
            items
        ):
        """
        Add the data into buckets
        """
        try:
            with self._lock:
                for time_index, path_key, metric_name, metric_value in items:
                    bucket_time_ns = time_index - (time_index % self._bucket_size_ns)
                    stats = self._buckets[bucket_time_ns].pathway_stats[path_key]
                    if hasattr(stats, metric_name):
                        log.debug("Added bucket entry: %s", metric_value)
                        getattr(stats, metric_name).add(metric_value)
        except Exception as e:
            log.debug("accupath - error", exc_info=True)
        log.debug("Added bucket data")

    def periodic(self):
        try:
            from ddtrace import tracer
            with tracer.trace("accupath.periodic"):
                log.debug("flushing stats")
                self._flush_stats(tracer.current_trace_context())
                log.debug("flushed stats")
        except Exception as e:
            log.debug("accupath - error _flush_stats", exc_info=True)

    def _flush_stats(self, trace_context):
        from ddtrace.propagation.http import HTTPPropagator
        headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
        HTTPPropagator.inject(trace_context, headers)
        log.debug(f"Submitting with headers: {headers}")
        to_del = set()
        with self._lock:
            for bucket_time, bucket in self._buckets.items():
                for path_info_key, actual_bucket in bucket.pathway_stats.items():
                    payload=None
                    try:
                        payload = _generate_payload_v0(
                            bucket_start_time=bucket_time,
                            bucket_duration=ACCUPATH_COLLECTION_DURATION,
                            root_hash=path_info_key.root_hash,
                            current_node_info = AccuPathServiceContext.from_local_env(),
                            path_key_info = path_info_key,
                            pathway_stat_bucket = actual_bucket
                            )
                    except Exception as e:
                        log.debug("Ran into an issue creating payloads", exc_info=True)
                        return 
                    try:
                        conn = self._conn()
                        conn.request("POST", ACCUPATH_ENDPOINT, payload, headers)
                        resp = get_connection_response(conn)
                    except Exception:
                        raise
                    else:
                        if resp.status >= 200 and resp.status < 300:
                            log.debug("accupath sent %s to %s", _human_size(len(payload)), ACCUPATH_BASE_URL)
                            to_del.add(bucket_time)
                        elif resp.status == 404:
                            log.error("Error sending data, response is: %s and conn is: %s" % (resp.__dict__, conn.__dict__))
                            return
                        else:
                            log.error(
                                "failed to send data stream stats payload, %s (%s) (%s) response from Datadog agent at %s",
                                resp.status,
                                resp.reason,
                                resp.read(),
                                ACCUPATH_BASE_URL
                            )
            for b_t in to_del:
                if b_t in self._buckets:
                    del self._buckets[b_t]

    def _conn(self):
        conn = get_connection(ACCUPATH_BASE_URL, ACCUPATH_TIMEOUT)
        return conn


def generate_parent_service_node_info_v0():
    return (str(core.get_item(PARENT_NODE_ID) or ""), int(core.get_item(PARENT_NODE_REQUEST_OUT_TIME) or 0))


def _generate_payload_v0(
        bucket_start_time=None,
        bucket_duration=None,
        root_hash=None,
        current_node_info=None,
        path_key_info=None,
        pathway_stat_bucket=None
    ):
    # Protobuf
    # https://protobuf.dev/getting-started/pythontutorial/
    """
    to regenerate the proto:
    protoc -I=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/ \
        --python_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        --pyi_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        /Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/payload.proto
    """
    log.info("accupath - generating payload")

    # ADD THIS NODE TO THE PAYLOAD
    node = NodeID()
    node.env = current_node_info.env
    node.host = current_node_info.hostname
    node.service = current_node_info.service
    log.debug("Setting payload service/host/env to: %s/%s/%s" % (node.service, node.host, node.env))

    # REPRESENT THIS EDGE
    edge = EdgeID()
    edge.resource_name = path_key_info.resource_name or ""
    edge.operation_name = path_key_info.operation_name or ""
    edge.type = EdgeType.HTTP
    log.debug(f"Setting resource name to: {path_key_info.resource_name}")

    # REPRESENT PATHWAY
    pathway = PathwayInfo()
    pathway.node_hash = path_key_info.node_hash
    pathway.parent_hash = path_key_info.request_pathway_id
    pathway.root_hash = root_hash
    pathway_string = " -> ".join([
        f"Time Bucket: {bucket_start_time}",
        f"root: {pathway.root_hash}",
        f"parent: {pathway.parent_hash}",
    f"current: {pathway.node_hash} - ({current_node_info.service}, {current_node_info.env})",
        f"resource: {path_key_info.resource_name}"
    ])
    log.debug(f"accupath payload -  {pathway_string}")

    #  LATENCIES
    latencies = Latencies()

    ok_latency_sketch = pathway_stat_bucket.root_to_request_out_latency
    ok_latency_proto = DDSketchProto.to_proto(ok_latency_sketch)

    error_latency_sketch = pathway_stat_bucket.root_to_request_out_latency_errors
    error_latency_proto = DDSketchProto.to_proto(error_latency_sketch)

    latencies.ok_latency = ok_latency_proto.SerializeToString()
    latencies.error_latency = error_latency_proto.SerializeToString()


    # PATHWAY STATS
    request_pathway_stats = PathwayStats()
    request_pathway_stats.edge.CopyFrom(edge)
    request_pathway_stats.info.CopyFrom(pathway)
    request_pathway_stats.latencies.CopyFrom(latencies)


    # PATHS info
    log.debug("Bucket start time: %s" % bucket_start_time)
    request_path = Paths()
    request_path.duration = bucket_duration
    request_path.start = bucket_start_time
    request_path.stats.append(request_pathway_stats)

    # PAYLOAD
    payload = DataPathAPIPayload()
    payload.node.CopyFrom(node)
    payload.paths.append(request_path)
    log.debug("Payload: \n%s", payload)

    return payload.SerializeToString()


class PathKey:
    def __init__(self, request_pathway_id, node_hash, request_id, resource_name, operation_name, root_hash):
        self.request_pathway_id = request_pathway_id
        self.request_id = request_id
        self.node_hash = node_hash
        self.resource_name = resource_name
        self.operation_name = operation_name
        self.root_hash = root_hash

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, PathKey):
            return False

        return all(
            [
                self.request_pathway_id == __value.request_pathway_id,
            ]
        )
    
    def __hash__(self):
        return hash((
            self.request_pathway_id,
            ))

def submit_metrics():
    try:
        current_pathway_context = _get_current_pathway_context()
        if current_pathway_context.submitted:
            return

        path_key = PathKey(
            request_pathway_id = current_pathway_context.parent_hash,
            node_hash = current_pathway_context.node_hash,
            request_id = current_pathway_context.uid,
            resource_name = current_pathway_context.resource_name,
            operation_name = current_pathway_context.operation_name,
            root_hash = current_pathway_context.root_hash,
        )

        response_in_time = current_pathway_context.checkpoint_times.get("response_in")
        response_out_time = current_pathway_context.checkpoint_times.get("response_out")
        request_in_time = current_pathway_context.checkpoint_times.get("request_in")
        request_out_time = current_pathway_context.checkpoint_times.get("request_out")
        root_request_out_time = current_pathway_context.root_checkpoint_time
        response_in_status = current_pathway_context.success



        bucket_time = request_in_time if request_in_time > 0 else request_out_time
        time_from_root = max(0, (bucket_time - root_request_out_time))
        log.debug("submit metrics: %s", current_pathway_context)
        log.debug("Metrics are: %s :: %s :: %s :: %s", root_request_out_time, bucket_time, response_in_status, time_from_root)


        to_submit = []
        if response_in_status:
            to_submit.extend([
                (bucket_time, path_key, "root_to_request_out_latency", time_from_root),
            ])
        else:
            to_submit.extend([
                (bucket_time, path_key, "root_to_request_out_latency_errors", time_from_root),
            ])
        _processor_singleton.add_bucket_data(to_submit)
        current_pathway_context.submitted = True
    except:
        log.debug("Error in submit_metrics", exc_info=True)


class AccuPathPathwayStats:
    """Aggregated pathway statistics."""

    __slots__ = (
        "root_to_request_out_latency",
        "root_to_request_out_latency_errors",
    )

    def __init__(self):
        self.root_to_request_out_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_request_out_latency_errors = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)

Bucket = NamedTuple(
    "Bucket",
    [
        ("pathway_stats", DefaultDict[PathKey, AccuPathPathwayStats]),
    ],
)

_buckets = defaultdict(
    lambda: Bucket(defaultdict(AccuPathPathwayStats))
)

_processor_singleton = _AccuPathProcessor()
_processor_singleton.set_metrics_bucket(_buckets)