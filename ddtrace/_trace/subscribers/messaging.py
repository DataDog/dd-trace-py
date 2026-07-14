from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.messaging import AioPikaEvents
from ddtrace.contrib._events.messaging import MessagingActionEvent
from ddtrace.contrib._events.messaging import MessagingConsumeEvent
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
        MessagingPublishEvent.event_name,
        MessagingConsumeEvent.event_name,
        MessagingActionEvent.event_name,
        AioPikaEvents.PUBLISH.value,
        AioPikaEvents.CONSUME.value,
        AioPikaEvents.ACTION.value,
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
        elif hasattr(event, "operation"):
            span._set_attribute(MESSAGING_OPERATION, event.operation)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,  # type: ignore[type-arg]
        _exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event = ctx.event
        if isinstance(event, MessagingConsumeEvent) and event.start_ns:
            # Queue.get must run before its span starts so that distributed
            # context can be extracted from the returned message. Preserve the
            # operation's full duration by moving the span start back to the
            # timestamp captured immediately before the call.
            ctx.span.start_ns = event.start_ns
