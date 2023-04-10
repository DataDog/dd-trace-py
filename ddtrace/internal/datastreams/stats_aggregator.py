import logging
import time
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from datadog import statsd
from ddsketch import (
    DDSketch,
    LogarithmicMapping,
)
from ddsketch.mapping import Mapping
from ddsketch.store import (
    InMemoryStore,
    Store,
)
_BUCKET_DURATION = 10.0
_DEFAULT_SERVICE_NAME = "unnamed-py-service"
_NUM_THREADS = 10


class StatsPoint:
    def __init__(
            self,
            service,  # type: str
            edge_tags,  # type: List[str]
            hash_value,  # type: int
            parent_hash,  # type: int
            timestamp_type,  # type: str
            timestamp,  # type: int
            pathway_latency,  # type: bytes
            edge_latency,  # type: bytes
    ):
        if edge_tags is None:
            edge_tags = []

        self.service = service
        self.edge_tags = edge_tags
        self.hash_value = hash_value
        self.parent_hash = parent_hash
        self.timestamp_type = timestamp_type
        self.timestamp = timestamp
        self.pathway_latency = pathway_latency
        self.edge_latency = edge_latency

class StatsGroup:
    def __init__(self, service, edge_tags, hash, parent_hash):
        # type: (str, List[str], int, int) -> None
        self.service = service
        self.edge_tags = edge_tags
        self.hash = hash
        self.parent_hash = parent_hash
        self.pathway_latency = DDSketch(sketch_mapping())
        self.edge_latency = DDSketch(sketch_mapping())

    def __repr__(self):
        # type: () -> str
        return (
            "StatsGroup(service={!r}, edge_tags={!r}, hash={!r}, "
            "parent_hash={!r}, pathway_latency={!r}, edge_latency={!r})"
        ).format(
            self.service,
            self.edge_tags,
            self.hash,
            self.parent_hash,
            self.pathway_latency,
            self.edge_latency,
        )
