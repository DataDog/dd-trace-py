from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._constants import PROXY_REQUEST


log = get_logger(__name__)


class LlmTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for all LLM integrations.

    Handles span creation, base tag setting, proxy detection,
    and LLMObs tag extraction. Provider-specific logic is delegated
    to the integration object carried by the event.
    """

    event_name = LlmRequestEvent.event_name

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: LlmRequestEvent = ctx.event
        span = ctx.span

        # Remove component/span.kind tags set by _start_span — the old
        # BaseLLMIntegration.trace() never set these, so existing snapshot
        # tests expect them absent.
        # TODO: should we keep these tags?
        span._meta.pop(COMPONENT, None)
        span._meta.pop(SPAN_KIND, None)

        # Set base span tags (provider-specific)
        event.integration._set_base_span_tags(span, **(event.base_tag_kwargs or {}))

        # Proxy detection
        base_url = event.integration._get_base_url(**(event.base_tag_kwargs or {}))
        if event.integration._is_instrumented_proxy_url(base_url):
            span._set_ctx_item(PROXY_REQUEST, True)

        # LLMObs integration marker
        if event.integration.llmobs_enabled:
            span._set_ctx_item(INTEGRATION, event.integration_name)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: LlmRequestEvent = ctx.event
        is_stream = ctx.get_item("is_stream", False)
        has_error = exc_info[1] is not None

        # For streaming with errors, re-enable span finishing since StreamHandler
        # won't get to finalize. This matches the original behavior where
        # `span.error or not stream` controlled llmobs_set_tags + span.finish.
        if is_stream and has_error:
            event._end_span = True

        # Call llmobs_set_tags for non-streaming, or for streaming with errors
        if not is_stream or has_error:
            response = ctx.get_item("response")
            event.integration.llmobs_set_tags(
                ctx.span,
                args=ctx.get_item("llmobs_args", []),
                kwargs=event.request_kwargs,
                response=response,
                operation=ctx.get_item("operation", ""),
            )
