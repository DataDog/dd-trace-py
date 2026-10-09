from ddtrace.contrib._events.llm import LlmEvents
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import LLMObsLlmSubscriber
from ddtrace.llmobs._integrations.llama_index import LlamaIndexIntegration


# Must match ddtrace.contrib.internal.llama_index.patch.COMPONENT. Duplicated so this module
# does not import the contrib, which imports the llama_index library.
LLAMA_INDEX_COMPONENT = "llama_index"


class LLMObsLlamaIndexSubscriber(LLMObsLlmSubscriber):
    component = LLAMA_INDEX_COMPONENT
    integration_cls = LlamaIndexIntegration


# Each subscriber defines its own on_event because Subscriber.__init_subclass__ binds
# on_event to the class that defines it; inheriting one from a base would leave
# `component` unresolved. The bodies delegate straight to LLMObsLlmSubscriber.
class LLMObsLlamaIndexSpanStartingSubscriber(LLMObsLlamaIndexSubscriber):
    event_names = (LlmEvents.SPAN_STARTING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_starting(event_instance)


class LLMObsLlamaIndexSpanStartedSubscriber(LLMObsLlamaIndexSubscriber):
    event_names = (LlmEvents.SPAN_STARTED.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_started(event_instance)


class LLMObsLlamaIndexSpanFinishingSubscriber(LLMObsLlamaIndexSubscriber):
    event_names = (LlmEvents.SPAN_FINISHING.value,)

    @classmethod
    def on_event(cls, event_instance: "core.ExecutionContext[LlmRequestEvent]") -> None:
        cls._handle_span_finishing(event_instance)
