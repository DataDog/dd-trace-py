from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
from ddtrace.contrib._events.messaging import MessagingEvents
from ddtrace.contrib._events.messaging import MessagingPublishEvent
from ddtrace.internal import core
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.propagation.http import HTTPPropagator


class MessagingTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for all messaging integrations.

    Sets standard messaging tags on spans and handles distributed tracing
    header injection for publish operations.  Extraction is handled during
    event construction (see MessagingConsumeEvent.__post_init__).

    Integration-specific event names are registered so that both this
    subscriber and integration-scoped handlers (e.g. DSM) fire on the
    same context identifier.
    """

    event_names = (
        f"{MessagingEvents.PUBLISH.value}.aio_pika",
        f"{MessagingEvents.CONSUME.value}.aio_pika",
        f"{MessagingEvents.ACTION.value}.aio_pika",
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event = ctx.event
        span = ctx.span

        span._set_attribute(MESSAGING_SYSTEM, event.messaging_system)
        span._set_attribute(MESSAGING_DESTINATION_NAME, event.destination)

        if isinstance(event, MessagingPublishEvent):
            span._set_attribute(MESSAGING_OPERATION, "publish")
            if event.distributed_tracing_enabled:
                HTTPPropagator.inject(span.context, cast(dict[str, str], event.headers))
        elif isinstance(event, (MessagingConsumeEvent,)):
            span._set_attribute(MESSAGING_OPERATION, event.operation)
        elif hasattr(event, "operation"):
            span._set_attribute(MESSAGING_OPERATION, event.operation)
