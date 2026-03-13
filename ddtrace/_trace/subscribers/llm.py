from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._constants import PROXY_REQUEST


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
        span._meta.pop(COMPONENT, None)
        span._meta.pop(SPAN_KIND, None)

        # Set base span tags (provider-specific)
        event.integration._set_base_span_tags(
            span,
            model=event.model,
            provider=event.provider,
            interface_type=event.interface_type,
            instance=event.instance,
        )

        # Proxy detection
        base_url = event.integration._get_base_url(instance=event.instance)  # type: ignore[arg-type]
        if event.integration._is_instrumented_proxy_url(base_url):
            span._set_ctx_item(PROXY_REQUEST, True)

        # LLMObs integration marker
        if event.integration.llmobs_enabled:
            span._set_ctx_item(INTEGRATION, event.integration_name)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext["LlmRequestEvent"],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # AIDEV-NOTE: This fires for both streaming and non-streaming paths.
        # For non-streaming, the context exits normally after setting response.
        # For streaming, the stream handler manually closes the context after
        # exhausting the stream and setting the response.
        event: LlmRequestEvent = ctx.event
        response = ctx.get_item("response")
        event.integration.llmobs_set_tags(
            ctx.span,
            args=[],
            kwargs=event.request_kwargs,
            response=response,
            operation=ctx.get_item("operation", ""),
        )
