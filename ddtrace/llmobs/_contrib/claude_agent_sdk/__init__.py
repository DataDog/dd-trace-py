from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib._events.span_lifecycle import SpanLifecycleEvents
from ddtrace.contrib.internal.claude_agent_sdk.utils import LLMOBS_ENABLED_QUERY_EVENT
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.claude_agent_sdk import ClaudeAgentSdkIntegration
from ddtrace.llmobs._llmobs import LLMObs


COMPONENT = "claude_agent_sdk"
_PREVIOUS_LLMOBS_ACTIVE_KEY = "llmobs.claude_agent_sdk.previous_active"
_NOT_STASHED = object()


class _ClaudeAgentSdkInactiveSpanStartingSubscriber(Subscriber):
    """Remember the active LLMObs span before an inactive (llm or tool) span starts.

    LLMObs activates every LLM-type span it sees start, regardless of whether the tracer
    activated it. The claude_agent_sdk llm and tool spans stay open while the turn is still
    streaming, so anything started meanwhile would parent under them instead of the step.
    """

    auto_register = False
    event_names = (f"{SpanLifecycleEvents.SPAN_STARTING.value}.{LlmRequestEvent.event_name}",)

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        if ctx.event.component != COMPONENT or ctx.event.activate or not LLMObs.enabled:
            return
        ctx.set_item(_PREVIOUS_LLMOBS_ACTIVE_KEY, LLMObs._instance._llmobs_context_provider.active())


class _ClaudeAgentSdkInactiveSpanStartedSubscriber(Subscriber):
    """Restore the LLMObs span that was active before an inactive span started."""

    auto_register = False
    event_names = (f"{SpanLifecycleEvents.SPAN_STARTED.value}.{LlmRequestEvent.event_name}",)

    @classmethod
    def on_event(cls, ctx: core.ExecutionContext[LlmRequestEvent]) -> None:
        if ctx.event.component != COMPONENT:
            return
        previous = ctx.get_item(_PREVIOUS_LLMOBS_ACTIVE_KEY, _NOT_STASHED)
        if previous is _NOT_STASHED or not LLMObs.enabled:
            return
        LLMObs._instance._llmobs_context_provider.activate(previous)


def _on_llmobs_enabled_query(state: dict[str, bool]) -> None:
    # Read LLMObs.enabled itself, the same flag the span subscribers check, so the contrib's
    # decision to force partial messages can never disagree with whether spans get LLMObs data.
    state["enabled"] = bool(LLMObs.enabled)


def listen() -> None:
    register(COMPONENT, ClaudeAgentSdkIntegration)
    core.on(LLMOBS_ENABLED_QUERY_EVENT, _on_llmobs_enabled_query)
    _ClaudeAgentSdkInactiveSpanStartingSubscriber.register()
    _ClaudeAgentSdkInactiveSpanStartedSubscriber.register()


def unlisten() -> None:
    unregister(COMPONENT)
    core.reset_listeners(LLMOBS_ENABLED_QUERY_EVENT, _on_llmobs_enabled_query)
