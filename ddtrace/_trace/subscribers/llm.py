from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.llm.apm import is_instrumented_proxy_url
from ddtrace.internal.llm.constants import PROXY_REQUEST
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context


log = get_logger(__name__)


class LlmTracingSubscriber(TracingSubscriber["LlmRequestEvent"]):
    """Shared tracing logic for all LLM integrations.

    Handles span creation, base tag setting, proxy detection,
    and LLMObs tag extraction. Provider-specific logic is delegated to the
    event's apm_tagger when present, and otherwise to its LLMObs integration.
    """

    event_names = (LlmRequestEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext["LlmRequestEvent"]) -> None:
        event: LlmRequestEvent = ctx.event
        span = span_from_context(ctx)

        # Remove component/span.kind tags set by _start_span — the old
        # BaseLLMIntegration.trace() never set these, so existing snapshot
        # tests expect them absent.
        # TODO: keep these tags once snapshots are updated
        span._remove_attribute(COMPONENT)
        span._remove_attribute(SPAN_KIND)

        integration = event.llmobs_integration
        tagger = event.apm_tagger
        if tagger is not None:
            tagger.set_base_span_tags(span, event.model, event.instance)
            base_url = tagger.get_base_url(event.instance)
        elif integration is not None:
            integration._set_base_span_tags(
                span,
                model=event.model,
                provider=event.provider,
                instance=event.instance,
            )
            base_url = integration._get_base_url(instance=event.instance)
        else:
            base_url = None

        if is_instrumented_proxy_url(base_url):
            span._set_ctx_item(PROXY_REQUEST, True)
        if integration is not None:
            integration._annotate_integration_tag(span)
            # Stamp kind at start; no ddtrace.llmobs import pulled into this module.
            integration._stamp_llmobs_span_kind_at_start(span, event.operation, operation=event.operation)

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
        span = span_from_context(ctx)
        integration = event.llmobs_integration
        tagger = event.apm_tagger
        if tagger is not None:
            try:
                tagger.set_apm_shadow_tags(span, event.response, integration is not None and integration.llmobs_enabled)
            except Exception:
                log.debug("Error setting APM shadow tags for span %s", span, exc_info=True)
        if integration is not None:
            integration.llmobs_set_tags(
                span,
                args=[],
                kwargs=event.request_kwargs,
                response=event.response,
                operation=event.operation,
                set_apm_shadow_tags=tagger is None,
            )
