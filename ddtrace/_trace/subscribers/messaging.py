from types import TracebackType
from typing import MutableMapping
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
                cast(MutableMapping[str, str], event.distributed_headers),
            )

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[MessagingEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event = ctx.event

        if not isinstance(event, MessagingProcessEvent):
            return

        span = span_from_context(ctx)
        if event.failed:
            span.error = 1

        for key, value in event.result_tags.items():
            span._set_attribute(key, value)
