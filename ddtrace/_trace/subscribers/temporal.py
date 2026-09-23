from ddtrace.contrib._events.temporal import TemporalWorkflowEvent
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.internal import core
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.contrib.internal.temporal.utils import get_workflow_attributes


class TemporalWorkflowSubscriber(TracingSubscriber[TemporalWorkflowEvent]):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_names = (TemporalWorkflowEvent.event_name)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: TemporalWorkflowEvent = ctx.event
        span: Span = span_from_context(ctx)

        span.set_tags(get_workflow_attributes(event.input))