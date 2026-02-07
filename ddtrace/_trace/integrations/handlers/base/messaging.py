"""
Base messaging event handler.

MessagingEventHandler is the base for all messaging integrations (Kafka, RabbitMQ, SQS, etc.).
Specific messaging handlers (KafkaEventHandler, etc.) are in separate files.
"""

from typing import TYPE_CHECKING
from typing import List
from typing import TypeVar

from ddtrace import config
from ddtrace._trace.integrations.events.contrib.messaging import MessagingEvent
from ddtrace._trace.integrations.handlers.base._base import SpanEventHandler
from ddtrace.propagation.http import HTTPPropagator


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


# TypeVar bound to MessagingEvent allows subclasses to specify their messaging event type
M = TypeVar("M", bound=MessagingEvent)


class MessagingEventHandler(SpanEventHandler[M]):
    """
    Handler for messaging events (Kafka, RabbitMQ, SQS, etc.).

    Adds:
    - Distributed tracing injection (producers)
    - Distributed tracing extraction and span linking (consumers)
    - Peer service resolution
    """

    peer_service_tags: List[str] = ["messaging_destination_name", "network_destination_name"]

    def create_span(self, event: M) -> "Span":
        """Create span with distributed tracing handling."""
        span = super().create_span(event)

        if event.span_kind == "producer":
            self._inject_distributed_tracing(span, event)
        elif event.span_kind == "consumer":
            self._extract_and_link(span, event)

        return span

    def on_finish(self, span: "Span", event: M) -> None:
        """Finish with peer service tagging for producers."""
        super().on_finish(span, event)
        if event.span_kind == "producer":
            self._set_peer_service(span, event)

    def _inject_distributed_tracing(self, span: "Span", event: M) -> None:
        """Inject trace context into message headers."""
        if event._inject_headers_callback:
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            event._inject_headers_callback(headers)

    def _extract_and_link(self, span: "Span", event: M) -> None:
        """Extract trace context and link to producer span."""
        if event._propagation_headers:
            ctx = HTTPPropagator.extract(event._propagation_headers)
            if ctx:
                span.link_span(ctx)

    def _set_peer_service(self, span: "Span", event: M) -> None:
        """Set peer.service from messaging destination."""
        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            value = span.get_tag(tag)
            if value:
                span._set_tag_str("peer.service", str(value))
                break
