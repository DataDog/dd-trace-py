"""
HTTP client event handler.

HTTPClientEventHandler processes HTTPClientEvent instances, adding:
- Distributed tracing header injection
- Peer service resolution
"""

from typing import TYPE_CHECKING
from typing import List

from ddtrace import config
from ddtrace._trace.integrations.events.base.http_client import HTTPClientEvent
from ddtrace._trace.integrations.handlers.base._base import SpanEventHandler
from ddtrace.propagation.http import HTTPPropagator


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


class HTTPClientEventHandler(SpanEventHandler[HTTPClientEvent]):
    """
    Handler for HTTP client events.

    Adds:
    - Distributed tracing header injection
    - Peer service resolution
    """

    peer_service_tags: List[str] = ["network_destination_name", "http_host"]

    def create_span(self, event: HTTPClientEvent) -> "Span":
        """Create span and inject distributed tracing headers."""
        span = super().create_span(event)
        self._inject_distributed_tracing(span, event)
        return span

    def on_finish(self, span: "Span", event: HTTPClientEvent) -> None:
        """Finish with peer service tagging."""
        super().on_finish(span, event)
        self._set_peer_service(span, event)

    def _inject_distributed_tracing(self, span: "Span", event: HTTPClientEvent) -> None:
        """Inject trace context into outbound request headers."""
        if event._inject_headers_callback:
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            event._inject_headers_callback(headers)

    def _set_peer_service(self, span: "Span", event: HTTPClientEvent) -> None:
        """Set peer.service from HTTP target info."""
        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            value = span.get_tag(tag)
            if value:
                span._set_tag_str("peer.service", str(value))
                break
