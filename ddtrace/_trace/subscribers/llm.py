from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger


# Duplicated from ddtrace.llmobs._constants to avoid importing
# ddtrace.llmobs at module level (triggers LLMObs -> multiprocessing/threading chain).
_PROXY_REQUEST = "llmobs.proxy_request"


log = get_logger(__name__)


class LlmTracingSubscriber(TracingSubscriber["LlmRequestEvent"]):
    """Shared tracing logic for all LLM integrations.

    Handles span creation, base tag setting, proxy detection,
    and LLMObs tag extraction. Provider-specific logic is delegated
    to the integration object carried by the event.
    """

    event_names = (LlmRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext["LlmRequestEvent"]) -> None:
        event: LlmRequestEvent = ctx.event
        span = ctx.span

        # Remove component/span.kind tags set by _start_span — the old
        # BaseLLMIntegration.trace() never set these, so existing snapshot
        # tests expect them absent.
        # TODO: keep these tags once snapshots are updated
        span._remove_attribute(COMPONENT)
        span._remove_attribute(SPAN_KIND)

        event.llmobs_integration._set_base_span_tags(
            span,
            model=event.model,
            provider=event.provider,
            instance=event.instance,
        )

        base_url = event.llmobs_integration._get_base_url(instance=event.instance)  # type: ignore[arg-type]
        if event.llmobs_integration._is_instrumented_proxy_url(base_url):
            span._set_ctx_item(_PROXY_REQUEST, True)
        event.llmobs_integration._annotate_integration_tag(span)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext["LlmRequestEvent"],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Set LLMObs tags on the span.

        Fires for both streaming and non-streaming paths. For streaming
        with deferred dispatch, this fires when the stream handler calls
        dispatch_ended_event().
        """
        event: LlmRequestEvent = ctx.event
        event.llmobs_integration.llmobs_set_tags(
            ctx.span,
            args=[],
            kwargs=event.request_kwargs,
            response=event.response,
            operation=event.operation,
        )
