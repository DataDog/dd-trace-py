"""Shared base for LLMObs' subscribers to the LlmEvents span lifecycle.

Contribs dispatch LlmEvents without importing LLMObs; subscribers built on this
base do the LLMObs-side work on the other end of the event bus. An integration
supplies its component name and integration class, and gets the span lifecycle
handling below (see ddtrace/llmobs/_contrib/anthropic for the shape).

The handlers live in _handle_* methods rather than in on_event because
Subscriber.__init_subclass__ collects `on_event` bound to the class that *defines*
it, so a base's on_event would see the base as `cls` and never resolve `component`.
Each concrete subscriber therefore defines a one-line on_event of its own.
"""

from typing import ClassVar
from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.span_bus import span_from_context
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LLMObsLlmSubscriber(Subscriber):
    """Base for LLMObs subscribers to an LLM contrib's LlmEvents.

    The events are shared by every LLM integration, so handlers ignore contexts
    whose event belongs to another component.
    """

    auto_register = False

    # Must match the contrib's LlmRequestEvent.component, which is also the name of
    # its entry in `config`.
    component: ClassVar[str]
    integration_cls: ClassVar[type[BaseLLMIntegration]]

    _integrations: ClassVar[dict[str, BaseLLMIntegration]] = {}

    @classmethod
    def integration(cls) -> BaseLLMIntegration:
        # config.<component> is only defined once the contrib is imported, so build lazily.
        integration = LLMObsLlmSubscriber._integrations.get(cls.component)
        if integration is None:
            integration = cls.integration_cls(integration_config=getattr(config, cls.component))
            LLMObsLlmSubscriber._integrations[cls.component] = integration
        return integration

    @classmethod
    def forget_integration(cls) -> None:
        """Drop the cached integration so a later patch() rebuilds it from current config."""
        LLMObsLlmSubscriber._integrations.pop(cls.component, None)

    @classmethod
    def _context_for(
        cls, event_instance: "core.ExecutionContext[LlmRequestEvent]"
    ) -> Optional["core.ExecutionContext[LlmRequestEvent]"]:
        # LlmTracingSubscriber dispatches the context rather than the event.
        return event_instance if event_instance.event.component == cls.component else None

    @classmethod
    def _handle_span_starting(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        """Re-assert the LLM span type before the span is created.

        LlmRequestEvent.__post_init__ cannot decide this on its own now that the event
        carries no integration, and this is the last point where span_type can change.
        """
        ctx = cls._context_for(event_instance)
        if ctx is None:
            return
        if ctx.event.submit_to_llmobs and cls.integration().llmobs_enabled:
            ctx.event.span_type = SpanTypes.LLM

    @classmethod
    def _handle_span_started(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        ctx = cls._context_for(event_instance)
        if ctx is None:
            return
        event = ctx.event
        span = span_from_context(ctx)
        integration = cls.integration()
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
    def _handle_span_finishing(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        ctx = cls._context_for(event_instance)
        if ctx is None:
            return
        event = ctx.event
        cls.integration().llmobs_set_tags(
            span_from_context(ctx),
            args=[],
            kwargs=event.request_kwargs,
            response=event.response,
            operation=event.operation,
        )
