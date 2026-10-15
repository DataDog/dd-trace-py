from ddtrace.contrib._events.llm import LlmEvents
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import LLMObsLlmSubscriber
from ddtrace.llmobs._integrations.mistralai import MistralAIIntegration


# Must match ddtrace.contrib.internal.mistralai.patch.COMPONENT. Duplicated so this module
# does not import the contrib, which imports the mistralai library.
MISTRALAI_COMPONENT = "mistralai"


class LLMObsMistralAISubscriber(LLMObsLlmSubscriber):
    component = MISTRALAI_COMPONENT
    integration_cls = MistralAIIntegration


# Each subscriber defines its own on_event because Subscriber.__init_subclass__ binds
# on_event to the class that defines it; inheriting one from a base would leave
# `component` unresolved. The bodies delegate straight to LLMObsLlmSubscriber.
class LLMObsMistralAISpanStartingSubscriber(LLMObsMistralAISubscriber):
    event_names = (LlmEvents.SPAN_STARTING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_starting(event_instance)


class LLMObsMistralAISpanStartedSubscriber(LLMObsMistralAISubscriber):
    event_names = (LlmEvents.SPAN_STARTED.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_started(event_instance)


class LLMObsMistralAISpanFinishingSubscriber(LLMObsMistralAISubscriber):
    event_names = (LlmEvents.SPAN_FINISHING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_finishing(event_instance)
