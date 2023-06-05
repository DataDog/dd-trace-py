from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.processor import SpanProcessor

from . import PEER_SERVICE_ENABLED


PEER_SERVICE_SOURCE_TAG = "_dd.peer.service.source"
PEER_SERVICE_TAG = "peer.service"
SPAN_KINDS_ENABLED_FOR_PEER_SERVICE = {SpanKind.CLIENT, SpanKind.PRODUCER}
PRIORITIZED_PEER_SERVICE_DATA_SOURCES = ["messaging.kafka.bootstrap.servers", "db.name", "rpc.service", "out.host"]


class PeerServiceProcessor(SpanProcessor):
    def on_span_start(self, span):
        pass

    def on_span_finish(self, span):
        if not PEER_SERVICE_ENABLED:
            return

        if span.get_tag(SPAN_KIND) not in SPAN_KINDS_ENABLED_FOR_PEER_SERVICE:
            return

        if span.get_tag(PEER_SERVICE_TAG):  # If the tag already exists, assume it is user generated
            span.set_tag(PEER_SERVICE_SOURCE_TAG, PEER_SERVICE_TAG)
            return

        for data_source in PRIORITIZED_PEER_SERVICE_DATA_SOURCES:
            peer_service_definition = span.get_tag(data_source)
            if peer_service_definition:
                span.set_tag(PEER_SERVICE_TAG, peer_service_definition)
                span.set_tag(PEER_SERVICE_SOURCE_TAG, data_source)
                return
