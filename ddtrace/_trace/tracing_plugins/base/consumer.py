"""
ConsumerPlugin - Base for message consumers.

Extends InboundPlugin since consuming messages is an inbound operation
(receiving data FROM a message broker/queue).
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Tuple

from ddtrace._trace.tracing_plugins.base.inbound import InboundPlugin


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace._trace.tracing_plugins.base.events import MessagingContext
    from ddtrace.internal.core import ExecutionContext


class ConsumerPlugin(InboundPlugin):
    """
    Base plugin for message consumers.

    Extends InboundPlugin - consuming is receiving data IN from a broker.

    Handles:
    - Trace context extraction from message headers
    - Span linking to producer span
    - Destination tagging (topic, queue, exchange)
    - Message metadata tagging (offset, partition, etc.)

    Subclasses define:
    - package: str (e.g., "kafka", "rabbitmq")
    - operation: str (e.g., "consume", "receive")
    """

    from ddtrace.ext import SpanKind

    kind = SpanKind.CONSUMER
    type = "worker"

    def on_start(self, ctx: "ExecutionContext") -> None:
        """
        Create consumer span with context extraction.

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
        span_name = ctx.get_item("span_name") or f"{msg_ctx.messaging_system}.consume"

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

        # Extract and link trace context from message headers
        if msg_ctx.headers:
            distributed_ctx = HTTPPropagator.extract(msg_ctx.headers)
            if distributed_ctx:
                span.link_span(distributed_ctx)

    def on_finish(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """
        Finish consumer span with message metadata.

        Reads from ctx:
        - messaging_context: For consume metadata (offset, partition, etc.)
        """
        span = ctx.span
        if not span:
            return

        msg_ctx: Optional["MessagingContext"] = ctx.get_item("messaging_context")

        # Set consume-specific metadata
        if msg_ctx:
            if msg_ctx.message_id:
                span._set_tag_str("messaging.message.id", msg_ctx.message_id)
            if msg_ctx.partition is not None:
                span.set_metric("messaging.kafka.partition", msg_ctx.partition)
            if msg_ctx.offset is not None:
                span.set_metric("messaging.kafka.offset", msg_ctx.offset)

        # Call parent for error handling
        super().on_finish(ctx, exc_info)

    def _set_messaging_tags(self, span: "Span", msg_ctx: "MessagingContext") -> None:
        """Set standard messaging tags."""
        span._set_tag_str("messaging.system", msg_ctx.messaging_system)
        span._set_tag_str("messaging.destination.name", msg_ctx.destination)
        span._set_tag_str("messaging.operation", "receive")

        if msg_ctx.key:
            span._set_tag_str("messaging.kafka.message.key", msg_ctx.key)

        if msg_ctx.batch_size is not None and msg_ctx.batch_size > 1:
            span.set_metric("messaging.batch.message_count", msg_ctx.batch_size)

        # Extra tags
        if msg_ctx.tags:
            span.set_tags(msg_ctx.tags)
