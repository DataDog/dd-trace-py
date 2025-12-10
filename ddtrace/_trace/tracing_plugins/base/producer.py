"""
ProducerPlugin - Base for message producers.

Extends OutboundPlugin since producing messages is an outbound operation
(sending data TO a message broker/queue).
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Tuple

from ddtrace._trace.tracing_plugins.base.outbound import OutboundPlugin


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace._trace.tracing_plugins.base.events import MessagingContext
    from ddtrace.internal.core import ExecutionContext


class ProducerPlugin(OutboundPlugin):
    """
    Base plugin for message producers.

    Extends OutboundPlugin - producing is sending data OUT to a broker.

    Handles:
    - Trace context injection into message headers
    - Destination tagging (topic, queue, exchange)
    - Message metadata tagging
    - Broker host tagging (for peer service)

    Subclasses define:
    - package: str (e.g., "kafka", "rabbitmq")
    - operation: str (e.g., "produce", "send")
    """

    from ddtrace.ext import SpanKind

    kind = SpanKind.PRODUCER
    type = "worker"

    def on_start(self, ctx: "ExecutionContext") -> None:
        """
        Create producer span with context injection.

        Reads from ctx:
        - pin: The Pin instance
        - messaging_context: MessagingContext with destination, headers, etc.
        - span_name: Optional override for span name
        """
        from ddtrace.constants import _SPAN_MEASURED_KEY
        from ddtrace.ext import SpanTypes
        from ddtrace.propagation.http import HTTPPropagator

        pin = ctx.get_item("pin")
        msg_ctx: Optional["MessagingContext"] = ctx.get_item("messaging_context")

        if not pin or not pin.enabled() or not msg_ctx:
            return

        # Determine span name
        span_name = ctx.get_item("span_name") or f"{msg_ctx.messaging_system}.produce"

        # Create span
        span = self.start_span(
            ctx,
            span_name,
            resource=msg_ctx.destination,
            span_type=SpanTypes.WORKER,
        )

        if not span:
            return

        # Mark as measured
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        # Set messaging tags
        self._set_messaging_tags(span, msg_ctx)

        # Add broker host for peer service
        if msg_ctx.host:
            self.add_host(span, msg_ctx.host, msg_ctx.port)

        # Inject trace context into message headers
        if msg_ctx.inject_headers:
            headers: dict = {}
            HTTPPropagator.inject(span.context, headers)
            msg_ctx.inject_headers(headers)

    def on_finish(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """
        Finish producer span with message metadata.

        Reads from ctx:
        - messaging_context: For any post-produce metadata (message_id, partition)
        """
        span = ctx.span
        if not span:
            return

        msg_ctx: Optional["MessagingContext"] = ctx.get_item("messaging_context")

        # Set any metadata that was determined after produce
        if msg_ctx:
            if msg_ctx.message_id:
                span._set_tag_str("messaging.message.id", msg_ctx.message_id)
            if msg_ctx.partition is not None:
                span.set_metric("messaging.kafka.partition", msg_ctx.partition)

        # Call parent for peer service and error handling
        super().on_finish(ctx, exc_info)

    def _set_messaging_tags(self, span: "Span", msg_ctx: "MessagingContext") -> None:
        """Set standard messaging tags."""
        span._set_tag_str("messaging.system", msg_ctx.messaging_system)
        span._set_tag_str("messaging.destination.name", msg_ctx.destination)
        span._set_tag_str("messaging.operation", "publish")

        if msg_ctx.key:
            span._set_tag_str("messaging.kafka.message.key", msg_ctx.key)

        if msg_ctx.batch_size is not None and msg_ctx.batch_size > 1:
            span.set_metric("messaging.batch.message_count", msg_ctx.batch_size)

        # Extra tags
        if msg_ctx.tags:
            span.set_tags(msg_ctx.tags)

    def _get_peer_service(self, ctx: "ExecutionContext", span: "Span") -> Optional[str]:
        """
        Get peer service for producer spans.

        For messaging, use the destination (topic/queue) as peer service.
        """
        destination = span.get_tag("messaging.destination.name")
        if destination:
            return str(destination)

        return super()._get_peer_service(ctx, span)
