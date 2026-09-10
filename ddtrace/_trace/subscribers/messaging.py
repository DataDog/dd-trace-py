from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.messaging import MessagingEvent
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator


class MessagingTracingSubscriber(TracingSubscriber[MessagingEvent]):
    event_names = (
        MessagingProducerEvent.event_name,
        MessagingProcessEvent.event_name,
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[MessagingEvent]) -> None:
        event = ctx.event

        if (
            isinstance(event, MessagingProducerEvent)
            and event.distributed_headers is not None
            and trace_utils.distributed_tracing_enabled(event.integration_config)
        ):
            HTTPPropagator.inject(
                span_from_context(ctx).context,
                cast(dict[str, str], event.distributed_headers),
            )

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[MessagingEvent],
        _exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event = ctx.event
        if not isinstance(event, MessagingProcessEvent):
            return

        span = span_from_context(ctx)
        for link_ctx in event.span_links:
            if not link_ctx.trace_id or not link_ctx.span_id:
                continue
            span.link_span(link_ctx)
            for extracted_link in link_ctx._span_links:
                span.set_link(
                    trace_id=extracted_link.trace_id,
                    span_id=extracted_link.span_id,
                    tracestate=extracted_link.tracestate,
                    flags=extracted_link.flags,
                    attributes=extracted_link.attributes,
                )
