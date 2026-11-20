from ddtrace._trace.subscribers._base import SpanLifecycleSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.span_bus import span_from_context


class LlmTracingSubscriber(SpanLifecycleSubscriber["LlmRequestEvent"]):
    """Shared tracing logic for all LLM integrations.

    Handles span creation and finishing. Products tag the span through
    subscribers of the SpanLifecycleEvents dispatched for llm.request.
    """

    event_names = (LlmRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext["LlmRequestEvent"]) -> None:
        span = span_from_context(ctx)

        # Remove component/span.kind tags set by _start_span — the old
        # BaseLLMIntegration.trace() never set these, so existing snapshot
        # tests expect them absent.
        # TODO: keep these tags once snapshots are updated
        span._remove_attribute(COMPONENT)
        span._remove_attribute(SPAN_KIND)
