from ddtrace.contrib._events.llm import LlmEvents
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.anthropic._constants import COMPONENT
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import LLMObsLlmSubscriber
from ddtrace.llmobs._integrations.anthropic import AnthropicIntegration


class LLMObsAnthropicSubscriber(LLMObsLlmSubscriber):
    component = COMPONENT
    integration_cls = AnthropicIntegration


# Each subscriber defines its own on_event because Subscriber.__init_subclass__ binds
# on_event to the class that defines it; inheriting one from a base would leave
# `component` unresolved. The bodies delegate straight to LLMObsLlmSubscriber.
class LLMObsAnthropicSpanStartingSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_STARTING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_starting(event_instance)


class LLMObsAnthropicSpanStartedSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_STARTED.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_started(event_instance)


class LLMObsAnthropicSpanFinishingSubscriber(LLMObsAnthropicSubscriber):
    event_names = (LlmEvents.SPAN_FINISHING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_finishing(event_instance)
