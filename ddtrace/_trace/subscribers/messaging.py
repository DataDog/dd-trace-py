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
