from typing import ClassVar
from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmEvents
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.span_bus import span_from_context
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._integrations.anthropic import AnthropicIntegration


# Must match ddtrace.contrib.internal.anthropic.patch.COMPONENT. Duplicated so this module
# does not import the contrib, which imports the anthropic library.
ANTHROPIC_COMPONENT = "anthropic"


class LLMObsAnthropicSubscriber(Subscriber):
    """Base for LLMObs subscribers to the Anthropic contrib's LlmEvents.

    The events are shared by every LLM integration, so handlers ignore contexts
    whose event belongs to another component.
    """

    auto_register = False
    _integration: ClassVar[Optional[AnthropicIntegration]] = None

    @classmethod
    def integration(cls) -> AnthropicIntegration:
        # config.anthropic is only defined once the contrib is imported, so build lazily.
        if LLMObsAnthropicSubscriber._integration is None:
            LLMObsAnthropicSubscriber._integration = AnthropicIntegration(integration_config=config.anthropic)
        return LLMObsAnthropicSubscriber._integration

    @staticmethod
    def _is_anthropic(ctx: core.ExecutionContext[LlmRequestEvent]) -> bool:
        return ctx.event.component == ANTHROPIC_COMPONENT


class LLMObsAnthropicSpanStartingSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_STARTING.value,)

    @classmethod
    def on_event(cls, event_instance: core.ExecutionContext[LlmRequestEvent]) -> None:
        # LlmTracingSubscriber dispatches the context rather than the event.
        ctx = event_instance
        if not cls._is_anthropic(ctx):
            return
        event = ctx.event
        if event.submit_to_llmobs and cls.integration().llmobs_enabled:
            event.span_type = SpanTypes.LLM


class LLMObsAnthropicSpanStartedSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_STARTED.value,)

    @classmethod
    def on_event(cls, event_instance: core.ExecutionContext[LlmRequestEvent]) -> None:
        # LlmTracingSubscriber dispatches the context rather than the event.
        ctx = event_instance
        if not cls._is_anthropic(ctx):
            return
        event = ctx.event
        span = span_from_context(ctx)
        integration = cls.integration()
        if event.submit_to_llmobs:
            span._set_attribute(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY, 1 if integration.llmobs_enabled else 0)
        integration._set_base_span_tags(span, instance=event.instance)
        if integration._is_instrumented_proxy_url(integration._get_base_url(instance=event.instance)):  # type: ignore[arg-type]
            span._set_ctx_item(PROXY_REQUEST, True)
        integration._annotate_integration_tag(span)
        integration._stamp_llmobs_span_kind_at_start(span, event.operation, operation=event.operation)


class LLMObsAnthropicSpanFinishingSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_FINISHING.value,)

    @classmethod
    def on_event(cls, event_instance: core.ExecutionContext[LlmRequestEvent]) -> None:
        # LlmTracingSubscriber dispatches the context rather than the event.
        ctx = event_instance
        if not cls._is_anthropic(ctx):
            return
        event = ctx.event
        cls.integration().llmobs_set_tags(
            span_from_context(ctx),
            args=[],
            kwargs=event.request_kwargs,
            response=event.response,
            operation=event.operation,
        )
