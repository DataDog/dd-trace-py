"""LLMObs subscriber for the spans of every LLM integration, selected by the request's component."""

from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib._events.span_lifecycle import SpanLifecycleListener
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._registration import LLM_SUBSCRIBERS


_CONTEXT_KEY = "llmobs.integration"

_integration_classes: dict[str, type[BaseLLMIntegration]] = {}
_integrations: dict[str, BaseLLMIntegration] = {}


def register(component: str, integration_cls: type[BaseLLMIntegration]) -> None:
    _integration_classes[component] = integration_cls
    LLM_SUBSCRIBERS.register()


def unregister(component: str) -> None:
    _integration_classes.pop(component, None)
    _integrations.pop(component, None)
    # Shared listeners remain registered for other components and in-flight requests.


def integration_for(component: str) -> Optional[BaseLLMIntegration]:
    integration_cls = _integration_classes.get(component)
    if integration_cls is None:
        return None
    if component not in _integrations:
        _integrations[component] = integration_cls(integration_config=getattr(config, component))
    return _integrations[component]


class LLMObsLlmSubscriber(SpanLifecycleListener):
    """Tags the llm.request spans of integrations that leave llmobs_integration unset on their event.

    Runs whether or not LLMObs is enabled, so APM spans carry the LLMObs shadow tags either way.
    """

    event_names = (LlmRequestEvent.event_name,)

    @classmethod
    def on_starting(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        event = ctx.event
        # Integrations that pass their own integration are tagged by the tracing subscriber.
        if event.llmobs_integration is not None:
            return
        integration = integration_for(event.component)
        if integration is None:
            return
        # Keep the request's integration on its context so the request finishes with the one it started with.
        ctx.set_item(_CONTEXT_KEY, integration)
        if event.submit_to_llmobs and integration.llmobs_enabled:
            event.span_type = SpanTypes.LLM

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
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
        base_url = integration._get_base_url(instance=event.instance)  # type: ignore[arg-type]
        if integration._is_instrumented_proxy_url(base_url):
            span._set_ctx_item(PROXY_REQUEST, True)
        integration._annotate_integration_tag(span)
        integration._stamp_llmobs_span_kind_at_start(span, event.operation, operation=event.operation)

    @classmethod
    def on_finishing(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        integration: Optional[BaseLLMIntegration] = ctx.get_item(_CONTEXT_KEY)
        if integration is None:
            return
        event = ctx.event
        integration.llmobs_set_tags(
            span_from_context(ctx),
            args=[],
            kwargs=event.request_kwargs,
            response=event.response,
            operation=event.operation,
        )
