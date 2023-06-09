import os

from ddtrace.ext import SpanKind
from ddtrace.internal.schema import SCHEMA_VERSION
from ddtrace.internal.utils.formats import asbool


class PeerServiceConfig(object):
    source_tag_name = "_dd.peer.service.source"
    tag_name = "peer.service"
    enabled_span_kinds = {SpanKind.CLIENT, SpanKind.PRODUCER}
    prioritized_data_sources = ["messaging.kafka.bootstrap.servers", "db.name", "rpc.service", "out.host"]

    @property
    def enabled(self):
        env_enabled = asbool(os.getenv("DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED", default=False))

        return SCHEMA_VERSION == "v1" or (SCHEMA_VERSION == "v0" and env_enabled)
