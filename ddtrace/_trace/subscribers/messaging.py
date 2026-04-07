from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.messaging import MessagingPublishEvent
from ddtrace.internal import core
from ddtrace.propagation.http import HTTPPropagator


class MessagingTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for all messaging integrations.

    Handles distributed tracing header injection for publish operations.
    Extraction is handled during event construction (see MessagingConsumeEvent.__post_init__).
    """

    event_names = (
        "messaging.publish",
        "messaging.consume",
        "messaging.action",
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event = ctx.event
        if isinstance(event, MessagingPublishEvent) and event.distributed_tracing_enabled:
            HTTPPropagator.inject(ctx.span.context, cast(dict[str, str], event.headers))
