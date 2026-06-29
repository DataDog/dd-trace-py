from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.messaging import AIO_PIKA_ACTION_EVENT
from ddtrace.contrib._events.messaging import AIO_PIKA_CONSUME_EVENT
from ddtrace.contrib._events.messaging import AIO_PIKA_PUBLISH_EVENT
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingEvents
from ddtrace.contrib._events.messaging import MessagingPublishEvent
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.propagation.http import HTTPPropagator


class MessagingTracingSubscriber(TracingSubscriber):  # type: ignore[type-arg, no-untyped-call]
    """Shared tracing logic for all messaging integrations.

    Sets standard messaging tags on spans and handles distributed tracing
    header injection/extraction.

    Integration-specific event names are registered so that both this
    subscriber and integration-scoped handlers (e.g. DSM) fire on the
    same context identifier.
    """

    event_names = (
        MessagingEvents.PUBLISH.value,
        MessagingEvents.CONSUME.value,
        MessagingEvents.ACTION.value,
        AIO_PIKA_PUBLISH_EVENT,
        AIO_PIKA_CONSUME_EVENT,
        AIO_PIKA_ACTION_EVENT,
    )

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext) -> None:  # type: ignore[type-arg]
        event = ctx.event
        if isinstance(event, MessagingConsumeEvent) and event.distributed_tracing_enabled and event.headers:
            event.distributed_context = HTTPPropagator.extract(event.headers)  # type: ignore[no-untyped-call]
            event.use_active_context = False

        super()._on_context_started(ctx)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:  # type: ignore[type-arg]
        event = ctx.event
        span = ctx.span

        span._set_attribute(MESSAGING_SYSTEM, event.messaging_system)
        span._set_attribute(MESSAGING_DESTINATION_NAME, event.destination)

        if isinstance(event, MessagingPublishEvent):
            span._set_attribute(MESSAGING_OPERATION, "publish")
            if event.distributed_tracing_enabled:
                HTTPPropagator.inject(span.context, cast(dict[str, str], event.headers))
        elif isinstance(event, MessagingConsumeEvent):
            span._set_attribute(MESSAGING_OPERATION, event.operation)
        elif hasattr(event, "operation"):
            span._set_attribute(MESSAGING_OPERATION, event.operation)
