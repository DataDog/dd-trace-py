"""Shared LLMObs lifecycle subscribers selected by the request's component."""

from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib._events.span_lifecycle import SpanLifecycleEvents
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.span_bus import span_from_context
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


_integration_classes: dict[str, type[BaseLLMIntegration]] = {}
_integrations: dict[str, BaseLLMIntegration] = {}
_CONTEXT_KEY = "llmobs.integration"


def register(component: str, integration_cls: type[BaseLLMIntegration]) -> None:
    _integration_classes[component] = integration_cls
    LLMObsSpanStartingSubscriber.register()
    LLMObsSpanStartedSubscriber.register()
    LLMObsSpanFinishingSubscriber.register()


def unregister(component: str) -> None:
    _integration_classes.pop(component, None)
    _integrations.pop(component, None)
    # Shared listeners remain registered for other components and in-flight requests.


class LLMObsSpanStartingSubscriber(Subscriber):
    auto_register = False
    event_names = (f"{SpanLifecycleEvents.SPAN_STARTING.value}.{LlmRequestEvent.event_name}",)

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        component = ctx.event.component
        integration_cls = _integration_classes.get(component)
        if integration_cls is None:
            return
        integration = _integrations.get(component)
        if integration is None:
            integration = integration_cls(integration_config=getattr(config, component))
            _integrations[component] = integration
        # Keep the request's integration alive across unpatch and repatch.
        ctx.set_item(_CONTEXT_KEY, integration)
        if ctx.event.submit_to_llmobs and integration.llmobs_enabled:
            ctx.event.span_type = SpanTypes.LLM


class LLMObsSpanStartedSubscriber(Subscriber):
    auto_register = False
    event_names = (f"{SpanLifecycleEvents.SPAN_STARTED.value}.{LlmRequestEvent.event_name}",)

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        integration: Optional[BaseLLMIntegration] = ctx.get_item(_CONTEXT_KEY)
        if integration is None:
            return
        event = ctx.event
        span = span_from_context(ctx)
        if event.submit_to_llmobs:
            span._set_attribute(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY, 1 if integration.llmobs_enabled else 0)
        integration._set_base_span_tags(
            span,
            model=event.model,
            provider=event.provider,
            instance=event.instance,
        )
        base_url = integration._get_base_url(instance=event.instance, request_kwargs=event.request_kwargs)  # type: ignore[arg-type]
        if integration._is_instrumented_proxy_url(base_url):
            span._set_ctx_item(PROXY_REQUEST, True)
        integration._annotate_integration_tag(span)
        integration._stamp_llmobs_span_kind_at_start(span, event.operation, operation=event.operation)


class LLMObsSpanFinishingSubscriber(Subscriber):
    auto_register = False
    event_names = (f"{SpanLifecycleEvents.SPAN_FINISHING.value}.{LlmRequestEvent.event_name}",)

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        integration: Optional[BaseLLMIntegration] = ctx.get_item(_CONTEXT_KEY)
        if integration is None:
            return
        event = ctx.event
        integration.llmobs_set_tags(
            span_from_context(ctx),
            args=list(event.request_args),
            kwargs=event.request_kwargs,
            response=event.response,
            operation=event.operation,
        )
