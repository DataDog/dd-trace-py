"""
Kafka-specific event handler.

KafkaEventHandler extends MessagingEventHandler with:
- Data Streams Monitoring (DSM) checkpoint handling
- Kafka-specific logic
"""

from typing import TYPE_CHECKING

from ddtrace._trace.integrations.events.contrib.messaging import KafkaEvent
from ddtrace._trace.integrations.handlers.base.messaging import MessagingEventHandler


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


class KafkaEventHandler(MessagingEventHandler[KafkaEvent]):
    """
    Handler for Kafka events.

    Extends MessagingEventHandler with:
    - Data Streams Monitoring (DSM) checkpoint handling
    - Kafka-specific finish logic
    """

    def create_span(self, event: KafkaEvent) -> "Span":
        """Create span with DSM produce checkpoint."""
        span = super().create_span(event)

        if event.span_kind == "producer":
            self._handle_dsm_produce(span, event)

        return span

    def on_finish(self, span: "Span", event: KafkaEvent) -> None:
        """Finish with DSM consume checkpoint."""
        super().on_finish(span, event)

        if event.span_kind == "consumer":
            self._handle_dsm_consume(span, event)

    def _handle_dsm_produce(self, span: "Span", event: KafkaEvent) -> None:
        """Set DSM checkpoint for produced messages."""
        # TODO: Implement DSM produce checkpoint logic
        pass

    def _handle_dsm_consume(self, span: "Span", event: KafkaEvent) -> None:
        """Set DSM checkpoint for consumed messages."""
        # TODO: Implement DSM consume checkpoint logic
        pass
