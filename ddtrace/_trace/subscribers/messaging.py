from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingActionEvent
from ddtrace.contrib._events.messaging import MessagingEvent
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.contrib._events.messaging import MessagingReceiveEvent
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator


def _messaging_direction(event: MessagingEvent) -> Optional[SpanDirection]:
    """Map produce/receive/process to schema directions. Action names stay v0."""
    if isinstance(event, MessagingProducerEvent):
        return SpanDirection.OUTBOUND
    if isinstance(event, MessagingReceiveEvent):
        return SpanDirection.INBOUND
    if isinstance(event, MessagingProcessEvent):
        return SpanDirection.PROCESSING
    return None


class MessagingTracingSubscriber(TracingSubscriber[MessagingEvent]):
    event_names = (
        MessagingProducerEvent.event_name,
        MessagingReceiveEvent.event_name,
        MessagingProcessEvent.event_name,
        MessagingActionEvent.event_name,
    )

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[MessagingEvent]) -> None:
        event = ctx.event
        direction = _messaging_direction(event)
        if direction is not None and event.messaging_system:
            event.operation_name = schematize_messaging_operation(  # type: ignore[operator]  # schema helper is selected at import
                event.operation,
                provider=event.messaging_system,
                direction=direction,
            )
        if event.resource is None:
            event.resource = event.operation_name
        propagated_context = None
        if (
            isinstance(event, (MessagingReceiveEvent, MessagingProcessEvent))
            and event.propagation_as_span_links
            and getattr(event, "request_headers", None)
            and trace_utils.distributed_tracing_enabled(event.integration_config)
        ):
            propagated_context = HTTPPropagator.extract(  # type: ignore[no-untyped-call]
                cast(dict[str, str], event.request_headers)
            )
            event.activate_distributed_headers = False

        super()._on_context_started(ctx)

        span = span_from_context(ctx)
        if propagated_context is not None and propagated_context.trace_id and propagated_context.span_id:
            span.link_span(propagated_context)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[MessagingEvent]) -> None:
        event = ctx.event
        span = span_from_context(ctx)

        if event.messaging_system is not None:
            span._set_attribute(MESSAGING_SYSTEM, event.messaging_system)
        semantic_operation = event.semantic_operation
        if semantic_operation is None and isinstance(event, MessagingActionEvent):
            semantic_operation = event.action
        if semantic_operation is not None:
            span._set_attribute(MESSAGING_OPERATION, semantic_operation)
        if event.destination is not None:
            span._set_attribute(MESSAGING_DESTINATION_NAME, event.destination)

        if isinstance(event, MessagingReceiveEvent) and event.start_ns is not None:
            span.start_ns = event.start_ns

        if (
            isinstance(event, MessagingProducerEvent)
            and event.distributed_headers is not None
            and trace_utils.distributed_tracing_enabled(event.integration_config)
        ):
            HTTPPropagator.inject(
                span.context,
                cast(dict[str, str], event.distributed_headers),
            )
