"""
HTTP server event handler.

HTTPServerEventHandler processes HTTPServerEvent instances, adding:
- Distributed tracing context extraction (parent-child relationship)
- Request span activation
"""

from typing import TYPE_CHECKING

from ddtrace import tracer
from ddtrace._trace.integrations.events.base.http_server import HTTPServerEvent
from ddtrace._trace.integrations.handlers.base._base import SpanEventHandler
from ddtrace.propagation.http import HTTPPropagator


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


class HTTPServerEventHandler(SpanEventHandler[HTTPServerEvent]):
    """
    Handler for HTTP server (web framework) events.

    Adds:
    - Distributed tracing context extraction (parent-child relationship)
    - Request span activation
    """

    def create_span(self, event: HTTPServerEvent) -> "Span":
        """Create span with extracted distributed tracing context as parent."""
        # Extract parent context FIRST
        parent_ctx = self._extract_distributed_tracing(event)

        # Create span with parent context using start_span
        span = tracer._start_span(
            event._span_name,
            service=event._service,
            resource=event._resource,
            span_type=event._span_type,
            child_of=parent_ctx,
            activate=True,
        )
        self.apply_tags(span, event)
        return span

    def _extract_distributed_tracing(self, event: HTTPServerEvent):
        """Extract trace context from inbound request headers."""
        if event._request_headers:
            return HTTPPropagator.extract(event._request_headers)
        return None
